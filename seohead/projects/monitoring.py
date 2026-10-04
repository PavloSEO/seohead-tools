"""Disabled-by-default local monitoring over immutable retained observations.

This module deliberately plans and records local work only. It neither starts a
timer nor fetches a URL: a caller supplies the bounded observations produced by
an already-authorized collector. That keeps schedule activation and delivery
outside a project file while still making restarts, overlap refusal, budgets and
full-refresh policy observable and testable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import read_document, write_document
from .workspace import _load as _workspace_load

FORMAT = "seohead.monitor.v1"
NAME = "monitor.json"
_SEVERITIES = ("notice", "warning", "critical")
_QUALIFIERS = ("fresh", "revalidated", "stale", "unavailable", "partial")
_TRACKED_FIELDS = ("status", "indexability", "canonical", "robots", "metadata", "content", "links")
_DEFAULTS = {
    "interval_seconds": 3600,
    "max_render_requests": 0,
    "suppression_runs": 1,
    "severity_threshold": "warning",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(directory: str | Path) -> tuple[Path, dict[str, Any]]:
    root, project = _workspace_load(directory)
    document = read_document(root, NAME) or {
        "format": FORMAT,
        "revision": 0,
        "project_uuid": project["project_uuid"],
        "policy": None,
        "runs": [],
        "runner": {"state": "idle", "runs_since_full_refresh": 0},
    }
    if document.get("format") != FORMAT or document.get("project_uuid") != project["project_uuid"]:
        raise ValueError("monitor belongs to another project or format")
    if not isinstance(document.get("runs"), list) or len(document["runs"]) > 10_000:
        raise ValueError("monitor has invalid retained run history")
    document.setdefault("runner", {"state": "idle", "runs_since_full_refresh": 0})
    return root, document


def _policy(value: Any) -> dict[str, Any]:
    required = {"enabled", "urls", "max_urls", "max_requests", "full_refresh_every"}
    if not isinstance(value, dict) or not required <= set(value):
        raise ValueError("monitor policy must declare bounded incremental scope")
    unknown = set(value) - (required | set(_DEFAULTS))
    if unknown:
        raise ValueError("monitor policy has unsupported fields")
    policy = {**_DEFAULTS, **value}
    if type(policy["enabled"]) is not bool:
        raise ValueError("monitor policy enabled must be boolean")
    urls = policy["urls"]
    if (
        not isinstance(urls, list)
        or not urls
        or len(urls) > 500
        or len(set(urls)) != len(urls)
        or any(
            not isinstance(url, str) or not url.startswith(("http://", "https://")) for url in urls
        )
    ):
        raise ValueError("monitor URLs must be a unique bounded absolute HTTP(S) list")
    for key in (
        "max_urls",
        "max_requests",
        "full_refresh_every",
        "interval_seconds",
        "suppression_runs",
    ):
        if type(policy[key]) is not int or policy[key] < 1:
            raise ValueError(f"monitor {key} must be a positive integer")
    if type(policy["max_render_requests"]) is not int or policy["max_render_requests"] < 0:
        raise ValueError("monitor max_render_requests must be a nonnegative integer")
    if policy["max_urls"] > len(urls) or policy["max_requests"] < len(urls):
        raise ValueError("monitor request and URL budgets are inconsistent")
    if policy["severity_threshold"] not in _SEVERITIES:
        raise ValueError("monitor severity_threshold must be notice, warning or critical")
    return policy


def _due(document: dict[str, Any]) -> dict[str, Any]:
    policy = document.get("policy")
    if policy is None:
        return {"state": "unconfigured", "reason": "configure a bounded policy first"}
    runner = document["runner"]
    if not policy["enabled"]:
        return {"state": "disabled", "reason": "monitoring is disabled by policy"}
    if runner.get("state") == "running":
        return {"state": "claimed", "reason": "a local runner already owns the next bounded pass"}
    full = (
        not document["runs"]
        or runner.get("runs_since_full_refresh", 0) >= policy["full_refresh_every"]
    )
    return {
        "state": "due",
        "mode": "full" if full else "incremental",
        "url_limit": len(policy["urls"]) if full else policy["max_urls"],
        "request_budget": policy["max_requests"],
        "render_request_budget": policy["max_render_requests"],
        "interval_seconds": policy["interval_seconds"],
        "reason": "periodic full coverage refresh" if full else "bounded incremental refresh",
    }


def _planned_due(document: dict[str, Any]) -> dict[str, Any]:
    """Keep a claimed full pass full even when the runner is already active."""
    runner = document["runner"]
    plan = runner.get("plan")
    if runner.get("state") == "running" and isinstance(plan, dict) and plan.get("state") == "due":
        return plan
    return _due(document)


def configure(directory: str | Path, policy: dict, expected_revision: int = 0) -> dict[str, Any]:
    root, document = _load(directory)
    if document["revision"] != expected_revision:
        raise ValueError("monitor revision conflict")
    if document["runner"].get("state") == "running":
        raise ValueError("cancel the active local runner before changing its policy")
    document["policy"] = _policy(policy)
    document["revision"] += 1
    write_document(root, NAME, document)
    return {
        "ok": True,
        "revision": document["revision"],
        "policy": document["policy"],
        "state": "configured_not_scheduled",
        "due": _due(document),
    }


def _change_key(change: dict[str, Any]) -> str:
    return json.dumps(change, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _threshold(policy: dict[str, Any], change: dict[str, Any]) -> bool:
    return _SEVERITIES.index(change.get("severity", "notice")) >= _SEVERITIES.index(
        policy["severity_threshold"]
    )


def _observation(item: Any, policy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) - {
        "url",
        "changes",
        "qualifier",
        "measurement",
        "request_count",
        "render_request_count",
        "cache_state",
    }:
        raise ValueError("invalid scoped monitor observation")
    if item.get("url") not in policy["urls"] or not isinstance(item.get("changes"), list):
        raise ValueError("invalid scoped monitor observation")
    qualifier = item.get("qualifier", "fresh")
    if qualifier not in _QUALIFIERS:
        raise ValueError("monitor observation qualifier is invalid")
    cache_state = item.get("cache_state", qualifier)
    if cache_state not in {"fresh", "revalidated", "cached", "unavailable"}:
        raise ValueError("monitor observation cache_state is invalid")
    request_count = item.get("request_count", 1)
    render_request_count = item.get("render_request_count", 0)
    if type(request_count) is not int or request_count < 0:
        raise ValueError("monitor observation request_count is invalid")
    if type(render_request_count) is not int or render_request_count < 0:
        raise ValueError("monitor observation render_request_count is invalid")
    measurement = item.get("measurement")
    if measurement is not None and (
        not isinstance(measurement, dict)
        or not set(measurement) <= set(_TRACKED_FIELDS)
        or len(measurement) > len(_TRACKED_FIELDS)
        or len(json.dumps(measurement, sort_keys=True, ensure_ascii=False).encode()) > 64 * 1024
    ):
        raise ValueError("monitor observation measurement is invalid")
    changes: list[dict[str, Any]] = []
    for change in item["changes"]:
        if not isinstance(change, dict) or change.get("severity") not in _SEVERITIES:
            raise ValueError("monitor changes need a declared severity")
        changes.append(change)
    return {
        "url": item["url"],
        "changes": changes,
        "qualifier": qualifier,
        "measurement": measurement,
        "request_count": request_count,
        "render_request_count": render_request_count,
        "cache_state": cache_state,
        "measured_at": _now(),
    }


def _previous_measurement(document: dict[str, Any], url: str) -> tuple[dict[str, Any], str] | None:
    for earlier in reversed(document["runs"]):
        for observation in reversed(earlier.get("observations", [])):
            if observation.get("url") == url and isinstance(observation.get("measurement"), dict):
                return observation["measurement"], earlier["scan_id"]
    return None


def _measurement_changes(
    document: dict[str, Any], observation: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compare only two measured values; omitted and unavailable fields stay unknown."""
    if observation["qualifier"] in {"stale", "unavailable", "partial"}:
        return []
    current = observation["measurement"]
    previous = _previous_measurement(document, observation["url"])
    if current is None or previous is None:
        return []
    baseline, baseline_scan_id = previous
    changes = []
    for field in _TRACKED_FIELDS:
        if field not in current or field not in baseline or current[field] == baseline[field]:
            continue
        changes.append(
            {
                "kind": f"{field}_changed",
                "field": field,
                "before": baseline[field],
                "after": current[field],
                "baseline_scan_id": baseline_scan_id,
                "severity": "warning"
                if field in {"status", "indexability", "canonical", "robots"}
                else "notice",
            }
        )
    return changes


