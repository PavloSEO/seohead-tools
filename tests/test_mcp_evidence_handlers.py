"""Evidence MCP handlers: argument validation and saved-scan projection boundaries."""

from __future__ import annotations

import pytest

from seohead.mcp import evidence_handlers


class _FakeCursor:
    def __init__(self, rows, count=None):
        self._rows = rows
        self._count = count

    def fetchone(self):
        if self._count is not None:
            return (self._count,)
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.closed = False

    def execute(self, sql, *_args, **_kwargs):
        if sql.lstrip().upper().startswith("SELECT COUNT"):
            return _FakeCursor([], count=len(self.rows))
        return _FakeCursor(self.rows)

    def close(self):
        self.closed = True


def _fake_open_scan(con, calls):
    def open_scan(path, require_audit=True):
        calls.append((path, require_audit))
        return con

    return open_scan


def test_scan_evidence_rejects_unknown_section_before_opening_scan(monkeypatch):
    calls = []
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(_FakeConnection(), calls))
    with pytest.raises(ValueError, match="unknown saved evidence section"):
        evidence_handlers.scan_evidence("scan.db", section="nope")
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 10001},
        {"limit": True},
        {"offset": -1},
        {"offset": "0"},
    ],
)
def test_scan_evidence_rejects_out_of_range_paging(monkeypatch, kwargs):
    calls = []
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(_FakeConnection(), calls))
    with pytest.raises(ValueError, match=r"limit must be 1\.\.10000"):
        evidence_handlers.scan_evidence("scan.db", **kwargs)
    assert calls == []


def test_scan_evidence_capabilities_without_saved_audit_is_unavailable(monkeypatch):
    con = _FakeConnection(rows=())
    calls = []
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(con, calls))
    result = evidence_handlers.scan_evidence("scan.db")
    assert calls == [("scan.db", False)]
    assert result == {
        "ok": True,
        "section": "capabilities",
        "evidence": {"state": "unavailable", "reason": "no saved audit capability projection"},
    }
    assert con.closed is True


def test_scan_evidence_corpus_without_retained_items_reports_unavailable(monkeypatch):
    con = _FakeConnection(rows=())
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(con, []))
    result = evidence_handlers.scan_evidence("scan.db", section="corpus", limit=5, offset=0)
    assert result["ok"] is True
    evidence = result["evidence"]
    assert evidence["state"] == "unavailable"
    assert evidence["total"] == 0
    assert evidence["items"] == []
    assert evidence["has_more"] is False
    assert con.closed is True


def test_scan_extract_rejects_invalid_representation_before_opening_scan(monkeypatch):
    calls = []
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(_FakeConnection(), calls))
    with pytest.raises(ValueError, match="invalid representation or page limit"):
        evidence_handlers.scan_extract("scan.db", rules=[], representation="live")
    assert calls == []


@pytest.mark.parametrize("limit", [0, 1001, False])
def test_scan_extract_rejects_out_of_range_limit(monkeypatch, limit):
    calls = []
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(_FakeConnection(), calls))
    with pytest.raises(ValueError, match="invalid representation or page limit"):
        evidence_handlers.scan_extract("scan.db", rules=[], limit=limit)
    assert calls == []


def test_scan_extract_without_retained_documents_returns_empty_result(monkeypatch):
    con = _FakeConnection(rows=())
    monkeypatch.setattr("seohead.storage.open_scan", _fake_open_scan(con, []))
    result = evidence_handlers.scan_extract("scan.db", rules=[])
    assert result["ok"] is True
    assert result["items"] == []
    assert result["truncated"] is False
    assert result["output_limit_bytes"] == 8 * 1024 * 1024
    assert con.closed is True
