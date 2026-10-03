"""Collect one Yandex Metrica traffic document for a dashboard-style traffic report.

The document answers the questions a monthly search-traffic dashboard answers: how many people
came from search, how that compares with the previous period and the same period a year earlier,
where they came from (search engine, city, country, device, age, gender), which pages they landed
on, which phrases brought them, and how search sits among all traffic channels. Everything a
renderer needs is calculated here, once, so a report renderer only formats this document
(``seohead.reports.traffic_dashboard``) and never derives a number of its own.

Attribution matters more than it looks. Metrica's own interface (and dashboards built on its
connector) attribute a visit to its *last significant* source: ``ym:s:lastSignTrafficSource``,
``ym:s:lastSignSearchEngineRoot`` and ``ym:s:lastSignSearchPhrase``. The plain last-click family
(``ym:s:lastTrafficSource`` ...) treats a later direct or internal visit as the source, so search
totals differ from the interface by a few percent. ``last_significant`` is therefore the default
and ``last_click`` an explicit choice; the document records which one was used.

Every Reporting API request passes ``offset=1``: the API's offset is 1-based and ``0`` is refused
with HTTP 400. Robot visits stay excluded, which is the Reporting API's default; the robot share
the API reports is recorded, and a high share becomes a document warning.

Missing data is never a silent zero. Each block carries ``status`` (``ok``, ``unavailable`` or
``skipped``) and a ``reason`` when it is not ``ok``; a comparison without a baseline carries a
``null`` delta and the reason instead of an infinite or zero change. The client is injected so
the whole collection runs offline in tests.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

SCHEMA = "seohead.metrika-traffic/1"
SOURCE = "yandex_metrika"

ATTRIBUTIONS: dict[str, dict[str, str]] = {
    "last_significant": {
        "source": "ym:s:lastSignTrafficSource",
        "engine": "ym:s:lastSignSearchEngineRoot",
        "phrase": "ym:s:lastSignSearchPhrase",
    },
    "last_click": {
        "source": "ym:s:lastTrafficSource",
        "engine": "ym:s:lastSearchEngineRoot",
        "phrase": "ym:s:lastSearchPhrase",
    },
}
TRAFFIC_SCOPES = ("organic", "all")
LANGUAGES = ("en", "ru")

# Metrica's Reporting API counts rows from 1; offset=0 is rejected with HTTP 400.
API_OFFSET = 1
MAX_PERIOD_DAYS = 366
MAX_TOP = 100
# Rows fetched from a comparison period to find the baseline of each current top row. A row
# missing from a complete lookup had no visits; one missing from a truncated lookup is unknown.
LOOKUP_LIMIT = 1000
ROBOT_WARNING_PCT = 5.0
WINDOW_MONTHS = (3, 6, 12)

# (document key, API metric) for the summary query. Order is the order of the KPI cards.
SUMMARY_METRICS: tuple[tuple[str, str], ...] = (
    ("users", "ym:s:users"),
    ("visits", "ym:s:visits"),
    ("pageviews", "ym:s:pageviews"),
    ("page_depth", "ym:s:pageDepth"),
    ("bounce_rate", "ym:s:bounceRate"),
    ("avg_visit_duration_seconds", "ym:s:avgVisitDurationSeconds"),
    ("new_visitors_pct", "ym:s:percentNewVisitors"),
)
# Share of visitors with two or three visits in the period. Requested on its own so a counter or
# API version that rejects it cannot take the core metrics down with it.
REPEAT_METRIC = ("repeat_visitors_2_3_pct", "ym:s:upTo3VisitsPerUserPercentage")
KPI_ORDER = (
    "users",
    "visits",
    "pageviews",
    "page_depth",
    "visits_per_day",
    "bounce_rate",
    "avg_visit_duration_seconds",
    "repeat_visitors_2_3_pct",
    "new_visitors_pct",
)
# A rate where a fall is the improvement; renderers colour its change accordingly.
LOWER_IS_BETTER = frozenset({"bounce_rate"})

# (block key, dimension or attribution role, default row limit, uses the traffic filter)
BREAKDOWNS: tuple[tuple[str, str, int | None, bool], ...] = (
    ("search_engines", "@engine", 10, True),
    ("cities", "ym:s:regionCity", 10, True),
    ("countries", "ym:s:regionCountry", 10, True),
    ("devices", "ym:s:deviceCategory", 10, True),
    ("age", "ym:s:ageInterval", 10, True),
    ("gender", "ym:s:gender", 10, True),
    ("landing_pages", "ym:s:startURL", None, True),
    ("search_phrases", "@phrase", None, True),
    ("channels", "@source", 20, False),
)

_COUNTER_RE = re.compile(r"^\d{1,12}$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AUTH_STATUSES = (401, 403)

# A function returning Search Console rows for the report period: fetch(start_date, end_date).
GscFetch = Callable[[str, str], dict[str, Any]]


class TrafficRequestError(ValueError):
    """The request cannot be collected as given; raised before any network call."""


# --- validation --------------------------------------------------------------


def parse_counter_ids(counter_id: Any) -> list[str]:
    """Return counter IDs as digit strings from an int, ``"1,2"`` text, or a list."""
    if isinstance(counter_id, int) and not isinstance(counter_id, bool):
        items = [str(counter_id)]
    elif isinstance(counter_id, str):
        items = [part.strip() for part in counter_id.split(",") if part.strip()]
    elif isinstance(counter_id, list | tuple):
        items = [str(part).strip() for part in counter_id if str(part).strip()]
    else:
        items = []
    if not items:
        raise TrafficRequestError("counter_id required: one Metrica counter ID or a list of IDs")
    bad = [item for item in items if not _COUNTER_RE.match(item)]
    if bad:
        raise TrafficRequestError(f"counter_id must contain only digits, got {bad!r}")
    return list(dict.fromkeys(items))


def parse_date(value: Any, name: str) -> date:
    if not isinstance(value, str) or not _ISO_RE.match(value):
        raise TrafficRequestError(f"{name} must be a YYYY-MM-DD date, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise TrafficRequestError(f"{name} is not a calendar date: {value!r}") from None


def validate_request(
    counter_id: Any,
    date1: Any,
    date2: Any,
    *,
    attribution: str = "last_significant",
    traffic: str = "organic",
    lang: str = "en",
    top: int = 15,
    filters: str | None = None,
) -> dict[str, Any]:
    """Validate and normalise every collection argument without touching the network."""
    ids = parse_counter_ids(counter_id)
    start = parse_date(date1, "date1")
    end = parse_date(date2, "date2")
    if start > end:
        raise TrafficRequestError(f"date1 must not be after date2: {date1} > {date2}")
    days = (end - start).days + 1
    if days > MAX_PERIOD_DAYS:
        raise TrafficRequestError(
            f"the period is {days} days; at most {MAX_PERIOD_DAYS} days are supported"
        )
    if attribution not in ATTRIBUTIONS:
        raise TrafficRequestError(f"attribution must be one of {sorted(ATTRIBUTIONS)}")
    if traffic not in TRAFFIC_SCOPES:
        raise TrafficRequestError(f"traffic must be one of {list(TRAFFIC_SCOPES)}")
    if lang not in LANGUAGES:
        raise TrafficRequestError(f"lang must be one of {list(LANGUAGES)}")
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= MAX_TOP:
        raise TrafficRequestError(f"top must be an integer from 1 to {MAX_TOP}")
    if filters is not None and (not isinstance(filters, str) or not filters.strip()):
        raise TrafficRequestError("filters must be a non-empty Metrica filter expression")
    return {
        "ids": ids,
        "start": start,
        "end": end,
        "days": days,
        "attribution": attribution,
        "traffic": traffic,
        "lang": lang,
        "top": top,
        "filters": filters.strip() if filters else None,
    }


# --- calendar helpers --------------------------------------------------------


def add_months(day: date, months: int) -> date:
    """Shift by whole months, clamping to the last day of a shorter month."""
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def comparison_periods(start: date, end: date) -> dict[str, tuple[date, date]]:
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    return {
        "current": (start, end),
        "previous": (previous_end - timedelta(days=days - 1), previous_end),
        "year_ago": (add_months(start, -12), add_months(end, -12)),
    }


def window_periods(end: date, months: int) -> dict[str, tuple[date, date]]:
    """A window of ``months`` ending on ``end`` and the window of equal months before it."""
    # Anchored on the day after ``end`` so a month-end date yields whole calendar months.
    current_start = add_months(end + timedelta(days=1), -months)
    previous_end = current_start - timedelta(days=1)
    previous_start = add_months(current_start, -months)
    return {"current": (current_start, end), "previous": (previous_start, previous_end)}


def _span(period: tuple[date, date]) -> dict[str, Any]:
    start, end = period
    return {"date1": start.isoformat(), "date2": end.isoformat(), "days": (end - start).days + 1}


# --- arithmetic shared by every block ---------------------------------------


def delta_pct(current: float | None, baseline: float | None) -> float | None:
    """Percentage change, or ``None`` when there is no usable baseline."""
    if current is None or baseline is None or baseline == 0:
        return None
    return round((current - baseline) / baseline * 100, 2)


def _delta(current: float | None, baseline: float | None, *, baseline_ok: bool = True) -> dict:
    if not baseline_ok:
        return {"delta_pct": None, "delta_status": "unavailable"}
    if baseline is None:
        return {"delta_pct": None, "delta_status": "unavailable"}
    if baseline == 0:
        return {"delta_pct": None, "delta_status": "new" if current else "no_baseline"}
    return {"delta_pct": delta_pct(current, baseline), "delta_status": "ok"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


# --- the collector ----------------------------------------------------------


class _Unavailable(Exception):
    """One block's query failed; carries the reason recorded in the document."""


