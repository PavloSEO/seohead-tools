"""Checklist record evidence: streamed audit.v2 scans and the documented input shapes."""

import json
import sqlite3

import pytest

from seohead import cli
from seohead.mcp import handlers
from seohead.mcp.mcp_server import build_server
from seohead.projects.coverage import coverage_status, initialize_coverage, record_execution
from seohead.projects.evidence import audit_facts
from seohead.projects.workspace import create_project
from seohead.storage import open_scan
from seohead.storage.audit_v2 import write_audit_v2
from tests.test_scan_reanalysis_integration import _source

CHECK = "check:BROKEN_PAGE_4XX"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    initialize_coverage(root)
    (root / "scans").mkdir(exist_ok=True)
    return root


def _stream_audit(path):
    """Move a saved scan's audit into its audit.v2 companion, as a streamed scan keeps it."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    doc = json.loads(con.execute("SELECT document_json FROM audit WHERE singleton=1").fetchone()[0])
    scan = dict(con.execute("SELECT * FROM scan WHERE singleton=1").fetchone())
    con.close()
    binding = {
        "scan_uuid": scan["scan_uuid"],
        "evidence_revision": scan["evidence_revision"],
        "analyzer_version": scan["writer_version"],
        "analyzer_revision": scan["writer_revision"],
    }
    header = {k: v for k, v in doc.items() if k not in ("issues", "pages", "groups")}
    header.update(issues=[], pages=[], groups=[])
    collections = {"/issues": doc["issues"], "/pages": doc["pages"], "/groups": doc["groups"]}
    write_audit_v2(path, header, collections, binding)
    con = sqlite3.connect(path)
    con.execute("DELETE FROM audit")
    con.commit()
    con.close()


def _record(root, item_id, entry):
    return record_execution(root, item_id, entry, coverage_status(root)["revision"])


def _row(root, item_id):
    return next(item for item in coverage_status(root)["items"] if item["id"] == item_id)


def test_streamed_audit_v2_automatic_record_is_accepted(project):
    source = project / "scans/source.sqlite"
    _source(source)
    with open_scan(source) as con:
        expected = audit_facts(source, con)
    _stream_audit(source)
    with open_scan(source) as con:
        assert con.execute("SELECT 1 FROM audit WHERE singleton=1").fetchone() is None
        assert audit_facts(source, con) == expected

    _record(
        project,
        CHECK,
        {
            "status": "succeeded",
            "reason": "Executed in the saved streamed scan",
            "artifact": "scans/source.sqlite",
        },
    )
    assert _row(project, CHECK)["complete"] is True


def test_missing_audit_refuses_cleanly_without_a_write(project):
    source = project / "scans/bare.sqlite"
    _source(source)
    con = sqlite3.connect(source)
    con.execute("DELETE FROM audit")
    con.commit()
    con.close()
    path = project / "coverage.json"
    original = path.read_bytes()
    with pytest.raises(ValueError):
        _record(
            project,
            CHECK,
            {"status": "succeeded", "reason": "No audit", "artifact": "scans/bare.sqlite"},
        )
    assert path.read_bytes() == original


def test_input_shapes_are_documented_in_cli_help_and_mcp_schema(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["project", "checklist-record", "--help"])
    text = capsys.readouterr().out
    for fragment in (
        '"artifact": "scans/',
        '"signoff": true',
        '"review": "approved"',
        '"not_applicable"',
        "streamed audit.v2",
    ):
        assert fragment in text

    tool = build_server()._tool_manager.get_tool("seo_project_checklist_record")
    for fragment in ("signoff", "approved", "scans/", "audit.v2"):
        assert fragment in tool.fn.__doc__


def test_cli_forwards_wrapped_record_to_core_unchanged(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        handlers,
        "project_checklist_record",
        lambda **kwargs: seen.update(kwargs) or {"state": "initialized"},
    )
    args = cli.build_parser().parse_args(
        [
            "project",
            "checklist-record",
            "--directory",
            "project",
            "--item-id",
            CHECK,
            "--expected-revision",
            "3",
            "--input",
            json.dumps(
                {"record": {"status": "succeeded", "reason": "ran", "artifact": "scans/x.sqlite"}}
            ),
        ]
    )
    _handler, kwargs = cli._build_kwargs("project-" + args.project_command, args)
    assert kwargs["record"] == {
        "status": "succeeded",
        "reason": "ran",
        "artifact": "scans/x.sqlite",
    }
