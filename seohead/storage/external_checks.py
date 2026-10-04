"""Typed retained outcomes for the bounded external-destination phase."""

from __future__ import annotations

from typing import Any

from . import MAX_RECORD_BYTES, ScanError, _dump

MAX_EXTERNAL_CHECKS = 50_000
_OUTCOMES = {"fetched", "failed", "blocked", "skipped"}


def ensure_schema(con: Any) -> None:
    """Install the additive v2 store; v1 readers stay explicitly unavailable."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS external_checks (ordinal INTEGER PRIMARY KEY CHECK (ordinal >= 0), outcome TEXT NOT NULL CHECK (outcome IN ('fetched','failed','blocked','skipped')), payload_json TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS external_check_summary (singleton INTEGER PRIMARY KEY CHECK (singleton=1), payload_json TEXT NOT NULL)"
    )


def check_item(ordinal: int, payload: dict[str, Any]) -> dict[str, str]:
    if type(ordinal) is not int or not 0 <= ordinal < MAX_EXTERNAL_CHECKS:
        raise ScanError("external check ordinal is outside the retained bound")
    item = {
        "kind": "external_check",
        "item_key": str(ordinal),
        "payload_version": "scan_context.v1",
        "payload_json": _dump(payload),
        "completeness": "complete",
        "reason": "",
    }
    if len(item["payload_json"].encode("utf-8")) > MAX_RECORD_BYTES:
        raise ScanError("external check exceeds the retained record bound")
    return item


def summary_item(summary: dict[str, Any]) -> dict[str, str]:
    item = {
        "kind": "external_checks_summary",
        "item_key": "run",
        "payload_version": "scan_context.v1",
        "payload_json": _dump(summary),
        "completeness": "complete",
        "reason": "",
    }
    if len(item["payload_json"].encode("utf-8")) > MAX_RECORD_BYTES:
        raise ScanError("external check summary exceeds the retained record bound")
    return item


def validate_check(item: dict[str, Any], payload: Any) -> None:
    required = {
        "url",
        "depth",
        "sources",
        "source_count",
        "outcome",
        "reason",
        "status_code",
        "content_type",
        "redirect_url",
        "redirect_chain",
        "final_url",
        "error",
        "error_kind",
    }
    if (
        not item["item_key"].isdigit()
        or int(item["item_key"]) >= MAX_EXTERNAL_CHECKS
        or item["completeness"] != "complete"
        or item["reason"]
        or not isinstance(payload, dict)
        or set(payload) != required
        or not isinstance(payload["url"], str)
        or type(payload["depth"]) is not int
        or payload["depth"] < 0
        or not isinstance(payload["sources"], list)
        or len(payload["sources"]) > 8
        or any(not isinstance(value, str) for value in payload["sources"])
        or type(payload["source_count"]) is not int
        or payload["source_count"] < len(payload["sources"])
        or payload["outcome"] not in _OUTCOMES
        or any(
            not isinstance(payload[key], str)
            for key in (
                "reason",
                "content_type",
                "redirect_url",
                "final_url",
                "error",
                "error_kind",
            )
        )
        or (payload["status_code"] is not None and type(payload["status_code"]) is not int)
        or not isinstance(payload["redirect_chain"], list)
        or len(payload["redirect_chain"]) > 10
    ):
        raise ScanError("native external check context is invalid")


def validate_summary(item: dict[str, Any], payload: Any) -> None:
    required = {
        "enabled",
        "ran",
        "policy",
        "robots_policy",
        "destinations",
        "records_carried",
        "resumed",
        "fetched",
        "failed",
        "blocked",
        "skipped",
        "skipped_reasons",
        "hosts_contacted",
        "requests",
        "finish_reason",
    }
    if (
        item["item_key"] != "run"
        or item["completeness"] != "complete"
        or item["reason"]
        or not isinstance(payload, dict)
        or set(payload) != required
        or payload["enabled"] is not True
        or type(payload["ran"]) is not bool
        or payload["robots_policy"] != "not_evaluated"
        or not isinstance(payload["policy"], dict)
        or not isinstance(payload["skipped_reasons"], dict)
        or any(
            type(payload[key]) is not int or payload[key] < 0
            for key in required
            & {
                "destinations",
                "records_carried",
                "resumed",
                "fetched",
                "failed",
                "blocked",
                "skipped",
                "hosts_contacted",
                "requests",
            }
        )
        or not isinstance(payload["finish_reason"], str)
    ):
        raise ScanError("native external check summary is invalid")
