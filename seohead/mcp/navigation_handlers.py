"""Bounded offline navigation evidence reads shared by CLI and local MCP."""

from __future__ import annotations

import json
from collections import Counter

from seohead.storage import ScanError, open_scan


def scan_navigation(
    input_path: str, document_id: int | None = None, limit: int = 100, offset: int = 0
) -> dict:
    """Read retained observed routes; the report never invents navigation causes."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if document_id is not None and (type(document_id) is not int or document_id < 1):
        raise ValueError("document_id must be a positive integer")
    from seohead.storage.bodies import read_document_navigation

    con = open_scan(input_path, require_audit=False)
    try:
        if (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()
            is None
        ):
            return {
                "ok": True,
                "schema": "seohead.navigation-report.v1",
                "state": "unavailable",
                "reason": "scan_has_no_retained_documents",
                "items": [],
                "has_more": False,
            }
        if document_id is None:
            ids = [
                row[0]
                for row in con.execute(
                    "SELECT document_id FROM documents WHERE representation != ? ORDER BY document_id LIMIT ? OFFSET ?",
                    ("static", limit + 1, offset),
                )
            ]
        else:
            ids = [document_id]
        items = []
        output_bytes = 0
        for number in ids[:limit]:
            item = read_document_navigation(con, number)
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if output_bytes + size > 2 * 1024 * 1024:
                break
            items.append(item)
            output_bytes += size
    except (ValueError, KeyError) as exc:
        raise ScanError(f"cannot read navigation evidence: {exc}") from exc
    finally:
        con.close()
    counts = Counter(event["kind"] for item in items for event in item["navigation"]["events"])
    states = Counter(item["navigation"]["state"] for item in items)
    omitted = sum(item["navigation"]["events_omitted"] for item in items)
    state = (
        "unavailable"
        if not items or states["unavailable"] == len(items)
        else "partial"
        if states["partial"] or states["unavailable"]
        else "complete"
    )
    return {
        "ok": True,
        "schema": "seohead.navigation-report.v1",
        "state": state,
        "scope": "selected_retained_documents",
        "items": items,
        "offset": offset,
        "has_more": len(ids) > len(items),
        "next_offset": offset + len(items),
        "output_byte_limit": 2 * 1024 * 1024,
        "event_counts": dict(counts),
        "document_states": dict(states),
        "events_omitted": omitted,
        "interpretation": "Observed navigation within the recorded render window; anchor cause does not prove a human click. Missing or partial capture is not a clean result.",
    }
