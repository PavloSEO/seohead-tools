"""Read-only integrity validation for the retained scan corpus lanes."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zlib
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from . import ScanError

_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
_OMITTED = {
    "not_enabled",
    "cache_control_no_store",
    "credentialed",
    "unsupported_media",
    "body_budget_exhausted",
    "resource_budget_exhausted",
}
_UNAVAILABLE = {"not_fetched", "not_in_corpus", "legacy_not_retained", "fetch_failed"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _iter_rows(con: sqlite3.Connection, sql: str) -> Iterator[dict[str, Any]]:
    cursor = con.execute(sql)
    names = [column[0] for column in cursor.description or ()]
    for row in cursor:
        yield dict(zip(names, row, strict=True))


def _json(value: Any, label: str) -> Any:
    if not isinstance(value, str):
        raise ScanError(f"{label} must be JSON text")
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ScanError(f"{label} is invalid JSON") from exc


def _timestamp(value: Any, label: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise ScanError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScanError(f"{label} is not an RFC3339 timestamp") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ScanError(f"{label} must be UTC")


def _headers(value: Any, label: str) -> None:
    pairs = _json(value, label)
    if not isinstance(pairs, list):
        raise ScanError(f"{label} must be an ordered header-pair list")
    for pair in pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(part) is not str for part in pair)
            or pair[0].lower() in _SENSITIVE_HEADERS
        ):
            raise ScanError(f"{label} contains an unredacted or malformed header")


def _has_no_store(value: Any) -> bool:
    from seohead.crawl.cache import _parse_cache_control

    return any(
        pair[0].lower() == "cache-control" and "no-store" in _parse_cache_control(pair[1])
        for pair in _json(value, "response headers")
        if isinstance(pair, list) and len(pair) == 2 and all(type(part) is str for part in pair)
    )


def _state(row: Mapping[str, Any], *, document: bool) -> None:
    state, reason, sha, fidelity = (
        row["body_state"],
        row["body_reason"],
        row["body_sha256"],
        row["fidelity" if document else "body_fidelity"],
    )
    if state == "complete":
        allowed = (
            {"entity_bytes", "reencoded_text", "serialized_dom"}
            if document
            else {
                "entity_bytes",
                "reencoded_text",
            }
        )
        if (
            sha is None
            or reason not in {"none", "preexisting_cache_snapshot"}
            or fidelity not in allowed
        ):
            raise ScanError("complete corpus row has invalid hash, reason, or fidelity")
    elif state == "truncated":
        if sha is not None or reason != "truncated" or fidelity != "unavailable":
            raise ScanError("truncated corpus row must retain no body")
    elif state == "omitted":
        if sha is not None or reason not in _OMITTED or fidelity != "unavailable":
            raise ScanError("omitted corpus row has an invalid reason or body")
    elif state == "unavailable":
        if sha is not None or reason not in _UNAVAILABLE or fidelity != "unavailable":
            raise ScanError("unavailable corpus row has an invalid reason or body")
    else:
        raise ScanError("unknown corpus body state")


def _decode_body_row(row: Mapping[str, Any], *, max_decoded_bytes: int) -> bytes:
    """Validate and decode one selected ``bodies`` row without a second lookup.

    ``validate_corpus`` already performs a full scan.  Keeping the BLOB with
    its metadata in that scan preserves ``read_body``'s byte-level checks while
    avoiding two indexed point queries per retained body during resume.
    """
    sha256 = row["sha256"]
    codec = row["codec"]
    decoded_bytes = row["decoded_bytes"]
    stored_bytes = row["stored_bytes"]
    actual_size = row["actual_size"]
    data = row["data"]
    if (
        not isinstance(sha256, str)
        or _SHA256.fullmatch(sha256) is None
        or type(codec) is not str
        or type(decoded_bytes) is not int
        or type(stored_bytes) is not int
        or type(actual_size) is not int
        or type(data) is not bytes
        or decoded_bytes < 0
        or stored_bytes < 0
        or stored_bytes != actual_size
    ):
        raise ScanError("body metadata or stored length is invalid")
    if decoded_bytes > max_decoded_bytes:
        raise ScanError("body unavailable: decoded byte limit exceeded")
    if stored_bytes > decoded_bytes:
        raise ScanError("body stored size exceeds its decoded-byte declaration")
    if codec == "identity":
        raw = data
        if len(raw) != decoded_bytes:
            raise ScanError("identity body decoded length disagrees")
    elif codec == "zlib":
        decoder = zlib.decompressobj()
        try:
            raw = decoder.decompress(data, max_decoded_bytes + 1)
        except zlib.error as exc:
            raise ScanError("compressed body has an invalid zlib stream") from exc
        if len(raw) > max_decoded_bytes or decoder.unconsumed_tail:
            raise ScanError("compressed body exceeds decoded byte limit")
        if not decoder.eof or decoder.unused_data or len(raw) != decoded_bytes:
            raise ScanError("compressed body is truncated, trailing, or length-mismatched")
    else:
        raise ScanError("body codec is unsupported")
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ScanError("body SHA-256 disagrees with decoded bytes")
    return raw


def validate_corpus(
    con: sqlite3.Connection,
    scan: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    verify_bodies: bool = True,
) -> None:
    """Validate body/response/document lineage without mutating the artifact."""
    if not isinstance(con, sqlite3.Connection):
        raise ScanError("corpus validator requires a sqlite3 connection")
    if not isinstance(scan, Mapping) or not isinstance(policy, Mapping):
        raise ScanError("corpus validator requires scan and policy metadata")
    if (
        con.execute("SELECT 1 FROM resource_refs LIMIT 1").fetchone() is not None
        or con.execute(
            "SELECT 1 FROM context_items WHERE kind='resource_inventory' LIMIT 1"
        ).fetchone()
    ):
        from .resources import validate_resources

        validate_resources(con)
    mode = policy.get("body_mode")
    cap = policy.get("max_body_bytes", 0)
    if mode not in {"off", "captured_entity_bytes"} or type(cap) is not int or cap < 0:
        raise ScanError("corpus body policy is invalid")
    if mode == "off" and con.execute("SELECT 1 FROM bodies LIMIT 1").fetchone():
        raise ScanError("off body policy cannot retain body BLOBs")
    stored_total = con.execute("SELECT COALESCE(SUM(stored_bytes),0) FROM bodies").fetchone()[0]
    if "max_body_store_bytes" in policy and stored_total > policy["max_body_store_bytes"]:
        raise ScanError("retained body bytes exceed the recorded store budget")
    for body in _iter_rows(
        con,
        "WITH serialized_dom AS (SELECT body_sha256 FROM documents "
        "WHERE fidelity='serialized_dom' AND body_sha256 IS NOT NULL GROUP BY body_sha256) "
        "SELECT b.sha256,b.codec,b.decoded_bytes,b.stored_bytes,length(data) AS actual_size,b.data,"
        "s.body_sha256 IS NOT NULL AS serialized_dom FROM bodies b "
        "LEFT JOIN serialized_dom s ON s.body_sha256=b.sha256",
    ):
        if (
            body["codec"] not in {"identity", "zlib"}
            or type(body["decoded_bytes"]) is not int
            or body["decoded_bytes"] < 0
            or body["decoded_bytes"] > cap
            or body["stored_bytes"] != body["actual_size"]
        ):
            raise ScanError("body metadata exceeds policy or disagrees with stored size")
        if verify_bodies:
            raw = _decode_body_row(body, max_decoded_bytes=min(cap, 64 * 1024 * 1024))
            if body["serialized_dom"]:
                try:
                    raw.decode("utf-8", errors="strict")
                except UnicodeError as exc:
                    raise ScanError("serialized DOM contains invalid UTF-8") from exc

    try:
        config = _json(scan.get("config_json"), "scan configuration")
    except ScanError:
        config = {}
    retain_no_store = bool(
        isinstance(config, dict)
        and isinstance(config.get("evidence"), dict)
        and config["evidence"].get("retain_no_store_acknowledged") is True
    )
    for row in _iter_rows(
        con,
        "SELECT r.*,p.request_ordinal AS source_request_ordinal,p.body_sha256 AS source_body_sha256,"
        "p.request_url_id AS source_request_url_id,p.variant_key AS source_variant_key,"
        "p.method AS source_method,p.body_state AS source_body_state,"
        "p.body_fidelity AS source_body_fidelity,p.effective_url_id AS source_effective_url_id,"
        "p.effective_status_code AS source_effective_status_code "
        "FROM responses r LEFT JOIN responses p ON p.response_id=r.source_response_id",
    ):
        _timestamp(row["requested_at"], "response requested_at")
        _timestamp(row["received_at"], "response received_at", nullable=True)
        _headers(row["request_headers_redacted_json"], "request headers")
        _headers(row["response_headers_redacted_json"], "response headers")
        _headers(row["effective_headers_redacted_json"], "effective headers")
        if not isinstance(row["variant_key"], str) or not row["variant_key"]:
            raise ScanError("response variant key must be a nonempty opaque string")
        if row["transport_source"] == "cache" and scan.get("source_kind") in {
            "native",
            "reanalysis",
        }:
            raise ScanError("native corpus response cannot claim legacy cache transport")
        _state(row, document=False)
        if row["body_state"] == "complete" and (
            row["credentials_used"] != 0
            or (
                not retain_no_store
                and (
                    _has_no_store(row["response_headers_redacted_json"])
                    or _has_no_store(row["effective_headers_redacted_json"])
                )
            )
        ):
            raise ScanError("credentialed or no-store response cannot retain a complete body")
        source = row["source_response_id"]
        if row["status_code"] == 304 and row["body_state"] == "complete" and source is None:
            raise ScanError("complete 304 response requires a source response")
        if source is not None and (
            row["source_request_ordinal"] is None
            or row["source_request_ordinal"] >= row["request_ordinal"]
            or row["status_code"] != 304
            or row["source_body_sha256"] != row["body_sha256"]
            or row["source_request_url_id"] != row["request_url_id"]
            or row["source_variant_key"] != row["variant_key"]
            or row["source_method"] != row["method"]
            or (
                row["body_state"] == "complete"
                and (
                    row["source_body_state"] != "complete"
                    or row["source_body_fidelity"] != row["body_fidelity"]
                    or row["source_effective_url_id"] != row["effective_url_id"]
                    or row["source_effective_status_code"] != row["effective_status_code"]
                )
            )
        ):
            raise ScanError("304 response source ordering or body lineage disagrees")
        chain = _json(row["redirect_chain_json"], "response redirect chain")
        if not isinstance(chain, list):
            raise ScanError("response redirect chain must be an ordered list")
        current = row["request_url_id"]
        for hop in chain:
            if (
                not isinstance(hop, dict)
                or set(hop)
                != {"request_url_id", "status_code", "location_raw", "next_url_id", "blocked"}
                or hop["request_url_id"] != current
                or type(hop["status_code"]) is not int
                or type(hop["location_raw"]) is not str
                or type(hop["blocked"]) is not bool
            ):
                raise ScanError("response redirect hop is malformed or disconnected")
            if hop["status_code"] not in {301, 302, 303, 307, 308}:
                raise ScanError("redirect hop status is not an HTTP redirect")
            if hop["blocked"]:
                if hop["next_url_id"] is not None:
                    raise ScanError("blocked redirect cannot name a next URL")
                current = None
                break
            if type(hop["next_url_id"]) is not int:
                raise ScanError("redirect next URL is invalid")
            request_url = con.execute(
                "SELECT url FROM urls WHERE url_id=?", (hop["request_url_id"],)
            ).fetchone()
            next_url = con.execute(
                "SELECT url FROM urls WHERE url_id=?", (hop["next_url_id"],)
            ).fetchone()
            if (
                request_url is None
                or next_url is None
                or urljoin(request_url[0], hop["location_raw"]) != next_url[0]
            ):
                raise ScanError("redirect hop location does not reproduce its next URL")
            current = hop["next_url_id"]
        if row["body_state"] == "complete" and current != row["effective_url_id"]:
            raise ScanError("complete response effective URL disagrees with redirect chain")

    for row in _iter_rows(
        con,
        "SELECT d.*,r.content_type AS response_content_type,r.request_url_id AS response_request_url_id,"
        "r.effective_url_id AS response_effective_url_id,r.effective_status_code AS response_effective_status_code,"
        "r.body_sha256 AS response_body_sha256,r.body_state AS response_body_state,"
        "r.body_fidelity AS response_body_fidelity "
        "FROM documents d LEFT JOIN responses r ON r.response_id=d.source_response_id",
    ):
        _timestamp(row["captured_at"], "document captured_at")
        _state(row, document=True)
        from .bodies import _renderer as validate_renderer
        from .bodies import decode_entity

        renderer = validate_renderer(row, con)
        if row["body_state"] != "complete":
            expected_decoder = ("scan_decoder.v1", "not_applicable", "unknown", "not_applicable")
        elif row["fidelity"] == "serialized_dom":
            expected_decoder = ("scan_decoder.v1", "renderer_utf8", "utf-8", "not_applicable")
        elif row["fidelity"] == "reencoded_text":
            expected_decoder = ("scan_decoder.v1", "legacy_unknown", "unknown", "unknown")
        else:
            if row["response_content_type"] is None:
                raise ScanError("entity document has no source response")
            _, decoder = decode_entity(b"", row["response_content_type"])
            expected_decoder = tuple(decoder.values())
        if (
            tuple(
                row[name]
                for name in (
                    "decoder_version",
                    "decoder_source",
                    "decoder_charset",
                    "decoder_errors",
                )
            )
            != expected_decoder
        ):
            raise ScanError("document decoder metadata is inconsistent with its fidelity")
        if (
            row["source_response_id"] is not None
            and renderer
            and (
                row["response_request_url_id"] is None
                or row["response_request_url_id"] != renderer["navigation_url_id"]
                or (
                    row["body_state"] == "complete"
                    and row["response_effective_url_id"] != renderer["final_url_id"]
                )
            )
        ):
            raise ScanError("renderer navigation disagrees with its source response")
        if (
            row["fidelity"] in {"entity_bytes", "reencoded_text"}
            and row["body_state"] == "complete"
        ):
            expected_request_url = row["url_id"]
            if row["representation"] == "legacy_fragment":
                renderer = _json(row["renderer_json"], "legacy fragment renderer")
                expected_request_url = renderer["navigation_url_id"]
                if type(expected_request_url) is not int:
                    raise ScanError("legacy fragment navigation URL provenance is invalid")
            if (
                row["response_request_url_id"] != expected_request_url
                or row["response_body_sha256"] != row["body_sha256"]
                or row["response_body_state"] != "complete"
            ):
                raise ScanError("document response URL, state, or hash lineage disagrees")
    for page in _iter_rows(
        con,
        "SELECT p.url_id,p.representation,p.document_id,d.document_id AS selected_document_id,"
        "d.url_id AS selected_url_id,d.representation AS selected_representation "
        "FROM pages p LEFT JOIN documents d ON d.document_id=p.document_id",
    ):
        if page["document_id"] is not None and (
            page["selected_document_id"] is None
            or page["selected_url_id"] != page["url_id"]
            or page["selected_representation"] != page["representation"]
        ):
            raise ScanError("selected page document URL or representation disagrees")
    for table, page_column, representation_column in (
        ("links", "source_url_id", "evidence_representation"),
        ("forms", "page_url_id", "evidence_representation"),
    ):
        for row in _iter_rows(
            con,
            f"SELECT t.{page_column},t.evidence_representation,t.source_document_id,"
            "d.document_id AS source_document_exists,d.url_id AS source_document_url_id,"
            "d.representation AS source_document_representation "
            f"FROM {table} t LEFT JOIN documents d ON d.document_id=t.source_document_id",
        ):
            if row["source_document_id"] is not None and (
                row["source_document_exists"] is None
                or row["source_document_url_id"] != row[page_column]
                or row["source_document_representation"] != row[representation_column]
            ):
                raise ScanError(f"{table} source document representation disagrees")