class _Collector:
    def __init__(self, client: Any, request: dict[str, Any]):
        self.client = client
        self.request = request
        self.requests = 0
        self.failures: list[str] = []
        self.auth_error: str | None = None
        roles = ATTRIBUTIONS[request["attribution"]]
        self.roles = roles
        parts: list[str] = []
        if request["traffic"] == "organic":
            parts.append(f"{roles['source']}=='organic'")
        if request["filters"]:
            parts.append(f"({request['filters']})")
        self.traffic_filter = " AND ".join(parts) or None

    def dimension(self, spec: str) -> str:
        return self.roles[spec[1:]] if spec.startswith("@") else spec

    def query(
        self,
        period: tuple[date, date],
        metrics: list[str],
        *,
        dimension: str | None = None,
        filtered: bool = True,
        sort: str | None = None,
        limit: int = 1,
    ) -> dict[str, Any]:
        if self.auth_error:
            raise _Unavailable(self.auth_error)
        params: dict[str, Any] = {
            "ids": ",".join(self.request["ids"]),
            "date1": period[0].isoformat(),
            "date2": period[1].isoformat(),
            "metrics": ",".join(metrics),
            "lang": self.request["lang"],
        }
        if dimension:
            params["dimensions"] = dimension
        if filtered and self.traffic_filter:
            params["filters"] = self.traffic_filter
        if sort:
            params["sort"] = sort
        from seohead.data_sources.metrika import MetrikaError

        self.requests += 1
        try:
            body = self.client.report(params, limit=limit, offset=API_OFFSET)
        except MetrikaError as exc:
            reason = f"Metrica {exc.status}: {exc.message}"
            if exc.status in _AUTH_STATUSES:
                self.auth_error = reason
            self.failures.append(reason)
            raise _Unavailable(reason) from None
        if not isinstance(body, dict):
            raise _Unavailable("Metrica returned a malformed response")
        return body

    def totals(self, period: tuple[date, date], metrics: list[str], **kwargs: Any) -> list:
        body = self.query(period, metrics, **kwargs)
        totals = body.get("totals")
        if not isinstance(totals, list) or len(totals) != len(metrics):
            raise _Unavailable("Metrica returned no totals for this query")
        return [_number(value) for value in totals]

    def rows(
        self,
        period: tuple[date, date],
        dimension: str,
        *,
        filtered: bool,
        limit: int,
        sort: str = "-ym:s:visits",
    ) -> tuple[list[dict[str, Any]], float | None, bool]:
        """Return ``(rows, total, complete)`` for a one-dimension visits breakdown."""
        body = self.query(
            period, ["ym:s:visits"], dimension=dimension, filtered=filtered, sort=sort, limit=limit
        )
        data = body.get("data")
        if not isinstance(data, list):
            raise _Unavailable("Metrica returned a malformed response")
        rows = []
        for item in data:
            dimensions = (item or {}).get("dimensions") or [{}]
            metrics = (item or {}).get("metrics") or [None]
            first = dimensions[0] if isinstance(dimensions[0], dict) else {"name": dimensions[0]}
            name = first.get("name")
            row_id = first.get("id")
            rows.append(
                {
                    "key": str(row_id if row_id not in (None, "") else name),
                    "id": None if row_id is None else str(row_id),
                    "name": None if name is None else str(name),
                    "visits": _number(metrics[0]) or 0.0,
                }
            )
        totals = body.get("totals")
        total = _number(totals[0]) if isinstance(totals, list) and totals else None
        total_rows = body.get("total_rows")
        complete = not isinstance(total_rows, int) or total_rows <= len(rows)
        return rows, total, complete


