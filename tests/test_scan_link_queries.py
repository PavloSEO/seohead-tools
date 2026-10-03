"""Offline operator queries over retained, occurrence-preserving scan links."""

from __future__ import annotations

import pytest

from seohead.storage import ScanError, open_scan
from seohead.storage.link_queries import reverse_inlinks, shortest_observed_path
from seohead.storage.native_scan import NativeScan
from tests.test_native_capture import _renderer
from tests.test_scan_native import _link, _metadata, _record, _runtime

ROOT = "https://example.test/"
A = ROOT + "a"
B = ROOT + "b"
TARGET = ROOT + "target"


def _scan(tmp_path, edges, *, pages=(ROOT, A, B, TARGET), partial=False):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([(url, index) for index, url in enumerate(pages)])
        for url in pages:
            lease = scan.claim(1)[0]
            assert lease.url == url
            record = _record(url)
            record["status_code"] = 200
            record["crawl_depth"] = lease.depth
            runtime = _runtime()
            runtime["max_depth_reached"] = lease.depth
            scan.commit_page(
                lease,
                record,
                links=edges.get(url, []),
                runtime=runtime,
            )
        if partial:
            scan.interrupt("synthetic partial graph")
        else:
            scan.finish_capture()
    return path


def _edge(source, destination, *, position="", nofollow=False):
    edge = _link(source, destination, destination.rsplit("/", 1)[-1])
    edge["position"] = position
    edge["nofollow"] = nofollow
    return edge


def test_shortest_path_uses_deterministic_first_occurrence_and_scan_records(tmp_path):
    path = _scan(
        tmp_path,
        {
            ROOT: [
                _edge(ROOT, B, position="nav"),
                _edge(ROOT, A, position="content"),
                _edge(ROOT, B, position="footer"),
            ],
            A: [_edge(A, TARGET)],
            B: [_edge(B, ROOT), _edge(B, TARGET + "#heading")],
        },
    )
    first = shortest_observed_path(path, ROOT, TARGET)
    second = shortest_observed_path(path, ROOT, TARGET)
    assert first == second
    assert first["state"] == "found"
    assert [hop["source_url"] for hop in first["hops"]] == [ROOT, B]
    assert [hop["destination_url"] for hop in first["hops"]] == [B, TARGET + "#heading"]
    assert first["coverage"]["global_reachability"] == "unknown"
    with open_scan(path, require_audit=False) as con:
        for hop in first["hops"]:
            row = con.execute("SELECT * FROM links WHERE link_id=?", (hop["link_id"],)).fetchone()
            assert row is not None
            assert row["source_document_id"] == hop["source_document_id"]
            assert row["ordinal"] == hop["ordinal"]
    assert shortest_observed_path(path, ROOT, ROOT)["hops"] == []


def test_unreachable_and_limited_are_not_orphan_claims(tmp_path):
    path = _scan(tmp_path, {ROOT: [_edge(ROOT, A)], A: [_edge(A, ROOT)]}, partial=True)
    missing = shortest_observed_path(path, ROOT, TARGET)
    assert missing["state"] == "unreachable_in_observed_graph"
    assert missing["coverage"]["state"] == "partial"
    assert missing["coverage"]["global_reachability"] == "unknown"
    for limits, reason in (
        ({"max_edges": 1}, "edge_budget_exhausted"),
        ({"max_nodes": 1}, "node_budget_exhausted"),
        ({"max_depth": 0}, "depth_budget_exhausted"),
    ):
        result = shortest_observed_path(path, ROOT, TARGET, **limits)
        assert result["state"] == "limit_reached"
        assert result["reason"] == reason
        assert result["coverage"]["global_reachability"] == "unknown"
    assert shortest_observed_path(path, ROOT + "unknown", TARGET)["state"] == "seed_unobserved"


def test_nofollow_external_and_representation_filter(tmp_path):
    outside = "https://outside.test/page"
    path = _scan(
        tmp_path,
        {
            ROOT: [
                _edge(ROOT, TARGET, nofollow=True),
                _edge(ROOT, outside),
                _edge(ROOT, A),
            ],
            A: [_edge(A, TARGET)],
        },
    )
    result = shortest_observed_path(path, ROOT, TARGET)
    assert result["state"] == "found"
    assert [hop["source_url"] for hop in result["hops"]] == [ROOT, A]
    rendered = shortest_observed_path(path, ROOT, TARGET, representation="rendered")
    assert rendered["state"] == "unavailable"
    assert "rendered representation was not captured" in rendered["coverage"]["reasons"]


