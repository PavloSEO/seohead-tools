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


def _scan_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Read retained scan evidence only; failures remain observable data."""
    from seohead.storage import open_scan, read_audit
    from seohead.storage.status import scan_status

    path = row["path"]
    try:
        status = scan_status(path)
        audit = read_audit(path)
        by_severity: dict[str, int] = {}
        finding_items = []
        for issue in audit.get("issues", []):
            severity = issue.get("severity") if isinstance(issue, dict) else None
            if isinstance(severity, str):
                by_severity[severity] = by_severity.get(severity, 0) + 1
            if isinstance(issue, dict) and len(finding_items) < 20:
                finding_items.append(
                    {
                        key: issue.get(key)
                        for key in (
                            "id",
                            "check",
                            "severity",
                            "target_url",
                            "message",
                            "fingerprint",
                        )
                    }
                )
        with open_scan(path, require_audit=False) as con:
            sitemap_rows = con.execute(
                "SELECT completeness,COUNT(*) FROM context_items "
                "WHERE kind='sitemap_fetch_summary' GROUP BY completeness"
            ).fetchall()
        return {
            "state": "available",
            "frontier": status["frontier"],
            "committed_page_outcomes": status["committed_page_outcomes"],
            "findings": {
                "total": sum(by_severity.values()),
                "by_severity": by_severity,
                "items": finding_items,
                "truncated": len(audit.get("issues", [])) > len(finding_items),
            },
            "sitemaps": {"fetch_summaries": {key: value for key, value in sitemap_rows}},
            "skipped_checks": audit.get("run", {}).get("checks_skipped", []),
        }
    except (OSError, ValueError, KeyError) as exc:
        return {"state": "unavailable", "reason": str(exc)}


def observe(directory: str, *, consumer: str | None = None, scan_limit: int = 20) -> dict[str, Any]:
    """Return a bounded observer snapshot without changing project evidence."""
    if type(scan_limit) is not int or not 1 <= scan_limit <= 100:
        raise ValueError("scan_limit must be from 1 to 100")
    root, _ = _load(directory)
    status = project_status(root)
    progress = project_progress(root, limit=100)
    from .execution import status as execution_status

    execution = execution_status(root)
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
    scan_rows = [{**row, "evidence": _scan_evidence(row)} for row in scans["items"][:scan_limit]]
    snapshot: dict[str, Any] = {
        "ok": True,
        "project": status["project"],
        "progress": progress,
        "execution": execution,
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
