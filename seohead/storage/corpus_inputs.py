"""Read bounded duplicate/boilerplate corpus inputs from one validated scan snapshot."""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    import sqlite3
except ImportError:  # A Python distributor may omit this optional stdlib module.
    sqlite3 = None  # type: ignore[assignment]

from . import READ_TIMEOUT_SECONDS, ScanError, open_scan
from .bodies import _DECODER_VERSION, read_document

MAX_CORPUS_DOCUMENTS = 10_000
MAX_CORPUS_INPUT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DUPLICATE_SHINGLES = 1_000_000
MAX_DUPLICATE_CANDIDATE_COMPARISONS = 250_000


def _implementation_identity(kind: str) -> dict[str, Any]:
    """Name the current byte-to-input implementation, separately from the capture writer."""
    root = Path(__file__).resolve().parents[1]
    names = ["storage/corpus_inputs.py", "storage/bodies.py", "crawl/evidence.py"]
    names += {
        "duplicate": [
            "checks/duplicate.py",
            "checks/markdown_extract.py",
            "checks/content_area.py",
        ],
        "boilerplate": ["checks/boilerplate_report.py"],
        "semantic": ["checks/content_area.py", "checks/text_normalize.py"],
    }[kind]
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return {
        "source_sha256": digest.hexdigest(),
        "files": names,
        "decoder_version": _DECODER_VERSION,
    }


def _indexable(page: dict[str, Any], blocked: bool) -> bool:
    """Use the crawler's one indexability policy rather than a scan-only shortcut."""
    from seohead.crawl.evidence import _indexability

    record = SimpleNamespace(**page)
    return _indexability(record, blocked)[0] == "Indexable"


def _source(header: dict[str, Any], *, kind: str, representations: Counter[str]) -> dict[str, Any]:
    capabilities = json.loads(header["capabilities_json"])
    return {
        "kind": "scan.v1",
        "scan_uuid": header["scan_uuid"],
        "evidence_revision": header["evidence_revision"],
        "writer_revision": header["writer_revision"],
        "config_fingerprint": header["config_fingerprint"],
        "lifecycle": header["lifecycle"],
        "finish_reason": header["finish_reason"],
        "crawl_partial": bool(header["crawl_partial"]),
        "corpus_partial": bool(header["corpus_partial"]),
        "html_body_capability": capabilities["html_bodies"],
        "representations": dict(sorted(representations.items())),
        "implementation": _implementation_identity(kind),
    }


def _coverage(
    eligible: int,
    prepared: int,
    omitted: Counter[str],
    *,
    unavailable: str = "",
    partial_reasons: list[str] | None = None,
    measured_empty: int = 0,
) -> dict[str, Any]:
    reasons = list(partial_reasons or [])
    if unavailable:
        reasons.insert(0, unavailable)
    elif eligible and not prepared:
        unavailable = "no retained supported page bodies are available"
        reasons.insert(0, unavailable)
    state = "unavailable" if unavailable else "partial" if omitted or reasons else "complete"
    return {
        "state": state,
        "reason": "; ".join(reasons),
        "eligible_documents": eligible,
        "prepared_documents": prepared,
        "analyzed_documents": 0,
        "measured_empty_documents": measured_empty,
        "omitted_documents": sum(omitted.values()),
        "omission_reasons": dict(sorted(omitted.items())),
        "input_byte_limit": MAX_CORPUS_INPUT_BYTES,
        "document_limit": MAX_CORPUS_DOCUMENTS,
    }


def _read_interrupted(exc: BaseException) -> bool:
    """Tell a progress-handler interrupt apart from other statement failures."""
    code = getattr(exc, "sqlite_errorcode", None)  # Python >= 3.11
    if code is not None:
        return code == getattr(sqlite3, "SQLITE_INTERRUPT", 9)
    return "interrupted" in str(exc).lower()


