"""Context: duplicate URL rows must not desync pages/page_by_url."""

from __future__ import annotations

import csv
import itertools
from pathlib import Path

import pytest

from seohead.crawl.settings import fingerprint
from seohead.crawl.settings import load as load_crawl_settings
from seohead.sf.config import load_config
from seohead.sf.core.aggregate import aggregate
from seohead.sf.core.context import AuditContext, _DiskPages
from seohead.sf.core.loader import load_exports
from seohead.sf.core.models import Page
from seohead.storage.audit_v2 import AuditV2Reader
from seohead.storage.native_scan import NativeScan


def test_duplicate_urls_collapse(tmp_path):
    rows = [
        ["https://example.com/", "text/html", "200", "OK", "Indexable"],
        ["https://example.com/", "text/html", "200", "OK", "Indexable"],  # duplicate
        ["https://example.com/x", "text/html", "200", "OK", "Indexable"],
    ]
    p = tmp_path / "internal_all.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        w.writerows(rows)
    ctx = AuditContext(load_exports(str(tmp_path)), load_config(None))
    urls = [pg.url for pg in ctx.pages]
    assert urls == ["https://example.com/", "https://example.com/x"]
    assert len(ctx.page_by_url) == len(ctx.pages)


def test_html_pages_excludes_redirect_and_error_stubs(tmp_path):
    """Issue #133: a 301 or 404 stub is routinely served as ``text/html`` too, so
    ``Page.is_html`` alone let both into ``html_pages()`` — the population
    ``.claude/skills/control/reference/populations.md`` defines as "fetched, 2xx, HTML by its
    own Content-Type". Only the 200 belongs in it.
    """
    rows = [
        ["https://example.com/live", "text/html", "200", "OK", "Indexable"],
        ["https://example.com/old-page", "text/html", "301", "Moved", "Non-Indexable"],
        [
            "https://example.com/gone",
            "text/html; charset=UTF-8",
            "404",
            "Not Found",
            "Non-Indexable",
        ],
    ]
    p = tmp_path / "internal_all.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        w.writerows(rows)
    ctx = AuditContext(load_exports(str(tmp_path)), load_config(None))
    assert [pg.url for pg in ctx.html_pages()] == ["https://example.com/live"]


def test_disk_backed_pages_preserve_metrics_and_normalized_lookup(tmp_path):
    p = tmp_path / "internal_all.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        writer.writerows(
            [
                ["https://example.com/old", "text/html", "301", "Moved", "Non-Indexable"],
                ["https://example.com/live/", "text/html", "200", "OK", "Indexable"],
            ]
        )
    ctx = AuditContext(load_exports(str(tmp_path)), load_config(None), disk_backed_pages=True)
    store_path = Path(ctx._disk_pages.path)
    issues_path = Path(ctx.issues.path)
    groups_path = Path(ctx.groups.path)
    try:
        assert len(ctx.pages) == 2
        live = ctx.page_by_norm["https://example.com/live"]
        assert live.url == "https://example.com/live/"
        live.metrics["bytes_per_word"] = 3.5
        live.issue_ids.append("ISSUE-000001")
        reopened = ctx.page_by_url["https://example.com/live/"]
        assert reopened.metrics["bytes_per_word"] == 3.5
        assert reopened.issue_ids == ["ISSUE-000001"]
        ctx.add("TITLE_MISSING", target_url="https://example.com/live/")
        assert [issue.check for issue in ctx.issues] == ["TITLE_MISSING"]
        ctx.retract("TITLE_MISSING", "fixture withdrawal")
        assert list(ctx.issues) == []
        ctx.add_group("TITLE_DUPLICATE", "same title", ["https://example.com/live/"])
        assert [group.group_id for group in ctx.groups] == ["GRP-TITLE-0001"]
        assert [page.url for page in ctx.indexable_html_pages()] == ["https://example.com/live/"]
    finally:
        ctx.close()
    assert not store_path.exists()
    assert not issues_path.exists()
    assert not groups_path.exists()


