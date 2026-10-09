"""Bounded fetches of canonical and hreflang targets the crawl never captured (#987).

A crawl records only what it fetched. A canonical or hreflang target that no link reached
is therefore unmeasured even when it answers 404 -- exactly the case CANONICAL_TARGET_ERROR
and HREFLANG_BROKEN_TARGET exist for. This module fetches such same-host targets once, after
the crawl, through the crawl's own no-follow client, and keeps one ``context_items`` row per
target. It records observations only; deciding whether an observation is a finding stays with
the audit (``seohead.sf``). A target without a stored row is still unmeasured, never clean.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

KIND = "target_probe"
SCHEMA_VERSION = "target_probe.v1"
# A hard cap keeps a 1M-URL site from turning the post-crawl step into a second crawl. Targets
# past the cap stay unmeasured, which the audit reports as such.
MAX_PROBES = 5000


def _same_host(url: str, host: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and (parts.hostname or "").lower() == host


def _candidate_targets(con: Any, host: str) -> dict[str, str]:
    """Same-host canonical and hreflang targets that no captured page answers, keyed by norm."""
    from seohead.sf.core.normalize import norm_url
    from seohead.storage.structured_evidence import read as read_structured

    captured = {
        norm_url(url)
        for (url,) in con.execute("SELECT u.url FROM pages p JOIN urls u USING(url_id)")
    }
    targets: dict[str, str] = {}

    def offer(url: str) -> None:
        if len(targets) >= MAX_PROBES or not url or not _same_host(url, host):
            return
        key = norm_url(url)
        if key not in captured and key not in targets:
            targets[key] = url

    for (canonical,) in con.execute(
        "SELECT DISTINCT canonical FROM pages WHERE canonical IS NOT NULL AND canonical<>''"
    ):
        offer(canonical)
    for evidence in read_structured(con, streaming=True)["language"]:
        for declaration in evidence.get("declarations") or []:
            if isinstance(declaration, dict):
                offer(str(declaration.get("target") or ""))
    return targets


def capture(
    scan: Any,
    settings: dict[str, Any],
    *,
    client: Any = None,
    fetcher: Any = None,
    wait: Any = None,
) -> dict[str, int]:
    """Probe every same-host uncaptured canonical/hreflang target once and store the result."""
    from seohead.crawl.collect import fetch_one
    from seohead.sf.core.normalize import norm_url

    start_url = scan.con.execute("SELECT start_url FROM scan WHERE singleton=1").fetchone()[0]
    host = (urlsplit(start_url or "").hostname or "").lower()
    if not host:
        return {"probed": 0, "answered": 0, "failed": 0}
    counts = {"probed": 0, "answered": 0, "failed": 0}
    user_agent = settings["http"]["user_agent"]
    for url in _candidate_targets(scan.con, host).values():
        if wait is not None:
            wait()
        try:
            record, _ = fetch_one(
                url,
                client=client,
                fetcher=fetcher,
                user_agent=user_agent,
                retry_on_timeout=0,
            )
            status = record.status_code
            payload = {
                "schema_version": SCHEMA_VERSION,
                "url": url,
                "status_code": status,
                "redirect_url": record.redirect_url or "",
                "error": record.error or "",
            }
        except Exception as exc:  # one unreachable target must not stop the others
            status = None
            payload = {
                "schema_version": SCHEMA_VERSION,
                "url": url,
                "status_code": None,
                "redirect_url": "",
                "error": type(exc).__name__,
            }
        counts["probed"] += 1
        counts["answered" if status is not None else "failed"] += 1
        scan.con.execute(
            "INSERT OR REPLACE INTO context_items(kind,item_key,payload_version,payload_json,completeness,reason) "
            "VALUES(?,?,?,?,?,?)",
            (
                KIND,
                f"url:{norm_url(url)}",
                "scan_context.v1",
                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                "complete" if status is not None else "unavailable",
                payload["error"],
            ),
        )
    return counts


def validate_context(item: dict[str, Any], payload: Any) -> None:
    """Reject a stored probe that the native scan reader could not have written."""
    from seohead.storage import ScanError

    status = payload.get("status_code") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "url", "status_code", "redirect_url", "error"}
        or payload["schema_version"] != SCHEMA_VERSION
        or type(payload["url"]) is not str
        or not payload["url"]
        or (status is not None and type(status) is not int)
        or type(payload["redirect_url"]) is not str
        or type(payload["error"]) is not str
        or not item["item_key"].startswith("url:")
        or item["completeness"] != ("complete" if status is not None else "unavailable")
        or item["reason"] != payload["error"]
    ):
        raise ScanError("native target probe context is invalid")


def load(con: Any) -> dict[str, dict[str, Any]]:
    """Stored probes keyed by normalized target URL; empty when no probe ran."""
    rows = con.execute("SELECT item_key,payload_json FROM context_items WHERE kind=?", (KIND,))
    return {key[len("url:") :]: json.loads(payload) for key, payload in rows}


def observation(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The target observation a probe supports, or None when it answered no status."""
    if type(payload.get("status_code")) is not int:
        return None
    return {
        "state": "probed",
        "status_code": payload["status_code"],
        "redirect_url": payload.get("redirect_url") or "",
        "reason": "fetched by the target probe; the target was not captured by the crawl",
        "probe_error": payload.get("error") or "",
    }
