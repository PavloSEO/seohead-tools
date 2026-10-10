"""Caller-executed AI custom column over selected retained page evidence.

SEOHEAD prepares a bounded plan with a size estimate and validates the values a
calling or delegated agent returns for each page. It holds no provider key, opens
no connection and transfers nothing: whoever runs the prompt owns the transfer and
the consent to it. The estimate is a character-based planning heuristic, not a
billing figure, and no price is applied.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "ai_column.v1"
MAX_PROMPT_CHARS = 2_000
MAX_PAGES = 500
MAX_EXCERPT_CHARS = 1_500
MAX_HEADINGS = 20
MAX_VALUE_CHARS = 1_000
MAX_COLUMN_CHARS = 64
CHARS_PER_TOKEN = 4


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _column(value: str) -> str:
    name = value.strip()
    if not name or len(name) > MAX_COLUMN_CHARS:
        raise ValueError(f"column name must be 1..{MAX_COLUMN_CHARS} characters")
    return name


def _page(url: str, item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {"url": url, "state": "unavailable", "reason": "url is not in the retained scan"}
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return {
            "url": url,
            "state": "unavailable",
            "reason": "normalized retained page content is unavailable",
        }
    source = item.get("normalized_sha256")
    if not isinstance(source, str) or not source:
        source = _sha(text)
    headings = [str(heading) for heading in item.get("headings") or [] if heading]
    return {
        "url": url,
        "state": "eligible",
        "source_reference": {
            "normalized_sha256": source,
            "body_sha256": item.get("body_sha256"),
        },
        "payload": {
            "title": str(item.get("title") or ""),
            "headings": headings[:MAX_HEADINGS],
            "excerpt": text[:MAX_EXCERPT_CHARS],
            "excerpt_truncated": len(text) > MAX_EXCERPT_CHARS,
        },
    }


def prepare_ai_column_plan(
    items: Iterable[dict[str, Any]],
    prompt: str,
    *,
    urls: Sequence[str] | None = None,
    column: str = "ai_column",
    max_pages: int = MAX_PAGES,
) -> dict[str, Any]:
    """Build the page-grounded plan, its size estimate and the consent scope; no model call."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
    by_url: dict[str, dict[str, Any]] = {}
    for item in items:
        url = str(item.get("url") or item.get("id") or "")
        if url and url not in by_url:
            by_url[url] = item
    selected = list(urls) if urls is not None else list(by_url)
    if len(selected) != len(set(selected)):
        raise ValueError("urls must not repeat")
    if len(selected) > max_pages:
        raise ValueError(f"selection has {len(selected)} URLs; the limit is {max_pages}")
    pages = [_page(url, by_url.get(url)) for url in selected]
    eligible = [page for page in pages if page["state"] == "eligible"]
    prompt_text = prompt.strip()
    characters = sum(
        len(prompt_text)
        + len(page["payload"]["title"])
        + sum(len(heading) for heading in page["payload"]["headings"])
        + len(page["payload"]["excerpt"])
        for page in eligible
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "column": _column(column),
        "prompt": prompt_text,
        "prompt_sha256": _sha(prompt_text),
        "pages": pages,
        "estimate": {
            "eligible": len(eligible),
            "input_characters": characters,
            "approx_input_tokens": math.ceil(characters / CHARS_PER_TOKEN),
            "method": "characters_div_4_heuristic",
            "pricing": "not_applied",
        },
        "consent": {
            "state": "required_before_transfer",
            "data_sent_by_seohead": False,
            "data_scope": ["url", "title", "headings", "excerpt"],
            "transfer": "caller_runtime",
        },
        "coverage": {
            "state": "partial" if len(eligible) != len(pages) else "complete",
            "eligible": len(eligible),
            "excluded": len(pages) - len(eligible),
            "unavailable": sum(page["state"] == "unavailable" for page in pages),
            "content_representation": "normalized retained scan content",
        },
    }


def apply_ai_column_results(plan: dict[str, Any], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Validate supplied per-URL values against the plan's source hashes; never invent a value."""
    if plan.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("plan contract version is unsupported")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list of objects")
    eligible = {page["url"] for page in plan["pages"] if page["state"] == "eligible"}
    supplied: dict[str, dict[str, Any]] = {}
    duplicated: set[str] = set()
    unmatched = 0
    for row in rows:
        url = row.get("url") if isinstance(row, dict) else None
        if url not in eligible:
            unmatched += 1
            continue
        if url in supplied:
            duplicated.add(url)
        supplied[url] = row
    out: list[dict[str, Any]] = []
    for page in plan["pages"]:
        url = page["url"]
        if page["state"] != "eligible":
            out.append(
                {
                    "url": url,
                    "state": "unavailable",
                    "column_value": None,
                    "error": page["reason"],
                }
            )
            continue
        row = supplied.get(url)
        expected = page["source_reference"]["normalized_sha256"]
        value = row.get("value") if row else None
        if row is None:
            state, error = "missing", "no row returned for page"
        elif url in duplicated:
            state, error = "failed", "duplicate row for page"
        elif row.get("source_sha256") != expected:
            state, error = "failed", "missing or stale source hash"
        elif not isinstance(value, str) or not value.strip():
            state, error = "failed", "value is missing"
        elif len(value) > MAX_VALUE_CHARS:
            state, error = "failed", f"value exceeds {MAX_VALUE_CHARS} characters"
        else:
            state, error = "completed", ""
        out.append(
            {
                "url": url,
                "state": state,
                "column_value": value.strip() if state == "completed" else None,
                "error": error,
            }
        )
    counts = {
        state: sum(row["state"] == state for row in out)
        for state in ("completed", "failed", "missing", "unavailable")
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "column": plan["column"],
        "prompt_sha256": plan["prompt_sha256"],
        "rows": out,
        "coverage": {
            **counts,
            "unmatched_rows": unmatched,
            "state": "complete" if counts["completed"] == len(plan["pages"]) else "partial",
        },
    }


def _spreadsheet_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def export_ai_column_csv(result: dict[str, Any], csv_path: str | Path) -> None:
    """Write the custom column as a formula-safe CSV; this never writes back to a site or CMS."""
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise ValueError("column result rows are required")
    fields = ["url", "state", result["column"], "error"]
    with Path(csv_path).open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow([_spreadsheet_text(field) for field in fields])
        for row in rows:
            writer.writerow(
                _spreadsheet_text(value)
                for value in (row["url"], row["state"], row["column_value"], row["error"])
            )
