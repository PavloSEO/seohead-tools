"""Issue #793: the versioned full-audit prompt stays addressable through each catalogue route."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json

import pytest

from seohead.cli import main
from seohead.projects.runtime import playbook_list, playbook_show

PROMPT_ID = "skill:workflow/full-audit-v1"
PROMPT_ROUTE = "workflow/full-audit-v1"
SCENARIO_ID = "scenario:full-audit"


def _cli_json(*argv: str) -> dict:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert main(list(argv)) == 0
    return json.loads(stdout.getvalue())


def test_full_audit_v1_is_a_catalogued_versioned_workflow_with_honest_gates():
    items = playbook_list()["items"]
    assert any(item["id"] == PROMPT_ID for item in items)
    prompt = playbook_show(PROMPT_ROUTE, "skill")
    assert prompt["id"] == PROMPT_ID
    assert "v1.0.0" in prompt["content"]
    for required in (
        "summary.check_coverage",
        "workflow/seo-deep-audit",
        "workflow/robots-audit",
        "log-scan",
        "report-build",
        "project-prepare",
        "Register controller work",
        "project-inbox-triage",
        "current incomplete custom task IDs",
        "unavailable evidence",
    ):
        assert required in prompt["content"]
    assert "full-registry is complete" not in prompt["content"]
    assert "audit-task progress" in prompt["content"]

    scenario = playbook_show("full-audit", "scenario")
    assert scenario["id"] == SCENARIO_ID
    assert PROMPT_ROUTE in scenario["content"]


def test_full_audit_skill_and_scenario_are_retrievable_from_the_cli():
    listed = _cli_json("skill-list", "--input", "{}")
    assert any(item["id"] == PROMPT_ID for item in listed["items"])
    prompt = _cli_json("skill-show", "--name", PROMPT_ROUTE)
    scenario = _cli_json("scenario-show", "--name", "full-audit")
    assert prompt["id"] == PROMPT_ID and "v1.0.0" in prompt["content"]
    assert scenario["id"] == SCENARIO_ID and PROMPT_ROUTE in scenario["content"]


def test_full_audit_skill_and_scenario_are_retrievable_from_mcp():
    pytest.importorskip("mcp")

    from seohead.servers.mcp_server import build_server

    server = build_server(profile="full")
    prompt = asyncio.run(server.call_tool("seo_skill_show", {"name": PROMPT_ROUTE}))[1]
    scenario = asyncio.run(server.call_tool("seo_scenario_show", {"name": "full-audit"}))[1]
    assert prompt["id"] == PROMPT_ID and "v1.0.0" in prompt["content"]
    assert scenario["id"] == SCENARIO_ID and PROMPT_ROUTE in scenario["content"]
