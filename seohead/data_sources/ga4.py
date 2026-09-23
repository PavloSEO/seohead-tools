"""Read-only GA4 Data API landing-page aggregates for evidence prioritization."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from seohead.data_sources.http import open_no_redirect

HOST = "https://analyticsdata.googleapis.com/v1beta"
READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
TIMEOUT = 30
MAX_ROWS = 25_000
Transport = Callable[[str, dict[str, Any], str], str]


def _default_transport(url: str, payload: dict[str, Any], token: str) -> str:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with open_no_redirect(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8")


def run_report(
    property_id: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    metrics: list[str],
    *,
    max_rows: int = MAX_ROWS * 4,
    token: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Page through one ``runReport`` query and return flat ``{name: value}`` records."""
    from seohead.data_sources.credentials import MissingCredential, ga4_access_token

    if not property_id or not start_date or not end_date or start_date > end_date:
        raise ValueError("property_id and an ordered date range are required")
    if not 1 <= max_rows <= 100_000:
        raise ValueError("max_rows must be between 1 and 100000")
    try:
        bearer = token or ga4_access_token()
    except MissingCredential as exc:
        return {"ok": False, "state": "not_configured", "error": str(exc)}
    records: list[dict[str, Any]] = []
    offset = 0
    declared_total: int | None = None
    data_loss = sampled = thresholded = False
    while True:
        payload = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [{"name": name} for name in dimensions],
            "metrics": [{"name": name} for name in metrics],
            "limit": str(min(MAX_ROWS, max_rows - offset)),
            "offset": str(offset),
        }
        try:
            body = json.loads(
                (transport or _default_transport)(
                    f"{HOST}/properties/{property_id}:runReport", payload, bearer
                )
            )
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            return {"ok": False, "state": "failed", "error": str(exc)}
        rows = body.get("rows", []) if isinstance(body, dict) else None
        total = body.get("rowCount") if isinstance(body, dict) else None
        if (
            not isinstance(rows, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < offset + len(rows)
            or len(rows) > int(payload["limit"])
            or not all(isinstance(row, dict) for row in rows)
        ):
            return {"ok": False, "state": "failed", "error": "malformed GA4 Data API response"}
        if declared_total is None:
            declared_total = total
        elif total != declared_total:
            return {"ok": False, "state": "failed", "error": "GA4 rowCount changed during paging"}
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            return {"ok": False, "state": "failed", "error": "malformed GA4 metadata"}
        data_loss = data_loss or bool(metadata.get("dataLossFromOtherRow"))
        sampled = sampled or bool(metadata.get("samplingMetadatas"))
        thresholded = thresholded or bool(metadata.get("subjectToThresholding"))
        for row in rows:
            dimension_values = row.get("dimensionValues")
            metric_values = row.get("metricValues")
            if (
                not isinstance(dimension_values, list)
                or not isinstance(metric_values, list)
                or len(dimension_values) != len(dimensions)
                or len(metric_values) != len(metrics)
                or not all(
                    isinstance(item, dict) and "value" in item
                    for item in [*dimension_values, *metric_values]
                )
            ):
                return {"ok": False, "state": "failed", "error": "malformed GA4 row values"}
            values = [item["value"] for item in [*dimension_values, *metric_values]]
            records.append(dict(zip(dimensions + metrics, values, strict=True)))
        offset += len(rows)
        if offset >= total or offset >= max_rows:
            break
        if not rows:
            return {"ok": False, "state": "failed", "error": "GA4 ended before declared rowCount"}
    truncated = offset < total
    return {
        "ok": True,
        "state": "partial" if truncated or data_loss or sampled or thresholded else "complete",
        "rows": records,
        "returned": len(records),
        "truncated": truncated,
        "data_loss": data_loss,
        "sampled": sampled,
        "thresholded": thresholded,
    }


def landing_pages(
    property_id: str,
    start_date: str,
    end_date: str,
    *,
    include_conversions: bool = False,
    include_revenue: bool = False,
    reporting_identity: str | None = None,
    token: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Return GA4 landing-page aggregates; sessions are not Search Console clicks."""
    from seohead.data_sources.credentials import MissingCredential, ga4_access_token

    if not property_id or not start_date or not end_date or start_date > end_date:
        raise ValueError("property_id and an ordered date range are required")
    try:
        bearer = token or ga4_access_token()
    except MissingCredential as exc:
        # Fall back to the same restricted service account as Search Console, scoped to
        # analytics.readonly; the account must be granted Viewer on the GA4 property.
        from seohead.data_sources.credentials import gsc_service_account_available
        from seohead.data_sources.gsc import service_account_access_token

        if not gsc_service_account_available():
            return {"ok": False, "state": "not_configured", "verified": False, "error": str(exc)}
        try:
            bearer = service_account_access_token(READONLY_SCOPE)
        except MissingCredential as sa_error:
            return {"ok": False, "state": "not_configured", "verified": False, "error": str(sa_error)}
    metrics = ["sessions", "engagedSessions"]
    if include_conversions:
        metrics.append("keyEvents")
    if include_revenue:
        metrics.append("totalRevenue")
    payload: dict[str, Any] = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": "landingPagePlusQueryString"}],
        "metrics": [{"name": name} for name in metrics],
        "limit": str(MAX_ROWS),
        "keepEmptyRows": False,
    }
    try:
        raw = (transport or _default_transport)(
            f"{HOST}/properties/{property_id}:runReport", payload, bearer
        )
        body = json.loads(raw)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "state": "failed", "error": str(exc)}
    # An empty report comes back without "rows": zero rows, not a malformed response.
    rows = body.get("rows", []) if isinstance(body, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return {"ok": False, "state": "failed", "error": "malformed GA4 Data API response"}
    sampling = (
        body.get("metadata", {}).get("dataLossFromOtherRow")
        if isinstance(body.get("metadata"), dict)
        else None
    )
    return {
        "ok": True,
        "state": "partial" if len(rows) >= MAX_ROWS or sampling else "complete",
        "property_reference": "redacted-local-artifact",
        "reporting_identity": reporting_identity or "provider default",
        "period": {"start_date": start_date, "end_date": end_date},
        "dimensions": ["landingPagePlusQueryString"],
        "metrics": metrics,
        "rows": rows,
        "returned": len(rows),
        "truncated": len(rows) >= MAX_ROWS,
        "sampling_or_thresholding": bool(sampling),
        "note": "GA4 sessions are analytics visits and are not Google Search Console clicks.",
    }
