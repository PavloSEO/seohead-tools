"""Read-only projection for terminal and future web project observers.

This module deliberately composes the existing project, coverage, preparation,
scan-history and inbox stores.  It owns no crawl, task, finding, or job state.
"""

from __future__ import annotations

import copy
import json
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any

from .inbox import list_entries, unread_summary
from .progress import project_progress
from .workspace import _load, project_status

LOG_BYTES = 32 * 1024
FINDING_PAGE_SIZE = 50
MAX_FINDING_PAGE_SIZE = 100
_FINDING_SORTS = ("severity", "check", "target_url", "id")
_SEVERITY_RANK = {"critical": 0, "error": 1, "warning": 2, "notice": 3, "info": 4}
_EVIDENCE_CACHE_LIMIT = 32
_EVIDENCE_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_EVIDENCE_CACHE_LOCK = RLock()
_FINDINGS_CACHE_LIMIT = 8
_FINDINGS_CACHE_MAX_BYTES = 8 * 1024 * 1024
_FINDINGS_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_FINDINGS_CACHE_LOCK = RLock()


def _log(root: Path) -> dict[str, Any]:
    path = root / "log.md"
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - LOG_BYTES))
        text = stream.read(LOG_BYTES).decode("utf-8", errors="replace")
    return {"text": text, "truncated": size > LOG_BYTES, "bytes": size}


def _stat_state(path: Path) -> tuple[int, int, int, int] | None:
    """Return enough identity to reject cache entries after an artifact replacement."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


def _evidence_cache_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Key one evidence projection by artifact bytes and retained scan configuration.

    A running SQLite scan can commit through its WAL while the main file's
    timestamp is unchanged.  Include the WAL and SHM state in every key, even
    when either sidecar is currently absent, so a new checkpoint immediately
    invalidates the prior projection on the next observer refresh.
    """
    from seohead.storage.audit_v2 import audit_v2_path

    source = Path(row["path"])
    return (
        str(source.resolve()),
        _stat_state(source),
        _stat_state(source.with_name(source.name + "-wal")),
        _stat_state(source.with_name(source.name + "-shm")),
        _stat_state(audit_v2_path(source)),
        row.get("uuid"),
        row.get("lifecycle"),
        row.get("finish_reason"),
        row.get("crawl_partial"),
        row.get("corpus_partial"),
        row.get("evidence_revision"),
        row.get("config_fingerprint"),
        row.get("format_version"),
        row.get("source_kind"),
    )


def _cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    with _EVIDENCE_CACHE_LOCK:
        value = _EVIDENCE_CACHE.get(key)
        if value is None:
            return None
        _EVIDENCE_CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _cache_put(key: tuple[Any, ...], value: dict[str, Any]) -> dict[str, Any]:
    with _EVIDENCE_CACHE_LOCK:
        _EVIDENCE_CACHE[key] = copy.deepcopy(value)
        _EVIDENCE_CACHE.move_to_end(key)
        while len(_EVIDENCE_CACHE) > _EVIDENCE_CACHE_LIMIT:
            _EVIDENCE_CACHE.popitem(last=False)
    return copy.deepcopy(value)


def _read_retained_findings(row: dict[str, Any]) -> dict[str, Any]:
    """Materialise only the audit fields observer summary/detail views need."""
    from seohead.storage import read_audit

    audit = read_audit(row["path"])
    issues = audit.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues):
        raise ValueError("retained scan audit has no readable finding list")
    run = audit.get("run")
    return {
        "issues": tuple(copy.deepcopy(issues)),
        "skipped_checks": copy.deepcopy(run.get("checks_skipped", []))
        if isinstance(run, dict)
        else [],
        "schema_version": audit.get("schema_version"),
        "generated_at": run.get("generated_at") if isinstance(run, dict) else None,
    }


def _findings_cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    with _FINDINGS_CACHE_LOCK:
        value = _FINDINGS_CACHE.get(key)
        if value is None:
            return None
        _FINDINGS_CACHE.move_to_end(key)
        return value


def _findings_cache_put(key: tuple[Any, ...], value: dict[str, Any]) -> None:
    with _FINDINGS_CACHE_LOCK:
        _FINDINGS_CACHE[key] = value
        _FINDINGS_CACHE.move_to_end(key)
        while len(_FINDINGS_CACHE) > _FINDINGS_CACHE_LIMIT:
            _FINDINGS_CACHE.popitem(last=False)