def scan_corpus(scan: str, *, kind: str) -> dict[str, Any]:
    """Return private analyzer input plus public provenance; raw bodies never leave this module."""
    if kind not in {"duplicate", "boilerplate", "semantic"}:
        raise ValueError("unsupported scan corpus kind")
    if not isinstance(scan, str) or not scan:
        raise ValueError("scan must name a scan.v1 SQLite artifact")
    if not Path(scan).is_file():
        raise ValueError(f"scan does not exist: {scan}")

    con = open_scan(scan, require_audit=False)
    try:
        # open_scan's deadline covers its validation pass; corpus reading instead
        # re-budgets each document so total wall clock no longer scales with the
        # corpus, while one pathological document read still gets cancelled
        # (issue #710).
        deadline = [time.monotonic() + READ_TIMEOUT_SECONDS]
        con.set_progress_handler(lambda: int(time.monotonic() > deadline[0]), 10_000)
        header = dict(con.execute("SELECT * FROM scan WHERE singleton=1").fetchone())
        config = json.loads(header["config_json"])
        eligible = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        omitted: Counter[str] = Counter()
        representations: Counter[str] = Counter()
        partial_reasons = []
        if header["crawl_partial"]:
            partial_reasons.append("scan crawl is partial")
        if header["lifecycle"] != "finished":
            partial_reasons.append(f"scan lifecycle is {header['lifecycle']}")
        lane = json.loads(header["capabilities_json"])["html_bodies"]
        if lane["state"] != "complete":
            partial_reasons.append(f"HTML body lane is {lane['state']}: {lane['reason']}")

        from seohead.checks.boilerplate_report import boilerplate_hash
        from seohead.checks.markdown_extract import extract_markdown
        from seohead.checks.text_normalize import normalization_policy, normalize_document

        items: list[dict[str, Any]] = []
        input_bytes = 0
        measured_empty = 0
        stop_reason = ""
        cursor = con.execute(
            "SELECT p.document_id,p.representation,p.content_type,p.status_code,p.canonical,p.meta_robots,"
            "p.x_robots,p.error,p.title,p.meta_description,p.h1,p.h1_2,p.h2,p.heading_outline_json,"
            "u.url,d.body_sha256 AS document_body_sha256,"
            "EXISTS(SELECT 1 FROM context_items c WHERE "
            "c.kind='robots_blocked_url' AND c.item_key='url:'||p.url_id) AS robots_blocked "
            "FROM pages p JOIN urls u USING(url_id) "
            "LEFT JOIN documents d ON d.document_id=p.document_id ORDER BY p.page_ordinal"
        )
        try:
            for row in cursor:
                if len(items) >= MAX_CORPUS_DOCUMENTS:
                    stop_reason = "scan corpus document limit exceeded"
                    break
                deadline[0] = time.monotonic() + READ_TIMEOUT_SECONDS
                page = dict(row)
                representations[page["representation"]] += 1
                if page["document_id"] is None:
                    omitted["document_not_recorded"] += 1
                    continue
                if "html" not in page["content_type"].lower():
                    omitted["unsupported_format"] += 1
                    continue
                try:
                    html = read_document(
                        con, page["document_id"], max_decoded_bytes=MAX_DOCUMENT_BYTES
                    )
                except ScanError as exc:
                    omitted[str(exc)] += 1
                    continue
                if kind == "duplicate":
                    value = extract_markdown(html, config.get("content_area"))["content_markdown"]
                    if not value.strip():
                        measured_empty += 1
                    # The retained analyzer input is the extracted Markdown.
                    size = len(value.encode("utf-8"))
                elif kind == "semantic":
                    # The retained analyzer input is normalized content-area
                    # text with its own provenance: the verified body hash is
                    # the corpus's evidence identity, the input hash is the
                    # exact decoded string the normalizer consumed, and the
                    # normalized hash is its output (issue #801).
                    prepared = normalize_document(html, config.get("content_area"))
                    value = prepared["text"]
                    if not value:
                        measured_empty += 1
                    size = len(value.encode("utf-8"))
                else:
                    # Only the boilerplate digest is retained, so coverage must
                    # not depend on the corpus's raw HTML size (issue #710).
                    value = boilerplate_hash(html)
                    size = len(page["url"].encode("utf-8")) + len(value.encode("ascii"))
                if input_bytes + size > MAX_CORPUS_INPUT_BYTES:
                    stop_reason = "scan corpus input-byte budget exceeded"
                    break
                input_bytes += size
                if kind == "duplicate":
                    items.append(
                        {
                            "id": page["url"],
                            "text": value,
                            "indexable": _indexable(page, bool(page["robots_blocked"])),
                        }
                    )
                elif kind == "semantic":
                    headings = [page["h1"], page["h1_2"], page["h2"]]
                    if page["heading_outline_json"]:
                        with suppress(TypeError, json.JSONDecodeError):
                            headings = [
                                str(item.get("text") or "")
                                for item in json.loads(page["heading_outline_json"])
                                if isinstance(item, dict)
                            ]
                    items.append(
                        {
                            "id": page["url"],
                            "url": page["url"],
                            "text": value,
                            "representation": page["representation"],
                            "indexable": _indexable(page, bool(page["robots_blocked"])),
                            "body_sha256": page["document_body_sha256"],
                            "title": page["title"],
                            "old_description": page["meta_description"],
                            "headings": [heading for heading in headings if heading],
                            **{key: prepared[key] for key in prepared if key != "text"},
                        }
                    )
                else:
                    items.append({"url": page["url"], "hash": value})
        except sqlite3.OperationalError as exc:
            if not _read_interrupted(exc):
                raise
            stop_reason = "scan corpus read exceeded the per-document read budget"
        if stop_reason:
            # A cap that ends the read is partial coverage with exact counts —
            # prepared items are still analyzed — not a clean or empty result.
            remaining = eligible - len(items) - sum(omitted.values())
            if remaining > 0:
                omitted[stop_reason] += remaining
            partial_reasons.append(stop_reason)
        corpus = {
            "items": items,
            "source": _source(header, kind=kind, representations=representations),
            "coverage": _coverage(
                eligible,
                len(items),
                omitted,
                partial_reasons=partial_reasons,
                measured_empty=measured_empty,
            ),
        }
        if kind == "semantic":
            # The normalization record travels with the public manifest so an
            # output hash can be reproduced or rejected rather than trusted.
            corpus["normalization"] = normalization_policy(config.get("content_area"))
        return corpus
    finally:
        con.close()


def corpus_public(
    corpus: dict[str, Any], *, analyzed: int = 0, unavailable: str = ""
) -> dict[str, Any]:
    """Return only public scan metadata after an analyzer consumed its private items."""
    coverage = dict(corpus["coverage"])
    coverage["analyzed_documents"] = analyzed
    if unavailable:
        coverage["state"] = "unavailable"
        coverage["reason"] = "; ".join(part for part in (unavailable, coverage["reason"]) if part)
    result = {"source": corpus["source"], "coverage": coverage}
    if "normalization" in corpus:
        result["normalization"] = corpus["normalization"]
    return result
