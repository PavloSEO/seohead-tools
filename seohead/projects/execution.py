"""Durable, registered workflow checkpoints for interruption-safe local work."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

from .coverage import coverage_status
from .runtime import read_document, write_document
from .workspace import _load as _load_workspace

FORMAT = "seohead.workflow-execution.v1"
NAME = "execution.json"
_TERMINAL = {"completed", "cancelled"}
_STEP_STATES = {"succeeded", "failed", "unavailable", "skipped", "interrupted"}


def _text(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _load(directory: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root, project = _load_workspace(directory)
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
    if not isinstance(document.get("runs"), list) or len(document["runs"]) > 10_000:
        raise ValueError("execution checkpoint has invalid run history")
    return root, project, document


def _save(root: Path, document: dict[str, Any], expected_revision: int) -> None:
    if document["revision"] != expected_revision:
        raise ValueError("execution revision conflict")
    document["revision"] += 1
    write_document(root, NAME, document)


def _catalogue_hash(item_id: str) -> str | None:
    from .catalogue import load_catalogue

    entry = load_catalogue().get(item_id)
    return entry.get("definition_hash") if entry else None


def _context(value: Any, project: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"prompt_reference", "competitors", "phase"}:
        raise ValueError("workflow context has unsupported fields")
    competitors = value.get("competitors", [])
    if (
        not isinstance(competitors, list)
        or len(competitors) > 20
        or len(set(competitors)) != len(competitors)
        or any(
            not isinstance(url, str) or not url.startswith(("http://", "https://"))
            for url in competitors
        )
    ):
        raise ValueError("workflow competitors must be a unique bounded HTTP(S) list")
    result = {"own_site": project["site"]["target"], "competitors": competitors}
    for key, maximum in (("prompt_reference", 512), ("phase", 128)):
        if key in value:
            result[key] = _text(value[key], key, maximum)
    return result


def _evidence(records: list[dict] | None) -> list[dict]:
    records = records or []
    if not isinstance(records, list) or len(records) > 20:
        raise ValueError("evidence must be a list of at most 20 records")
    result = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"reference", "sha256"}:
            raise ValueError("evidence records require reference and sha256")
        reference = _text(record["reference"], "evidence reference")
        digest = record["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("evidence sha256 must be a lowercase 64-character digest")
        result.append({"reference": reference, "sha256": digest})
    return result


def _review(value: Any, execution_kind: str, state: str) -> dict[str, str] | None:
    if state != "succeeded" or execution_kind == "automatic":
        return None
    if not isinstance(value, dict) or set(value) != {"actor", "state", "reason"}:
        raise ValueError("manual and deliverable steps require an explicit approved review")
    if value["state"] != "approved":
        raise ValueError("manual and deliverable steps require an approved review")
    return {
        "actor": _text(value["actor"], "review actor", 128),
        "state": "approved",
        "reason": _text(value["reason"], "review reason", 512),
    }


def _rows(root: Path) -> dict[str, dict[str, Any]]:
    view = coverage_status(root)
    if view.get("state") == "not_initialized":
        return {}
    items = view.get("items")
    if not isinstance(items, list) or any(
        not isinstance(row, dict) or "id" not in row for row in items
    ):
        raise ValueError("initialized coverage status has invalid checklist items")
    return {row["id"]: row for row in items}


def _public_run(run: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = copy.deepcopy(run)
    stale: list[dict[str, str]] = []
    scenario_hash = _catalogue_hash(item["scenario"]["id"])
    if scenario_hash != item["scenario"].get("definition_hash"):
        stale.append(
            {"id": item["scenario"]["id"], "reason": "scenario or skill definition changed"}
        )
    for step in item["steps"]:
        row = rows.get(step["id"])
        if row is None:
            stale.append({"id": step["id"], "reason": "registered checklist step was removed"})
        elif row["stale"]:
            stale.append({"id": step["id"], "reason": row["reason"]})
        elif _catalogue_hash(step["id"]) != step.get("definition_hash"):
            stale.append({"id": step["id"], "reason": "registered step definition changed"})
    item["stale_dependencies"] = stale
    item["next_action"] = next(
        (step["id"] for step in item["steps"] if step["state"] == "pending"), None
    )
    return item


def start(
    directory: str | Path,
    *,
    scenario_id: str,
    steps: list[str],
    expected_revision: int = 0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root, project, document = _load(directory)
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
    rows = _rows(root)
    scenario = rows.get(scenario_id)
    if scenario is None or scenario["kind"] not in {"scenario", "skill"}:
        raise ValueError("scenario_id must be a registered project scenario or skill")
    if any(step not in rows for step in steps):
        raise ValueError("every workflow step must be an existing checklist identity")
    run = {
        "id": f"run:{uuid.uuid4()}",
        "attempt": 1
        + sum(existing["scenario"]["id"] == scenario_id for existing in document["runs"]),
        "scenario": {"id": scenario_id, "definition_hash": _catalogue_hash(scenario_id)},
        "context": _context(context, project),
        "phase": (context or {}).get("phase", "registered"),
        "steps": [
            {
                "id": step,
                "definition_hash": _catalogue_hash(step),
                "execution_kind": rows[step]["execution_kind"],
                "state": "pending",
                "evidence": [],
            }
            for step in steps
        ],
        "state": "running",
    }
    document["runs"].append(run)
    _save(root, document, expected_revision)
    return {
        "ok": True,
        "revision": document["revision"],
        "run": _public_run(run, rows),
        "next_action": steps[0],
    }


def checkpoint(
    directory: str | Path,
    *,
    run_id: str,
    step_id: str,
    state: str,
    evidence: list[dict] | None = None,
    expected_revision: int,
    review: dict[str, Any] | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    root, _, document = _load(directory)
    if document["revision"] != expected_revision:
        raise ValueError("execution revision conflict")
    if state not in _STEP_STATES:
        raise ValueError("checkpoint state is invalid")
    run = next((item for item in document["runs"] if item["id"] == run_id), None)
    if run is None or run["state"] != "running":
        raise ValueError("workflow run is not running")
    step = next((item for item in run["steps"] if item["id"] == step_id), None)
    if step is None or step["state"] != "pending":
        raise ValueError("workflow step is not pending")
    if any(item["state"] == "pending" for item in run["steps"][: run["steps"].index(step)]):
        raise ValueError("workflow steps must be checkpointed in registered order")
    rows = _rows(root)
    current = rows.get(step_id)
    if state == "succeeded" and (current is None or current["stale"] or current["blocked_by"]):
        raise ValueError(
            "stale or blocked dependencies must be reconciled before a successful checkpoint"
        )
    step.update(state=state, evidence=_evidence(evidence))
    approved = _review(review, step["execution_kind"], state)
    if approved:
        step["review"] = approved
    if phase is not None:
        run["phase"] = _text(phase, "phase", 128)
    if state == "succeeded" and all(item["state"] == "succeeded" for item in run["steps"]):
        run["state"] = "completed"
    elif state != "succeeded":
        run["state"] = "interrupted" if state == "interrupted" else "blocked"
    _save(root, document, expected_revision)
    public = _public_run(run, rows)
    return {
        "ok": True,
        "revision": document["revision"],
        "run": public,
        "next_action": public["next_action"],
    }


def resume(directory: str | Path, *, run_id: str, expected_revision: int) -> dict[str, Any]:
    """Deliberately reopen the first non-successful step after a handoff or interruption."""
    root, _, document = _load(directory)
    if document["revision"] != expected_revision:
        raise ValueError("execution revision conflict")
    run = next((item for item in document["runs"] if item["id"] == run_id), None)
    if run is None or run["state"] not in {"blocked", "interrupted"}:
        raise ValueError("workflow run is not resumable")
    rows = _rows(root)
    public = _public_run(run, rows)
    if public["stale_dependencies"]:
        raise ValueError("reconcile stale dependencies before resuming workflow")
    retry = next((step for step in run["steps"] if step["state"] != "succeeded"), None)
    if retry is None:
        raise ValueError("completed workflow run cannot be resumed")
    retry.update(state="pending", evidence=[])
    retry.pop("review", None)
    run["state"] = "running"
    _save(root, document, expected_revision)
    return {
        "ok": True,
        "revision": document["revision"],
        "run": _public_run(run, rows),
        "next_action": retry["id"],
    }


def status(directory: str | Path) -> dict[str, Any]:
    root, _, document = _load(directory)
    rows = _rows(root)
    runs = [_public_run(run, rows) for run in document["runs"]]
    active = next((run for run in reversed(runs) if run["state"] == "running"), None)
    resumable = next(
        (run for run in reversed(runs) if run["state"] in {"blocked", "interrupted"}), None
    )
    return {
        "ok": True,
        "revision": document["revision"],
        "runs": runs,
        "next_action": active["next_action"] if active else None,
        "resumable_run": resumable["id"] if resumable else None,
    }


def execute(
    directory: str | Path,
    *,
    scenario_id: str,
    steps: list[str],
    outcomes: list[dict],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Checkpoint supplied local outcomes; this helper executes no commands or network work."""
    started = start(
        directory,
        scenario_id=scenario_id,
        steps=steps,
        context=context,
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
            review=outcome.get("review"),
            phase=outcome.get("phase"),
            expected_revision=revision,
        )
        revision = result["revision"]
        if result["run"]["state"] != "running":
            return result
    return status(directory)
