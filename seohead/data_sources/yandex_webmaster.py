"""Read-only Yandex Webmaster REST adapter with injected transport support."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from seohead.data_sources.http import open_no_redirect

HOST = "https://api.webmaster.yandex.net/v4"
TIMEOUT = 30
Transport = Callable[[str, str, dict[str, Any] | None, str], str]


def _default_transport(method: str, url: str, payload: dict[str, Any] | None, token: str) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload else None,
        method=method,
        headers={"Authorization": f"OAuth {token}", "Content-Type": "application/json"},
    )
    with open_no_redirect(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8")


# Operation -> API v4 path below ``/user/{user_id}``; ``{host}`` marks host-level reads.
OPERATIONS: dict[str, str] = {
    "hosts": "/hosts",
    "indexing": "/hosts/{host}/search-urls",
    "crawl": "/hosts/{host}/search-urls/events/samples",
    "sitemaps": "/hosts/{host}/sitemaps",
    "search_performance": "/hosts/{host}/search-queries/popular",
    "summary": "/hosts/{host}/summary",
    "diagnostics": "/hosts/{host}/diagnostics",
    "sqi_history": "/hosts/{host}/sqi-history",
    "search_history": "/hosts/{host}/search-queries/all/history",
    "query_history": "/hosts/{host}/search-queries/{query}/history",
    "in_search_history": "/hosts/{host}/search-urls/in-search/history",
    "events_history": "/hosts/{host}/search-urls/events/history",
    "indexing_history": "/hosts/{host}/indexing/history",
    "important_urls": "/hosts/{host}/important-urls",
    "broken_links_history": "/hosts/{host}/links/internal/broken/history",
    "external_links_history": "/hosts/{host}/links/external/history",
}
# Paged sample lists: operation -> key of the list in the answer. The API caps a page at 500.
PAGED = {"search_performance": "queries", "crawl": "samples"}
PAGE_SIZE = 500
MAX_ROWS = 50_000
DEFAULT_PARAMS = {"search_performance": {"order_by": "TOTAL_SHOWS"}}


def resolve_user_id(token: str, transport: Transport | None = None) -> str:
    """Return the account user id that every other API v4 path starts with."""
    body = json.loads((transport or _default_transport)("GET", HOST + "/user", None, token))
    return str(body["user_id"])


def collect(
    operation: str,
    *,
    user_id: str | None = None,
    host_id: str | None = None,
    query_id: str | None = None,
    params: dict[str, Any] | None = None,
    paginate: bool = False,
    max_rows: int = MAX_ROWS,
    token: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Collect one read-only Yandex Webmaster API v4 resource.

    ``user_id`` is resolved from the token when omitted. ``params`` become the query string;
    a list value repeats the key (``query_indicator=TOTAL_SHOWS&query_indicator=TOTAL_CLICKS``).
    With ``paginate`` the paged sample lists (search queries, crawl events) are read page by page
    up to ``max_rows``; the answer says when that ceiling truncated the list.
    """
    from seohead.data_sources.credentials import MissingCredential, yandex_webmaster_token

    if operation not in OPERATIONS:
        raise ValueError(f"unsupported operation; supported: {', '.join(OPERATIONS)}")
    if "{host}" in OPERATIONS[operation] and not host_id:
        raise ValueError("host_id is required for this Yandex Webmaster operation")
    if "{query}" in OPERATIONS[operation] and not query_id:
        raise ValueError("query_id (from search_performance) is required for query_history")
    try:
        bearer = token or yandex_webmaster_token()
    except MissingCredential as exc:
        return {"ok": False, "state": "not_configured", "verified": False, "error": str(exc)}
    send = transport or _default_transport
    query = dict(DEFAULT_PARAMS.get(operation, {}), **(params or {}))
    list_key = PAGED.get(operation) if paginate else None
    try:
        user = user_id or resolve_user_id(bearer, send)
        path = (
            HOST
            + f"/user/{user}"
            + (
                OPERATIONS[operation]
                .replace("{host}", host_id or "")
                .replace("{query}", urllib.parse.quote(query_id or "", safe=""))
            )
        )

        def get(extra: dict[str, Any]) -> Any:
            encoded = urllib.parse.urlencode(dict(query, **extra), doseq=True)
            return json.loads(send("GET", path + (f"?{encoded}" if encoded else ""), None, bearer))

        if not list_key:
            body = get({})
            truncated = False
        else:
            body, rows, offset, truncated = None, [], int(query.get("offset", 0)), False
            while True:
                page = get({"offset": offset, "limit": PAGE_SIZE})
                body = body if body is not None else page
                chunk = page.get(list_key) or [] if isinstance(page, dict) else []
                rows.extend(chunk)
                offset += len(chunk)
                if len(rows) >= max_rows:
                    rows, truncated = rows[:max_rows], True
                    break
                if len(chunk) < PAGE_SIZE:
                    break
            body = dict(body or {}, **{list_key: rows})
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        KeyError,
    ) as exc:
        return {"ok": False, "state": "failed", "error": str(exc)}
    if not isinstance(body, dict):
        return {"ok": False, "state": "failed", "error": "malformed Yandex Webmaster response"}
    result = {
        "ok": True,
        "state": "complete",
        "operation": operation,
        "data": body,
        "read_only": True,
    }
    if list_key:
        result.update(returned=len(body[list_key]), truncated=truncated)
    return result
