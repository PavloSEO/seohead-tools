"""CLI and MCP expose the same local remediation ledger workflow."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json

import pytest

from seohead.cli import main
from seohead.storage.ledger import ingest_scan, ledger_summary, read_cases
from tests.test_remediation_ledger import A, _issue, _ledger, _scan


def _cli(*argv: str) -> dict:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert main(list(argv)) == 0
    return json.loads(stdout.getvalue())


def _prepared(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[_issue("ISSUE-000001", "CHECK_ONE", target=A)],
    )
    ingest_scan(ledger, scan)
    return ledger


def test_remediation_cli_reads_and_records_a_revision_safe_decision(tmp_path):
    ledger = _prepared(tmp_path)
    summary = _cli("remediation-summary", "--ledger", str(ledger))
    case = _cli("remediation-cases", "--ledger", str(ledger), "--limit", "1")["findings"][0][
        "occurrences"
    ][0]
    transition = _cli(
        "remediation-transition",
        "--ledger",
        str(ledger),
        "--occurrence-key",
        case["occurrence_key"],
        "--state",
        "verified",
        "--actor",
        "reviewer",
        "--reason",
        "baseline reviewed",
        "--expected-revision",
        str(summary["ledger_revision"]),
    )
    assert transition["state"] == "verified"
    result = _cli(
        "remediation-report", "--ledger", str(ledger), "--out-dir", str(tmp_path / "report")
    )
    assert result["data"].endswith("remediation.json")
    assert read_cases(ledger)["findings"][0]["occurrences"][0]["current_state"] == "verified"
    assert ledger_summary(ledger)["ledger_revision"] == transition["ledger_revision"]


def test_remediation_mcp_uses_the_same_handlers(tmp_path):
    pytest.importorskip("mcp")
    from seohead.servers.mcp_server import build_server

    ledger = _prepared(tmp_path)
    server = build_server(profile="full")
    summary = asyncio.run(server.call_tool("seo_remediation_summary", {"ledger": str(ledger)}))[1]
    page = asyncio.run(
        server.call_tool("seo_remediation_cases", {"ledger": str(ledger), "limit": 1})
    )[1]
    assert summary["counts"]["detected"] == 1
    assert page["total"] == 1 and page["findings"][0]["check"] == "CHECK_ONE"
