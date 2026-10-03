"""Bounded, read-only operator queries over retained hyperlink occurrences."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from seohead.crawl.spider import Scope, _canonical_key

from . import ScanError, open_scan

_REPRESENTATIONS = {"all", "static", "rendered", "legacy_fragment", "legacy_unknown"}
_ORDER = (
    "CASE l.evidence_representation WHEN 'static' THEN 0 "
    "WHEN 'rendered' THEN 1 WHEN 'legacy_fragment' THEN 2 ELSE 3 END, "
    "l.ordinal, l.link_id"
)


def _url(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ScanError(f"{label} must be an absolute HTTP(S) URL of at most 8192 characters")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.netloc
            or not parts.hostname
            or (parts.port is not None and not 1 <= parts.port <= 65535)
            or parts.username is not None
            or parts.password is not None
            or any(char.isspace() for char in value)
        ):
            raise ValueError
    except ValueError as exc:
        raise ScanError(f"{label} must be an absolute HTTP(S) URL without credentials") from exc
    return _canonical_key(value)


def _representation(value: str) -> str:
    if value not in _REPRESENTATIONS:
        raise ScanError(
            "representation must be all, static, rendered, legacy_fragment, or legacy_unknown"
        )
    return value


def _header(con, representation: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT scan_uuid,evidence_revision,lifecycle,finish_reason,crawl_partial,"
        "capabilities_json,limitations_json,config_json FROM scan WHERE singleton=1"
    ).fetchone()
    capabilities = json.loads(row["capabilities_json"])
    links = capabilities.get("links") or {
        "state": "unavailable",
        "reason": "link capability was not recorded",
    }
    reasons = []
    if row["lifecycle"] != "finished":
        reasons.append(f"scan lifecycle is {row['lifecycle']}")
    if row["crawl_partial"]:
        reasons.append("crawl is partial")
    if links.get("state") != "complete":
        reasons.append(
            links.get("reason") or f"link capability is {links.get('state', 'unavailable')}"
        )
    limitations = json.loads(row["limitations_json"])
    reasons.extend(
        reason for reason in limitations if reason.startswith("link_observations_omitted")
    )
    if representation != "all":
        measured = con.execute(
            "SELECT 1 FROM links WHERE evidence_representation=? LIMIT 1", (representation,)
        ).fetchone()
        if measured is None and representation != "legacy_unknown":
            measured = con.execute(
                "SELECT 1 FROM pages WHERE representation=? LIMIT 1", (representation,)
            ).fetchone()
        if measured is None:
            reasons.append(f"{representation} representation was not captured")
    return {
        "scan_uuid": row["scan_uuid"],
        "evidence_revision": row["evidence_revision"],
        "coverage": {
            "state": (
                "unavailable"
                if links.get("state") == "unavailable"
                or any(reason.endswith("representation was not captured") for reason in reasons)
                else "partial"
                if reasons
                else "observed_scope_only"
            ),
            "reasons": reasons,
            "global_reachability": "unknown",
            "scope": "retained links in this scan and selected representation",
        },
        "config": json.loads(row["config_json"]),
    }


def _spellings(key: str) -> tuple[str, str]:
    """The crawler treats an empty root path and slash as one page identity."""
    parts = urlsplit(key)
    alternate = (
        urlunsplit((parts.scheme, parts.netloc, "", parts.query, "")) if parts.path == "/" else key
    )
    return key, alternate


def _source_ids(con, key: str) -> list[int]:
    """Find fetched source pages without loading a site-wide URL map."""
    return [
        row[0]
        for row in con.execute(
            "SELECT p.url_id FROM pages p JOIN urls u USING(url_id) "
            "WHERE u.url IN (?,?) ORDER BY p.page_ordinal,p.url_id",
            _spellings(key),
        )
    ]


def _edge(con, link_id: int) -> dict[str, Any]:
    row = con.execute(
        "SELECT l.link_id,l.source_url_id,l.destination_url_id,l.source_document_id,"
        "l.evidence_representation,l.ordinal,l.anchor,l.nofollow,l.position,"
        "s.url AS source_url,d.url AS destination_url "
        "FROM links l JOIN urls s ON s.url_id=l.source_url_id "
        "JOIN urls d ON d.url_id=l.destination_url_id WHERE l.link_id=?",
        (link_id,),
    ).fetchone()
    if row is None:
        raise ScanError("observed path refers to a missing link record")
    return {
        **dict(row),
        "nofollow": bool(row["nofollow"]),
        "position": row["position"] or None,
    }


def shortest_observed_path(
    scan_path: str | Path,
    seed: str,
    target: str,
    *,
    representation: str = "all",
    max_nodes: int = 10_000,
    max_edges: int = 200_000,
    max_depth: int = 20,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Find one shortest retained path; no result asserts global orphanhood.

    FIFO source expansion and source-local representation/ordinal/link-id order
    choose the first parent for equal-length routes. A source is looked up in
    indexed ``urls``/``pages`` and expanded through the existing links source
    index, so the whole graph is never reconstructed for a single query.
    """
    seed_key, target_key = _url(seed, "seed"), _url(target, "target")
    representation = _representation(representation)
    if (
        type(max_nodes) is not int
        or not 1 <= max_nodes <= 100_000
        or type(max_edges) is not int
        or not 1 <= max_edges <= 2_000_000
        or type(max_depth) is not int
        or not 0 <= max_depth <= 100
        or type(timeout_seconds) not in {int, float}
        or not 0 < timeout_seconds <= 30
    ):
        raise ScanError(
            "invalid path budget; nodes 1..100000, edges 1..2000000, depth 0..100, timeout 0..30s"
        )
    deadline = time.monotonic() + timeout_seconds
    con = open_scan(scan_path, require_audit=False)
    try:
        header = _header(con, representation)
        scope = Scope.from_config(header.pop("config").get("scope"))
        result: dict[str, Any] = {
            **header,
            "seed": seed_key,
            "target": target_key,
            "representation": representation,
            "identity": "fragmentless exact crawler URL",
            "policy": "internal followed hyperlinks; static then rendered then legacy_fragment then legacy_unknown",
            "limits": {"max_nodes": max_nodes, "max_edges": max_edges, "max_depth": max_depth},
            "visited_nodes": 0,
            "examined_edges": 0,
            "hops": [],
        }
        if not _source_ids(con, seed_key):
            result["state"] = "seed_unobserved"
            result["reason"] = "seed is not a retained page in this scan"
            return result
        if result["coverage"]["state"] == "unavailable" and seed_key != target_key:
            result["state"] = "unavailable"
            result["reason"] = "selected link evidence is unavailable"
            return result
        parents: dict[str, tuple[str | None, int | None, int]] = {seed_key: (None, None, 0)}
        queue = deque([seed_key])
        examined = 0
        depth_limited = False
        limit_reason = ""
        state = "found" if seed_key == target_key else "unreachable_in_observed_graph"
        while queue and state != "found":
            if time.monotonic() >= deadline:
                state = "limit_reached"
                limit_reason = "time_budget_exhausted"
                break
            source = queue.popleft()
            depth = parents[source][2]
            if depth >= max_depth:
                depth_limited = True
                continue
            for source_id in _source_ids(con, source):
                where = "AND l.evidence_representation=?" if representation != "all" else ""
                params = (source_id, representation) if where else (source_id,)
                rows = con.execute(
                    "SELECT l.link_id,l.nofollow,d.url AS destination_url "
                    "FROM links l JOIN urls d ON d.url_id=l.destination_url_id "
                    f"WHERE l.source_url_id=? {where} ORDER BY {_ORDER}",
                    params,
                )
                for row in rows:
                    if time.monotonic() >= deadline:
                        state = "limit_reached"
                        limit_reason = "time_budget_exhausted"
                        break
                    if examined >= max_edges:
                        state = "limit_reached"
                        limit_reason = "edge_budget_exhausted"
                        break
                    examined += 1
                    destination = _canonical_key(row["destination_url"])
                    if row["nofollow"] or not scope.is_internal(
                        destination, urlsplit(seed_key).hostname or ""
                    ):
                        continue
                    if destination in parents:
                        continue
                    if len(parents) >= max_nodes:
                        state = "limit_reached"
                        limit_reason = "node_budget_exhausted"
                        break
                    parents[destination] = (source, row["link_id"], depth + 1)
                    queue.append(destination)
                    if destination == target_key:
                        state = "found"
                        break
                if state in {"found", "limit_reached"}:
                    break
            if state == "limit_reached":
                break
        if state == "unreachable_in_observed_graph" and depth_limited:
            state = "limit_reached"
            limit_reason = "depth_budget_exhausted"
        result["state"] = state
        if limit_reason:
            result["reason"] = limit_reason
        result["visited_nodes"] = len(parents)
        result["examined_edges"] = examined
        if state == "found":
            path = []
            node = target_key
            while node != seed_key:
                parent, link_id, _ = parents[node]
                if parent is None or link_id is None:
                    raise ScanError("observed path predecessor is invalid")
                path.append(_edge(con, link_id))
                node = parent
            result["hops"] = list(reversed(path))
        return result
    finally:
        con.close()


