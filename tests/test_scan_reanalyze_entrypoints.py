"""End-to-end check of the live scan-reanalyze entry points (CLI and MCP).

The CLI and MCP tests in test_scan_reanalysis_cli.py replace the handler with a
stub, so nothing there proves the real command writes a derived scan. This file
runs both entry points on a retained scan without stubs and without network.
"""

from __future__ import annotations

import json
import socket
import sqlite3

from seohead import cli
from seohead.mcp import handlers
from tests.test_scan_reanalyze_offline import _build_scan, _sha256


def _scan_uuid(path: str) -> str:
    with sqlite3.connect(path) as con:
        return con.execute("SELECT scan_uuid FROM scan WHERE singleton=1").fetchone()[0]


def _forbid_network(monkeypatch) -> None:
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("scan-reanalyze attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


def _assert_derived(result: dict, source: str, out) -> None:
    assert result["source_kind"] == "reanalysis"
    assert result["parent_scan_uuid"] == _scan_uuid(source)
    assert result["audit_available"] is True
    assert out.exists()


def test_cli_scan_reanalyze_writes_derived_scan_and_leaves_source(tmp_path, monkeypatch, capsys):
    _forbid_network(monkeypatch)
    source = _build_scan(tmp_path)
    before = _sha256(source)
    out = tmp_path / "derived.sqlite"

    payload = json.dumps({"input_path": source, "out": str(out), "producer_build": "b" * 40})
    assert cli.main(["scan-reanalyze", "--input", payload]) == 0

    _assert_derived(json.loads(capsys.readouterr().out), source, out)
    assert _sha256(source) == before


def test_mcp_scan_reanalyze_writes_derived_scan_and_leaves_source(tmp_path, monkeypatch):
    _forbid_network(monkeypatch)
    source = _build_scan(tmp_path)
    before = _sha256(source)
    out = tmp_path / "derived.sqlite"

    result = handlers.HANDLERS["scan_reanalyze"](
        input_path=source, out=str(out), producer_build="b" * 40
    )

    _assert_derived(result, source, out)
    assert _sha256(source) == before
