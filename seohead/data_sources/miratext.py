"""Bounded Miratext SEO analysis adapter; paid modes require an explicit opt-in."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from seohead.data_sources.http import open_no_redirect

ENDPOINT = "https://miratext.com/api2/call/article/seoAnalizText"
SOURCE = "miratext"
Transport = Callable[[str, bytes], str]


def _table(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    if isinstance(value, dict):
        rows = value.get("items") or value.get("rows") or value.get("data")
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return rows
    return []


def _first(row: dict[str, Any], *names: str) -> Any:
    return next((row[name] for name in names if name in row), None)


def _author_tables(data: Any, top: int) -> dict[str, Any]:
    """Reduce variable provider result shapes without inventing units or recommendations."""
    if not isinstance(data, dict):
        return {
            "state": "unavailable",
            "reason": "missing_result_data",
            "words": [],
            "density_deviation": [],
        }
    tz = data.get("tz") if isinstance(data.get("tz"), dict) else data
    word_rows = _table(tz.get("keywordsAll"))
    deviation_rows = _table(tz.get("densityDeviation") or tz.get("density_deviation"))
    words = []
    for row in word_rows:
        word = _first(row, "word", "keyword", "text", "name")
        if not isinstance(word, str) or not word.strip():
            continue
        words.append(
            {
                "word": word,
                "sites": _first(row, "sites", "count_sites", "competitors"),
                "median_density": _first(row, "median_density", "density", "median"),
                "mine": _first(row, "mine", "my_count", "count_my"),
                "recommended": _first(row, "recommended", "recommend", "recommended_count"),
                "unit": "provider_reported",
            }
        )
    deviations = []
    for row in deviation_rows:
        word = _first(row, "word", "keyword", "text", "name")
        if isinstance(word, str) and word.strip():
            deviations.append(
                {
                    "word": word,
                    "mine": _first(row, "mine", "my_density", "count_my"),
                    "median_density": _first(row, "median_density", "density", "median"),
                    "deviation": _first(row, "deviation", "delta", "difference"),
                    "unit": "provider_reported",
                }
            )
    return {
        "state": "complete" if words or deviations else "unavailable",
        "reason": None
        if words or deviations
        else "provider_result_has_no_recognized_author_tables",
        "words": words[:top],
        "density_deviation": deviations[:top],
        "stopwords": tz.get("stopwords") or tz.get("stop_words") or "provider_not_reported",
        "filters": tz.get("filters") or "provider_not_reported",
    }


def _transport(key: str) -> Transport:
    def send(encoded: str, _: bytes) -> str:
        request = urllib.request.Request(
            ENDPOINT,
            data=encoded.encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with open_no_redirect(request, timeout=30) as response:
            return response.read().decode("utf-8")

    return send


def analyze(
    *,
    urls: list[str] | None = None,
    texts: list[str] | None = None,
    my: str | None = None,
    hash: str | None = None,
    check_type: str = "url",
    keywords: str | None = None,
    paid: bool = False,
    confirm_paid: bool = False,
    timeout: int = 120,
    top: int = 100,
    api_key: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Start or resume an analysis. Provider responses are reduced and keys never return."""
    from seohead.data_sources.credentials import MissingCredential, miratext_api_key

    if (
        type(timeout) is not int
        or not 1 <= timeout <= 600
        or type(top) is not int
        or not 1 <= top <= 500
    ):
        raise ValueError("timeout must be 1..600 and top must be 1..500")
    if (paid or keywords) and not confirm_paid:
        return {
            "ok": False,
            "state": "confirmation_required",
            "error": "paid or keyword analysis requires confirm_paid=true",
        }
    if hash is None and not ((urls or texts) and my):
        raise ValueError("pass hash to resume, or URLs/texts and my")
    if hash is not None and (
        not isinstance(hash, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", hash)
    ):
        raise ValueError("hash must be a 1..128 character provider identifier")
    if check_type not in {"url", "content"}:
        raise ValueError("check_type must be url or content")
    if not all(isinstance(value, str) and value for value in (urls or []) + (texts or [])):
        raise ValueError("urls and texts must contain non-empty strings")
    if (urls and texts) or (urls and len(urls) > 10) or (texts and len(texts) > 10):
        raise ValueError("pass one of urls or texts with at most 10 items")
    try:
        key = api_key or miratext_api_key()
    except MissingCredential as exc:
        return {"ok": False, "state": "not_configured", "error": str(exc)}
    fields: list[tuple[str, str]] = [("api_key", key), ("check_type", check_type)]
    if hash:
        fields.append(("hash", hash))
    else:
        fields.extend(("url[]", value) for value in urls or [])
        fields.extend(("content[]", value) for value in texts or [])
        fields.extend(
            [
                ("my", my or ""),
                ("shop_type", "keywords_search" if keywords else "normal"),
                ("options[paid_type]", "paid" if paid else "free"),
            ]
        )
        if keywords:
            fields.append(("keywords_search[keywords]", keywords))
    encoded = urllib.parse.urlencode(fields)

    def request(payload: str) -> dict[str, Any] | None:
        try:
            response = json.loads((transport or _transport(key))(payload, b""))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            return None
        return (
            response
            if isinstance(response, dict) and isinstance(response.get("result"), str)
            else None
        )

    body = request(encoded)
    if body is None:
        return {"ok": False, "state": "failed", "error": "Miratext request failed"}
    if body["result"] != "ok":
        return {"ok": False, "state": "failed", "error": "Miratext rejected the request"}
    state = body.get("status", "accepted")
    if not isinstance(state, str):
        return {"ok": False, "state": "failed", "error": "malformed Miratext response"}
    deadline = time.monotonic() + timeout
    polls = 0
    while state in {"draft", "working"} and isinstance(body.get("hash"), str):
        if time.monotonic() >= deadline or (transport is not None and polls):
            break
        if transport is None:
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        resumed = request(
            urllib.parse.urlencode(
                [("api_key", key), ("check_type", check_type), ("hash", body["hash"])]
            )
        )
        if resumed is None or resumed.get("result") != "ok":
            break
        body, state = resumed, resumed.get("status", "accepted")
        if not isinstance(state, str):
            return {"ok": False, "state": "failed", "error": "malformed Miratext response"}
        polls += 1
    result = {
        "ok": True,
        "state": state,
        "hash": body.get("hash"),
        "paid": paid,
        "resumable": state in {"draft", "working"},
        "wait_exhausted": state in {"draft", "working"},
    }
    if state == "accepted":
        result["author_tables"] = _author_tables(body.get("data"), top)
    if paid and not hash:
        from seohead.data_sources import spend

        result["spend"] = spend.record(
            "miratext", "seo_analysis", unit="unknown", extra={"cost_unknown": True}
        )
    return result
