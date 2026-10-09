"""Page-grounded, resumable meta-description drafting contracts.

SEOHEAD prepares evidence and validates supplied drafts.  It does not select a
hosted model, hold an API key, or claim control over the calling agent's token
or subscription limits.  The default executor is therefore a caller-supplied
result adapter; provider executors remain a separately selected integration.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bs4 import BeautifulSoup

from seohead.checks.text_normalize import normalize_document

CONTRACT_VERSION = "meta_description_drafts.v1"
DEFAULT_MIN_CHARS = 120
DEFAULT_MAX_CHARS = 155
MAX_CONTENT_CHARS = 6_000
MAX_BATCH_SIZE = 100
_SUPERLATIVE_RE = re.compile(r"\b(best|leading|number one|guaranteed)\b", flags=re.IGNORECASE)


class DraftExecutor(Protocol):
    """Interchangeable executor boundary owned by the caller or selected provider."""

    def describe(self) -> dict[str, Any]: ...

    def execute(self, records: Sequence[dict[str, Any]]) -> Sequence[dict[str, Any]]: ...


@dataclass(frozen=True)
class SuppliedDraftExecutor:
    """Synthetic/default contract adapter for drafts returned by a calling agent."""

    drafts: Sequence[dict[str, Any]]
    model_identity: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "calling_agent",
            "contract_version": CONTRACT_VERSION,
            "model_identity": self.model_identity,
            "data_transfer": "caller_runtime",
        }

    def execute(self, records: Sequence[dict[str, Any]]) -> Sequence[dict[str, Any]]:
        expected = {record["url"] for record in records}
        return [draft for draft in self.drafts if draft.get("url") in expected]


@dataclass(frozen=True)
class StaticDraftExecutor:
    """A second synthetic executor used to prove the contract is not model-specific."""

    drafts: Sequence[dict[str, Any]]

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "delegated_agent",
            "contract_version": CONTRACT_VERSION,
            "model_identity": None,
            "data_transfer": "caller_runtime",
        }

    def execute(self, records: Sequence[dict[str, Any]]) -> Sequence[dict[str, Any]]:
        expected = {record["url"] for record in records}
        return [draft for draft in self.drafts if draft.get("url") in expected]


@dataclass(frozen=True)
class DeclaredDraftExecutor:
    """Replay structured drafts from any declared caller-owned executor kind."""

    declaration: dict[str, Any]
    drafts: Sequence[dict[str, Any]]

    def describe(self) -> dict[str, Any]:
        return dict(self.declaration)

    def execute(self, records: Sequence[dict[str, Any]]) -> Sequence[dict[str, Any]]:
        expected = {record["url"] for record in records}
        return [draft for draft in self.drafts if draft.get("url") in expected]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _context(context: dict[str, Any] | None) -> dict[str, Any]:
    value = context or {}
    if not isinstance(value, dict):
        raise ValueError("site context must be an object")
    prohibited = value.get("prohibited_phrases", [])
    if not isinstance(prohibited, list) or any(not isinstance(item, str) for item in prohibited):
        raise ValueError("prohibited_phrases must be a list of strings")
    return {
        "version": str(value.get("version") or "site_context.v1"),
        "language": str(value.get("language") or ""),
        "brand_voice": str(value.get("brand_voice") or ""),
        "page_type_instructions": value.get("page_type_instructions") or {},
        "prohibited_phrases": prohibited,
        "keywords": value.get("keywords") or {},
        "min_chars": int(value.get("min_chars", DEFAULT_MIN_CHARS)),
        "max_chars": int(value.get("max_chars", DEFAULT_MAX_CHARS)),
    }


def _page_input(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or "")
    html = item.get("html")
    status = item.get("status_code", 200)
    content_type = str(item.get("content_type") or "text/html")
    if not url or not isinstance(html, str) or not html.strip():
        return {"url": url, "state": "unavailable", "reason": "page has no supplied HTML body"}
    if status >= 400 or "html" not in content_type.lower():
        return {
            "url": url,
            "state": "unavailable",
            "reason": "page representation is not an eligible HTML page",
        }
    normalized = normalize_document(html, item.get("content_area"))
    if not normalized["text"]:
        return {"url": url, "state": "unavailable", "reason": "page content area is empty"}
    soup = BeautifulSoup(html, features="lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description = soup.find(
        "meta", attrs={"name": lambda name: name and name.lower() == "description"}
    )
    old_description = str(description.get("content") or "") if description else ""
    headings = [heading.get_text(" ", strip=True) for heading in soup.find_all(["h1", "h2"])]
    keyword = context["keywords"].get(url)
    return {
        "url": url,
        "state": "eligible",
        "source_reference": {"normalized_sha256": normalized["normalized_sha256"]},
        "title": title,
        "headings": headings,
        "old_description": old_description,
        "content": normalized["text"][:MAX_CONTENT_CHARS],
        "content_truncated": len(normalized["text"]) > MAX_CONTENT_CHARS,
        "language": context["language"] or normalized["language"]["declared_primary"],
        "language_evidence": normalized["language"],
        "keyword": keyword if isinstance(keyword, str) else "",
        "keyword_origin": "operator_supplied"
        if isinstance(keyword, str) and keyword
        else "unknown",
    }


def prepare_draft_plan(
    items: Iterable[dict[str, Any]], context: dict[str, Any] | None = None, *, batch_size: int = 20
) -> dict[str, Any]:
    """Build bounded, page-evidence batches without invoking an AI provider."""
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    configured = _context(context)
    if configured["min_chars"] < 1 or configured["max_chars"] < configured["min_chars"]:
        raise ValueError("meta description length policy is invalid")
    pages = [_page_input(item, configured) for item in items]
    eligible = [page for page in pages if page["state"] == "eligible"]
    return {
        "contract_version": CONTRACT_VERSION,
        "context": configured,
        "pages": pages,
        "batches": [
            eligible[index : index + batch_size] for index in range(0, len(eligible), batch_size)
        ],
        "coverage": {
            "state": "partial" if len(eligible) != len(pages) else "complete",
            "eligible": len(eligible),
            "excluded": len(pages) - len(eligible),
            "unavailable": sum(page["state"] == "unavailable" for page in pages),
            "content_representation": "supplied HTML content area",
        },
    }


def prepare_draft_plan_from_normalized(
    items: Iterable[dict[str, Any]], context: dict[str, Any] | None = None, *, batch_size: int = 20
) -> dict[str, Any]:
    """Plan drafts from #801 normalized retained bodies without re-reading a scan.

    ``scan_corpus(kind="semantic")`` is the one body reader and normalizer.  It
    supplies private normalized text plus page metadata, while this function
    adds only draft-specific context and batching.
    """
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    configured = _context(context)
    if configured["min_chars"] < 1 or configured["max_chars"] < configured["min_chars"]:
        raise ValueError("meta description length policy is invalid")
    pages: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("url") or item.get("id") or "")
        text = item.get("text")
        source_hash = item.get("normalized_sha256")
        if not url or not isinstance(text, str) or not text or not isinstance(source_hash, str):
            pages.append(
                {
                    "url": url,
                    "state": "unavailable",
                    "reason": "normalized retained page content is unavailable",
                }
            )
            continue
        keyword = configured["keywords"].get(url)
        pages.append(
            {
                "url": url,
                "state": "eligible",
                "source_reference": {
                    "normalized_sha256": source_hash,
                    "body_sha256": item.get("body_sha256"),
                },
                "title": str(item.get("title") or ""),
                "headings": list(item.get("headings") or []),
                "old_description": str(item.get("old_description") or ""),
                "content": text[:MAX_CONTENT_CHARS],
                "content_truncated": len(text) > MAX_CONTENT_CHARS,
                "language": configured["language"]
                or item.get("language", {}).get("declared_primary", ""),
                "language_evidence": item.get("language") or {},
                "keyword": keyword if isinstance(keyword, str) else "",
                "keyword_origin": "operator_supplied"
                if isinstance(keyword, str) and keyword
                else "unknown",
            }
        )
    eligible = [page for page in pages if page["state"] == "eligible"]
    return {
        "contract_version": CONTRACT_VERSION,
        "context": configured,
        "pages": pages,
        "batches": [
            eligible[index : index + batch_size] for index in range(0, len(eligible), batch_size)
        ],
        "coverage": {
            "state": "partial" if len(eligible) != len(pages) else "complete",
            "eligible": len(eligible),
            "excluded": len(pages) - len(eligible),
            "unavailable": sum(page["state"] == "unavailable" for page in pages),
            "content_representation": "normalized retained scan content",
        },
    }


def _executor(executor: DraftExecutor) -> dict[str, Any]:
    declared = executor.describe()
    required = {"kind", "contract_version", "model_identity", "data_transfer"}
    if not isinstance(declared, dict) or required - declared.keys():
        raise ValueError("draft executor does not declare the versioned contract")
    if declared["contract_version"] != CONTRACT_VERSION:
        raise ValueError("draft executor contract version is unsupported")
    if declared["kind"] not in {"calling_agent", "delegated_agent", "external_provider"}:
        raise ValueError("draft executor kind is unsupported")
    return declared


def _draft_key(page: dict[str, Any], context: dict[str, Any], executor: dict[str, Any]) -> str:
    return _sha(
        {
            "url": page["url"],
            "source": page["source_reference"],
            "context": context,
            "executor": executor,
            "prompt_version": CONTRACT_VERSION,
        }
    )


class DraftCheckpoint:
    """Per-page local resume state; source/context/executor changes get a new key."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS meta_description_drafts ("
                "draft_key TEXT PRIMARY KEY, record_json TEXT NOT NULL, updated_at TEXT NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP)"
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT record_json FROM meta_description_drafts WHERE draft_key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def put(self, key: str, record: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO meta_description_drafts (draft_key, record_json) VALUES (?, ?)",
                (key, _canonical(record)),
            )


