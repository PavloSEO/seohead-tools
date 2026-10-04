from __future__ import annotations

import asyncio
import contextlib
import io
import json

import pytest

from seohead.projects.coverage import initialize_coverage
from seohead.projects.execution import checkpoint, execute, resume, start, status
from seohead.projects.inbox import set_goal_state, submit
from seohead.projects.workspace import create_project


def _evidence(reference: str) -> list[dict[str, str]]:
    return [{"reference": reference, "sha256": "a" * 64}]


def _accepted_context(project) -> dict[str, object]:
    proposed = submit(
        project,
        text="Complete the recorded synthetic full audit.",
        kind="proposed_goal",
        references=["goal:synthetic-full-audit", "task:handoff"],
    )
    set_goal_state(
        project,
        entry_id=proposed["entry"]["id"],
        state="accepted",
        expected_revision=proposed["revision"],
    )
    return {
        "goal_id": proposed["entry"]["id"],
        "prompt_reference": "skill:workflow/full-audit-v1",
        "competitors": ["https://competitor.test/"],
    }


def _cli_json(*argv: str) -> dict:
    from seohead.cli import main

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(list(argv)) == 0
    return json.loads(output.getvalue())


def test_registered_steps_checkpoint_and_resume(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    rows = status(project)
    assert rows["next_action"] is None
    steps = ["check:TITLE_MISSING", "check:DESC_MISSING"]
    run = start(
        project,
        scenario_id="scenario:full-audit",
        steps=steps,
        expected_revision=0,
        context=_accepted_context(project),
    )
    first = checkpoint(
        project,
        run_id=run["run"]["id"],
        step_id=steps[0],
        state="succeeded",
        evidence=_evidence("reports/title.json"),
        expected_revision=run["revision"],
    )
    assert status(project)["next_action"] == steps[1]
    done = checkpoint(
        project,
        run_id=run["run"]["id"],
        step_id=steps[1],
        state="succeeded",
        evidence=_evidence("reports/description.json"),
        expected_revision=first["revision"],
    )
    assert done["run"]["state"] == "completed"


def test_execution_status_is_readable_before_checklist_initialization(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    before = (project / "project.json").read_bytes()
    result = status(project)
    assert result["runs"] == []
    assert result["next_action"] is None
    assert (project / "project.json").read_bytes() == before


def test_local_executor_records_each_registered_outcome(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    steps = ["check:TITLE_MISSING"]
    result = execute(
        project,
        scenario_id="scenario:full-audit",
        steps=steps,
        outcomes=[{"id": steps[0], "state": "succeeded", "evidence": _evidence("scan.sqlite")}],
        context=_accepted_context(project),
    )
    assert result["run"]["state"] == "completed"


def test_second_session_resumes_only_the_interrupted_registered_step(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    steps = ["check:TITLE_MISSING", "check:DESC_MISSING"]
    started = start(
        project,
        scenario_id="scenario:full-audit",
        steps=steps,
        context=_accepted_context(project),
    )
    first = checkpoint(
        project,
        run_id=started["run"]["id"],
        step_id=steps[0],
        state="succeeded",
        evidence=_evidence("reports/own-site.json"),
        phase="own-site-capture",
        expected_revision=started["revision"],
    )
    interrupted = checkpoint(
        project,
        run_id=started["run"]["id"],
        step_id=steps[1],
        state="interrupted",
        expected_revision=first["revision"],
    )
    recovered = resume(
        project, run_id=started["run"]["id"], expected_revision=interrupted["revision"]
    )
    assert recovered["next_action"] == steps[1]
    assert recovered["run"]["steps"][0]["state"] == "succeeded"
    assert recovered["run"]["context"]["own_site"] == "https://example.test/"
    assert recovered["run"]["context"]["goal"]["references"] == [
        "goal:synthetic-full-audit",
        "task:handoff",
    ]
    assert recovered["run"]["context"]["prompt"]["id"] == "skill:workflow/full-audit-v1"
    assert recovered["run"]["phase"] == "own-site-capture"


def test_manual_step_needs_an_explicit_review_before_completion(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    started = start(
        project,
        scenario_id="scenario:full-audit",
        steps=["skill:workflow/control"],
        context=_accepted_context(project),
    )
    with pytest.raises(ValueError, match="explicit approved review"):
        checkpoint(
            project,
            run_id=started["run"]["id"],
            step_id="skill:workflow/control",
            state="succeeded",
            evidence=_evidence("reports/review.md"),
            expected_revision=started["revision"],
        )


def test_deliverable_step_needs_an_explicit_review_before_completion(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(
        project,
        template={
            "format": "seohead.checklist-template.v1",
            "items": [
                {
                    "id": "custom:deliverable",
                    "title": "Synthetic approved report",
                    "execution_kind": "deliverable",
                }
            ],
        },
    )
    started = start(
        project,
        scenario_id="scenario:full-audit",
        steps=["custom:deliverable"],
        context=_accepted_context(project),
    )
    with pytest.raises(ValueError, match="explicit approved review"):
        checkpoint(
            project,
            run_id=started["run"]["id"],
            step_id="custom:deliverable",
            state="succeeded",
            evidence=_evidence("reports/deliverable.pdf"),
            expected_revision=started["revision"],
        )


def test_successful_checkpoint_requires_exact_evidence(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    started = start(
        project,
        scenario_id="scenario:full-audit",
        steps=["check:TITLE_MISSING"],
        context=_accepted_context(project),
    )
    with pytest.raises(ValueError, match="exact evidence"):
        checkpoint(
            project,
            run_id=started["run"]["id"],
            step_id="check:TITLE_MISSING",
            state="succeeded",
            expected_revision=started["revision"],
        )


def test_start_refuses_duplicate_registered_steps(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    with pytest.raises(ValueError, match="unique nonempty"):
        start(
            project,
            scenario_id="scenario:full-audit",
            steps=["check:TITLE_MISSING", "check:TITLE_MISSING"],
            context=_accepted_context(project),
        )


def test_start_requires_an_accepted_goal_and_registered_prompt(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    with pytest.raises(ValueError, match="accepted goal_id and prompt_reference"):
        start(
            project,
            scenario_id="scenario:full-audit",
            steps=["check:TITLE_MISSING"],
        )


def test_stale_prompt_reopens_the_earliest_step_without_losing_prior_evidence(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    started = start(
        project,
        scenario_id="scenario:full-audit",
        steps=["check:TITLE_MISSING", "check:DESC_MISSING"],
        context=_accepted_context(project),
    )
    first = checkpoint(
        project,
        run_id=started["run"]["id"],
        step_id="check:TITLE_MISSING",
        state="succeeded",
        evidence=_evidence("reports/title-before.json"),
        expected_revision=started["revision"],
    )
    interrupted = checkpoint(
        project,
        run_id=started["run"]["id"],
        step_id="check:DESC_MISSING",
        state="interrupted",
        expected_revision=first["revision"],
    )
    path = project / "execution.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["runs"][0]["context"]["prompt"]["definition_hash"] = "0" * 64
    path.write_text(json.dumps(saved), encoding="utf-8")
    pending = status(project)
    assert pending["runs"][0]["state"] == "stale"
    assert pending["next_action"] == "check:TITLE_MISSING"
    recovered = resume(
        project,
        run_id=started["run"]["id"],
        expected_revision=interrupted["revision"],
    )
    assert recovered["run"]["state"] == "running"
    assert recovered["next_action"] == "check:TITLE_MISSING"
    assert recovered["run"]["steps"][0]["stale_evidence"] == _evidence("reports/title-before.json")
    assert recovered["run"]["steps"][1]["state"] == "pending"


def test_stale_checklist_dependency_requires_checklist_reconciliation(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    started = start(
        project,
        scenario_id="scenario:full-audit",
        steps=["check:TITLE_MISSING"],
        context=_accepted_context(project),
    )
    interrupted = checkpoint(
        project,
        run_id=started["run"]["id"],
        step_id="check:TITLE_MISSING",
        state="interrupted",
        expected_revision=started["revision"],
    )
    path = project / "coverage.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    definition = saved["items"]["check:TITLE_MISSING"]["definition"]
    definition["source_hash"] = "0" * 64
    saved["items"]["check:TITLE_MISSING"]["definitions"][-1]["definition"] = definition
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert status(project)["runs"][0]["state"] == "stale"
    with pytest.raises(ValueError, match="reconcile stale checklist dependencies"):
        resume(
            project,
            run_id=started["run"]["id"],
            expected_revision=interrupted["revision"],
        )


def test_two_agent_cli_and_mcp_handoff_persists_goal_prompt_versions_and_evidence(tmp_path):
    pytest.importorskip("mcp")
    from seohead.servers.mcp_server import build_server

    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    context = _accepted_context(project)
    steps = ["check:TITLE_MISSING", "check:DESC_MISSING"]
    agent_one = _cli_json(
        "workflow-start",
        "--directory",
        str(project),
        "--input",
        json.dumps({"scenario_id": "scenario:full-audit", "steps": steps, "context": context}),
    )
    server = build_server(profile="full")
    agent_two_status = asyncio.run(
        server.call_tool("seo_workflow_status", {"directory": str(project)})
    )[1]
    assert agent_two_status["next_action"] == steps[0]
    agent_two = asyncio.run(
        server.call_tool(
            "seo_workflow_checkpoint",
            {
                "directory": str(project),
                "run_id": agent_one["run"]["id"],
                "step_id": steps[0],
                "state": "succeeded",
                "evidence": _evidence("reports/title-agent-two.json"),
                "phase": "own-site-capture",
                "expected_revision": agent_one["revision"],
            },
        )
    )[1]
    interrupted = _cli_json(
        "workflow-checkpoint",
        "--directory",
        str(project),
        "--input",
        json.dumps(
            {
                "run_id": agent_one["run"]["id"],
                "step_id": steps[1],
                "state": "interrupted",
                "expected_revision": agent_two["revision"],
            }
        ),
    )
    resumed = asyncio.run(
        server.call_tool(
            "seo_workflow_resume",
            {
                "directory": str(project),
                "run_id": agent_one["run"]["id"],
                "expected_revision": interrupted["revision"],
            },
        )
    )[1]
    assert resumed["next_action"] == steps[1]
    assert resumed["run"]["steps"][0]["state"] == "succeeded"
    assert resumed["run"]["steps"][0]["evidence"] == _evidence("reports/title-agent-two.json")
    assert resumed["run"]["context"]["goal"]["id"] == context["goal_id"]
    assert resumed["run"]["context"]["prompt"]["definition_hash"]
    assert resumed["run"]["scenario"]["definition_hash"]