def _safe(fn: Callable[[], Any]) -> tuple[Any, str | None]:
    try:
        return fn(), None
    except _Unavailable as exc:
        return None, str(exc)


# --- blocks ------------------------------------------------------------------


def _summary(collector: _Collector, periods: dict[str, tuple[date, date]]) -> dict[str, Any]:
    keys = [key for key, _ in SUMMARY_METRICS]
    api = [metric for _, metric in SUMMARY_METRICS]
    values: dict[str, dict[str, float | None]] = {}
    errors: dict[str, str | None] = {}
    repeat_errors: dict[str, str | None] = {}
    for name, period in periods.items():
        totals, error = _safe(lambda period=period: collector.totals(period, api))
        errors[name] = error
        values[name] = dict(zip(keys, totals, strict=True)) if totals else {}
        repeat, repeat_error = _safe(
            lambda period=period: collector.totals(period, [REPEAT_METRIC[1]])
        )
        values[name][REPEAT_METRIC[0]] = repeat[0] if repeat else None
        repeat_errors[name] = repeat_error
        visits = values[name].get("visits")
        days = (period[1] - period[0]).days + 1
        values[name]["visits_per_day"] = None if visits is None else visits / days

    if errors["current"]:
        return {"status": "unavailable", "reason": errors["current"], "metrics": {}}

    metrics: dict[str, Any] = {}
    for key in KPI_ORDER:
        current = values["current"].get(key)
        entry: dict[str, Any] = {
            "value": _round(current),
            "lower_is_better": key in LOWER_IS_BETTER,
        }
        if key == REPEAT_METRIC[0]:
            entry["api_metric"] = REPEAT_METRIC[1]
            entry["definition"] = "share of visitors with two or three visits in the period"
            if current is None:
                entry.update(
                    {
                        "status": "unavailable",
                        "reason": repeat_errors["current"] or "Metrica returned no value",
                    }
                )
                metrics[key] = entry
                continue
        elif key == "visits_per_day":
            entry["definition"] = "visits divided by the days in the period"
        else:
            entry["api_metric"] = dict(SUMMARY_METRICS)[key]
        entry["status"] = "ok"
        for name, label in (("previous", "previous"), ("year_ago", "year_ago")):
            baseline = values[name].get(key)
            entry[label] = _round(baseline)
            change = _delta(current, baseline, baseline_ok=not errors[name])
            entry[f"{label}_delta_pct"] = change["delta_pct"]
            entry[f"{label}_delta_status"] = change["delta_status"]
        metrics[key] = entry
    return {"status": "ok", "metrics": metrics}