def test_disk_page_cache_evicts_and_batched_findings_keep_persisted_state():
    records = [
        {"url": "https://example.test/a", "status_code": 301},
        {"url": "https://example.test/a/", "status_code": 200},
        {"url": "https://example.test/b", "status_code": 200},
    ]
    store = _DiskPages(records, lambda record: Page(**record))
    store._cache_limit = 1
    try:
        page = store.representative("https://example.test/a")
        assert page.url == records[1]["url"]
        assert store.get(page.url) is page
        page.metrics["observed"] = 42
        store.get(records[2]["url"])
        assert len(store._cache) == 1
        assert store.get(page.url).metrics["observed"] == 42
        store.attach_issue(page.url, "TITLE_MISSING", "ISSUE-1")
        store.attach_issue(page.url, "TITLE_MISSING", "ISSUE-2")
        store.attach_issue(page.url, "H1_MISSING", "ISSUE-3", suppressed=True)
        reloaded = store.get(page.url)
        assert reloaded.metrics["observed"] == 42
        assert reloaded.issues == ["TITLE_MISSING"]
        assert reloaded.issue_ids == ["ISSUE-1", "ISSUE-2"]
        assert reloaded.suppressed_issue_ids == ["ISSUE-3"]
    finally:
        store.close()


@pytest.mark.parametrize("disk_backed", (False, True))
def test_html_page_keys_stream_unique_normalized_fetched_html(tmp_path, disk_backed):
    p = tmp_path / "internal_all.csv"
    with p.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Address", "Content Type", "Status Code"])
        writer.writerows(
            [
                ["https://example.test/a", "text/html", 200],
                ["https://example.test/a/", "application/xhtml+xml", 200],
                ["https://example.test/gone", "text/html", 404],
                ["https://example.test/image", "image/png", 200],
            ]
        )
    ctx = AuditContext(
        load_exports(str(tmp_path)), load_config(None), disk_backed_pages=disk_backed
    )
    try:
        keys = ctx.html_page_keys()
        if disk_backed:
            assert not isinstance(keys, (list, set, dict))
        assert list(keys) == ["https://example.test/a"]
    finally:
        ctx.close()


@pytest.mark.parametrize("count", (1_000, 50_000))
def test_disk_backed_aggregate_keeps_final_findings_reiterable_until_audit_v2_writes(
    tmp_path, count
):
    """The final sort/suppression stage must not reassemble native findings in RAM."""
    p = tmp_path / "internal_all.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        writer.writerows(
            [
                [f"https://example.com/{index}", "text/html", "200", "OK", "Indexable"]
                for index in range(count)
            ]
        )
    ctx = AuditContext(load_exports(str(tmp_path)), load_config(None), disk_backed_pages=True)
    final_path = None
    try:
        for page in ctx.pages:
            ctx.add("TITLE_MISSING", target_url=page.url)
        result = aggregate(ctx, {"input_mode": "crawl", "crawl_partial": False}, {}, {})
        assert not isinstance(result.issues, list)
        assert len(result.issues) == count
        assert [issue.id for issue in itertools.islice(result.issues, 2)] == [
            "ISSUE-000001",
            "ISSUE-000002",
        ]
        header, collections = result.audit_v2_parts()
        assert next(iter(collections["/issues"])) == next(iter(collections["/issues"]))
        assert ctx.page_by_url["https://example.com/0"].issue_ids == ["ISSUE-000001"]
        final_path = Path(ctx._disk_final_issues.path)
        assert final_path.exists()
        scan_path = tmp_path / "native.sqlite"
        config = load_crawl_settings(overrides={"speed.min_delay_seconds": 0})
        with NativeScan.create(
            scan_path,
            start_url="https://example.com/",
            config=config,
            config_fingerprint=fingerprint(config),
            writer_version="test",
            writer_revision="a" * 40,
            runtime_versions={
                "python": "test",
                "sqlite": "test",
                "httpx": "test",
                "lxml": "test",
                "beautifulsoup4": "test",
            },
        ) as scan:
            scan.save_audit_v2(header, collections)
            assert scan.finish_without_audit("disk-backed aggregate fixture")
        with AuditV2Reader(scan_path) as reader:
            assert reader.count("/issues") == reader.count("/pages") == count
    finally:
        ctx.close()
    assert final_path is not None and not final_path.exists()
