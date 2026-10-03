"""Bounded read-only Topvisor API access using central provider credentials."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from seohead.data_sources.credentials import read

SOURCE = "topvisor"

ENDPOINTS = {
    "projects": "projects_2/projects",
    "competitors": "projects_2/competitors",
    "groups": "keywords_2/groups",
    "keywords": "keywords_2/keywords",
    "history": "positions_2/history",
    "summary": "positions_2/summary",
}

# Documented result shapes: projects/competitors/groups/keywords return arrays;
# history and summary return objects (history rows live at result.keywords).
_LIST_OPERATIONS = frozenset({"projects", "competitors", "groups", "keywords"})

# Provider envelope keys preserved verbatim when present. The continuation
# signal is the presence of nextOffset, never len(result) == limit.
_PAGE_META = ("nextOffset", "total", "limitedBy")


class TopvisorError(RuntimeError):
    """Redacted provider or transport error."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TopvisorError("Topvisor redirect refused; credentials were not forwarded")


def fetch(operation: str = "projects", params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch exactly one page from an allowlisted get endpoint; never launch checks.

    The returned page echoes the requested ``limit``/``offset`` separately from
    provider metadata: ``nextOffset`` (its presence, not ``len(result)``, is the
    continuation signal), ``total`` and ``limitedBy`` pass through when Topvisor
    sends them. ``result`` keeps the provider shape — a list for
    projects/competitors/groups/keywords, an object for history (rows at
    ``result.keywords``) and summary. Inside history ``positionsData`` a
    ``position`` is an integer ordinal rank; the provider's ``"--"`` marker
    means the query had no position inside the checked depth — an unavailable
    value, not rank 0 or 100 — and a requested date without an entry is a
    missing observation, not zero movement.
    """
    if operation not in ENDPOINTS:
        raise ValueError("Unsupported read operation")
    if params is not None and not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    body = dict(params or {})
    body.setdefault("limit", 100)
    body.setdefault("offset", 0)
    for key, low, high in (("limit", 1, 1000), ("offset", 0, 1_000_000)):
        value = body[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    if operation != "projects" and not body.get("project_id"):
        raise ValueError("project_id is required")
    token = read("topvisor/access_token", "TOPVISOR_TOKEN")
    user_id = read("topvisor/user_id", "TOPVISOR_USER_ID")
    if not user_id.isascii() or not user_id.isdecimal():
        raise ValueError("Topvisor user_id must contain only ASCII digits")
    if any(char.isspace() for char in token):
        raise ValueError("Topvisor token contains whitespace")
    request = urllib.request.Request(
        "https://api.topvisor.com/v2/json/get/" + ENDPOINTS[operation],
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Id": user_id,
            "Authorization": "bearer " + token,
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(10_000_001)
        if len(raw) > 10_000_000:
            raise TopvisorError("Response exceeded 10 MB; request fewer rows or fields")
        data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise TopvisorError(f"Topvisor HTTP {exc.code}; response body omitted") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TopvisorError("Topvisor connection failed") from None
    except (ValueError, UnicodeError):
        raise TopvisorError("Topvisor returned invalid JSON") from None
    if not isinstance(data, dict):
        raise TopvisorError("Topvisor returned an unexpected envelope")
    if data.get("errors"):
        # Keep diagnostics useful without exposing echoed credentials.
        errors = data["errors"]
        details = []
        for error in errors if isinstance(errors, list) else [errors]:
            if isinstance(error, dict):
                detail = ": ".join(
                    str(error[k]) for k in ("code", "message", "string") if k in error
                )
                details.append(detail.replace(token, "[redacted]").replace(user_id, "[redacted]"))
        raise TopvisorError("Topvisor API errors: " + ("; ".join(details)[:1000] or "unspecified"))
    if "result" not in data:
        raise TopvisorError("Topvisor response has no result")
    result = data["result"]
    if result is None:
        raise TopvisorError("Topvisor returned a null result")
    if not isinstance(result, list if operation in _LIST_OPERATIONS else dict):
        raise TopvisorError(f"Topvisor {operation} returned an unexpected result type")
    page = {
        "ok": True,
        "operation": operation,
        "limit": body["limit"],
        "offset": body["offset"],
        "page_only": True,
        "result": result,
    }
    for key in _PAGE_META:
        if key in data:
            page[key] = data[key]
    return page