def _robots(collector: _Collector, period: tuple[date, date]) -> dict[str, Any]:
    totals, error = _safe(lambda: collector.totals(period, ["ym:s:robotPercentage"]))
    if error:
        return {"status": "unavailable", "reason": error}
    share = totals[0]
    return {
        "status": "ok",
        "robot_pct": _round(share),
        "threshold_pct": ROBOT_WARNING_PCT,
        "note": (
            "robot visits are excluded from Reporting API data by default; the share is "
            "measured within that same view"
        ),
    }


def _breakdown(
    collector: _Collector,
    periods: dict[str, tuple[date, date]],
    spec: str,
    limit: int,
    filtered: bool,
) -> dict[str, Any]:
    dimension = collector.dimension(spec)
    current, error = _safe(
        lambda: collector.rows(periods["current"], dimension, filtered=filtered, limit=limit)
    )
    block: dict[str, Any] = {"dimension": dimension, "traffic_filter_applied": filtered}
    if error:
        return {"status": "unavailable", "reason": error, **block}
    rows, total, _ = current
    if not rows:
        return {
            "status": "unavailable",
            "reason": "Metrica returned no rows for this period and filter",
            **block,
        }
    previous, previous_error = _safe(
        lambda: collector.rows(
            periods["previous"], dimension, filtered=filtered, limit=LOOKUP_LIMIT
        )
    )
    baseline: dict[str, float] = {}
    complete = False
    previous_total = None
    if previous:
        previous_rows, previous_total, complete = previous
        baseline = {row["key"]: row["visits"] for row in previous_rows}
    out_rows = []
    for row in rows:
        if previous_error:
            change = {"delta_pct": None, "delta_status": "unavailable"}
            prior = None
        elif row["key"] in baseline:
            prior = baseline[row["key"]]
            change = _delta(row["visits"], prior)
        elif complete:
            prior = 0.0
            change = {"delta_pct": None, "delta_status": "new"}
        else:
            prior = None
            change = {"delta_pct": None, "delta_status": "unknown"}
        share = None if not total else round(row["visits"] / total * 100, 2)
        out_rows.append({**row, "share_pct": share, "previous": prior, **change})
    shown = sum(row["visits"] for row in rows)
    other = None if total is None else max(total - shown, 0.0)
    total_change = _delta(total, previous_total, baseline_ok=not previous_error)
    result = {
        "status": "ok",
        **block,
        "rows": out_rows,
        "total": total,
        "other": {
            "visits": other,
            "share_pct": None if not total or other is None else round(other / total * 100, 2),
        },
        "previous_total": previous_total,
        "total_delta_pct": total_change["delta_pct"],
        "total_delta_status": total_change["delta_status"],
        "share_basis": "visits with a determined value for this dimension",
    }
    if previous_error:
        result["comparison_reason"] = previous_error
    elif not complete:
        result["comparison_note"] = (
            f"the previous period has more than {LOOKUP_LIMIT} rows; a row outside them has "
            "an unknown baseline"
        )
    return result


