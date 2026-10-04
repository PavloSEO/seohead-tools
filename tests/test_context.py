"""Context: duplicate URL rows must not desync pages/page_by_url."""

from __future__ import annotations

import csv
from pathlib import Path

from seohead.sf.config import load_config
from seohead.sf.core.context import AuditContext
from seohead.sf.core.loader import load_exports


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
        assert [page.url for page in ctx.indexable_html_pages()] == ["https://example.com/live/"]
    finally:
        ctx.close()
    assert not store_path.exists()
    assert not issues_path.exists()
