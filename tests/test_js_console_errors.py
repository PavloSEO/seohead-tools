"""Pages With JavaScript Errors (#1015): offline reads of retained render console sidecars."""

from __future__ import annotations

import json
import sqlite3

from seohead.storage import browser_artifacts
from seohead.storage.browser_artifacts import console_error_pages, save


def _connection():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE urls(url_id INTEGER PRIMARY KEY, url TEXT)")
    con.execute("CREATE TABLE documents(document_id INTEGER PRIMARY KEY, url_id INTEGER)")
    con.execute(
        "CREATE TABLE context_items(kind TEXT, item_key TEXT, payload_json TEXT, "
        "PRIMARY KEY(kind, item_key))"
    )
    con.execute("INSERT INTO urls VALUES(1, 'https://example.com/a')")
    con.execute("INSERT INTO urls VALUES(3, 'https://example.com/b')")
    con.execute("INSERT INTO documents VALUES(2, 1)")
    con.execute("INSERT INTO documents VALUES(4, 3)")
    return con


def _store(con, scan_path, page_url_id, document_id, rendered):
    item = save(
        scan_path, page_url_id, document_id, rendered, screenshots=False, console_errors=True
    )
    con.execute(
        "INSERT INTO context_items VALUES(?,?,?)",
        (item["kind"], item["item_key"], item["payload_json"]),
    )


def test_pages_with_console_errors_are_reported_and_clean_pages_are_not(tmp_path):
    scan = tmp_path / "scan.sqlite"
    con = _connection()
    _store(con, scan, 1, 2, {"ok": True, "console_errors": ["Uncaught TypeError: x"]})
    _store(con, scan, 3, 4, {"ok": True, "console_errors": []})

    result = console_error_pages(con, scan)

    assert result["captured"] == 2
    assert result["unreadable"] == 0
    assert result["pages"] == [
        {
            "target_url": "https://example.com/a",
            "error_count": 1,
            "errors": ["Uncaught TypeError: x"],
        }
    ]


def test_omitted_console_errors_still_make_a_page_a_finding(tmp_path):
    scan = tmp_path / "scan.sqlite"
    con = _connection()
    _store(
        con,
        scan,
        1,
        2,
        {"ok": True, "console_errors": [], "console_errors_omitted": 4},
    )

    [page] = console_error_pages(con, scan)["pages"]

    assert page["error_count"] == 4
    assert page["errors"] == []


def test_missing_or_tampered_sidecar_is_unreadable_not_clean(tmp_path):
    scan = tmp_path / "scan.sqlite"
    con = _connection()
    _store(con, scan, 1, 2, {"ok": True, "console_errors": ["boom"]})
    payload = json.loads(con.execute("SELECT payload_json FROM context_items").fetchone()[0])
    digest = payload["console"]["ref"]["sha256"]
    sidecar = browser_artifacts._root(scan) / "console" / f"{digest}.json"
    sidecar.unlink()

    result = console_error_pages(con, scan)

    assert result == {"pages": [], "captured": 0, "unreadable": 1}


def test_console_not_retained_yields_no_evidence(tmp_path):
    scan = tmp_path / "scan.sqlite"
    con = _connection()
    _store(con, scan, 1, 2, {"ok": False})

    assert console_error_pages(con, scan) == {"pages": [], "captured": 0, "unreadable": 0}
