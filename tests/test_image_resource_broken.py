"""IMG_BROKEN (#989): an <img> whose fetched target answered 4xx/5xx is a finding
with its source page as evidence. A 200 image, a redirect, and a crawl without
resource capture must not produce one; the last must skip, never read clean.
"""

from __future__ import annotations

import sqlite3

from seohead.sf.core.context import AuditContext
from seohead.sf.core.loader import LoadedExports
from seohead.sf.core.rules import check_native_image_resources

_SCHEMA = """
CREATE TABLE urls (url_id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE);
CREATE TABLE resource_graph_occurrences (
  occurrence_id INTEGER PRIMARY KEY, page_url_id INTEGER NOT NULL,
  kind TEXT NOT NULL, resolved_url TEXT NOT NULL);
CREATE TABLE resource_graph_fetches (
  resolved_url TEXT PRIMARY KEY, status_code INTEGER);
"""


def _scan(images: list[tuple[str, str, str]], fetches: dict[str, int]) -> sqlite3.Connection:
    """images: (page_url, image_url, kind); fetches: resolved_url -> status_code."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    pages: dict[str, int] = {}
    for page_url, image_url, kind in images:
        page_id = pages.setdefault(page_url, len(pages) + 1)
        con.execute("INSERT OR IGNORE INTO urls(url_id,url) VALUES(?,?)", (page_id, page_url))
        con.execute(
            "INSERT INTO resource_graph_occurrences(page_url_id,kind,resolved_url) VALUES(?,?,?)",
            (page_id, kind, image_url),
        )
    for url, status in fetches.items():
        con.execute("INSERT INTO resource_graph_fetches VALUES(?,?)", (url, status))
    return con


def _run(con: sqlite3.Connection | None) -> AuditContext:
    ctx = AuditContext(LoadedExports(), {"thresholds": {}})
    ctx.scan_con = con
    check_native_image_resources(ctx)
    return ctx


def test_image_answering_404_is_a_finding_with_source_page():
    con = _scan(
        [("https://s.test/a", "https://s.test/gone.jpg", "image")],
        {"https://s.test/gone.jpg": 404},
    )
    ctx = _run(con)
    issues = [i for i in ctx.issues if i.check == "IMG_BROKEN"]
    assert len(issues) == 1
    assert issues[0].target_url == "https://s.test/gone.jpg"
    assert issues[0].details["source_page"] == "https://s.test/a"
    assert issues[0].details["status_code"] == 404


def test_healthy_image_and_redirect_are_not_findings():
    con = _scan(
        [
            ("https://s.test/a", "https://s.test/ok.jpg", "image"),
            ("https://s.test/a", "https://s.test/moved.jpg", "image"),
        ],
        {"https://s.test/ok.jpg": 200, "https://s.test/moved.jpg": 301},
    )
    ctx = _run(con)
    assert not [i for i in ctx.issues if i.check == "IMG_BROKEN"]
    assert "IMG_BROKEN" not in {s.id for s in ctx.skipped}


def test_non_image_resource_is_not_checked():
    con = _scan(
        [("https://s.test/a", "https://s.test/app.css", "stylesheet")],
        {"https://s.test/app.css": 500},
    )
    ctx = _run(con)
    assert not [i for i in ctx.issues if i.check == "IMG_BROKEN"]


def test_missing_resource_tables_skip_honestly():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE urls (url_id INTEGER PRIMARY KEY, url TEXT)")
    ctx = _run(con)
    assert not [i for i in ctx.issues if i.check == "IMG_BROKEN"]
    assert "IMG_BROKEN" in {s.id for s in ctx.skipped}


def test_no_measured_image_skips_instead_of_clean():
    con = _scan([("https://s.test/a", "https://s.test/x.jpg", "image")], {})
    ctx = _run(con)
    assert not [i for i in ctx.issues if i.check == "IMG_BROKEN"]
    assert "IMG_BROKEN" in {s.id for s in ctx.skipped}


def test_export_audit_without_scan_connection_skips():
    ctx = _run(None)
    assert not [i for i in ctx.issues if i.check == "IMG_BROKEN"]
