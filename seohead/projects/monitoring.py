"""Configured local incremental monitoring over retained scan observations."""

from __future__ import annotations

import json
from pathlib import Path

from .runtime import read_document, write_document
from .workspace import _load as _workspace_load

FORMAT = "seohead.monitor.v1"
NAME = "monitor.json"


def _load(directory: str | Path):
    root, project = _load_project(directory)
    doc = read_document(root, NAME) or {
        "format": FORMAT,
        "revision": 0,
        "project_uuid": project["project_uuid"],
        "policy": None,
        "runs": [],
    }
    if doc.get("format") != FORMAT or doc.get("project_uuid") != project["project_uuid"]:
        raise ValueError("monitor belongs to another project or format")
    return root, doc


def _load_project(directory):
    return _workspace_load(directory)


def configure(directory: str, policy: dict, expected_revision: int = 0) -> dict:
    root, doc = _load(directory)
    if doc["revision"] != expected_revision:
        raise ValueError("monitor revision conflict")
    required = {"enabled", "urls", "max_urls", "max_requests", "full_refresh_every"}
    if (
        not isinstance(policy, dict)
        or set(policy) != required
        or type(policy["enabled"]) is not bool
    ):
        raise ValueError("monitor policy must declare bounded incremental scope")
    if (
        not isinstance(policy["urls"], list)
        or not policy["urls"]
        or len(policy["urls"]) > 500
        or len(set(policy["urls"])) != len(policy["urls"])
    ):
        raise ValueError("monitor URLs must be a unique bounded nonempty list")
    if any(
        not isinstance(url, str) or not url.startswith(("http://", "https://"))
        for url in policy["urls"]
    ):
        raise ValueError("monitor URLs must be absolute HTTP(S) URLs")
    if any(
        type(policy[key]) is not int or policy[key] < 1
        for key in ("max_urls", "max_requests", "full_refresh_every")
    ):
        raise ValueError("monitor budgets and full refresh interval must be positive integers")
    if policy["max_urls"] > len(policy["urls"]):
        raise ValueError("max_urls cannot exceed configured URL scope")
    doc["policy"] = policy
    doc["revision"] += 1
    write_document(root, NAME, doc)
    return {
        "ok": True,
        "revision": doc["revision"],
        "policy": policy,
        "state": "configured_not_scheduled",
    }


def run(directory: str, scan_id: str, observations: list[dict], expected_revision: int) -> dict:
    root, doc = _load(directory)
    if doc["revision"] != expected_revision or doc["policy"] is None:
        raise ValueError("monitor revision conflict or missing policy")
    policy = doc["policy"]
    if not isinstance(scan_id, str) or not scan_id:
        raise ValueError("scan_id required")
    if not isinstance(observations, list) or len(observations) > policy["max_urls"]:
        raise ValueError("observations exceed configured incremental scope")
    prior = {
        json.dumps(change, sort_keys=True, separators=(",", ":"))
        for earlier in doc["runs"]
        for alert in earlier.get("alerts", [])
        for change in alert.get("changes", [])
    }
    alerts, recoveries = [], []
    for item in observations:
        if (
            not isinstance(item, dict)
            or set(item) != {"url", "changes"}
            or item["url"] not in policy["urls"]
            or not isinstance(item["changes"], list)
        ):
            raise ValueError("invalid scoped monitor observation")
        meaningful = [
            change
            for change in item["changes"]
            if isinstance(change, dict) and change.get("severity") in {"critical", "warning"}
        ]
        new = [
            change
            for change in meaningful
            if json.dumps(change, sort_keys=True, separators=(",", ":")) not in prior
        ]
        if new:
            alerts.append({"url": item["url"], "changes": new, "state": "actionable"})
        recoveries.extend(
            {"url": item["url"], "change": change, "state": "recovery"}
            for change in item["changes"]
            if isinstance(change, dict) and change.get("kind") == "recovered"
        )
    run = {
        "scan_id": scan_id,
        "observed_urls": [item["url"] for item in observations],
        "alerts": alerts,
        "recoveries": recoveries,
        "baseline": observations,
        "state": "actionable" if alerts or recoveries else "quiet",
    }
    doc["runs"].append(run)
    doc["revision"] += 1
    write_document(root, NAME, doc)
    return {"ok": True, "revision": doc["revision"], "run": run, "notification": "none"}


def status(directory: str) -> dict:
    _, doc = _load(directory)
    return {
        "ok": True,
        "revision": doc["revision"],
        "policy": doc["policy"],
        "last_run": doc["runs"][-1] if doc["runs"] else None,
    }


def schedule(directory: str, *, action: str, expected_revision: int) -> dict:
    """Record local runner state only; callers schedule no background job here."""
    root, doc = _load(directory)
    if doc["revision"] != expected_revision or doc["policy"] is None:
        raise ValueError("monitor revision conflict or missing policy")
    if action not in {"start", "cancel", "backoff"}:
        raise ValueError("schedule action must be start, cancel or backoff")
    active = doc.get("runner", {}).get("state") == "running"
    if action == "start" and active:
        raise ValueError("monitor runner already active; overlapping schedules are refused")
    state = {"start": "running", "cancel": "cancelled", "backoff": "backoff"}[action]
    doc["runner"] = {"state": state, "runs_since_full_refresh": len(doc["runs"])}
    doc["revision"] += 1
    write_document(root, NAME, doc)
    return {"ok": True, "revision": doc["revision"], "runner": doc["runner"], "scheduled": False}