def _recent_alerts(document: dict[str, Any], suppression_runs: int) -> set[str]:
    """Suppress repeats for a bounded number of completed monitor passes only."""
    return {
        _change_key(change)
        for earlier in document["runs"][-suppression_runs:]
        for alert in earlier.get("alerts", [])
        for change in alert.get("changes", [])
    }


def run(
    directory: str | Path, scan_id: str, observations: list[dict], expected_revision: int
) -> dict[str, Any]:
    """Retain an already-collected bounded diff; no fetch, timer or delivery occurs."""
    root, document = _load(directory)
    if document["revision"] != expected_revision or document["policy"] is None:
        raise ValueError("monitor revision conflict or missing policy")
    policy = document["policy"]
    if not isinstance(scan_id, str) or not scan_id.strip() or len(scan_id) > 512:
        raise ValueError("scan_id required")
    due = _planned_due(document)
    limit = len(policy["urls"]) if due.get("mode") == "full" else policy["max_urls"]
    if not isinstance(observations, list) or not observations or len(observations) > limit:
        raise ValueError("observations exceed configured incremental scope")
    normalized = [_observation(item, policy) for item in observations]
    for item in normalized:
        derived = _measurement_changes(document, item)
        seen = {_change_key(change) for change in item["changes"]}
        item["changes"].extend(change for change in derived if _change_key(change) not in seen)
    if len({item["url"] for item in normalized}) != len(normalized):
        raise ValueError("monitor observations may contain each URL only once")
    if sum(item["request_count"] for item in normalized) > policy["max_requests"]:
        raise ValueError("monitor observations exceed configured request budget")
    if sum(item["render_request_count"] for item in normalized) > policy["max_render_requests"]:
        raise ValueError("monitor observations exceed configured render request budget")
    if (
        due.get("state") == "due"
        and due.get("mode") == "full"
        and set(item["url"] for item in normalized) != set(policy["urls"])
    ):
        raise ValueError(
            "periodic full refresh must retain an observation for every configured URL"
        )
    previous = _recent_alerts(document, policy["suppression_runs"])
    alerts: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    partial = False
    for item in normalized:
        if item["qualifier"] in {"stale", "unavailable", "partial"}:
            partial = True
            continue
        meaningful = [
            change
            for change in item["changes"]
            if _threshold(policy, change) and change.get("kind") != "recovered"
        ]
        fresh = [change for change in meaningful if _change_key(change) not in previous]
        if fresh:
            alerts.append({"url": item["url"], "changes": fresh, "state": "actionable"})
        recoveries.extend(
            {"url": item["url"], "change": change, "state": "recovery"}
            for change in item["changes"]
            if change.get("kind") == "recovered" and _threshold(policy, change)
        )
    full = due.get("mode") == "full" and not partial
    state = "partial" if partial else "actionable" if alerts or recoveries else "quiet"
    retained = {
        "scan_id": scan_id.strip(),
        "recorded_at": _now(),
        "mode": "full" if full else "incremental",
        "observations": normalized,
        "observed_urls": [item["url"] for item in normalized],
        "alerts": alerts,
        "recoveries": recoveries,
        "baseline": {"scan_id": scan_id.strip(), "observations": normalized},
        "state": state,
        "notification": "none",
    }
    document["runs"].append(retained)
    document["runner"] = {
        "state": "idle",
        "runs_since_full_refresh": (
            0
            if full
            else policy["full_refresh_every"]
            if due.get("mode") == "full"
            else document["runner"].get("runs_since_full_refresh", 0) + 1
        ),
        "last_finished_at": retained["recorded_at"],
    }
    document["revision"] += 1
    write_document(root, NAME, document)
    return {
        "ok": True,
        "revision": document["revision"],
        "run": retained,
        "notification": "none",
        "due": _due(document),
    }


