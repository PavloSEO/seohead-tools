"""Read-only projection for terminal and future web project observers.

This module deliberately composes the existing project, coverage, preparation,
scan-history and inbox stores.  It owns no crawl, task, finding, or job state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .inbox import list_entries, unread_summary
from .progress import project_progress
from .workspace import _load, project_status

LOG_BYTES = 32 * 1024


def _log(root: Path) -> dict[str, Any]:
    path = root / "log.md"
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - LOG_BYTES))
        text = stream.read(LOG_BYTES).decode("utf-8", errors="replace")
    return {"text": text, "truncated": size > LOG_BYTES, "bytes": size}


def observe(directory: str, *, consumer: str | None = None, scan_limit: int = 20) -> dict[str, Any]:
    """Return a bounded observer snapshot without changing project evidence."""
    if type(scan_limit) is not int or not 1 <= scan_limit <= 100:
        raise ValueError("scan_limit must be from 1 to 100")
    root, _ = _load(directory)
    status = project_status(root)
    progress = project_progress(root, limit=100)
    preparation = status["preparation"]
    checks = status["checklist"].get("items", [])
    methods = [
        {
            "id": item["id"],
            "title": item["title"],
            "kind": item["kind"],
            "state": item["state"],
            "attempt_status": item["attempt_status"],
            "stale": item["stale"],
            "reason": item["reason"],
        }
        for item in checks
        if item["kind"] in {"scenario", "skill"}
    ]
    scans = status["scans"]
    scan_rows = scans["items"][:scan_limit]
    snapshot: dict[str, Any] = {
        "ok": True,
        "project": status["project"],
        "progress": progress,
        "methods": methods,
        "preparation": {
            "state": preparation["state"],
            "reason": preparation.get("reason"),
            "steps": preparation.get("steps", {}),
            "competitors": preparation.get("competitors", []),
        },
        "scans": {
            "total": scans["total"],
            "shown": len(scan_rows),
            "errors": scans["errors"],
            "items": scan_rows,
        },
        "log": _log(root),
        # Goals and handoff prompts are inbox entries.  Listing is read-only;
        # the observer must never acknowledge an agent's work by looking at it.
        "inbox": list_entries(root, consumer=consumer or "observer/local", limit=100),
    }
    if consumer is not None:
        snapshot["inbox_unread"] = unread_summary(root, consumer=consumer)
    return snapshot