def _series(
    collector: _Collector, period: tuple[date, date], granularity: str
) -> list[dict[str, Any]]:
    """Visits per day or per month, with gaps filled as zero visits inside a fetched period."""
    dimension = "ym:s:date" if granularity == "day" else "ym:s:startOfMonth"
    rows, _, _ = collector.rows(
        period, dimension, filtered=True, limit=LOOKUP_LIMIT, sort=dimension
    )
    by_label = {row["name"]: row["visits"] for row in rows if row["name"]}
    labels: list[str] = []
    if granularity == "day":
        day = period[0]
        while day <= period[1]:
            labels.append(day.isoformat())
            day += timedelta(days=1)
    else:
        month = period[0].replace(day=1)
        while month <= period[1]:
            labels.append(month.isoformat())
            month = add_months(month, 1)
    return [{"label": label, "visits": by_label.get(label, 0.0)} for label in labels]


def _daily(collector: _Collector, periods: dict[str, tuple[date, date]]) -> dict[str, Any]:
    series: dict[str, Any] = {}
    reasons: dict[str, str] = {}
    for name, period in periods.items():
        values, error = _safe(lambda period=period: _series(collector, period, "day"))
        series[name] = values
        if error:
            reasons[name] = error
    if series["current"] is None:
        return {"status": "unavailable", "reason": reasons["current"]}
    points = []
    for index, point in enumerate(series["current"]):
        entry = {"date": point["label"], "visits": point["visits"]}
        for name in ("previous", "year_ago"):
            other = series[name]
            matched = other[index] if other and index < len(other) else None
            entry[f"{name}_date"] = matched["label"] if matched else None
            entry[name] = matched["visits"] if matched else None
        points.append(entry)
    block: dict[str, Any] = {"status": "ok", "points": points}
    unavailable = {name: reason for name, reason in reasons.items() if name != "current"}
    if unavailable:
        block["comparison_reasons"] = unavailable
    return block


