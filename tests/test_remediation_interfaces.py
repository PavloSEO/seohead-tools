"""CLI and MCP expose the same local remediation ledger workflow."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
from pathlib import Path

import pytest

from seohead.cli import main
from seohead.projects.workspace import create_project
from seohead.storage.ledger import (
    create_ledger,
    ingest_scan,
    ledger_summary,
    read_cases,
    remediation_summary,
)
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
    from seohead.mcp.mcp_server import build_server

    ledger = _prepared(tmp_path)
    server = build_server(profile="full")
    summary = asyncio.run(server.call_tool("seo_remediation_summary", {"ledger": str(ledger)}))[1]
    page = asyncio.run(
        server.call_tool("seo_remediation_cases", {"ledger": str(ledger), "limit": 1})
    )[1]
    assert summary["counts"]["detected"] == 1
    assert page["total"] == 1 and page["findings"][0]["check"] == "CHECK_ONE"


def test_ledger_selected_synthetic_recheck_records_measured_artifact_and_task_coverage(tmp_path):
    """A pending ledger key binds a later measured synthetic audit without a site crawl."""
    project = create_project(tmp_path / "project", "https://example.test/")
    scan = Path(project["path"]) / "scans" / "baseline.sqlite"
    _scan(scan, issues=[_issue("ISSUE-000001", "TITLE_MISSING", target=A)])
    ledger = create_ledger(
        Path(project["path"]) / "scans" / "remediation.sqlite",
        project_dir=project["path"],
        producer_build="a" * 40,
    )
    ingest_scan(ledger, scan)
    try:
        occurrence = next(
            item
            for finding in read_cases(ledger)["findings"]
            if finding["check"] == "TITLE_MISSING"
            for item in finding["occurrences"]
        )
        revision = ledger_summary(ledger)["ledger_revision"]
        for next_state in ("verified", "fix_reported", "recheck_pending"):
            _cli(
                "remediation-transition",
                "--ledger",
                str(ledger),
                "--occurrence-key",
                occurrence["occurrence_key"],
                "--state",
                next_state,
                "--actor",
                "reviewer",
                "--reason",
                f"move to {next_state}",
                "--expected-revision",
                str(revision),
            )
            revision += 1
        from seohead.mcp import handlers

        baseline = handlers._load_audit(str(scan), "baseline")
        after = json.loads(json.dumps(baseline))
        after["issues"] = []
        after["run"]["scan_uuid"] = "later-synthetic-scan"
        after["run"]["generated_at"] = "2026-10-02T00:00:00Z"
        after["summary"]["check_coverage"] = {"checks_silent_ids": ["TITLE_MISSING"]}
        after["pages"][1]["content_type"] = "text/html"
        after_path = tmp_path / "after.json"
        after_path.write_text(json.dumps(after), encoding="utf-8")
        result = _cli(
            "remediation-recheck",
            "--ledger",
            str(ledger),
            "--baseline",
            str(scan),
            "--occurrence-keys",
            occurrence["occurrence_key"],
            "--actor",
            "loopback-recheck",
            "--expected-revision",
            str(revision),
            "--task-id",
            "title-fix",
            "--after",
            str(after_path),
            "--out-dir",
            str(tmp_path / "verification"),
        )
        assert result["verification"]["summary"]["resolved"] == 1, result["verification"][
            "findings"
        ]
        stored = next(
            item
            for finding in read_cases(ledger)["findings"]
            if finding["check"] == "TITLE_MISSING"
            for item in finding["occurrences"]
        )
        binding = stored["decisions"][-1]["verification"]
        assert stored["current_state"] == "resolved"
        assert binding["task_id"] == "title-fix" and binding["measured"] is True
        assert Path(binding["artifact_path"]).is_file()
        summary = remediation_summary(ledger)
        assert summary["ledger_revision"] == result["recording"]["ledger_revision"]
        assert summary["counts"]["resolved"] == 1
        assert summary["tasks"]["title-fix"]["resolved_percent"] == 100.0
        pytest.importorskip("mcp")
        from seohead.mcp.mcp_server import build_server

        resumed = asyncio.run(
            build_server(profile="full").call_tool(
                "seo_remediation_summary", {"ledger": str(ledger)}
            )
        )[1]
        assert resumed["ledger_revision"] == summary["ledger_revision"]
        assert resumed["tasks"]["title-fix"]["status"] == "complete"
    finally:
        baseline.close() if hasattr(baseline, "close") else None
