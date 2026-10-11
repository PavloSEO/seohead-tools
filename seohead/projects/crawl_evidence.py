"""Turn a project crawl into checklist evidence for the automatic checks it measured.

Outcomes per automatic check item: ``succeeded`` when the saved crawl ran the check,
``unavailable`` when the crawl skipped or disabled it (reason kept), ``failed`` when
the crawl itself did not finish. A check the saved audit does not mention is
``unmeasured``: it is returned by name and its record is left untouched, so it never
reads as 0 completed.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .coverage import coverage_status, record_execution

_RECORDABLE_ERRORS = (OSError, ValueError, TypeError, sqlite3.Error)
_SKIPPED = ("checks_skipped", "checks_disabled")


def _check_items(status: dict) -> list[dict]:
    return [
        item
        for item in status["items"]
        if item["kind"] == "check"
        and item["enabled"]
        and item["applicability"] not in {"excluded", "not_agreed"}
    ]


def _saved_audit(path: Path) -> dict:
    from seohead.storage import open_scan

    with contextlib.closing(open_scan(path)) as con:
        row = con.execute("SELECT document_json FROM audit WHERE singleton=1").fetchone()
    return json.loads(row[0])


def record_failure(root: Path, reason: str) -> dict[str, Any]:
    """Mark every automatic check failed when a crawl did not finish."""
    status = coverage_status(root)
    if "items" not in status:
        return {"skipped": status.get("reason", "checklist not initialized")}
    failed, refused = [], []
    for item in _check_items(status):
        if item["execution_kind"] != "automatic":
            continue
        try:
            status = record_execution(
                root,
                item["id"],
                {"status": "failed", "reason": str(reason)[:500]},
                status["revision"],
            )
            failed.append(item["id"])
        except ValueError as exc:
            refused.append({"id": item["id"], "reason": str(exc)})
    return {"failed": failed, "refused": refused}


def record_scan(root: Path, scan: Path) -> dict[str, Any]:
    """Record a saved project crawl as evidence for the automatic checks it measured."""
    status = coverage_status(root)
    if "items" not in status:
        return {"skipped": status.get("reason", "checklist not initialized")}
    try:
        relative = scan.resolve().relative_to(root.resolve()).as_posix()
        audit = _saved_audit(scan)
    except _RECORDABLE_ERRORS as exc:
        return record_failure(root, f"saved crawl has no usable audit: {exc}")
    run = audit.get("run", {})
    coverage = audit.get("summary", {}).get("check_coverage", {})
    executed = set(coverage.get("checks_silent_ids", []))
    executed.update(issue.get("check") for issue in audit.get("issues", []))
    unavailable: dict[str, str] = {}
    for key in _SKIPPED:
        for row in run.get(key, []):
            unavailable[row.get("id")] = str(row.get("reason") or "check unavailable in the crawl")
    for check_id in coverage.get("checks_disabled_ids", []):
        unavailable.setdefault(check_id, "check disabled for the crawl")
    recorded, unavailable_items, unmeasured, refused = [], [], [], []
    for item in _check_items(status):
        if item["execution_kind"] != "automatic":
            continue
        check_id = item["id"].removeprefix("check:")
        if check_id in executed:
            entry = {
                "status": "succeeded",
                "reason": "Executed in the saved project crawl",
                "artifact": relative,
            }
        elif check_id in unavailable:
            entry = {"status": "unavailable", "reason": unavailable[check_id][:500]}
        else:
            unmeasured.append(item["id"])
            continue
        try:
            status = record_execution(root, item["id"], entry, status["revision"])
        except ValueError as exc:
            refused.append({"id": item["id"], "reason": str(exc)})
            continue
        (recorded if entry["status"] == "succeeded" else unavailable_items).append(item["id"])
    return {
        "succeeded": recorded,
        "unavailable": unavailable_items,
        "unmeasured": unmeasured,
        "refused": refused,
    }
