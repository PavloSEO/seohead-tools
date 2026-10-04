"""Durable registered-step checkpoints for interruption-safe local workflows."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .coverage import coverage_status
from .runtime import read_document, write_document
from .workspace import _load as _load_workspace

FORMAT = "seohead.workflow-execution.v1"
NAME = "execution.json"


def _text(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _load(directory: str | Path) -> tuple[Path, dict, dict]:
    root, project = _load_project(directory)
    document = read_document(root, NAME)
    if document is None:
        document = {
            "format": FORMAT,
            "revision": 0,
            "project_uuid": project["project_uuid"],
            "runs": [],
        }
    if document.get("format") != FORMAT or document.get("project_uuid") != project["project_uuid"]:
        raise ValueError("execution checkpoint belongs to another project or format")
    return root, project, document


def _load_project(directory: str | Path):
    return _load_workspace(directory)


def _save(root: Path, document: dict, expected_revision: int) -> None:
    if document["revision"] != expected_revision:
        raise ValueError("execution revision conflict")
    document["revision"] += 1
    write_document(root, NAME, document, expected_revision=None)


def start(
    directory: str | Path, *, scenario_id: str, steps: list[str], expected_revision: int = 0
) -> dict:
    root, _, document = _load(directory)
    if document["revision"] != expected_revision:
        raise ValueError("execution revision conflict")
    scenario_id = _text(scenario_id, "scenario_id")
    if (
        not isinstance(steps, list)
        or not steps
        or len(steps) > 200
        or len(set(steps)) != len(steps)
    ):
        raise ValueError("steps must be a unique nonempty list of at most 200 checklist ids")
    items = {row["id"] for row in coverage_status(root)["items"]}
    if any(step not in items for step in steps):
        raise ValueError("every workflow step must be an existing checklist identity")
    run = {
        "id": f"run:{uuid.uuid4()}",
        "scenario_id": scenario_id,
        "steps": [{"id": step, "state": "pending", "evidence": []} for step in steps],
        "state": "running",
    }
    document["runs"].append(run)
    _save(root, document, expected_revision)
    return {"ok": True, "revision": document["revision"], "run": run, "next_action": steps[0]}


def checkpoint(
    directory: str | Path,
    *,
    run_id: str,
    step_id: str,
    state: str,
    evidence: list[dict] | None = None,
    expected_revision: int,
) -> dict:
    root, _, document = _load(directory)
    if document["revision"] != expected_revision:
        raise ValueError("execution revision conflict")
    if state not in {"succeeded", "failed", "unavailable", "skipped"}:
        raise ValueError("checkpoint state must be succeeded, failed, unavailable or skipped")
    run = next((item for item in document["runs"] if item["id"] == run_id), None)
    if run is None or run["state"] != "running":
        raise ValueError("workflow run is not running")
    step = next((item for item in run["steps"] if item["id"] == step_id), None)
    if step is None or step["state"] != "pending":
        raise ValueError("workflow step is not pending")
    if any(item["state"] == "pending" for item in run["steps"][: run["steps"].index(step)]):
        raise ValueError("workflow steps must be checkpointed in registered order")
    records = evidence or []
    if not isinstance(records, list) or len(records) > 20:
        raise ValueError("evidence must be a list of at most 20 records")
    for record in records:
        if not isinstance(record, dict) or set(record) != {"reference", "sha256"}:
            raise ValueError("evidence records require reference and sha256")
        _text(record["reference"], "evidence reference")
        if not isinstance(record["sha256"], str) or len(record["sha256"]) != 64:
            raise ValueError("evidence sha256 must be a 64-character digest")
    step.update(state=state, evidence=records)
    if state != "succeeded":
        run["state"] = state
    elif all(item["state"] == "succeeded" for item in run["steps"]):
        run["state"] = "completed"
    _save(root, document, expected_revision)
    pending = next((item["id"] for item in run["steps"] if item["state"] == "pending"), None)
    return {"ok": True, "revision": document["revision"], "run": run, "next_action": pending}


def status(directory: str | Path) -> dict:
    _, _, document = _load(directory)
    active = next((run for run in reversed(document["runs"]) if run["state"] == "running"), None)
    next_action = (
        next((item["id"] for item in active["steps"] if item["state"] == "pending"), None)
        if active
        else None
    )
    return {
        "ok": True,
        "revision": document["revision"],
        "runs": document["runs"],
        "next_action": next_action,
    }


def execute(
    directory: str | Path, *, scenario_id: str, steps: list[str], outcomes: list[dict]
) -> dict:
    """Checkpoint a supplied local registered-step sequence without any network work."""
    started = start(
        directory,
        scenario_id=scenario_id,
        steps=steps,
        expected_revision=status(directory)["revision"],
    )
    run, revision = started["run"], started["revision"]
    if not isinstance(outcomes, list) or [item.get("id") for item in outcomes] != steps:
        raise ValueError("outcomes must cover registered steps once in declared order")
    for outcome in outcomes:
        result = checkpoint(
            directory,
            run_id=run["id"],
            step_id=outcome.get("id"),
            state=outcome.get("state"),
            evidence=outcome.get("evidence"),
            expected_revision=revision,
        )
        revision = result["revision"]
        if result["run"]["state"] != "running":
            return result
    return status(directory)