def test_raw_and_rendered_edges_are_separate_observations(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([(ROOT, 0), (TARGET, 1)])
        for url, links in ((ROOT, [_edge(ROOT, A)]), (TARGET, [])):
            lease = scan.claim(1)[0]
            record = _record(url)
            record["crawl_depth"] = lease.depth
            runtime = _runtime()
            runtime["max_depth_reached"] = lease.depth
            scan.commit_page(lease, record, links=links, runtime=runtime)
        rendered_record = _record(ROOT)
        rendered_record["representation"] = "rendered"
        scan.commit_render(
            ROOT,
            rendered_record,
            html="<html><body><a href='/target'>Rendered</a></body></html>",
            renderer=_renderer(ROOT),
            captured_at="2026-10-03T00:00:00Z",
            links=[_edge(ROOT, TARGET)],
        )
        scan.finish_capture()
    assert shortest_observed_path(path, ROOT, TARGET, representation="static")["state"] == (
        "unreachable_in_observed_graph"
    )
    rendered = shortest_observed_path(path, ROOT, TARGET, representation="rendered")
    assert rendered["state"] == "found"
    assert rendered["hops"][0]["evidence_representation"] == "rendered"
    assert rendered["hops"][0]["source_document_id"] is not None
    assert reverse_inlinks(path, TARGET, representation="static")["items"] == []
    assert reverse_inlinks(path, TARGET, representation="rendered")["returned"] == 1


def test_reverse_occurrences_are_distinct_paginated_and_fragment_aware(tmp_path):
    path = _scan(
        tmp_path,
        {
            ROOT: [
                _edge(ROOT, TARGET, position="nav"),
                _edge(ROOT, TARGET + "#x", position="content"),
                _edge(ROOT, TARGET, position="footer"),
            ],
            A: [_edge(A, TARGET)],
            B: [_edge(B, ROOT.rstrip("/") + "#top")],
        },
    )
    first = reverse_inlinks(path, TARGET, limit=2)
    second = reverse_inlinks(path, TARGET, limit=2, after_link_id=first["next_after_link_id"])
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert first["returned"] == second["returned"] == 2
    items = first["items"] + second["items"]
    assert [item["link_id"] for item in items] == sorted(item["link_id"] for item in items)
    assert [item["position"] for item in items] == ["nav", "content", "footer", None]
    assert items[1]["destination_url"] == TARGET + "#x"
    assert all(item["source_url_id"] > 0 for item in items)
    assert reverse_inlinks(path, TARGET + "#ignored")["items"] == items
    root_inlinks = reverse_inlinks(path, ROOT)
    assert root_inlinks["returned"] == 1
    assert root_inlinks["items"][0]["destination_url"] == ROOT.rstrip("/") + "#top"


def test_reverse_byte_budget_returns_a_resumable_page(tmp_path):
    large = _edge(ROOT, TARGET)
    large["anchor"] = "x" * 3000
    path = _scan(tmp_path, {ROOT: [large, large]})
    first = reverse_inlinks(path, TARGET, max_bytes=4096)
    assert first["returned"] == 1
    assert first["has_more"] is True
    second = reverse_inlinks(
        path, TARGET, max_bytes=4096, after_link_id=first["next_after_link_id"]
    )
    assert second["returned"] == 1
    assert second["has_more"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"representation": "unknown"},
        {"max_nodes": 0},
        {"max_edges": True},
        {"max_depth": -1},
        {"timeout_seconds": 0},
    ],
)
def test_invalid_path_options_are_rejected_before_scanning(tmp_path, kwargs):
    with pytest.raises(ScanError):
        shortest_observed_path(tmp_path / "none.sqlite", ROOT, TARGET, **kwargs)


def test_invalid_reverse_options_and_url_are_rejected(tmp_path):
    with pytest.raises(ScanError):
        reverse_inlinks(tmp_path / "none.sqlite", "file:///etc/passwd")
    with pytest.raises(ScanError):
        reverse_inlinks(tmp_path / "none.sqlite", TARGET, limit=501)
