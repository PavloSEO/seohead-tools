from __future__ import annotations

from seohead.projects.coverage import initialize_coverage
from seohead.projects.execution import checkpoint, execute, start, status
from seohead.projects.workspace import create_project


def test_registered_steps_checkpoint_and_resume(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    rows = status(project)
    assert rows["next_action"] is None
    steps = ["check:TITLE_MISSING", "check:DESC_MISSING"]
    run = start(project, scenario_id="scenario:full-audit", steps=steps, expected_revision=0)
    first = checkpoint(
        project,
        run_id=run["run"]["id"],
        step_id=steps[0],
        state="succeeded",
        expected_revision=run["revision"],
    )
    assert status(project)["next_action"] == steps[1]
    done = checkpoint(
        project,
        run_id=run["run"]["id"],
        step_id=steps[1],
        state="succeeded",
        expected_revision=first["revision"],
    )
    assert done["run"]["state"] == "completed"


def test_local_executor_records_each_registered_outcome(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    steps = ["check:TITLE_MISSING"]
    result = execute(
        project,
        scenario_id="scenario:full-audit",
        steps=steps,
        outcomes=[{"id": steps[0], "state": "succeeded", "evidence": []}],
    )
    assert result["run"]["state"] == "completed"