def _windows(collector: _Collector, end: date) -> dict[str, Any]:
    items = []
    api = ["ym:s:visits", "ym:s:users", "ym:s:pageviews"]
    for months in WINDOW_MONTHS:
        periods = window_periods(end, months)
        granularity = "month" if months >= 12 else "day"
        item: dict[str, Any] = {"months": months, "granularity": granularity}
        totals: dict[str, list | None] = {}
        reasons: dict[str, str] = {}
        series: dict[str, list | None] = {}
        for name, period in periods.items():
            item[name] = _span(period)
            values, error = _safe(lambda period=period: collector.totals(period, api))
            totals[name] = values
            if error:
                reasons[name] = error
            points, series_error = _safe(
                lambda period=period, granularity=granularity: _series(
                    collector, period, granularity
                )
            )
            series[name] = points
            if series_error and name not in reasons:
                reasons[name] = series_error
        if totals["current"] is None:
            item.update({"status": "unavailable", "reason": reasons.get("current")})
            items.append(item)
            continue
        metrics = {}
        for index, key in enumerate(("visits", "users", "pageviews")):
            current = totals["current"][index]
            previous = totals["previous"][index] if totals["previous"] else None
            change = _delta(current, previous, baseline_ok=totals["previous"] is not None)
            metrics[key] = {"value": current, "previous": previous, **change}
        item.update(
            {
                "status": "ok",
                "metrics": metrics,
                "series": {"current": series["current"], "previous": series["previous"]},
            }
        )
        if "previous" in reasons:
            item["comparison_reason"] = reasons["previous"]
        items.append(item)
    statuses = {item["status"] for item in items}
    if statuses == {"unavailable"}:
        return {"status": "unavailable", "reason": items[0].get("reason"), "items": items}
    return {"status": "ok", "items": items}


def normalize_gsc_rows(rows: Any) -> list[dict[str, Any]]:
    """Accept Search Console rows as ``{"keys": [query], ...}`` or ``{"query": ...}`` records."""
    if not isinstance(rows, list):
        raise TrafficRequestError("gsc_rows must be a list of Search Console rows")
    out = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TrafficRequestError(f"gsc_rows[{index}] must be an object")
        query = row.get("query")
        if query is None and isinstance(row.get("keys"), list) and row["keys"]:
            query = row["keys"][0]
        if not isinstance(query, str):
            raise TrafficRequestError(f"gsc_rows[{index}] has no query text")
        values = {}
        for field in ("clicks", "impressions", "ctr", "position"):
            value = row.get(field)
            if value is not None and _number(value) is None:
                raise TrafficRequestError(f"gsc_rows[{index}].{field} must be a number")
            values[field] = _number(value)
        if values["ctr"] is None and values["clicks"] is not None and values["impressions"]:
            values["ctr"] = values["clicks"] / values["impressions"]
        out.append({"query": query, **values})
    out.sort(key=lambda row: (-(row["clicks"] or 0), -(row["impressions"] or 0), row["query"]))
    return out


def _gsc_block(
    gsc_rows: list[dict[str, Any]] | None,
    gsc_fetch: GscFetch | None,
    period: tuple[date, date],
) -> dict[str, Any]:
    if gsc_rows is not None:
        rows = gsc_rows
        source = "supplied rows"
        truncated = False
    elif gsc_fetch is not None:
        try:
            result = gsc_fetch(period[0].isoformat(), period[1].isoformat())
        except Exception as exc:  # an injected fetcher's failure is block data, not a crash
            return {"status": "unavailable", "reason": f"Search Console request failed: {exc}"}
        if not isinstance(result, dict) or result.get("ok") is not True:
            reason = (result or {}).get("error") if isinstance(result, dict) else None
            return {"status": "unavailable", "reason": reason or "Search Console returned no data"}
        try:
            rows = normalize_gsc_rows(result.get("rows") or [])
        except TrafficRequestError as exc:
            return {"status": "unavailable", "reason": f"malformed Search Console rows: {exc}"}
        source = "Search Console API"
        truncated = bool(result.get("truncated"))
    else:
        return {
            "status": "skipped",
            "reason": "no Search Console rows or property were supplied",
        }
    if not rows:
        return {"status": "unavailable", "reason": "Search Console returned no query rows"}
    totals = {
        "clicks": sum(row["clicks"] or 0 for row in rows),
        "impressions": sum(row["impressions"] or 0 for row in rows),
    }
    return {
        "status": "ok",
        "source": source,
        "rows": rows,
        "count": len(rows),
        "truncated": truncated,
        "totals": totals,
        "note": (
            "Search Console counts clicks in Google results; they are not Metrica visits "
            "and the two must not be added together"
        ),
    }