def _review_reasons(
    record: dict[str, Any], page: dict[str, Any], context: dict[str, Any]
) -> list[str]:
    text = record["proposed_description"]
    reasons: list[str] = []
    count = len(text)  # Unicode code points, deliberately not bytes or pixels.
    if not context["min_chars"] <= count <= context["max_chars"]:
        reasons.append("outside_configured_character_policy")
    folded = text.casefold()
    if any(phrase.casefold() in folded for phrase in context["prohibited_phrases"]):
        reasons.append("contains_prohibited_brand_phrase")
    # This is a review cue, not a factual-verification claim: a marketing
    # superlative absent from the supplied page evidence needs an editor to
    # find support or remove it before publication.
    source = " ".join([page["title"], *page["headings"], page["content"]]).casefold()
    if _SUPERLATIVE_RE.search(text) and not _SUPERLATIVE_RE.search(source):
        reasons.append("marketing_claim_not_observed_in_page_evidence")
    if (
        page["language"]
        and page["language_evidence"].get("script") == "cyrillic"
        and any("a" <= char.lower() <= "z" for char in text)
    ):
        reasons.append("possible_language_mismatch")
    return reasons


def run_draft_plan(
    plan: dict[str, Any], executor: DraftExecutor, checkpoint: DraftCheckpoint
) -> dict[str, Any]:
    """Execute supplied/delegated drafts, validate them and preserve resume state."""
    context = plan["context"]
    declared = _executor(executor)
    rows: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for page in plan["pages"]:
        if page["state"] != "eligible":
            rows.append(
                {**page, "generation_state": "unavailable", "review_reasons": [page["reason"]]}
            )
            continue
        key = _draft_key(page, context, declared)
        saved = checkpoint.get(key)
        if saved and saved.get("generation_state") == "completed":
            rows.append({**saved, "resumed": True})
        else:
            pending.append({**page, "draft_key": key})
    for index in range(0, len(pending), max(1, len(plan["batches"][0]) if plan["batches"] else 1)):
        batch = pending[index : index + max(1, len(plan["batches"][0]) if plan["batches"] else 1)]
        try:
            supplied = executor.execute(batch)
        except Exception as exc:  # Executor errors are checkpoint-visible, never a false success.
            for page in batch:
                row = {
                    **page,
                    "generation_state": "failed",
                    "error": str(exc),
                    "review_reasons": [],
                }
                checkpoint.put(page["draft_key"], row)
                rows.append(row)
            continue
        by_url = {draft.get("url"): draft for draft in supplied if isinstance(draft, dict)}
        for page in batch:
            draft = by_url.get(page["url"])
            base = {
                **page,
                "executor": declared,
                "prompt_version": CONTRACT_VERSION,
                "correction_attempts": 0,
                "resumed": False,
            }
            if (
                not isinstance(draft, dict)
                or draft.get("source_sha256") != page["source_reference"]["normalized_sha256"]
            ):
                row = {
                    **base,
                    "generation_state": "failed",
                    "error": "missing or stale structured draft",
                    "review_reasons": [],
                }
            elif (
                not isinstance(draft.get("proposed_description"), str)
                or not draft["proposed_description"].strip()
            ):
                row = {
                    **base,
                    "generation_state": "failed",
                    "error": "draft description is missing",
                    "review_reasons": [],
                }
            else:
                row = {
                    **base,
                    "generation_state": "completed",
                    "proposed_description": draft["proposed_description"].strip(),
                    "model_identity": draft.get("model_identity", declared["model_identity"]),
                    "review_state": str(draft.get("review_state") or "needs_human_review"),
                }
                row["character_count"] = len(row["proposed_description"])
                row["review_reasons"] = _review_reasons(row, page, context)
            checkpoint.put(page["draft_key"], row)
            rows.append(row)
    completed = [row for row in rows if row.get("generation_state") == "completed"]
    descriptions: dict[str, list[dict[str, Any]]] = {}
    for row in completed:
        descriptions.setdefault(row["proposed_description"].casefold(), []).append(row)
    for duplicated in descriptions.values():
        if len(duplicated) > 1:
            for row in duplicated:
                row["review_reasons"].append("duplicate_proposed_description")
    openings: dict[str, list[dict[str, Any]]] = {}
    for row in completed:
        opening = " ".join(row["proposed_description"].casefold().split()[:3])
        if opening:
            openings.setdefault(opening, []).append(row)
    for repeated in openings.values():
        if len(repeated) > 1:
            for row in repeated:
                row["review_reasons"].append("repetitive_opening")
    return {"contract_version": CONTRACT_VERSION, "executor": declared, "rows": rows}


def _spreadsheet_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def export_draft_review(
    result: dict[str, Any], json_path: str | Path, csv_path: str | Path
) -> None:
    """Write local review artifacts; this never applies a CMS change."""
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise ValueError("draft result rows are required")
    Path(json_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "url",
        "generation_state",
        "old_description",
        "proposed_description",
        "character_count",
        "review_state",
        "review_reasons",
    ]
    with Path(csv_path).open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: _spreadsheet_text(
                        "; ".join(row[field])
                        if field == "review_reasons" and isinstance(row.get(field), list)
                        else row.get(field)
                    )
                    for field in fields
                }
            )
