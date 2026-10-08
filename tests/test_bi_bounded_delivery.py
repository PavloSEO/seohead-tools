"""Selected export, index recovery and large-projection boundaries."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from seohead.reports import bi, bi_destinations
from seohead.reports.bi import BIExportError, export_bi
from seohead.reports.bi_destinations import BIDestinationError, export_bi_xlsx, filter_package
from seohead.reports.bi_index import GroupIndex, InlinkIndex, projection_index
from seohead.servers.bi_handlers import bi_filter


def package(tmp_path, *, title="=SUM(1,2)"):
    root = tmp_path / "package"
    export_bi(
        audit={
            "schema": "seohead.site-audit/1",
            "url": "https://example.test/",
            "site": {},
            "pages": [
                {"url": f"https://example.test/{i}", "status_code": 200, "title": title}
                for i in range(4)
            ],
            "findings": [],
            "summary": {
                "pages_checked": 4,
                "findings_total": 0,
                "tools_run": [],
                "tools_failed": [{"tool": "render", "error": "unavailable"}],
            },
        },
        out_dir=root,
    )
    return root


def test_filter_streams_hashes_and_keeps_source_coverage_and_omitted_population(
    tmp_path, monkeypatch
):
    root = package(tmp_path)
    monkeypatch.setattr(
        Path, "read_bytes", lambda *_: pytest.fail("whole-file reads are forbidden")
    )
    result = bi_filter(
        str(root),
        "pages",
        str(tmp_path / "selected"),
        where={"url": ["https://example.test/0", "https://example.test/3"]},
        columns=["url", "title"],
        max_rows_per_file=1,
    )
    assert result["conservation"] == {
        "source_rows": 4,
        "selected_rows": 2,
        "omitted_rows": 2,
        "source_states": {"not_declared": 4},
        "selected_states": {"not_declared": 2},
        "omission_reason": "explicit exact-filter selection; not evidence of resolution",
    }
    assert result["source"]["crawl_completeness"]["state"] == "unknown"
    assert result["source_coverage"]["coverage"]["source_rows"] == 4
    assert [part["rows"] for part in result["partitions"]] == [1, 1]
    rows = []
    for part in result["partitions"]:
        path = tmp_path / "selected" / part["path"]
        with path.open("rb") as stream:
            assert hashlib.sha256(stream.read()).hexdigest() == part["sha256"]
        with path.open(newline="") as stream:
            rows.extend(csv.DictReader(stream))
    assert [row["title"] for row in rows] == ["'=SUM(1,2)"] * 2


@pytest.mark.parametrize("failure", ["low_disk", "bytes", "interrupt"])
def test_filter_failure_never_publishes_partial_output_or_changes_source(
    tmp_path, monkeypatch, failure
):
    root = package(tmp_path)
    original = {path.name: path.read_bytes() for path in root.iterdir()}
    kwargs = {}
    error = BIDestinationError
    if failure == "low_disk":
        monkeypatch.setattr(bi.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    elif failure == "bytes":
        kwargs = {"max_bytes_per_file": 1024, "max_output_bytes": 1024}
    else:
        monkeypatch.setattr(
            bi._PartitionWriter,
            "write_cells",
            lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        error = KeyboardInterrupt
    with pytest.raises(error):
        filter_package(root, dataset="pages", out_dir=tmp_path / "selected", **kwargs)
    assert not (tmp_path / "selected").exists()
    assert not list(tmp_path.glob(".seohead-bi-filter-*"))
    assert original == {path.name: path.read_bytes() for path in root.iterdir()}


def test_xlsx_index_maps_every_selected_row_and_is_the_completion_marker(tmp_path):
    root = package(tmp_path)
    selected = tmp_path / "selected"
    projection = filter_package(
        root, dataset="pages", out_dir=selected, columns=["url", "title"], max_rows_per_file=3
    )
    result = export_bi_xlsx(
        selected, dataset="pages", out=tmp_path / "pages.xlsx", max_rows_per_sheet=2
    )
    index = json.loads(Path(result["index"]).read_text())
    assert index["state"] == "complete"
    assert index["conservation"] == projection["conservation"]
    assert index["rows"] == 4
    assert [
        (
            r["partition"],
            r["source_row_start"],
            r["source_row_end"],
            r["worksheet"],
            r["worksheet_row_start"],
            r["worksheet_row_end"],
        )
        for r in index["ranges"]
    ] == [
        ("pages-0001.csv", 1, 2, "pages-0001", 2, 3),
        ("pages-0001.csv", 3, 3, "pages-0002", 2, 2),
        ("pages-0002.csv", 1, 1, "pages-0002", 3, 3),
    ]
    with Path(result["output"]).open("rb") as stream:
        assert hashlib.sha256(stream.read()).hexdigest() == index["workbook_sha256"]


def test_xlsx_refuses_cell_truncation_and_retains_complete_csv(tmp_path):
    root = package(tmp_path, title="x" * 32_768)
    with pytest.raises(BIDestinationError, match="32767-character"):
        export_bi_xlsx(root, dataset="pages", out=tmp_path / "pages.xlsx")
    assert (root / "manifest.json").exists()
    assert not (tmp_path / "pages.xlsx").exists()
    assert not (tmp_path / "pages.xlsx.index.json").exists()


def test_xlsx_index_publication_failure_rolls_back_new_workbook(tmp_path, monkeypatch):
    root = package(tmp_path)
    replace = bi_destinations.os.replace

    def fail_index(source, target):
        if str(target).endswith(".index.json"):
            raise OSError("synthetic disk failure")
        return replace(source, target)

    monkeypatch.setattr(bi_destinations.os, "replace", fail_index)
    with pytest.raises(OSError, match="synthetic disk failure"):
        export_bi_xlsx(root, dataset="pages", out=tmp_path / "pages.xlsx")
    assert not (tmp_path / "pages.xlsx").exists()
    assert not list(tmp_path.glob(".seohead-bi-xlsx*"))


def test_group_index_uses_disk_bound_and_cleans_up_after_failure(tmp_path):
    with (
        pytest.raises(BIExportError, match="disk bound"),
        projection_index(tmp_path, 32 * 1024) as con,
    ):
        GroupIndex(con, ({"id": str(i), "value": "x" * 8192} for i in range(100)))
    assert not list(tmp_path.glob(".seohead-bi-index-*"))


def test_quadrants_stream_repeated_observations_and_keep_ambiguity(tmp_path):
    def observations(metric, count):
        for i in range(count):
            yield {
                "row": {"dimensions": {}, "row_index": i},
                "metric": {"name": metric, "unit": "count"},
                "entry": {"state": "measured", "value": 0},
                "population": "matched",
                "url": {"state": "keyed", "normalized": "https://example.test/a"},
                "matched_page_count": 1,
            }

    def source(provider, metric, count):
        return (
            SimpleNamespace(source_id=provider),
            observations(metric, count),
            {
                "evidence": {
                    "provider": provider,
                    "collection": {
                        "state": "complete",
                        "sampled": False,
                        "thresholded": False,
                        "truncated": False,
                    },
                    "field_origins": {
                        name: "declared"
                        for name in ("collection_state", "sampled", "thresholded", "truncated")
                    },
                    "timezone": "UTC",
                    "period": {"start_date": "2026-01-01", "end_date": "2026-01-07"},
                }
            },
        )

    with projection_index(tmp_path, 1024 * 1024) as con:
        pairs, _, blocked = bi._quadrant_candidates(
            [source("gsc", "clicks", 20_000), source("ga4", "sessions", 1)], "clicks", con=con
        )
        assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
        assert pairs.get("https://example.test/a") is None
        assert "more than one complete" in blocked.get("https://example.test/a")


def test_inlink_share_excludes_unmeasured_sources_and_counts_repeated_links_once(tmp_path):
    pages = [
        {"url": "https://example.test/a", "document_id": 1, "content_type": "text/html"},
        {"url": "https://example.test/b", "document_id": 2, "content_type": "text/html"},
        {"url": "https://example.test/c", "document_id": None, "content_type": "text/html"},
    ]
    links = [
        {"source_url": source, "destination_url": "https://example.test/b"}
        for source in [pages[0]["url"], pages[0]["url"], pages[2]["url"]]
    ]
    run = SimpleNamespace(pages_factory=lambda: iter(pages), links_factory=lambda: iter(links))
    with projection_index(tmp_path, 1024 * 1024) as con:
        index = InlinkIndex(con, run)
        assert index.denominator == 2
        assert index.numerator(pages[1]["url"]) == 1
        assert index.numerator(pages[2]["url"]) == 0