def _retained_findings(row: dict[str, Any]) -> dict[str, Any]:
    """Reuse bounded finding rows while source, audit and retained state match."""
    key = _evidence_cache_key(row)
    cached = _findings_cache_get(key)
    if cached is not None:
        return cached
    findings = _read_retained_findings(row)
    if _evidence_cache_key(row) != key:
        return findings
    try:
        size = len(json.dumps(findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return findings
    if size <= _FINDINGS_CACHE_MAX_BYTES:
        _findings_cache_put(key, findings)
    return findings


def _read_scan_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Read collector evidence even while an audit is not yet available."""
    from seohead.storage import open_scan
    from seohead.storage.status import scan_status

    path = row["path"]
    try:
        status = scan_status(path)
        with open_scan(path, require_audit=False) as con:
            sitemap_rows = con.execute(
                "SELECT completeness,COUNT(*) FROM context_items "
                "WHERE kind='sitemap_fetch_summary' GROUP BY completeness"
            ).fetchall()
    except (OSError, ValueError, KeyError) as exc:
        return {"state": "unavailable", "reason": str(exc)}

    evidence = {
        "state": "available",
        "frontier": status["frontier"],
        "committed_page_outcomes": status["committed_page_outcomes"],
        "sitemaps": {"fetch_summaries": {key: value for key, value in sitemap_rows}},
    }
    try:
        retained = _retained_findings(row)
    except (OSError, ValueError, KeyError) as exc:
        evidence.update(
            findings={
                "state": "unavailable",
                "reason": str(exc),
                "total": None,
                "by_severity": {},
                "items": [],
                "truncated": False,
            },
            skipped_checks=None,
        )
        return evidence
    by_severity: dict[str, int] = {}
    finding_items = []
    for issue in retained["issues"]:
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
    evidence.update(
        findings={
            "state": "available",
            "reason": None,
            "total": sum(by_severity.values()),
            "by_severity": by_severity,
            "items": finding_items,
            "truncated": len(retained["issues"]) > len(finding_items),
        },
        skipped_checks=retained["skipped_checks"],
    )
    return evidence


def _scan_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Read one retained projection once per unchanged scan/audit state.

    The terminal observer redraws each second.  Reopening and materialising a
    completed audit for every redraw made a passive second-screen view slower
    than the crawler it was meant to observe.  Cache only successful projections
    and verify source, WAL/SHM, audit companion and retained config state before
    reusing one.  Failures remain uncached so recovery is visible immediately.
    """
    key = _evidence_cache_key(row)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    evidence = _read_scan_evidence(row)
    if evidence.get("state") != "available":
        return evidence
    # Do not retain an observation read while its source was being changed.
    # The following refresh will read the stable state instead.
    if _evidence_cache_key(row) != key:
        return evidence
    return _cache_put(key, evidence)


def _relative_artifact(root: Path, path: str) -> str | None:
    """Return a project-relative retained-artifact reference, never a host path."""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _site_scans(root: Path, scans: dict[str, Any], *, limit: int) -> dict[str, Any]:
    """Bound one site's retained scans and keep their evidence tied to an artifact."""
    items = []
    for row in scans["items"][:limit]:
        artifact = _relative_artifact(root, row["path"])
        item = {key: value for key, value in row.items() if key != "path"}
        if artifact is None:
            item["artifact"] = {
                "state": "unavailable",
                "reason": "retained scan path is outside the project workspace",
            }
            item["evidence"] = item["artifact"]
        else:
            item["artifact"] = {"state": "available", "path": artifact}
            item["evidence"] = _scan_evidence(row)
        items.append(item)
    return {
        "total": scans["total"],
        "shown": len(items),
        "has_more": scans.get("has_more", False) or scans["total"] > len(items),
        "errors": scans["errors"],
        "items": items,
    }


def _method_coverage(checklist: dict[str, Any]) -> dict[str, Any]:
    """Summarise expected and recorded scenario/skill work without a false percentage."""
    state = checklist.get("state")
    if state != "initialized":
        return {
            "state": state or "unavailable",
            "reason": checklist.get("reason") or "method coverage is unavailable",
            "kinds": {},
        }
    kinds: dict[str, dict[str, Any]] = {}
    for kind in ("scenario", "skill"):
        rows = [item for item in checklist.get("items", []) if item.get("kind") == kind]
        states: dict[str, int] = {}
        for row in rows:
            value = row.get("state")
            if isinstance(value, str):
                states[value] = states.get(value, 0) + 1
        kinds[kind] = {
            "expected": len(rows),
            "completed": sum(1 for row in rows if row.get("complete") is True),
            "states": states,
        }
    return {"state": "available", "reason": None, "kinds": kinds}


def _coverage_summary(checklist: dict[str, Any]) -> dict[str, Any]:
    """Expose a bounded coverage projection, preserving unknown and partial states."""
    axes = {}
    for name, axis in (checklist.get("coverage") or {}).items():
        if not isinstance(axis, dict):
            continue
        axes[name] = {
            key: axis.get(key)
            for key in (
                "state",
                "reason",
                "numerator",
                "denominator",
                "measured_urls",
                "unfinished",
                "unverified_measurements",
                "population_kind",
                "population_name",
            )
            if key in axis
        }
    return {
        "state": checklist.get("state") or "unavailable",
        "reason": checklist.get("reason"),
        "complete": checklist.get("complete"),
        "counts": checklist.get("counts"),
        "axes": axes,
    }


def _site_projection(
    root: Path,
    project: dict[str, Any],
    checklist: dict[str, Any],
    scans: dict[str, Any],
    *,
    role: str,
    source: str | None = None,
    observed_at: str | None = None,
    candidate_state: str | None = None,
    directory: str = ".",
    scan_limit: int,
) -> dict[str, Any]:
    """Build one read-only own-site or competitor observation row."""
    return {
        "role": role,
        "project_uuid": project["project_uuid"],
        "directory": directory,
        "site": project["site"],
        "candidate": {
            "state": candidate_state,
            "source": source,
            "observed_at": observed_at,
        }
        if role == "competitor"
        else None,
        "coverage": _coverage_summary(checklist),
        "methods": _method_coverage(checklist),
        "scans": _site_scans(root, scans, limit=scan_limit),
    }


def _competitor_sites(root: Path, preparation: dict[str, Any], *, scan_limit: int) -> list[dict]:
    """Read declared competitor workspaces only; absent or damaged work stays explicit."""
    from seohead.storage.history import list_scans

    from .coverage import coverage_status

    sites = []
    for candidate in preparation.get("competitors", []):
        directory = candidate.get("directory")
        try:
            if not isinstance(directory, str):
                raise ValueError("competitor workspace reference is unavailable")
            child = root / directory
            child_root, child_project = _load(child)
            if child_project["project_uuid"] != candidate.get("project_uuid"):
                raise ValueError("competitor project identity mismatch")
            if child_project["site"]["target"] != candidate.get("url"):
                raise ValueError("competitor site identity mismatch")
            sites.append(
                _site_projection(
                    child_root,
                    child_project,
                    coverage_status(child_root),
                    list_scans(child_root / "scans"),
                    role="competitor",
                    source=candidate.get("source"),
                    observed_at=candidate.get("observed_at"),
                    candidate_state=candidate.get("state"),
                    directory=directory,
                    scan_limit=scan_limit,
                )
            )
        except (OSError, ValueError) as exc:
            sites.append(
                {
                    "role": "competitor",
                    "project_uuid": candidate.get("project_uuid"),
                    "directory": directory,
                    "site": {"target": candidate.get("url"), "host": None, "label": None},
                    "candidate": {
                        "state": candidate.get("state"),
                        "source": candidate.get("source"),
                        "observed_at": candidate.get("observed_at"),
                    },
                    "coverage": {
                        "state": "unavailable",
                        "reason": str(exc),
                        "complete": None,
                        "counts": None,
                        "axes": {},
                    },
                    "methods": {
                        "state": "unavailable",
                        "reason": str(exc),
                        "kinds": {},
                    },
                    "scans": {
                        "total": None,
                        "shown": 0,
                        "has_more": False,
                        "errors": [],
                        "items": [],
                    },
                }
            )
    return sites


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
    issues = _retained_findings(row)["issues"]
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
    retained = _retained_findings(row)
    issues = retained["issues"]
    if ordinal >= len(issues):
        raise ValueError("retained finding ordinal is unavailable")
    issue = issues[ordinal]
    return {
        "ok": True,
        "scan_uuid": row["uuid"],
        "finding": _finding_summary(issue, ordinal),
        "evidence": _bounded_value(issue),
        "audit": {
            "schema_version": retained["schema_version"],
            "generated_at": retained["generated_at"],
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
    root, project = _load(directory)
    status = project_status(root)
    progress = project_progress(root, limit=100)
    from .coverage import coverage_status
    from .execution import status as execution_status
    from .monitoring import status as monitor_status
    from .run_observation import status as run_status
    from .runtime import project_policy

    execution = execution_status(root)
    monitor = monitor_status(root)
    preparation = status["preparation"]
    sites = [
        _site_projection(
            root,
            project,
            coverage_status(root),
            status["scans"],
            role="primary",
            scan_limit=scan_limit,
        )
    ]
    sites.extend(_competitor_sites(root, preparation, scan_limit=scan_limit))
    site_identities = {
        item["project_uuid"]: {"role": item["role"], "target": item["site"]["target"]}
        for item in sites
        if isinstance(item.get("project_uuid"), str)
    }
    checks = status["checklist"].get("items", [])
    methods = [
        {
            "id": item["id"],
            "item_id": item.get("item_id", item["id"]),
            "title": item["title"],
            "kind": item["kind"],
            "state": item["state"],
            "attempt_status": item["attempt_status"],
            "stale": item["stale"],
            "reason": item["reason"],
            "site": site_identities.get(
                item.get("project_uuid", project["project_uuid"]),
                {
                    "role": "unknown",
                    "target": item.get("scope", {}).get("site"),
                },
            ),
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
        "runs": run_status(root),
        "policy": project_policy(str(root)),
        "sites": {
            "total": len(sites),
            "scan_limit_per_site": scan_limit,
            "items": sites,
        },
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
