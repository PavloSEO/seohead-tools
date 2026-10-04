"""Bounded Miratext SEO analysis adapter; paid modes require an explicit opt-in."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from seohead.data_sources.http import open_no_redirect

ENDPOINT = "https://miratext.com/api2/call/article/seoAnalizText"
Transport = Callable[[str, bytes], str]


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
    if paid or keywords:
        if not confirm_paid:
            return {
                "ok": False,
                "state": "confirmation_required",
                "error": "paid or keyword analysis requires confirm_paid=true",
            }
    if hash is None and not ((urls or texts) and my):
        raise ValueError("pass hash to resume, or URLs/texts and my")
    if urls and texts or (urls and len(urls) > 10) or (texts and len(texts) > 10):
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
    try:
        body = json.loads((transport or _transport(key))(encoded, b""))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return {"ok": False, "state": "failed", "error": "Miratext request failed"}
    if not isinstance(body, dict) or not isinstance(body.get("result"), str):
        return {"ok": False, "state": "failed", "error": "malformed Miratext response"}
    if body["result"] != "ok":
        return {"ok": False, "state": "failed", "error": "Miratext rejected the request"}
    state = body.get("status", "accepted")
    result = {
        "ok": True,
        "state": state,
        "hash": body.get("hash"),
        "paid": paid,
        "resumable": state in {"draft", "working"},
    }
    if paid and not hash:
        from seohead.data_sources import spend

        result["spend"] = spend.record(
            "miratext", "seo_analysis", unit="unknown", extra={"cost_unknown": True}
        )
    return result