def reverse_inlinks(
    scan_path: str | Path,
    target: str,
    *,
    representation: str = "all",
    after_link_id: int = 0,
    limit: int = 100,
    max_bytes: int = 1_048_576,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Page retained inlinks by stable link ID without an unbounded count scan."""
    target_key = _url(target, "target")
    representation = _representation(representation)
    if (
        type(after_link_id) is not int
        or after_link_id < 0
        or type(limit) is not int
        or not 1 <= limit <= 500
        or type(max_bytes) is not int
        or not 4096 <= max_bytes <= 8_388_608
        or type(timeout_seconds) not in {int, float}
        or not 0 < timeout_seconds <= 30
    ):
        raise ScanError(
            "invalid inlink page; cursor >= 0, limit 1..500, max_bytes 4096..8388608, timeout 0..30s"
        )
    con = open_scan(scan_path, require_audit=False)
    try:
        header = _header(con, representation)
        header.pop("config")
        where = "AND l.evidence_representation=?" if representation != "all" else ""
        first, second = _spellings(target_key)
        params = [
            first,
            second,
            first + "#",
            first + "$",
            second + "#",
            second + "$",
            after_link_id,
            *([representation] if where else []),
            limit + 1,
        ]
        items = []
        used = 0
        has_more = False
        state = "complete"
        deadline = time.monotonic() + timeout_seconds
        con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10_000)
        try:
            # CROSS JOIN pins URL lookup before link lookup; an ORDER BY link_id
            # otherwise tempts SQLite into scanning every link row in the scan.
            rows = con.execute(
                "SELECT l.link_id FROM (SELECT url_id FROM urls WHERE url IN (?,?) "
                "OR (url>=? AND url<?) OR (url>=? AND url<?)) d "
                "CROSS JOIN links l INDEXED BY links_destination_source_position "
                "WHERE l.destination_url_id=d.url_id AND l.link_id>? "
                f"{where} ORDER BY l.link_id LIMIT ?",
                params,
            )
            for row in rows:
                if len(items) == limit:
                    has_more = True
                    break
                item = _edge(con, row["link_id"])
                size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
                if used + size > max_bytes:
                    if not items:
                        raise ScanError("one inlink record exceeds max_bytes")
                    has_more = True
                    break
                items.append(item)
                used += size
        except sqlite3.OperationalError as exc:
            if "interrupted" not in str(exc).lower():
                raise
            state = "limit_reached"
            has_more = True
        return {
            **header,
            "target": target_key,
            "representation": representation,
            "identity": "exact destination URL plus its fragment occurrences",
            "after_link_id": after_link_id,
            "limit": limit,
            "max_bytes": max_bytes,
            "bytes": used,
            "state": state,
            "reason": "time_budget_exhausted" if state == "limit_reached" else "",
            "items": items,
            "returned": len(items),
            "has_more": has_more,
            "next_after_link_id": items[-1]["link_id"] if has_more and items else None,
        }
    finally:
        con.close()