def schedule(directory: str | Path, *, action: str, expected_revision: int) -> dict[str, Any]:
    """Claim, cancel, back off or recover a local runner without starting a timer."""
    root, document = _load(directory)
    if document["revision"] != expected_revision or document["policy"] is None:
        raise ValueError("monitor revision conflict or missing policy")
    if action not in {"start", "cancel", "backoff", "recover"}:
        raise ValueError("schedule action must be start, cancel, backoff or recover")
    current = document["runner"].get("state", "idle")
    if action == "start":
        if not document["policy"]["enabled"]:
            raise ValueError("monitor policy is disabled; explicit enablement is required")
        if current == "running":
            raise ValueError("monitor runner already active; overlapping schedules are refused")
        runner = {
            **document["runner"],
            "state": "running",
            "claimed_at": _now(),
            "plan": _due(document),
        }
    elif action == "recover":
        if current != "running":
            raise ValueError("only an interrupted running claim can be recovered")
        runner = {**document["runner"], "state": "interrupted", "interrupted_at": _now()}
    else:
        runner = {
            **document["runner"],
            "state": "cancelled" if action == "cancel" else "backoff",
            "changed_at": _now(),
        }
    document["runner"] = runner
    document["revision"] += 1
    write_document(root, NAME, document)
    return {
        "ok": True,
        "revision": document["revision"],
        "runner": runner,
        "scheduled": False,
        "due": _due(document),
    }


