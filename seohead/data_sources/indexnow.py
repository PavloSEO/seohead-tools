"""IndexNow: push changed URLs to Bing, Yandex, Naver, and Seznam in one call.

**Google has not joined IndexNow as of 2026.** Any caller-facing text about this tool must say
so — implying otherwise would be worse than not having the tool at all (issue #97). Natural
pairing: after ``compare-crawls`` reports which URLs appeared or changed, submit exactly those.

Free, no quota, no vendor lock-in. The protocol needs one self-generated key, not a
provider-issued secret: host it as a plain-text file at ``https://<host>/<key>.txt`` so the
receiving search engine can verify the submitter controls the site, then send that same key with
every request. This is a credential-gated skeleton: submission is built and tested against a
recorded response shape, but has never reached the live endpoint.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from seohead.data_sources.http import open_no_redirect

HOST = "https://api.indexnow.org/indexnow"
TIMEOUT = 30
MAX_URLS_PER_BATCH = 10_000
# As of 2026; state this wherever the tool is described so a caller does not assume Google
# crawl behavior changes because of a submission here.
NOT_ADOPTED_BY = ("Google",)

_STATUS_MESSAGES = {
    400: "invalid request: check the URL list and host",
    403: "key not valid: the key or its published key-location file could not be verified",
    422: "one or more URLs do not belong to the submitted host or do not match the key",
    429: "too many requests: IndexNow is rate limiting this key",
}

# payload -> (status code, response body text)
Fetcher = Callable[[dict[str, Any]], tuple[int, str]]


def _hostname(value: str) -> str:
    """Normalize a bare hostname without collapsing distinct subdomains."""
    # Use the HTTP client's IDNA2008 rules; stdlib IDNA merges faß.de with fass.de.
    name = httpx.URL(scheme="https", host=value).raw_host.decode("ascii").removesuffix(".")
    if len(name) > 253 or not all(
        re.fullmatch(r"(?!-)[a-z0-9-]{1,63}(?<!-)", label) for label in name.split(".")
    ):
        raise ValueError("invalid hostname")
    return name


def _default_fetcher(payload: dict[str, Any]) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        HOST, data=data, method="POST", headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        # The request URL is the fixed HTTPS IndexNow endpoint; the key travels only in the
        # POST body the protocol itself requires, never in the URL.
        with open_no_redirect(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def submit(
    urls: list[str],
    *,
    host: str,
    key: str | None = None,
    key_location: str | None = None,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    """Submit up to 10,000 changed URLs belonging to a bare ``host``.

    Match hostnames exactly after case, IDNA, and trailing-dot normalization;
    subdomains remain distinct. HTTP and HTTPS URLs may use any valid numeric
    port: membership is by hostname, not origin. URL strings stay unchanged.
    A successful response acknowledges receipt, never guarantees indexing.
    """
    from seohead.data_sources.credentials import MissingCredential, indexnow_key

    if not urls:
        raise ValueError("urls required")
    if not host:
        raise ValueError("host required")
    if len(urls) > MAX_URLS_PER_BATCH:
        return {
            "ok": False,
            "error": f"IndexNow accepts at most {MAX_URLS_PER_BATCH} URLs per batch; got {len(urls)}",
        }
    try:
        host = _hostname(host)
    except (ValueError, httpx.InvalidURL):
        return {"ok": False, "error": "host must be a bare hostname without a port or path"}
    for position, url in enumerate(urls, 1):
        try:
            if any(char.isspace() or ord(char) < 32 or 127 <= ord(char) < 160 for char in url):
                raise ValueError("whitespace or control character")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or "\\" in url
                or re.search(r"%(?![0-9a-fA-F]{2})", url)
                or parsed.netloc.endswith(":")
                or parsed.port == 0
                or _hostname(parsed.hostname) != host
            ):
                raise ValueError("invalid URL or host mismatch")
        except (ValueError, httpx.InvalidURL):
            return {
                "ok": False,
                "error": f"URL {position} must be an absolute HTTP(S) URL belonging to host",
            }
    try:
        submission_key = key or indexnow_key()
    except MissingCredential as exc:
        return {"ok": False, "error": str(exc)}

    payload: dict[str, Any] = {"host": host, "key": submission_key, "urlList": list(urls)}
    if key_location:
        payload["keyLocation"] = key_location

    fetch = fetcher or _default_fetcher
    try:
        status, body = fetch(payload)
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "error": f"IndexNow submission failed: {exc}"}

    ok = status in (200, 202)
    result: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "submitted": len(urls),
        "not_adopted_by": list(NOT_ADOPTED_BY),
    }
    if not ok:
        result["error"] = _STATUS_MESSAGES.get(status, body[:300] or f"HTTP {status}")
    else:
        result["message"] = (
            "URLs received; key validation pending. Receipt does not guarantee indexing."
            if status == 202
            else "URLs received. Receipt does not guarantee indexing."
        )
    return result
