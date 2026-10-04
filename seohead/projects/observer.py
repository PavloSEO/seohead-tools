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
FINDING_PAGE_SIZE = 50
MAX_FINDING_PAGE_SIZE = 100
_FINDING_SORTS = ("severity", "check", "target_url", "id")
_SEVERITY_RANK = {"critical": 0, "error": 1, "warning": 2, "notice": 3, "info": 4}


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


def _scan_row(directory: str | Path, scan_uuid: str | None) -> dict[str, Any]:
    """Resolve one retained scan through the project history, never a caller path."""
    status = project_status(directory)
    rows = status["scans"]["items"]
    if not rows:
        raise ValueError("project has no retained scans")
    if scan_uuid is None:
        return rows[0]
    row = next((item for item in rows if item["uuid"] == scan_uuid), None)
    if row is None:
        raise ValueError("retained scan is not part of this project")
    return row


def _finding_summary(issue: dict[str, Any], ordinal: int) -> dict[str, Any]:
    """Keep a browse page small while retaining a stable ordinal for its detail page."""
    return {
        "ordinal": ordinal,
        "id": issue.get("id"),
        "check": issue.get("check"),
        "severity": issue.get("severity"),
        "target_url": issue.get("target_url") or issue.get("url"),
        "message": issue.get("message") or issue.get("text"),
        "fingerprint": issue.get("fingerprint"),
    }


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Expose retained finding evidence without turning a terminal page into a dump."""
    if depth >= 3:
        return "[nested evidence omitted]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:4096] + ("…" if len(value) > 4096 else "")
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    return str(value)[:1024]


def findings_page(
    directory: str | Path,
    *,
    scan_uuid: str | None = None,
    offset: int = 0,
    limit: int = FINDING_PAGE_SIZE,
    query: str = "",
    sort: str = "severity",
    descending: bool = False,
) -> dict[str, Any]:
    """Browse a retained scan's findings with bounded filter, sort and pagination.

    The scan is selected from the project's retained history, so this helper is
    deliberately unable to read an arbitrary SQLite path.  It is read-only and
    keeps an original ordinal that :func:`finding_detail` can use after sorting.
    """
    if type(offset) is not int or offset < 0:
        raise ValueError("finding offset must be non-negative")
    if type(limit) is not int or not 1 <= limit <= MAX_FINDING_PAGE_SIZE:
        raise ValueError("finding limit must be from 1 to 100")
    if type(query) is not str or len(query) > 256:
        raise ValueError("finding query must be text of at most 256 characters")
    if sort not in _FINDING_SORTS:
        raise ValueError("finding sort must be severity, check, target_url or id")
    row = _scan_row(directory, scan_uuid)
    from seohead.storage import read_audit

    audit = read_audit(row["path"])
    issues = audit.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues):
        raise ValueError("retained scan audit has no readable finding list")
    needle = query.casefold().strip()
    indexed = list(enumerate(issues))
    if needle:
        indexed = [
            (ordinal, issue)
            for ordinal, issue in indexed
            if needle
            in " ".join(
                str(issue.get(key, ""))
                for key in ("id", "check", "severity", "target_url", "url", "message", "text")
            ).casefold()
        ]

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[bool, Any, int]:
        ordinal, issue = item
        value = issue.get(sort)
        if sort == "target_url":
            value = value or issue.get("url")
        if sort == "severity":
            rank = _SEVERITY_RANK.get(str(value).casefold())
            return rank is None, rank if rank is not None else 99, ordinal
        return value in (None, ""), str(value or "").casefold(), ordinal

    indexed.sort(key=sort_key, reverse=descending)
    selected = indexed[offset : offset + limit]
    return {
        "ok": True,
        "scan": {
            key: row.get(key)
            for key in ("uuid", "source_kind", "lifecycle", "finish_reason", "path")
        },
        "query": query,
        "sort": {"field": sort, "direction": "desc" if descending else "asc"},
        "counts": {"source": len(issues), "matched": len(indexed), "returned": len(selected)},
        "pagination": {
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(selected)
            if offset + len(selected) < len(indexed)
            else None,
            "previous_offset": max(0, offset - limit) if offset else None,
            "truncated": offset + len(selected) < len(indexed),
        },
        "items": [_finding_summary(issue, ordinal) for ordinal, issue in selected],
    }


def finding_detail(
    directory: str | Path, *, scan_uuid: str | None = None, ordinal: int
) -> dict[str, Any]:
    """Return one retained finding and its bounded source evidence projection."""
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError("finding ordinal must be non-negative")
    row = _scan_row(directory, scan_uuid)
    from seohead.storage import read_audit

    audit = read_audit(row["path"])
    issues = audit.get("issues")
    if (
        not isinstance(issues, list)
        or ordinal >= len(issues)
        or not isinstance(issues[ordinal], dict)
    ):
        raise ValueError("retained finding ordinal is unavailable")
    issue = issues[ordinal]
    return {
        "ok": True,
        "scan_uuid": row["uuid"],
        "finding": _finding_summary(issue, ordinal),
        "evidence": _bounded_value(issue),
        "audit": {
            "schema_version": audit.get("schema_version"),
            "generated_at": (audit.get("run") or {}).get("generated_at"),
        },
    }


def saved_view_page(
    directory: str | Path, *, name: str, scan_uuid: str | None = None, offset: int = 0
) -> dict[str, Any]:
    """Apply a saved local finding view to an in-project retained scan only."""
    row = _scan_row(directory, scan_uuid)
    from seohead.projects.finding_views import apply_view_to_audit
    from seohead.storage import read_audit

    result = apply_view_to_audit(directory, name, read_audit(row["path"]), offset=offset)
    return {"ok": True, "scan_uuid": row["uuid"], "result": result}


def observe(directory: str, *, consumer: str | None = None, scan_limit: int = 20) -> dict[str, Any]:
    """Return a bounded observer snapshot without changing project evidence."""
    if type(scan_limit) is not int or not 1 <= scan_limit <= 100:
        raise ValueError("scan_limit must be from 1 to 100")
    root, _ = _load(directory)
    status = project_status(root)
    progress = project_progress(root, limit=100)
    from .execution import status as execution_status
    from .monitoring import status as monitor_status

    execution = execution_status(root)
    monitor = monitor_status(root)
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
        "monitor": monitor,
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
    from .coverage import coverage_status
    from .finding_views import list_views

    coverage = coverage_status(root)
    snapshot["review"] = {
        "state": coverage.get("state"),
        "manual_waiting": (coverage.get("views") or {}).get("waiting_for_manual_review", []),
        "deliverable_ready": (coverage.get("views") or {}).get("deliverable_ready", []),
        "plan": coverage.get("plan"),
    }
    snapshot["saved_views"] = list_views(root)
    if consumer is not None:
        snapshot["inbox_unread"] = unread_summary(root, consumer=consumer)
    return snapshot
