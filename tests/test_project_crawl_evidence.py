"""A saved project crawl moves the automatic checklist items it measured."""

from pathlib import Path

import pytest

from seohead.mcp import handlers
from seohead.projects import crawl_evidence
from seohead.projects.coverage import coverage_status, initialize_coverage
from seohead.projects.crawl_evidence import record_failure, record_scan
from seohead.projects.workspace import create_project
from tests.test_scan_reanalysis_integration import _source


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "shop"
    create_project(root, "https://example.test/")
    initialize_coverage(root)
    return root


def row(status, item_id):
    return next(item for item in status["items"] if item["id"] == item_id)


def test_executed_check_becomes_evidence_and_progress_moves(project):
    _source(project / "scans/crawl.sqlite")
    before = coverage_status(project)["counts"]["remaining"]

    evidence = record_scan(project, project / "scans/crawl.sqlite")

    assert "check:BROKEN_PAGE_4XX" in evidence["succeeded"]
    status = coverage_status(project)
    measured = row(status, "check:BROKEN_PAGE_4XX")
    assert measured["state"] == "run" and measured["complete"]
    assert measured["measurement"]["state"] in {"measured", "limited"}
    assert status["counts"]["remaining"] < before


def test_check_absent_from_the_audit_is_unmeasured_not_zero(project, monkeypatch):
    _source(project / "scans/crawl.sqlite")
    before = coverage_status(project)["counts"]["remaining"]
    monkeypatch.setattr(
        crawl_evidence,
        "_saved_audit",
        lambda _path: {"run": {}, "summary": {}, "issues": []},
    )

    evidence = record_scan(project, project / "scans/crawl.sqlite")

    assert "check:BROKEN_PAGE_4XX" in evidence["unmeasured"]
    assert evidence["succeeded"] == []
    status = coverage_status(project)
    unmeasured = row(status, "check:BROKEN_PAGE_4XX")
    assert unmeasured["state"] == "not_run" and unmeasured["attempt_status"] == "not_run"
    assert status["counts"]["remaining"] == before


def test_unusable_crawl_marks_automatic_checks_failed(project):
    (project / "scans/broken.sqlite").write_bytes(b"not a scan")

    evidence = record_scan(project, project / "scans/broken.sqlite")

    assert "check:BROKEN_PAGE_4XX" in evidence["failed"]
    failed = row(coverage_status(project), "check:BROKEN_PAGE_4XX")
    assert failed["attempt_status"] == "failed" and failed["state"] == "not_run"


def test_crashed_crawl_records_failure_reason(project):
    result = record_failure(project, "KeyboardInterrupt")

    assert "check:BROKEN_PAGE_4XX" in result["failed"]
    assert row(coverage_status(project), "check:BROKEN_PAGE_4XX")["reason"] == "KeyboardInterrupt"


def test_uninitialized_checklist_is_skipped_without_error(tmp_path):
    root = tmp_path / "bare"
    create_project(root, "https://example.test/")
    _source(root / "scans/crawl.sqlite")

    assert "skipped" in record_scan(root, root / "scans/crawl.sqlite")
    assert "skipped" in record_failure(root, "boom")


def test_project_crawl_handler_attaches_its_evidence(tmp_path, monkeypatch):
    project = tmp_path / "shop"
    create_project(project, "https://example.test/")
    initialize_coverage(project)

    def crawl(_url, **kwargs):
        _source(Path(kwargs["scan_out"]))
        return {
            "ok": True,
            "scan": kwargs["scan_out"],
            "partial": False,
            "finish_reason": "finished",
        }

    monkeypatch.setattr("seohead.mcp.scan_handlers.crawl_site_scan", crawl)
    result = handlers.crawl_site(
        project=str(project), producer_build="a" * 40, approve_large_crawl=True
    )

    assert "check:BROKEN_PAGE_4XX" in result["checklist_evidence"]["succeeded"]
    assert row(coverage_status(project), "check:BROKEN_PAGE_4XX")["complete"]
