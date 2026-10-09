"""Stream literal content-presence evidence from one retained scan.v1 artifact.

The module deliberately reads only retained bytes.  It never fetches a URL,
recovers an omitted body, or treats a missing/corrupt body as an absence.  A
caller may write records to NDJSON through ``on_record``; this reader keeps no
corpus-sized result list in memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from seohead.checks.custom_search import _target_text, _validate_selector_syntax

from . import READ_TIMEOUT_SECONDS, ScanError, open_scan
from .bodies import read_document

MAX_QUERY_CHARS = 512
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_SNIPPET_CHARS = 320
_SCOPES = {"raw_html", "head_markup", "body_text", "selector_markup"}
_REPRESENTATIONS = {"static", "rendered"}
_MODES = {"contains", "not_contains"}


def _arguments(
    query: str,
    scope: str,
    mode: str,
    representations: Iterable[str],
    selector: str | None,
) -> tuple[tuple[str, ...], str]:
    if type(query) is not str or not query.strip():
        raise ValueError("content search query must be a nonempty literal string")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"content search query exceeds {MAX_QUERY_CHARS} characters")
    if scope not in _SCOPES:
        raise ValueError(f"unknown content search scope {scope!r}")
    if mode not in _MODES:
        raise ValueError(f"unknown content search mode {mode!r}")
    if isinstance(representations, str):
        raise ValueError("representations must be an explicit iterable, not a string")
    values = tuple(representations)
    if not values or len(set(values)) != len(values) or set(values) - _REPRESENTATIONS:
        raise ValueError("representations must be a unique nonempty subset of static,rendered")
    selector = selector or ""
    if scope == "selector_markup":
        if not selector:
            raise ValueError("selector_markup scope requires a CSS selector")
        _validate_selector_syntax("element", selector)
    elif selector:
        raise ValueError("selector is only valid for selector_markup scope")
    return values, selector


def _snippet(target: str, query: str, *, case_sensitive: bool) -> str:
    """Return a bounded match excerpt without commonly shaped credential values."""
    haystack = target if case_sensitive else target.lower()
    needle = query if case_sensitive else query.lower()
    start = haystack.find(needle)
    if start < 0:
        return ""
    left = max(0, start - MAX_SNIPPET_CHARS // 3)
    right = min(len(target), start + len(query) + MAX_SNIPPET_CHARS * 2 // 3)
    value = " ".join(target[left:right].split())
    # The result is evidence for a literal marker, never a credential dump.
    value = re.sub(
        r"(?i)\b(authorization|bearer|token|api[_-]?key|secret|password)\s*([:=])\s*(?:'[^']*'|\"[^\"]*\"|[^\s<'\">]+)",
        r"\1\2[REDACTED]",
        value,
    )
    return value[:MAX_SNIPPET_CHARS]


def _source(header: dict[str, Any], query: str, *, active: bool) -> dict[str, Any]:
    return {
        "kind": "scan.v1.retained-content-search",
        "search_id": str(uuid4()),
        "scan_uuid": header["scan_uuid"],
        "evidence_revision": header["evidence_revision"],
        "lifecycle": header["lifecycle"],
        "active": active,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "writer_revision": header["writer_revision"],
    }


def _row_stream(con, representations: tuple[str, ...]):
    requested = ",".join("(?)" for _ in representations)
    # The newest retained document wins per URL and representation.  A former
    # static document may remain after a rendered capture, but it is not the
    # current observation requested by this search.
    sql = f"""
        WITH requested(representation) AS (VALUES {requested}),
        latest AS (
            SELECT d.*,ROW_NUMBER() OVER (
                PARTITION BY d.url_id,d.representation ORDER BY d.document_id DESC
            ) AS position
            FROM documents d JOIN requested USING(representation)
        ), observations AS (
            SELECT l.document_id,l.url_id,l.representation,l.body_state,l.body_reason,
                   l.fidelity,l.decoder_errors,l.decoder_source,
                   u.url,p.page_ordinal,p.status_code,
                   COALESCE(r.content_type,'text/html') AS content_type
            FROM latest l JOIN urls u USING(url_id)
            LEFT JOIN pages p USING(url_id)
            LEFT JOIN responses r ON r.response_id=l.source_response_id
            WHERE l.position=1
            UNION ALL
            SELECT NULL,p.url_id,requested.representation,'unavailable',
                   CASE requested.representation
                     WHEN 'static' THEN COALESCE((SELECT r.body_reason FROM responses r
                                                 WHERE r.request_url_id=p.url_id AND r.purpose='page'
                                                 ORDER BY r.response_id DESC LIMIT 1),'not_in_corpus')
                     ELSE 'not_rendered'
                   END,
                   'unavailable','unknown','not_applicable',u.url,p.page_ordinal,
                   p.status_code,p.content_type
            FROM pages p JOIN urls u USING(url_id) CROSS JOIN requested
            WHERE NOT EXISTS (
                SELECT 1 FROM documents d
                WHERE d.url_id=p.url_id AND d.representation=requested.representation
            )
        )
        SELECT * FROM observations
        ORDER BY page_ordinal IS NULL,page_ordinal,url,representation
    """
    return con.execute(sql, representations)


def search_scan(
    scan: str | Path,
    *,
    query: str,
    scope: str = "raw_html",
    mode: str = "contains",
    representations: Iterable[str] = ("static",),
    selector: str | None = None,
    case_sensitive: bool = False,
    allow_active: bool = False,
    include_snippets: bool = False,
    on_record: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Search a closed retained scan without materializing its corpus.

    ``absence_confirmed`` is true only when every selected HTML observation is
    readable, the marker is absent from all of them, and the source scan itself
    is complete.  Non-HTML, failed, unretained, and corrupt bodies remain
    named unavailable evidence.
    """
    selected, selector = _arguments(query, scope, mode, representations, selector)
    if (
        type(case_sensitive) is not bool
        or type(allow_active) is not bool
        or type(include_snippets) is not bool
    ):
        raise ValueError("case_sensitive, allow_active, and include_snippets must be boolean")
    scan_path = Path(scan)
    if not scan_path.is_file():
        raise ValueError(f"scan does not exist: {scan_path}")

    measured = present = absent = filter_matches = unavailable = non_html = documents = 0
    url_pages = 0
    reasons: Counter[str] = Counter()
    previous_url: str | None = None
    stream_error = ""
    try:
        reader = open_scan(
            scan_path, require_audit=False, query_timeout_seconds=READ_TIMEOUT_SECONDS
        )
    except ScanError as exc:
        return {
            "ok": False,
            "search_completed": False,
            "source": {
                "kind": "scan.v1.retained-content-search",
                "search_id": str(uuid4()),
                "scan_uuid": None,
                "evidence_revision": None,
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            },
            "scope": scope,
            "mode": mode,
            "representations": list(selected),
            "coverage": {
                "state": "unavailable",
                "url_pages_selected": 0,
                "documents_selected": 0,
                "documents_measured": 0,
                "filter_matching_documents": 0,
                "present_documents": 0,
                "absent_documents": 0,
                "unavailable_documents": 0,
                "non_html_documents": 0,
                "unavailable_reasons": {},
                "partial_reasons": [],
                "integrity_error": str(exc),
                "stream_error": None,
                "body_byte_limit": MAX_DOCUMENT_BYTES,
            },
            "absence_confirmed": False,
        }
    with closing(reader) as con:
        header = dict(con.execute("SELECT * FROM scan WHERE singleton=1").fetchone())
        active = header["lifecycle"] != "finished"
        if active and not allow_active:
            raise ScanError(
                "content search requires a finished scan; set allow_active deliberately"
            )
        capabilities = json.loads(header["capabilities_json"])
        source = _source(header, query, active=active)
        try:
            cursor = _row_stream(con, selected)
        except sqlite3.Error as exc:
            cursor = iter(())
            stream_error = f"SQLite read deadline or query failure: {exc}"
        while True:
            try:
                row = next(cursor)
            except StopIteration:
                break
            except sqlite3.Error as exc:
                stream_error = f"SQLite read deadline or query failure: {exc}"
                break
            item = dict(row)
            documents += 1
            if item["url"] != previous_url:
                url_pages += 1
                previous_url = item["url"]
            record: dict[str, Any] = {
                "scan_uuid": source["scan_uuid"],
                "evidence_revision": source["evidence_revision"],
                "url": item["url"],
                "page_ordinal": item["page_ordinal"],
                "document_id": item["document_id"],
                "capture_mode": item["representation"],
                "status_code": item["status_code"],
                "presence": None,
                "status": "unavailable",
                "reason": "",
            }
            content_type = item["content_type"].lower()
            if item["representation"] == "static" and "html" not in content_type:
                non_html += 1
                record["reason"] = "non_html_content_type"
            elif item["document_id"] is None or item["body_state"] != "complete":
                unavailable += 1
                record["reason"] = f"body_absent/{item['body_state']}/{item['body_reason']}"
            else:
                try:
                    html = read_document(
                        con, int(item["document_id"]), max_decoded_bytes=MAX_DOCUMENT_BYTES
                    )
                    target = _target_text(
                        {"html": html},
                        {
                            "raw_html": "raw",
                            "head_markup": "head_markup",
                            "body_text": "body_text",
                            "selector_markup": "selector_markup",
                        }[scope],
                        selector,
                    )
                    found = query in target if case_sensitive else query.lower() in target.lower()
                    # A replacement character or legacy re-encoded body can
                    # prove a positive marker, never a clean site-wide absence.
                    uncertain_absence = not found and (
                        item["fidelity"] == "reencoded_text"
                        or item["decoder_errors"] == "unknown"
                        or "\ufffd" in html
                    )
                    if uncertain_absence:
                        unavailable += 1
                        record["reason"] = "decode_fidelity_unavailable_for_absence"
                    else:
                        measured += 1
                        record["presence"] = found
                        if found:
                            present += 1
                        else:
                            absent += 1
                        is_match = found if mode == "contains" else not found
                        record["status"] = "matched" if is_match else "not_matched"
                        if is_match:
                            filter_matches += 1
                            if found and include_snippets:
                                record["snippet"] = _snippet(
                                    target, query, case_sensitive=case_sensitive
                                )
                except ScanError as exc:
                    unavailable += 1
                    record["reason"] = f"body_integrity_error/{exc}"
            if record["status"] == "unavailable":
                reasons[record["reason"]] += 1
            if on_record is not None:
                on_record(record)

    partial_reasons: list[str] = []
    if header["crawl_partial"]:
        partial_reasons.append("scan crawl is partial")
    lane = capabilities.get("html_bodies", {})
    if lane.get("state") != "complete":
        partial_reasons.append(
            f"HTML body lane is {lane.get('state', 'unknown')}: {lane.get('reason', '')}"
        )
    if active:
        partial_reasons.append("scan lifecycle is active")
    if stream_error:
        partial_reasons.append(stream_error)
    if documents == 0:
        partial_reasons.append("no selected URL pages")
    if unavailable or non_html or partial_reasons:
        coverage = "unavailable" if measured == 0 else "partial"
    else:
        coverage = "complete"
    absence_confirmed = measured > 0 and absent == measured and coverage == "complete"
    return {
        "ok": coverage == "complete",
        "search_completed": not stream_error,
        "source": source,
        "scope": scope,
        "mode": mode,
        "representations": list(selected),
        "coverage": {
            "state": coverage,
            "url_pages_selected": url_pages,
            "documents_selected": documents,
            "documents_measured": measured,
            "filter_matching_documents": filter_matches,
            "present_documents": present,
            "absent_documents": absent,
            "unavailable_documents": unavailable,
            "non_html_documents": non_html,
            "unavailable_reasons": dict(sorted(reasons.items())),
            "partial_reasons": partial_reasons,
            "stream_error": stream_error or None,
            "body_byte_limit": MAX_DOCUMENT_BYTES,
        },
        "absence_confirmed": absence_confirmed,
    }