# --- public entry point ------------------------------------------------------


def build_traffic_document(
    counter_id: Any,
    date1: str,
    date2: str,
    *,
    client: Any,
    attribution: str = "last_significant",
    traffic: str = "organic",
    filters: str | None = None,
    lang: str = "en",
    top: int = 15,
    site_label: str | None = None,
    gsc_rows: list[dict[str, Any]] | None = None,
    gsc_fetch: GscFetch | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect the traffic document for one period from an injected Metrica client.

    ``client`` needs only ``report(params, *, limit, offset)`` returning the Reporting API's JSON
    body, which is what :class:`seohead.data_sources.metrika.MetrikaClient` provides. Invalid
    arguments raise :class:`TrafficRequestError` before the first request.
    """
    request = validate_request(
        counter_id,
        date1,
        date2,
        attribution=attribution,
        traffic=traffic,
        lang=lang,
        top=top,
        filters=filters,
    )
    supplied_gsc = normalize_gsc_rows(gsc_rows) if gsc_rows is not None else None
    periods = comparison_periods(request["start"], request["end"])
    collector = _Collector(client, request)

    blocks: dict[str, Any] = {"summary": _summary(collector, periods)}
    if collector.auth_error:
        return {
            "schema": SCHEMA,
            "ok": False,
            "error": collector.auth_error,
            "counter_ids": request["ids"],
            "requests": collector.requests,
        }
    robots = _robots(collector, periods["current"])
    blocks["daily"] = _daily(collector, periods)
    blocks["windows"] = _windows(collector, request["end"])
    for key, spec, limit, filtered in BREAKDOWNS:
        blocks[key] = _breakdown(collector, periods, spec, limit or request["top"], filtered)
    blocks["gsc_queries"] = _gsc_block(supplied_gsc, gsc_fetch, periods["current"])

    warnings = []
    if robots.get("status") == "ok" and (robots.get("robot_pct") or 0) >= ROBOT_WARNING_PCT:
        warnings.append(
            {
                "code": "robots_high",
                "value": robots["robot_pct"],
                "message": (
                    f"Metrica classified {robots['robot_pct']}% of visits as robots "
                    "(above the warning threshold); human traffic may be overstated elsewhere"
                ),
            }
        )
    visits = (blocks["summary"].get("metrics") or {}).get("visits", {}).get("value")
    if blocks["summary"]["status"] == "ok" and not visits:
        warnings.append(
            {
                "code": "no_visits",
                "message": "no visits matched the filter in this period; check the counter and dates",
            }
        )
    unavailable = sorted(
        key for key, block in blocks.items() if block.get("status") == "unavailable"
    )
    if unavailable:
        warnings.append(
            {
                "code": "blocks_unavailable",
                "blocks": unavailable,
                "message": "some report blocks could not be collected; each states its reason",
            }
        )
    ok = blocks["summary"]["status"] == "ok"
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": ok,
        "source": SOURCE,
        "generated_at": (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat(),
        "counter_ids": request["ids"],
        "site_label": site_label,
        "lang": request["lang"],
        "period": _span(periods["current"]),
        "comparisons": {
            "previous": _span(periods["previous"]),
            "year_ago": _span(periods["year_ago"]),
        },
        "attribution": request["attribution"],
        "traffic": request["traffic"],
        "methodology": {
            "attribution_dimensions": collector.roles,
            "traffic_filter": collector.traffic_filter,
            "api_offset": API_OFFSET,
            "robots": robots,
            "requests": collector.requests,
            "lookup_limit": LOOKUP_LIMIT,
        },
        "warnings": warnings,
        "blocks": blocks,
    }
    if not ok:
        document["error"] = blocks["summary"].get("reason") or "summary unavailable"
    return document
