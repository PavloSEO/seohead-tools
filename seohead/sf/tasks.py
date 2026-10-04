"""Turn an audit into a prioritized, actionable task backlog.

A thin, configurable pipeline on top of ``audit.json``: it groups issues into
work items, maps severity to priority/effort, caps the URL lists and emits both
a machine-readable ``tasks.json`` and a readable ``tasks.md``. Drives the
``sf-tasks`` skill and the ``sf-analyzer tasks`` CLI / ``sf_audit_tasks`` MCP tool.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from typing import Any
from urllib.parse import urlsplit

from ..reports.client_findings import reproduction
from ..reports.facts import crawl_domain
from .config import DEFAULT_CONFIG
from .core.registry import check_meta

_PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
LINK_CHECKS = {"BROKEN_INTERNAL_LINK", "LINK_TO_5XX", "INTERNAL_LINK_TO_REDIRECT"}
_ASSIGNMENT_KINDS = {"template", "component"}
_ASSIGNMENT_STATES = {"declared", "confirmed"}


def _pipeline_cfg(config: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(DEFAULT_CONFIG["tasks_pipeline"])
    if config and isinstance(config.get("tasks_pipeline"), dict):
        base.update(config["tasks_pipeline"])
    return base


def _task_id(check: str, key: str) -> str:
    digest = hashlib.sha1(f"{check}|{key}".encode(), usedforsecurity=False).hexdigest()[:8]
    return "TASK-" + digest


def _reproductions(issues: list[dict[str, Any]], cap: int) -> list[str]:
    """Keep recorded URLs/statuses for the human backlog without tool labels."""
    seen: set[str] = set()
    rows: list[str] = []
    for issue in issues:
        row = reproduction(
            {
                "url": issue.get("target_url"),
                "status_code": issue.get("status_code"),
                "locations": issue.get("locations"),
                "details": issue.get("details"),
                "text": issue.get("message"),
            }
        )
        if row not in seen:
            seen.add(row)
            rows.append(row)
        if len(rows) >= cap:
            break
    return rows


def _evidence_references(
    issues: list[dict[str, Any]], cap: int
) -> tuple[list[dict[str, str]], int]:
    """Carry only closed saved-observation references into the task contract.

    Older SF/export audits often retain no scan UUID.  Their absence remains a
    first-class unavailable reference instead of a synthetic issue locator or
    an export path that a recipient cannot resolve.
    """
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for issue in issues:
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        contract = evidence.get("contract") if isinstance(evidence.get("contract"), dict) else {}
        if contract.get("state") in {"measured", "imported_projection"}:
            record = {
                "state": str(contract["state"]),
                "id": str(contract.get("id") or ""),
                "source_table": str(contract.get("source_table") or ""),
                "observation_id": str(contract.get("observation_id") or ""),
            }
            if not all(record.values()):
                record = {
                    "state": "unavailable",
                    "reason": "saved evidence reference is incomplete",
                }
        else:
            record = {
                "state": "unavailable",
                "reason": str(
                    contract.get("reason") or "no stable saved-evidence reference is present"
                ),
            }
        key = tuple(
            record.get(name, "")
            for name in ("state", "id", "source_table", "observation_id", "reason")
        )
        if key not in seen:
            seen.add(key)
            rows.append(record)
    return rows[:cap], max(0, len(rows) - cap)


def _unassigned_assignment(reason: str) -> dict[str, Any]:
    return {
        "id": "unassigned",
        "name": "Unassigned",
        "kind": "unknown",
        "state": "unassigned",
        "rationale": reason,
        "provenance": {"kind": "unassigned", "reason": reason},
    }


def _declared_assignments(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read the closed URL assignment list without inventing a rule language."""
    raw = cfg.get("assignments", [])
    if not isinstance(raw, list):
        raise ValueError("tasks_pipeline.assignments must be a list")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("each task assignment must be an object")
        name = item.get("name")
        kind = item.get("kind")
        rationale = item.get("rationale")
        urls = item.get("urls")
        state = item.get("state", "declared")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("task assignment name must be a non-empty string")
        if kind not in _ASSIGNMENT_KINDS:
            raise ValueError("task assignment kind must be template or component")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("task assignment rationale must be a non-empty string")
        if state not in _ASSIGNMENT_STATES:
            raise ValueError("task assignment state must be declared or confirmed")
        if state == "confirmed" and not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("confirmed_by", "confirmation")
        ):
            raise ValueError("a confirmed task assignment needs confirmed_by and confirmation")
        if (
            not isinstance(urls, list)
            or not urls
            or any(not isinstance(url, str) or not url for url in urls)
        ):
            raise ValueError("task assignment urls must be a non-empty list of URLs")
        assignment = {
            "id": f"operator:{kind}:{name}",
            "name": name,
            "kind": kind,
            "state": state,
            "rationale": rationale,
            "provenance": {
                "kind": "operator_assignment",
                "source": "tasks_pipeline.assignments",
                "index": index,
            },
        }
        if state == "confirmed":
            assignment["confirmed_by"] = item["confirmed_by"]
            assignment["confirmation"] = item["confirmation"]
        for url in urls:
            if url in result:
                raise ValueError(f"task assignment URL appears more than once: {url}")
            result[url] = assignment
    return result


