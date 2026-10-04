"""Durable, read-only-observable local collector attempts.

This is intentionally a project-side operation log, not another crawler or a
job scheduler.  A collector records its own lifecycle around existing native or
Screaming Frog calls; the observer only reads the document and checks whether a
recorded local PID is still alive.
"""

from __future__ import annotations

import copy
import math
import os
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import read_document, write_document
from .workspace import _load as _load_workspace

FORMAT = "seohead.run-observation.v1"
NAME = "run-observation.json"
MAX_RUNS = 100
MAX_EVENTS_PER_RUN = 200
MAX_EVENT_MESSAGE = 240
WRITE_ATTEMPTS = 5
_KINDS = {"native", "screaming_frog"}
_STATES = {"running", "finished", "partial", "failed", "cancelled"}
_PHASES = {"admission", "collection", "render", "external", "analysis", "finalizing"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    return value


def _counter(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _optional_counter(value: Any, name: str) -> int | None:
    return None if value is None else _counter(value, name)


def _relative_artifact(root: Path, artifact: str | Path | None) -> str | None:
    if artifact is None:
        return None
    try:
        relative = Path(artifact).resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("collector artifact must be inside the project workspace") from exc
    if not relative.parts or relative.parts[0] not in {"scans", "reports"}:
        raise ValueError("collector artifact must be under scans/ or reports/")
    return relative.as_posix()


def _empty(project_uuid: str) -> dict[str, Any]:
    return {"format": FORMAT, "revision": 0, "project_uuid": project_uuid, "runs": []}


def _process_identity(pid: int) -> str | None:
    """Return a platform process-start identity when the host exposes one."""
    if os.name == "nt":
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None


def _normalize(document: dict[str, Any]) -> None:
    """Read old live-run records without making an observer write a migration."""
    for run in document.get("runs", []) if isinstance(document.get("runs"), list) else []:
        if not isinstance(run, dict):
            continue
        if "controller_pid" not in run and "pid" in run:
            run["controller_pid"] = run["pid"]
            run["controller_start_identity"] = None
            run["collector_pid"] = None
            run["collector_start_identity"] = None
            run["collector_started_at"] = None


def _validate(document: dict[str, Any], project_uuid: str) -> None:
    if (
        set(document) != {"format", "revision", "project_uuid", "runs"}
        or document["format"] != FORMAT
        or document["project_uuid"] != project_uuid
        or type(document["revision"]) is not int
        or document["revision"] < 0
        or not isinstance(document["runs"], list)
        or len(document["runs"]) > MAX_RUNS
    ):
        raise ValueError("run observation document has an unsupported shape")
    for run in document["runs"]:
        if not isinstance(run, dict) or set(run) != {
            "id",
            "kind",
            "state",
            "started_at",
            "finished_at",
            "pid",
            "controller_pid",
            "controller_start_identity",
            "collector_pid",
            "collector_start_identity",
            "collector_started_at",
            "collector",
            "artifact",
            "counters",
            "finish_reason",
            "events",
        }:
            raise ValueError("run observation contains an unsupported run")
        _text(run["id"], "run id", 64)
        if run["kind"] not in _KINDS or run["state"] not in _STATES:
            raise ValueError("run observation has an unsupported kind or state")
        if type(run["pid"]) is not int or run["pid"] <= 0:
            raise ValueError("run observation has an invalid PID")
        if run["controller_pid"] != run["pid"]:
            raise ValueError("run observation controller PID disagrees with its compatibility PID")
        if run["controller_start_identity"] is not None and not isinstance(
            run["controller_start_identity"], str
        ):
            raise ValueError("run observation controller identity is invalid")
        if run["collector_pid"] is not None and (
            type(run["collector_pid"]) is not int or run["collector_pid"] <= 0
        ):
            raise ValueError("run observation collector PID is invalid")
        if run["collector_start_identity"] is not None and not isinstance(
            run["collector_start_identity"], str
        ):
            raise ValueError("run observation collector identity is invalid")
        if run["collector_started_at"] is not None and not isinstance(
            run["collector_started_at"], str
        ):
            raise ValueError("run observation collector start time is invalid")
        if not isinstance(run["collector"], dict) or set(run["collector"]) != {
            "mode",
            "max_urls",
            "max_requests",
            "max_crawl_seconds",
            "max_requests_per_second",
            "config_fingerprint",
            "resumed",
        }:
            raise ValueError("run observation collector shape is invalid")
        if run["collector"]["mode"] not in {"spider", "list", "sf_live", "sf_exports"}:
            raise ValueError("run observation collector mode is invalid")
        _counter(run["collector"]["max_urls"], "collector max_urls")
        _counter(run["collector"]["max_requests"], "collector max_requests")
        _counter(run["collector"]["max_crawl_seconds"], "collector max_crawl_seconds")
        if run["collector"]["max_requests_per_second"] is not None and (
            not isinstance(run["collector"]["max_requests_per_second"], float)
            or not math.isfinite(run["collector"]["max_requests_per_second"])
            or run["collector"]["max_requests_per_second"] < 0
        ):
            raise ValueError("run observation request-rate limit is invalid")
        if not isinstance(run["collector"]["config_fingerprint"], str):
            raise ValueError("run observation config fingerprint is invalid")
        if type(run["collector"]["resumed"]) is not bool:
            raise ValueError("run observation resumed flag is invalid")
        if run["artifact"] is not None and not isinstance(run["artifact"], str):
            raise ValueError("run observation artifact is invalid")
        if not isinstance(run["counters"], dict) or set(run["counters"]) != {
            "fetched",
            "queued",
            "inflight",
            "excluded",
            "rate_per_second",
        }:
            raise ValueError("run observation counters are invalid")
        for name, value in run["counters"].items():
            if name == "rate_per_second":
                if value is not None and (
                    not isinstance(value, float) or not math.isfinite(value) or value < 0
                ):
                    raise ValueError("run observation rate is invalid")
            else:
                _optional_counter(value, name)
        if run["finish_reason"] is not None and not isinstance(run["finish_reason"], str):
            raise ValueError("run observation finish reason is invalid")
        if not isinstance(run["events"], list) or len(run["events"]) > MAX_EVENTS_PER_RUN:
            raise ValueError("run observation events are invalid")


def _load(directory: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root, project = _load_workspace(directory)
    document = read_document(root, NAME) or _empty(project["project_uuid"])
    _normalize(document)
    _validate(document, project["project_uuid"])
    return root, project, document


def _event(run: dict[str, Any], phase: str, code: str, *, message: str | None = None) -> None:
    if phase not in _PHASES:
        raise ValueError("run observation phase is invalid")
    entry = {"at": _now(), "phase": phase, "code": _text(code, "event code", 64)}
    if message is not None:
        entry["message"] = _text(message, "event message", MAX_EVENT_MESSAGE)
    run["events"].append(entry)
    del run["events"][:-MAX_EVENTS_PER_RUN]


def _find(document: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_id = _text(run_id, "run id", 64)
    found = next((item for item in document["runs"] if item["id"] == run_id), None)
    if found is None:
        raise ValueError("run observation is not part of this project")
    return found


def _save(root: Path, document: dict[str, Any]) -> None:
    expected = document["revision"]
    document["revision"] += 1
    write_document(root, NAME, document, expected_revision=expected)


def _contention(exc: ValueError) -> bool:
    return str(exc) in {"project document revision conflict", f"another writer owns {NAME}"}


def _retry_delay(attempt: int) -> None:
    time.sleep(0.01 * (attempt + 1))


def start(
    directory: str | Path,
    *,
    kind: str,
    mode: str,
    max_urls: int,
    max_requests: int = 0,
    max_crawl_seconds: int = 0,
    max_requests_per_second: float | None = None,
    config_fingerprint: str,
    artifact: str | Path | None,
    resumed: bool = False,
    counters: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    """Persist one project-bound local collector before it starts doing work."""
    if kind not in _KINDS or mode not in {"spider", "list", "sf_live", "sf_exports"}:
        raise ValueError("run kind or collector mode is invalid")
    _counter(max_urls, "max_urls")
    _counter(max_requests, "max_requests")
    _counter(max_crawl_seconds, "max_crawl_seconds")
    if max_requests_per_second is not None and (
        not isinstance(max_requests_per_second, float)
        or not math.isfinite(max_requests_per_second)
        or max_requests_per_second < 0
    ):
        raise ValueError("max_requests_per_second must be a finite nonnegative float")
    if type(resumed) is not bool:
        raise ValueError("resumed must be boolean")
    run_id = uuid.uuid4().hex
    started_at = _now()
    controller_pid = os.getpid()
    controller_identity = _process_identity(controller_pid)
    for attempt in range(WRITE_ATTEMPTS):
        root, _project, document = _load(directory)
        run = {
            "id": run_id,
            "kind": kind,
            "state": "running",
            "started_at": started_at,
            "finished_at": None,
            "pid": controller_pid,
            "controller_pid": controller_pid,
            "controller_start_identity": controller_identity,
            "collector_pid": None,
            "collector_start_identity": None,
            "collector_started_at": None,
            "collector": {
                "mode": mode,
                "max_urls": max_urls,
                "max_requests": max_requests,
                "max_crawl_seconds": max_crawl_seconds,
                "max_requests_per_second": max_requests_per_second,
                "config_fingerprint": _text(config_fingerprint, "config fingerprint", 256),
                "resumed": resumed,
            },
            "artifact": _relative_artifact(root, artifact),
            "counters": {
                **{
                    name: _optional_counter((counters or {}).get(name, 0), name)
                    for name in ("fetched", "queued", "inflight", "excluded")
                },
                "rate_per_second": (counters or {}).get("rate_per_second"),
            },
            "finish_reason": None,
            "events": [],
        }
        _event(run, "admission", "started")
        document["runs"].append(run)
        del document["runs"][:-MAX_RUNS]
        try:
            _save(root, document)
            return copy.deepcopy(run)
        except ValueError as exc:
            if not _contention(exc) or attempt + 1 == WRITE_ATTEMPTS:
                raise
            _retry_delay(attempt)
    raise AssertionError("unreachable")


def phase(directory: str | Path, run_id: str, name: str, *, code: str = "entered") -> None:
    root, _project, document = _load(directory)
    run = _find(document, run_id)
    if run["state"] != "running":
        return
    _event(run, name, code)
    _save(root, document)


def collector_started(directory: str | Path, run_id: str, pid: int) -> None:
    """Bind an SF child PID after spawn; it is distinct from the controller PID."""
    if type(pid) is not int or pid <= 0:
        raise ValueError("collector PID must be a positive integer")
    identity = _process_identity(pid)
    started_at = _now()
    for attempt in range(WRITE_ATTEMPTS):
        root, _project, document = _load(directory)
        run = _find(document, run_id)
        if run["state"] != "running":
            return
        run["collector_pid"] = pid
        run["collector_start_identity"] = identity
        run["collector_started_at"] = started_at
        _event(run, "collection", "collector_started")
        try:
            _save(root, document)
            return
        except ValueError as exc:
            if not _contention(exc) or attempt + 1 == WRITE_ATTEMPTS:
                raise
            _retry_delay(attempt)


def progress(
    directory: str | Path,
    run_id: str,
    *,
    fetched: int,
    queued: int,
    inflight: int = 0,
    excluded: int = 0,
    rate_per_second: float | None = None,
) -> None:
    """Record measured collector counters; no percentage or site total is inferred."""
    root, _project, document = _load(directory)
    run = _find(document, run_id)
    if run["state"] != "running":
        return
    values = {
        "fetched": _counter(fetched, "fetched"),
        "queued": _counter(queued, "queued"),
        "inflight": _counter(inflight, "inflight"),
        "excluded": _counter(excluded, "excluded"),
        "rate_per_second": rate_per_second,
    }
    if rate_per_second is not None and (
        not isinstance(rate_per_second, float)
        or not math.isfinite(rate_per_second)
        or rate_per_second < 0
    ):
        raise ValueError("rate_per_second must be a finite nonnegative float")
    run["counters"] = values
    _event(run, "collection", "progress")
    _save(root, document)


def finish(
    directory: str | Path,
    run_id: str,
    *,
    state: str,
    reason: str,
    counters: dict[str, int | float | None] | None = None,
) -> None:
    if state not in _STATES - {"running"}:
        raise ValueError("run terminal state is invalid")
    normalized_counters = (
        {
            **{
                name: _optional_counter(counters.get(name, 0), name)
                for name in ("fetched", "queued", "inflight", "excluded")
            },
            "rate_per_second": counters.get("rate_per_second"),
        }
        if counters is not None
        else None
    )
    finish_reason = _text(reason, "finish reason", 500)
    finished_at = _now()
    for attempt in range(WRITE_ATTEMPTS):
        root, _project, document = _load(directory)
        run = _find(document, run_id)
        if run["state"] != "running":
            return
        if normalized_counters is not None:
            run["counters"] = normalized_counters
        run["state"] = state
        run["finish_reason"] = finish_reason
        run["finished_at"] = finished_at
        _event(run, "finalizing", "finished" if state == "finished" else state)
        try:
            _save(root, document)
            return
        except ValueError as exc:
            if not _contention(exc) or attempt + 1 == WRITE_ATTEMPTS:
                raise
            _retry_delay(attempt)


def _runtime_state(pid: int | None, identity: str | None, *, running: bool) -> str:
    if not running:
        return "retained"
    if pid is None:
        return "unavailable"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "abandoned"
    except PermissionError:
        return "unknown"
    except (OSError, OverflowError, ValueError):
        return "unknown"
    current = _process_identity(pid)
    if identity is None or current is None:
        return "unknown"
    if current != identity:
        return "stale"
    return "live"


def status(directory: str | Path, *, limit: int = 20) -> dict[str, Any]:
    """Read a bounded current/retained collector view without writing anything."""
    if type(limit) is not int or not 1 <= limit <= MAX_RUNS:
        raise ValueError(f"run observation limit must be 1..{MAX_RUNS}")
    _root, _project, document = _load(directory)
    rows = []
    for run in reversed(document["runs"][-limit:]):
        row = copy.deepcopy(run)
        observed_at = _now()
        running = run["state"] == "running"
        controller_state = _runtime_state(
            run["controller_pid"], run["controller_start_identity"], running=running
        )
        collector_state = _runtime_state(
            run["collector_pid"], run["collector_start_identity"], running=running
        )
        row["pid_state"] = controller_state
        row["controller"] = {
            "pid": run["controller_pid"],
            "start_identity": run["controller_start_identity"],
            "state": controller_state,
            "observed_at": observed_at,
        }
        row["collector_runtime"] = {
            "pid": run["collector_pid"],
            "start_identity": run["collector_start_identity"],
            "started_at": run["collector_started_at"],
            "state": collector_state,
            "observed_at": observed_at,
        }
        rows.append(row)
    return {
        "ok": True,
        "revision": document["revision"],
        "total": len(document["runs"]),
        "items": rows,
        "has_more": len(document["runs"]) > len(rows),
    }


class NativeRunReporter:
    """Throttle durable progress writes while preserving every caller's terminal callback."""

    def __init__(self, directory: str | Path, run_id: str, downstream=None) -> None:
        self.directory = str(directory)
        self.run_id = run_id
        self.downstream = downstream
        self.last_write = 0.0
        self.fetched = 0
        self.queued = 0
        self.samples: deque[tuple[float, int]] = deque(maxlen=2)
        self.rate_per_second: float | None = None

    def __call__(self, fetched: int, queued: int) -> None:
        if self.downstream is not None:
            self.downstream(fetched, queued)
        self.fetched = fetched
        self.queued = queued
        now = time.monotonic()
        self.samples.append((now, fetched))
        if len(self.samples) == 2:
            (then, prior), (current, total) = self.samples
            self.rate_per_second = (total - prior) / (current - then) if current > then else None
        if self.last_write and now - self.last_write < 0.5:
            return
        self.last_write = now
        try:
            progress(
                self.directory,
                self.run_id,
                fetched=fetched,
                queued=queued,
                rate_per_second=self.rate_per_second,
            )
        except (OSError, ValueError):
            # Observation persistence never turns a successful collector into a failed crawl.
            return

    def enter(self, name: str, *, code: str = "entered") -> None:
        try:
            phase(self.directory, self.run_id, name, code=code)
        except (OSError, ValueError):
            return

    def counters(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "queued": self.queued,
            "inflight": 0,
            "excluded": 0,
            "rate_per_second": self.rate_per_second,
        }