def deliver(
    directory: str | Path,
    *,
    scan_id: str,
    destination: str,
    service: Any,
    expected_revision: int,
) -> dict[str, Any]:
    """Explicitly hand one retained complete run to an injected authorized service.

    This function cannot construct a destination, load credentials, or activate a
    schedule.  ``service`` is a caller-owned :class:`MonitorServiceDelivery`.
    """
    root, document = _load(directory)
    if document["revision"] != expected_revision:
        raise ValueError("monitor revision conflict")
    if getattr(service, "project_uuid", None) != document["project_uuid"]:
        raise PermissionError("monitor service is not authorized for this project")
    if document["runner"].get("state") in {"cancelled", "backoff"}:
        raise ValueError("cancelled or backed-off monitor work is not deliverable")
    run = next(
        (item for item in reversed(document["runs"]) if item.get("scan_id") == scan_id), None
    )
    if run is None:
        raise ValueError("retained monitor scan_id is unknown")
    result = service.deliver(run, destination)
    # Evidence stays immutable; this small receipt projection only records that
    # a caller-owned authorized destination accepted the already-retained event.
    newly_sent = [receipt for receipt in result["receipts"] if receipt["state"] == "sent"]
    if newly_sent:
        run.setdefault("delivery", []).extend(
            {"destination": destination, **receipt} for receipt in newly_sent
        )
        document["revision"] += 1
        write_document(root, NAME, document)
    return {"ok": True, "revision": document["revision"], **result}


def status(directory: str | Path) -> dict[str, Any]:
    _, document = _load(directory)
    return {
        "ok": True,
        "revision": document["revision"],
        "policy": document["policy"],
        "runner": document["runner"],
        "last_run": document["runs"][-1] if document["runs"] else None,
        "due": _due(document),
    }