def _audit_segment_definitions(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse the crawl's validated segment format; never evaluate caller code."""
    config = (audit.get("run") or {}).get("crawl_config") or {}
    if not isinstance(config, dict):
        return []
    analysis = config.get("analysis.segments") or []
    if isinstance(analysis, list) and analysis:
        return analysis
    scope = config.get("scope.segments") or []
    if not isinstance(scope, list):
        return []
    definitions = []
    for item in scope:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return []
        rules = []
        if item.get("prefix"):
            rules.append({"op": "prefix", "field": "path", "value": item["prefix"]})
        if item.get("host"):
            rules.append({"op": "eq", "field": "host", "value": item["host"]})
        if item.get("pattern"):
            rules.append({"op": "regex", "field": "url", "value": item["pattern"]})
        if not rules:
            return []
        definitions.append({"name": item["name"], "rules": rules})
    return definitions


def _segment_assignments(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    definitions = _audit_segment_definitions(audit)
    if not definitions:
        return {}
    from .core.segments import SCHEMA_VERSION, UNSEGMENTED, assign_segments

    pages = []
    for page in audit.get("pages") or []:
        if not isinstance(page, dict) or not isinstance(page.get("url"), str) or not page["url"]:
            continue
        parts = urlsplit(page["url"])
        pages.append({**page, "path": parts.path, "host": (parts.hostname or "").lower()})
    assignment = assign_segments(pages, definitions)["primary"]
    partial = bool((audit.get("run") or {}).get("crawl_partial"))
    result: dict[str, dict[str, Any]] = {}
    targets = {
        issue.get("target_url")
        for issue in audit.get("issues") or []
        if isinstance(issue, dict)
        and isinstance(issue.get("target_url"), str)
        and issue["target_url"]
    }
    for target in targets:
        name = assignment.get(target)
        # A target absent from a complete retained page set is unassigned. For a
        # partial or page-less audit, reuse the same validated rules only to make
        # a candidate grouping, never a claimed template match.
        if name is None and (not pages or partial):
            parts = urlsplit(target)
            name = assign_segments(
                [{"url": target, "path": parts.path, "host": (parts.hostname or "").lower()}],
                definitions,
            )["primary"].get(target)
        if name not in (None, UNSEGMENTED):
            result[target] = {
                "id": f"segment:{name}",
                "name": name,
                "kind": "segment",
                "state": "candidate",
                "rationale": "Matched the audit's validated segment rule; shared implementation remains unconfirmed.",
                "provenance": {
                    "kind": "audit_segment_rules",
                    "schema_version": SCHEMA_VERSION,
                    "rules": next(item["rules"] for item in definitions if item["name"] == name),
                },
            }
    return result


def build_tasks(audit: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a task backlog from an ``audit.json`` dict (``AuditResult.to_json()``)."""
    cfg = _pipeline_cfg(config)
    sev_filter = set(cfg["include_severities"])
    include = set(cfg["include_checks"])
    exclude = set(cfg["exclude_checks"])
    prio = cfg["priority_map"]
    effort = cfg["effort_map"]
    cap = cfg["max_urls_per_task"]
    # A separate location cap (#309): max_urls_per_task caps the number of
    # distinct target URLs a task lists, and must not also silently cap how
    # many source locations a broken-link task carries. Absent an explicit
    # override, locations are capped at the pipeline's own default target
    # cap rather than whatever value the caller passed for max_urls_per_task,
    # so lowering the target cap alone cannot reduce location coverage.
    loc_cap = cfg.get(
        "max_locations_per_task", DEFAULT_CONFIG["tasks_pipeline"]["max_urls_per_task"]
    )
    min_occ = cfg["min_occurrences"]
    group_by = cfg["group_by"]

    issues = [
        i
        for i in audit.get("issues", [])
        if i["severity"] in sev_filter
        and (not include or i["check"] in include)
        and i["check"] not in exclude
    ]

    grouping: dict[str, Any] | None = None
    if group_by == "check":
        tasks = _group_by_check(issues, prio, effort, cap, loc_cap, min_occ)
    elif group_by == "issue":
        tasks = _per_issue(issues, prio, effort, cap, loc_cap, min_occ)
    elif group_by == "check_assignment":
        declared = _declared_assignments(cfg)
        assignments = declared or _segment_assignments(audit)
        source = "operator_assignments" if declared else "audit_segments" if assignments else "none"
        tasks = _group_by_check_assignment(issues, assignments, prio, effort, cap, loc_cap, min_occ)
        grouping = {
            "mode": source,
            "assignment_count": len({row["id"] for row in assignments.values()}),
            "unassigned_findings": sum(
                1 for issue in issues if issue.get("target_url") not in assignments
            ),
        }
    else:
        raise ValueError("tasks_pipeline.group_by must be check, issue, or check_assignment")
    tasks.sort(key=lambda t: (_PRIORITY_ORDER.get(t["priority"], 9), -t["affected_count"]))

    by_priority: dict[str, int] = {}
    for t in tasks:
        by_priority[t["priority"]] = by_priority.get(t["priority"], 0) + 1

    run = audit.get("run", {})
    summary = audit.get("summary", {})
    source = {
        "project": run.get("project"),
        # Resolved once here, from the same start_url -> source -> project
        # order facts.py uses for its own site-domain fact (#640), so the
        # heading names the actual site instead of the literal string
        # "None" whenever a native crawl or reanalysis left "project" unset.
        "site_name": crawl_domain(run) or None,
        "generated_at": run.get("generated_at"),
        "health_score": summary.get("health_score"),
        "crawl_valid": run.get("crawl_valid", True),
        "crawl_invalid_reason": run.get("crawl_invalid_reason"),
        # Carried through, not recomputed (#308): a partial or
        # coverage-limited audit must keep saying so all the way to the
        # backlog a developer actually reads, instead of a normal-looking
        # scored task list quietly losing what it was scored against.
        "crawl_partial": run.get("crawl_partial", False),
        "crawl_finish_reason": run.get("crawl_finish_reason"),
        "health_score_scope": summary.get("health_score_scope"),
        "health_score_basis": summary.get("health_score_basis"),
        "check_coverage": summary.get("check_coverage"),
    }
    if summary.get("finding_exclusions") is not None:
        source["finding_exclusions"] = summary["finding_exclusions"]
    output = {
        "schema_version": "1.0",
        "source": source,
        "pipeline": cfg,
        "summary": {"tasks_total": len(tasks), "by_priority": by_priority},
        "tasks": tasks,
    }
    if grouping is not None:
        output["grouping"] = grouping
    return output


def build_tasks_from_audit_v2(
    scan_path: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the default backlog from an audit.v2 companion without materializing it.

    ``check`` grouping is the configured default and has a bounded output: one
    task per check, capped URL/evidence samples.  Other grouping modes need a
    different declared grouping contract, so refusing them here is preferable
    to reading an oversized compatibility document behind the caller's back.
    """
    from seohead.storage.audit_v2 import AuditV2Reader

    cfg = _pipeline_cfg(config)
    if cfg["group_by"] not in {"check", "check_assignment"}:
        raise ValueError(
            "streamed audit.v2 tasks require tasks_pipeline.group_by=check or check_assignment"
        )
    declared = _declared_assignments(cfg) if cfg["group_by"] == "check_assignment" else {}
    if cfg["group_by"] == "check_assignment" and not declared:
        raise ValueError("streamed audit.v2 check_assignment tasks require declared assignments")
    with AuditV2Reader(scan_path) as reader:
        if "/issues" not in reader.collections:
            raise ValueError("audit.v2 has no findings collection")
        return _stream_grouped_tasks(
            reader.header.get("run") or {},
            reader.header.get("summary") or {},
            reader.iter_collection("/issues"),
            cfg,
            declared,
        )


def _stream_grouped_tasks(
    run: dict[str, Any],
    summary: dict[str, Any],
    rows,
    cfg: dict[str, Any],
    declared: dict[str, dict[str, Any]],
):
    """Aggregate default check tasks in SQLite, retaining only capped samples in RAM."""
    descriptor, name = tempfile.mkstemp(prefix="seohead-task-rows-", suffix=".sqlite")
    os.close(descriptor)
    con = sqlite3.connect(name)
    try:
        con.execute(
            "CREATE TABLE urls (check_id TEXT, url TEXT, ordinal INTEGER, "
            "PRIMARY KEY(check_id,url)) WITHOUT ROWID"
        )
        con.execute(
            "CREATE TABLE evidence (check_id TEXT, key TEXT, ordinal INTEGER, value_json TEXT, "
            "PRIMARY KEY(check_id,key)) WITHOUT ROWID"
        )
        severities, include, exclude = (
            set(cfg["include_severities"]),
            set(cfg["include_checks"]),
            set(cfg["exclude_checks"]),
        )
        cap = cfg["max_urls_per_task"]
        loc_cap = cfg.get(
            "max_locations_per_task", DEFAULT_CONFIG["tasks_pipeline"]["max_urls_per_task"]
        )
        groups: dict[str, dict[str, Any]] = {}
        unassigned_findings = 0
        for ordinal, issue in enumerate(rows):
            if not isinstance(issue, dict) or issue.get("severity") not in severities:
                continue
            check = issue.get("check")
            if not isinstance(check, str) or (include and check not in include) or check in exclude:
                continue
            url = issue.get("target_url")
            assignment = None
            group_key = check
            if cfg["group_by"] == "check_assignment":
                assignment = declared.get(url) or _unassigned_assignment(
                    "No declared template/component assignment matched this finding."
                )
                group_key = f"{check}\x00{assignment['id']}"
                unassigned_findings += int(assignment["id"] == "unassigned")
            group = groups.setdefault(
                group_key,
                {
                    "check": check,
                    "assignment": assignment,
                    "first": issue,
                    "issues": 0,
                    "occurrences": 0,
                    "reproductions": [],
                    "reproduction_seen": set(),
                    "links": [],
                    "links_total": 0,
                },
            )
            group["issues"] += 1
            group["occurrences"] += issue.get("occurrences_count", 1)
            if isinstance(url, str) and url:
                con.execute("INSERT OR IGNORE INTO urls VALUES (?,?,?)", (group_key, url, ordinal))
            for value in _reproductions([issue], 1):
                if value not in group["reproduction_seen"] and len(group["reproductions"]) < cap:
                    group["reproduction_seen"].add(value)
                    group["reproductions"].append(value)
            evidence, _unused = _evidence_references([issue], 1)
            for value in evidence:
                key = json.dumps(value, sort_keys=True, separators=(",", ":"))
                con.execute(
                    "INSERT OR IGNORE INTO evidence VALUES (?,?,?,?)",
                    (group_key, key, ordinal, json.dumps(value, ensure_ascii=False)),
                )
            locations = issue.get("locations") or []
            group["links_total"] += len(locations) if check in LINK_CHECKS else 0
            if check in LINK_CHECKS and len(group["links"]) < loc_cap:
                for location in locations:
                    if len(group["links"]) >= loc_cap:
                        break
                    group["links"].append(
                        {
                            "target_url": url,
                            "status_code": issue.get("status_code"),
                            "source_url": location.get("source_url"),
                            "anchor": location.get("anchor"),
                            "link_position": location.get("link_position"),
                            "link_path": location.get("link_path"),
                        }
                    )
        tasks = []
        for group_key, group in groups.items():
            if group["occurrences"] < cfg["min_occurrences"]:
                continue
            check, assignment = group["check"], group["assignment"]
            urls = [
                row[0]
                for row in con.execute(
                    "SELECT url FROM urls WHERE check_id=? ORDER BY ordinal LIMIT ?",
                    (group_key, cap),
                )
            ]
            url_total = con.execute(
                "SELECT COUNT(*) FROM urls WHERE check_id=?", (group_key,)
            ).fetchone()[0]
            evidence = [
                json.loads(row[0])
                for row in con.execute(
                    "SELECT value_json FROM evidence WHERE check_id=? ORDER BY ordinal LIMIT ?",
                    (group_key, cap),
                )
            ]
            evidence_total = con.execute(
                "SELECT COUNT(*) FROM evidence WHERE check_id=?", (group_key,)
            ).fetchone()[0]
            first, meta = group["first"], check_meta(check)
            severity = first["severity"]
            label = "page" if url_total == 1 else "pages"
            title = f"{meta['message']} — {url_total} {label}" if url_total else meta["message"]
            if assignment is not None:
                title = f"{meta['message']} — {assignment['name']} — {url_total} {label}"
            task = {
                "id": _task_id(check, assignment["id"] if assignment is not None else "all"),
                "check": check,
                "priority": cfg["priority_map"].get(severity, "P3"),
                "severity": severity,
                "effort": cfg["effort_map"].get(severity, "medium"),
                "title": title,
                "fix_hint": meta.get("fix"),
                "source": first.get("source"),
                "affected_count": url_total or group["issues"],
                "occurrences": group["occurrences"],
                "urls": urls,
                "urls_truncated": max(0, url_total - len(urls)),
                "reproductions": group["reproductions"],
                "evidence_references": evidence,
                "evidence_references_truncated": max(0, evidence_total - len(evidence)),
            }
            if assignment is not None:
                task["assignment"] = assignment
                task["membership"] = {
                    "findings_total": group["issues"],
                    "affected_urls_total": url_total,
                    "urls_returned": len(urls),
                    "urls_truncated": max(0, url_total - len(urls)),
                    "retrieval": "Filter the source audit by this task's check and assignment provenance; URL caps do not change the source findings.",
                }
                task["verification_scope"] = {
                    "state": assignment["state"],
                    "affected_urls_total": url_total,
                    "representative_urls": urls,
                    "representative_urls_truncated": max(0, url_total - len(urls)),
                    "guidance": "Inspect a representative page, then verify every affected URL after a change; assignment alone does not prove a shared cause.",
                }
            if check in LINK_CHECKS:
                task.update(
                    broken_links=group["links"],
                    broken_links_total=group["links_total"],
                    broken_links_truncated=max(0, group["links_total"] - len(group["links"])),
                )
            tasks.append(task)
        base = build_tasks({"run": run, "summary": summary, "issues": []}, {"tasks_pipeline": cfg})
        tasks.sort(
            key=lambda task: (_PRIORITY_ORDER.get(task["priority"], 9), -task["affected_count"])
        )
        base["tasks"] = tasks
        base["summary"] = {
            "tasks_total": len(tasks),
            "by_priority": {
                priority: sum(1 for task in tasks if task["priority"] == priority)
                for priority in sorted({task["priority"] for task in tasks})
            },
        }
        if cfg["group_by"] == "check_assignment":
            base["grouping"] = {
                "mode": "operator_assignments",
                "assignment_count": len({row["id"] for row in declared.values()}),
                "unassigned_findings": unassigned_findings,
            }
        return base
    finally:
        con.close()
        os.unlink(name)


def _group_by_check(issues, prio, effort, cap, loc_cap, min_occ) -> list[dict[str, Any]]:
    groups: dict[str, list[dict]] = {}
    for issue in issues:
        groups.setdefault(issue["check"], []).append(issue)

    tasks: list[dict[str, Any]] = []
    for check, group in groups.items():
        task = _group_task(check, group, prio, effort, cap, loc_cap, min_occ, key="all")
        if task is not None:
            tasks.append(task)
    return tasks


def _group_by_check_assignment(
    issues, assignments, prio, effort, cap, loc_cap, min_occ
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for issue in issues:
        assignment = assignments.get(issue.get("target_url")) or _unassigned_assignment(
            "No declared template/component assignment or validated segment matched this finding."
        )
        key = (issue["check"], assignment["id"])
        if key not in groups:
            groups[key] = (assignment, [])
        groups[key][1].append(issue)
    tasks = []
    for (check, assignment_id), (assignment, group) in groups.items():
        task = _group_task(
            check,
            group,
            prio,
            effort,
            cap,
            loc_cap,
            min_occ,
            key=assignment_id,
            assignment=assignment,
        )
        if task is not None:
            tasks.append(task)
    return tasks


def _group_task(check, group, prio, effort, cap, loc_cap, min_occ, *, key, assignment=None):
    urls = [i["target_url"] for i in group if i.get("target_url")]
    unique_urls = list(dict.fromkeys(urls))
    occurrences = sum(i.get("occurrences_count", 1) for i in group)
    if occurrences < min_occ:
        return None
    meta = check_meta(check)
    severity = group[0]["severity"]
    page_count_label = "page" if len(unique_urls) == 1 else "pages"
    title = (
        f"{meta['message']} — {len(unique_urls)} {page_count_label}"
        if unique_urls
        else meta["message"]
    )
    if assignment is not None:
        title = f"{meta['message']} — {assignment['name']} — {len(unique_urls)} {page_count_label}"
    task = {
        "id": _task_id(check, key),
        "check": check,
        "priority": prio.get(severity, "P3"),
        "severity": severity,
        "effort": effort.get(severity, "medium"),
        "title": title,
        "fix_hint": meta.get("fix"),
        "source": group[0].get("source"),
        "affected_count": len(unique_urls) or len(group),
        "occurrences": occurrences,
        "urls": unique_urls[:cap],
        "urls_truncated": max(0, len(unique_urls) - cap),
        "reproductions": _reproductions(group, cap),
    }
    evidence, evidence_truncated = _evidence_references(group, cap)
    task["evidence_references"] = evidence
    task["evidence_references_truncated"] = evidence_truncated
    if assignment is not None:
        task["assignment"] = assignment
        task["membership"] = {
            "findings_total": len(group),
            "affected_urls_total": len(unique_urls),
            "urls_returned": len(task["urls"]),
            "urls_truncated": task["urls_truncated"],
            "retrieval": "Filter the source audit by this task's check and assignment provenance; URL caps do not change the source findings.",
        }
        task["verification_scope"] = {
            "state": assignment["state"],
            "affected_urls_total": len(unique_urls),
            "representative_urls": task["urls"],
            "representative_urls_truncated": task["urls_truncated"],
            "guidance": "Inspect a representative page, then verify every affected URL after a change; assignment alone does not prove a shared cause.",
        }
    if check in LINK_CHECKS:
        links, total, truncated = _link_evidence(group, loc_cap)
        task["broken_links"] = links
        task["broken_links_total"] = total
        task["broken_links_truncated"] = truncated
    return task


def _per_issue(issues, prio, effort, cap, loc_cap, min_occ) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for issue in issues:
        # Same threshold semantics as _group_by_check (#224): min_occurrences
        # was previously never enforced on this branch at all.
        if issue.get("occurrences_count", 1) < min_occ:
            continue
        severity = issue["severity"]
        meta = check_meta(issue["check"])
        task = {
            "id": "TASK-"
            + (
                issue.get("id", "").replace("ISSUE-", "")
                or _task_id(issue["check"], str(issue.get("target_url")))
            ),
            "check": issue["check"],
            "priority": prio.get(severity, "P3"),
            "severity": severity,
            "effort": effort.get(severity, "medium"),
            "title": f"{meta['message']}: {issue.get('target_url') or ''}".strip(),
            "fix_hint": issue.get("fix_hint") or meta.get("fix"),
            "source": issue.get("source"),
            "affected_count": 1,
            "occurrences": issue.get("occurrences_count", 1),
            "urls": [issue["target_url"]] if issue.get("target_url") else [],
            "urls_truncated": 0,
            "reproductions": _reproductions([issue], cap),
        }
        evidence, evidence_truncated = _evidence_references([issue], cap)
        task["evidence_references"] = evidence
        task["evidence_references_truncated"] = evidence_truncated
        if issue["check"] in LINK_CHECKS:
            links, total, truncated = _link_evidence([issue], loc_cap)
            task["broken_links"] = links
            task["broken_links_total"] = total
            task["broken_links_truncated"] = truncated
        tasks.append(task)
    return tasks


def _link_evidence(group: list[dict], loc_cap: int) -> tuple[list[dict[str, Any]], int, int]:
    """Return (kept locations, total locations, omitted count).

    ``loc_cap`` caps how many source locations are kept per task — a cap
    named and reported separately from the target-URL cap (#309), so a
    caller can always tell whether any evidence was left out.
    """
    total = sum(len(issue.get("locations") or []) for issue in group)
    out: list[dict[str, Any]] = []
    for issue in group:
        for loc in issue.get("locations") or []:
            if len(out) >= loc_cap:
                break
            out.append(
                {
                    "target_url": issue.get("target_url"),
                    "status_code": issue.get("status_code"),
                    "source_url": loc.get("source_url"),
                    "anchor": loc.get("anchor"),
                    "link_position": loc.get("link_position"),
                    "link_path": loc.get("link_path"),
                }
            )
        if len(out) >= loc_cap:
            break
    return out, total, max(0, total - len(out))


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------
def _esc(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def render_tasks_md(backlog: dict[str, Any]) -> str:
    src = backlog["source"]
    # `dict.get`'s default never fires here: "project" is always a present key
    # (set at build time from `run.get("project")`), so a missing project
    # stores `None` rather than leaving the key absent. `or` catches that
    # stored `None` where `.get(..., default)` cannot (#640).
    site_name = src.get("project") or src.get("site_name") or "site"
    lines = [f"# Audit Tasks — {site_name}", ""]
    # A failed crawl says so before anything else: the tasks below describe the
    # failed run, and a reader must not take them for a picture of the site.
    if src.get("crawl_valid") is False:
        reason = src.get("crawl_invalid_reason") or "the crawl produced no usable data"
        lines.append(f"> **Crawl failed — no health score.** {reason}.")
        lines.append("")
    # A partial crawl still scores, so this warning is distinct from the
    # failed-crawl one above: the run produced a real backlog, but only for
    # the slice of the site it actually reached (#308). Both can appear
    # together — a run can fail validity for other reasons while also having
    # stopped early — since they report different facts.
    if src.get("crawl_partial"):
        stop = src.get("crawl_finish_reason")
        stop_text = f" Stopped early: `{stop}`." if stop else ""
        lines.append(f"> **Partial crawl — this is a sample, not the whole site.**{stop_text}")
        if src.get("health_score_scope"):
            lines.append(f"> {src['health_score_scope']}")
        lines.append("")
    # health_score_basis is set whenever checks were skipped or disabled,
    # independent of crawl_partial (#457) — a fully-crawled, export-based run
    # missing files gets this warning too, and it must reach the human-facing
    # backlog even when the crawl_partial block above never fires.
    if src.get("health_score_basis"):
        lines.append(f"> {src['health_score_basis']}")
        lines.append("")
    exclusions = src.get("finding_exclusions") or {}
    if exclusions.get("rules_configured"):
        findings = int(exclusions.get("suppressed_total", 0))
        rules = int(exclusions["rules_configured"])
        finding_label = "finding" if findings == 1 else "findings"
        rule_label = "rule" if rules == 1 else "rules"
        lines.append(
            "> Explicit URL-pattern exclusions: "
            f"{findings} {finding_label} suppressed by {rules} {rule_label}. Suppressed findings are absent "
            "from this task list; see the audit JSON for their full records and reasons."
        )
        lines.append("")
    health = src.get("health_score")
    health_text = "n/a" if health is None else health
    lines.append(f"- Source: audit generated at {src.get('generated_at')} (health {health_text})")
    lines.append(
        f"- Tasks: **{backlog['summary']['tasks_total']}** "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(backlog['summary']['by_priority'].items()))})"
    )
    lines.append("")

    by_prio: dict[str, list[dict]] = {}
    for t in backlog["tasks"]:
        by_prio.setdefault(t["priority"], []).append(t)

    for prio in sorted(by_prio, key=lambda p: _PRIORITY_ORDER.get(p, 9)):
        lines.append(f"## {prio} ({len(by_prio[prio])})")
        lines.append("")
        for t in by_prio[prio]:
            lines.append(f"- [ ] **{_esc(t['title'])}** · {t['severity']} · effort: {t['effort']}")
            assignment = t.get("assignment")
            if assignment:
                lines.append(
                    "    - Assignment: "
                    f"{_esc(assignment['name'])} ({_esc(assignment['kind'])}, "
                    f"{_esc(assignment['state'])}) — {_esc(assignment['rationale'])}"
                )
                scope = t["verification_scope"]
                lines.append(
                    "    - Verification scope: inspect a representative, then verify "
                    f"all {scope['affected_urls_total']} affected URL(s); assignment does not prove one shared fix."
                )
            if t.get("fix_hint"):
                lines.append(f"    - _How to fix:_ {_esc(t['fix_hint'])}")
            reproductions = t.get("reproductions") or []
            if reproductions:
                for item in reproductions[:15]:
                    lines.append(f"    - Reproduction: {_esc(item)}")
            else:
                lines.append("    - Reproduction unavailable from the saved audit.")
            if t.get("broken_links"):
                lines.append("    - Broken links (destination ← source · position · XPath):")
                shown = t["broken_links"][:15]
                for bl in shown:
                    lines.append(
                        f"        - {_esc(bl['target_url'])} ({bl.get('status_code')}) "
                        f"← {_esc(bl['source_url'])} · {_esc(bl.get('link_position'))} "
                        f"· `{_esc(bl.get('link_path'))}`"
                    )
                # Locations cut by the display slice above and locations cut
                # by the location cap (#309) are both real omissions — a
                # reader must not mistake either for the full evidence list.
                hidden = t.get("broken_links_truncated", 0) + max(
                    0, len(t["broken_links"]) - len(shown)
                )
                if hidden:
                    lines.append(f"        - … {hidden} more source location(s) omitted")
            elif t["urls"]:
                for url in t["urls"][:15]:
                    lines.append(f"        - {_esc(url)}")
                shown = min(15, len(t["urls"]))
                hidden = t["urls_truncated"] + max(0, len(t["urls"]) - shown)
                if hidden:
                    lines.append(f"        - … {hidden} more")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_tasks(backlog: dict[str, Any], json_path: str, md_path: str) -> tuple[str, str]:
    import json

    os.makedirs(os.path.dirname(os.path.abspath(json_path)) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(backlog, fh, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_tasks_md(backlog))
    return json_path, md_path
