"""Read-only visited-page views, distinct from GA4 session landing pages.

Reuses the existing GA4 transport, credential helpers and paginated runReport
reader. Nothing is collected while importing or normalizing saved evidence.
"""

from __future__ import annotations

import json
import time
import urllib.error
from datetime import date
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from seohead.data_sources import ga4

DIMENSIONS = ["date", "hostName", "pagePathPlusQueryString"]
METRICS = ["screenPageViews"]
MAX_PAGE_REQUESTS = 32
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES = 32 * 1024 * 1024


def page_views(
    property_id: str,
    start_date: str,
    end_date: str,
    site_origin: str,
    *,
    max_rows: int = 100_000,
    token: str | None = None,
    transport: ga4.Transport | None = None,
) -> dict[str, Any]:
    """Collect bounded date/host/path view counts using an explicit URL origin."""
    from seohead.data_sources.credentials import (
        MissingCredential,
        ga4_access_token,
        gsc_service_account_available,
    )
    from seohead.data_sources.gsc import service_account_access_token

    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        origin = urlsplit(site_origin)
        port = origin.port
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "page_views requires ISO dates and an explicit HTTP(S) site origin"
        ) from exc
    if (
        not isinstance(property_id, str)
        or not property_id.isdecimal()
        or int(property_id) < 1
        or start > end
        or type(max_rows) is not int
        or not 1 <= max_rows <= 100_000
        or origin.scheme not in {"http", "https"}
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(
            "page_views requires a numeric property, ordered dates, bounded rows and a bare site origin"
        )
    try:
        bearer = token or ga4_access_token()
    except MissingCredential:
        if not gsc_service_account_available():
            return {
                "ok": False,
                "state": "not_configured",
                "error": "GA4 read-only credentials are not configured",
            }
        try:
            bearer = service_account_access_token(ga4.READONLY_SCOPE)
        except MissingCredential:
            return {
                "ok": False,
                "state": "not_configured",
                "error": "GA4 read-only credentials are unavailable",
            }
    facts: list[dict[str, Any]] = []
    timezone: str | None = None
    failure_status: int | None = None
    attempts = 0
    response_bytes = 0

    def collect(url, payload, access_token):
        nonlocal timezone, failure_status, attempts, response_bytes
        payload = {
            **payload,
            "returnPropertyQuota": True,
            "orderBys": [{"dimension": {"dimensionName": name}} for name in DIMENSIONS],
        }
        raw = None
        for attempt in range(3):
            if attempts >= MAX_PAGE_REQUESTS:
                raise ValueError("GA4 page-view request budget exhausted")
            attempts += 1
            try:
                raw = (transport or ga4._default_transport)(url, payload, access_token)
                break
            except urllib.error.HTTPError as exc:
                failure_status = exc.code
                if (exc.code == 429 or 500 <= exc.code <= 599) and attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise urllib.error.URLError(
                    f"GA4 page-view request failed (HTTP {exc.code})"
                ) from None
            except (urllib.error.URLError, TimeoutError):
                raise urllib.error.URLError("GA4 page-view transport unavailable") from None
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("GA4 response exceeds the bounded page size")
        response_bytes += len(raw.encode("utf-8"))
        if response_bytes > MAX_TOTAL_RESPONSE_BYTES:
            raise ValueError("GA4 page-view response byte budget exhausted")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("GA4 response is not an object")
        for field, expected in (("dimensionHeaders", DIMENSIONS), ("metricHeaders", METRICS)):
            headers = body.get(field)
            if (
                not isinstance(headers, list)
                or any(not isinstance(item, dict) for item in headers)
                or [item.get("name") for item in headers] != expected
            ):
                raise ValueError("GA4 returned different page-view dimensions or metrics")
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("GA4 metadata is invalid")
        current_timezone = metadata.get("timeZone")
        if current_timezone is not None:
            if not isinstance(current_timezone, str):
                raise ValueError("GA4 timezone is invalid")
            try:
                ZoneInfo(current_timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("GA4 timezone is unavailable") from exc
        if facts and current_timezone != timezone:
            raise ValueError("GA4 timezone changed during pagination")
        timezone = current_timezone
        fact = {
            "offset": payload["offset"],
            "limit": payload["limit"],
            "row_count": body.get("rowCount"),
            "timezone": timezone,
            "sampled": bool(metadata.get("samplingMetadatas")),
            "sampling_metadata": metadata.get("samplingMetadatas"),
            "thresholded": bool(metadata.get("subjectToThresholding")),
            "data_loss": bool(metadata.get("dataLossFromOtherRow")),
            "property_quota": body.get("propertyQuota"),
        }
        if len(json.dumps(fact).encode("utf-8")) > 64 * 1024:
            raise ValueError("GA4 page metadata exceeds its bound")
        facts.append(fact)
        failure_status = None
        return raw

    result = ga4.run_report(
        property_id,
        start_date,
        end_date,
        DIMENSIONS,
        METRICS,
        max_rows=max_rows,
        token=bearer,
        transport=collect,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "state": "failed",
            "failure_kind": "not_granted"
            if failure_status in {401, 403}
            else "quota_exhausted"
            if failure_status == 429
            else "collection_failed",
            "status": failure_status,
            "error": "GA4 page-view evidence could not be collected",
            "completed_pages": len(facts),
            "request_attempts": attempts,
        }
    rows = []
    try:
        for item in result["rows"]:
            day = item["date"]
            if not isinstance(day, str) or len(day) != 8:
                raise ValueError("invalid GA4 date")
            observed_day = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
            if not start <= observed_day <= end:
                raise ValueError("GA4 row is outside requested dates")
            count = item["screenPageViews"]
            if not isinstance(count, str) or not count.isdecimal():
                raise ValueError("GA4 page views must be a nonnegative count")
            host, path = item["hostName"], item["pagePathPlusQueryString"]
            eligible = (
                isinstance(host, str)
                and host.casefold().rstrip(".") == origin.hostname.casefold().rstrip(".")
                and isinstance(path, str)
                and path.startswith("/")
                and not path.startswith("//")
                and not urlsplit(path).fragment
            )
            rows.append(
                {
                    **item,
                    "date": observed_day.isoformat(),
                    "source_date": day,
                    "screenPageViews": int(count),
                    "url": f"{origin.scheme}://{origin.netloc}{path}" if eligible else None,
                    "url_binding_state": "declared_origin" if eligible else "unkeyable",
                    "url_binding_reason": None
                    if eligible
                    else "host/path cannot be bound to the explicitly selected site origin",
                }
            )
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "state": "failed", "error": "GA4 page-view row contract is invalid"}
    return {
        **result,
        "rows": rows,
        "operation": "page_views",
        "reporting_identity": f"properties/{property_id}",
        "read_only": True,
        "period": {"start_date": start_date, "end_date": end_date},
        "dimensions": DIMENSIONS,
        "metrics": METRICS,
        "timezone": timezone,
        "attribution": "page_view event count; not session landing-page attribution",
        "grain": "date x hostName x pagePathPlusQueryString",
        "site_origin": site_origin,
        "result_state": "observed" if rows else "empty",
        "property_access": "verified_by_report",
        "page_metadata": facts,
        "request_attempts": attempts,
        "resource_bounds": {
            "max_rows": max_rows,
            "max_requests": MAX_PAGE_REQUESTS,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "max_total_response_bytes": MAX_TOTAL_RESPONSE_BYTES,
        },
        "coverage_note": "absent rows are not measured zero; screenPageViews are not sessions or Search Console clicks",
    }
