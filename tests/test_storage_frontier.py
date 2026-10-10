"""Negative and limit cases for frontier seeding, through the public NativeScan API."""

import pytest

from seohead.storage import ScanError
from seohead.storage.frontier import apply_candidates
from seohead.storage.native_scan import NativeScan
from tests.test_scan_native import _metadata


def seed(url, **overrides):
    entry = {
        "requested_url": url,
        "frontier_url": url,
        "depth": 0,
        "reason": "",
        "source": "sitemap",
        "reserve_query": True,
        "seed": True,
    }
    entry.update(overrides)
    return entry


def test_query_variant_limit_excludes_seed_without_reserving_a_slot(tmp_path):
    with NativeScan.create(
        tmp_path / "scan.sqlite", **_metadata(**{"limits.max_query_variants_per_path": 1})
    ) as scan:
        assert (
            scan.seed_frontier(
                [seed("https://example.test/", seed=False, reserve_query=False, source="start")]
            )["queued"]
            == 1
        )
        assert scan.seed_frontier([seed("https://example.test/a?one")])["queued"] == 1
        result = scan.seed_frontier([seed("https://example.test/a?two")])
        assert result == {"queued": 0, "excluded": 1, "already_seen": 0}
        assert tuple(scan.con.execute("SELECT url,reason FROM decisions").fetchone()) == (
            "https://example.test/a?two",
            "query_variants_limit",
        )
        assert [tuple(row) for row in scan.con.execute("SELECT * FROM query_variants")] == [
            ("/a", "one")
        ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"seed": False, "reserve_query": False, "source": "start"},  # not the scan start
        {"depth": 1},
        {"depth": "0"},
        {"reserve_query": "yes"},
        {"source": ""},
        {"frontier_url": "https://example.test/b"},  # request text must match identity
    ],
)
def test_invalid_seed_is_rejected_and_writes_nothing(tmp_path, overrides):
    with NativeScan.create(tmp_path / "scan.sqlite", **_metadata()) as scan:
        scan.seed_frontier(
            [seed("https://example.test/", seed=False, reserve_query=False, source="start")]
        )
        before = scan.con.execute("SELECT COUNT(*) FROM frontier").fetchone()[0]
        bad = seed("https://example.test/x", **overrides)
        if overrides.get("frontier_url"):
            bad["requested_url"] = "https://example.test/x"
        with pytest.raises(ScanError):
            scan.seed_frontier([bad])
        assert scan.con.execute("SELECT COUNT(*) FROM frontier").fetchone()[0] == before


def test_seed_with_unknown_field_is_rejected(tmp_path):
    with NativeScan.create(tmp_path / "scan.sqlite", **_metadata()) as scan:
        bad = seed("https://example.test/x")
        bad["extra"] = 1
        with pytest.raises(ScanError, match="unknown or missing fields"):
            scan.seed_frontier([bad])


def test_candidate_with_unknown_field_is_rejected(tmp_path):
    with (
        NativeScan.create(tmp_path / "scan.sqlite", **_metadata()) as scan,
        pytest.raises(ScanError, match="unknown or missing fields"),
    ):
        apply_candidates(
            scan.con,
            [{"path_key": "/", "query_key": ""}],
            source="page",
            queue_ordinal=0,
            limit=0,
        )
