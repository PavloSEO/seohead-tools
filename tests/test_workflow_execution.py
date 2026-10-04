from __future__ import annotations

import pytest

from seohead.projects.coverage import initialize_coverage
from seohead.projects.execution import checkpoint, execute, resume, start, status
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
        outcomes=[{"id": steps[0], "state": "succeeded", "evidence": []}],
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
        context={"prompt_reference": "inbox:goal-1", "competitors": ["https://competitor.test/"]},
    )
    first = checkpoint(
        project,
        run_id=started["run"]["id"],
        step_id=steps[0],
        state="succeeded",
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
    assert recovered["run"]["phase"] == "own-site-capture"


def test_manual_step_needs_an_explicit_review_before_completion(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    initialize_coverage(project)
    started = start(project, scenario_id="scenario:full-audit", steps=["skill:workflow/control"])
    with pytest.raises(ValueError, match="explicit approved review"):
        checkpoint(
            project,
            run_id=started["run"]["id"],
            step_id="skill:workflow/control",
            state="succeeded",
            expected_revision=started["revision"],
        )
