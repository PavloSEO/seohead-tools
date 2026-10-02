"""Yandex Metrica API client for traffic, goals, counter settings, and raw logs.

A crawl shows what a website **contains**, Wordstat shows the **demand** for it, and Metrica
shows what visitors **actually did**. Without analytics, a client report relies on assumptions:
a technically excellent page may receive no visits, while a weaker page may attract substantial
traffic. Metrica therefore completes the client-onboarding data model by joining analytics with
crawl evidence in the knowledge system.

The client includes four operational safeguards:

* retries inspect the real HTTP status and **honor the ``Retry-After`` header** on HTTP 429;
* a ``Query is too complicated`` refusal is retried in calendar-month slices with a backoff
  and, when a slice still refuses, at a sampled accuracy; the merged body states the slices
  and the accuracy actually used;
* ``offset``/``limit`` pagination has a row ceiling so an accidental query cannot download a
  million rows, plus an inter-page delay so thousands of sequential pages do not exhaust quota;
* exceptions carry the status and message returned by the API instead of a generic
  ``request failed`` string.

⚠️ **Privacy.** Logs API exports can contain raw ``ClientID`` values, which are visitor personal
data. Never commit these exports or include them in client reports. This downloader returns text
only; callers choose where to persist it, and that path must be covered by ``.gitignore``.
"""

from __future__ import annotations

import calendar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

from seohead.data_sources import spend
from seohead.data_sources.credentials import metrika_token

API_BASE = "https://api-metrika.yandex.net"
API_MANAGEMENT = "management/v1"
API_REPORTS = "stat/v1/data"
SOURCE = "metrika"

TIMEOUT = 30
RETRIES = 3
PAGE_PAUSE = 0.15  # Delay between automatically paginated requests.
ROW_CAP = 100_000  # Row ceiling that prevents unbounded downloads.
MAX_BACKOFF = 30.0

# "Query is too complicated" is a workload refusal, not a syntax error: the same query
# succeeds over a shorter period or a smaller sample. A refused period is retried in
# calendar-month slices; a slice that still refuses is resubmitted at a sampled accuracy.
COMPLEXITY_ATTEMPTS = 3  # Attempts per slice before the accuracy is degraded.
COMPLEXITY_PAUSE = 5.0  # Seconds between attempts; grows toward COMPLEXITY_PAUSE_MAX.
COMPLEXITY_PAUSE_MAX = 15.0
SAMPLED_ACCURACY = 0.1  # Last-resort sample share for a slice that stays too complex.

_DAYS_AGO_RE = re.compile(r"^(\d+)daysAgo$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPLEXITY_MARKERS = ("too complicated", "слишком сложн")


class MetrikaError(RuntimeError):
    """API error carrying the HTTP status and the service's own message."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Metrica {status}: {message}")
        self.status = status
        self.message = message


class MetrikaClient:
    def __init__(self, token: str | None = None):
        self.token = token or metrika_token()

    # --- transport ---------------------------------------------------------

    def _request(self, url: str, method: str = "GET", raw: bool = False) -> Any:
        """Request with retries for HTTP 429, 5xx, and network failures.

        Other failures raise immediately. The open-ended loop is intentional: every branch
        either retries, returns, or raises, so control cannot fall through the bottom.
        """
        attempt = 0
        while True:
            attempt += 1
            request = urllib.request.Request(
                url,
                method=method,
                headers={"Authorization": f"OAuth {self.token}", "Accept": "application/json"},
            )
            try:
                # The request URL is built from the fixed HTTPS provider base.
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # nosec B310
                    body = response.read().decode("utf-8", "replace")
                    return body if raw else json.loads(body)
            except urllib.error.HTTPError as exc:
                text = exc.read().decode("utf-8", "replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt <= RETRIES:
                    time.sleep(self._backoff(attempt, exc.headers.get("Retry-After")))
                    continue
                raise MetrikaError(exc.code, _api_message(text)) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt <= RETRIES:
                    time.sleep(self._backoff(attempt, None))
                    continue
                raise MetrikaError(0, f"network: {exc}") from None

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        """Calculate retry delay, preferring the service's ``Retry-After`` header."""
        if retry_after:
            try:
                seconds = float(retry_after)
                if seconds > 0:
                    return min(seconds, 60.0)
            except ValueError:
                pass
        return min(1.0 * 2 ** (attempt - 1), MAX_BACKOFF)

    @staticmethod
    def _url(path: str, params: dict[str, Any] | None = None) -> str:
        query = {k: str(v) for k, v in (params or {}).items() if v is not None and v != ""}
        return f"{API_BASE}/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")

    # --- counter configuration (Management API) ----------------------------

    def counters(self) -> list[dict]:
        """Return all counters visible to the token."""
        counters = (self._request(self._url(f"{API_MANAGEMENT}/counters")) or {}).get(
            "counters", []
        )
        spend.record(SOURCE, "management.counters", cost=1, unit="requests", items=len(counters))
        return counters

    def counter(self, counter_id: str | int) -> dict:
        body = self._request(self._url(f"{API_MANAGEMENT}/counter/{counter_id}"))
        spend.record(SOURCE, "management.counter", cost=1, unit="requests", items=1)
        return body

    def goals(self, counter_id: str | int) -> list[dict]:
        """Return configured goals; an empty list means "no goals", not a failed request."""
        body = self._request(self._url(f"{API_MANAGEMENT}/counter/{counter_id}/goals"))
        goals = (body or {}).get("goals", [])
        spend.record(SOURCE, "management.goals", cost=1, unit="requests", items=len(goals))
        return goals

    def filters(self, counter_id: str | int) -> dict:
        body = self._request(self._url(f"{API_MANAGEMENT}/counter/{counter_id}/filters"))
        filters = (body or {}).get("filters", [])
        spend.record(SOURCE, "management.filters", cost=1, unit="requests", items=len(filters))
        return body

    def operations(self, counter_id: str | int) -> dict:
        """Return data operations, such as URL-parameter removal, which can alter reports silently."""
        body = self._request(self._url(f"{API_MANAGEMENT}/counter/{counter_id}/operations"))
        operations = (body or {}).get("operations", [])
        spend.record(
            SOURCE, "management.operations", cost=1, unit="requests", items=len(operations)
        )
        return body

    # --- reports (Reporting API) -------------------------------------------

    def report(
        self, params: dict[str, Any], *, paginate: bool = False, limit: int = 100, offset: int = 1
    ) -> dict:
        """Request ``stat/v1/data``.

        ``offset`` is 1-based, as the Reporting API requires: ``offset=0`` is answered with 400.

        With ``paginate=True``, fetch all pages and combine rows up to :data:`ROW_CAP`. Without
        this ceiling, one grouping typo can accidentally request a million rows.

        A ``Query is too complicated`` refusal is a workload error, not a syntax error: the
        same query succeeds over a shorter period or a smaller sample. The period is then
        retried in calendar-month slices with a backoff, a slice that still refuses is
        resubmitted at :data:`SAMPLED_ACCURACY`, and the merged body states what happened via
        ``split`` and ``accuracy_used``. Summed slices keep additive metrics exact;
        distinct-visitor and ratio metrics become approximations, so a merged body that
        needed sampling is flagged ``sampled``.
        """
        base = {"accuracy": "full", **params}
        try:
            return self._fetch_report(base, paginate=paginate, limit=limit, offset=offset)
        except MetrikaError as exc:
            if not _is_too_complicated(exc):
                raise
            return self._split_report(
                base, paginate=paginate, limit=limit, offset=offset, cause=exc
            )

    def _fetch_report(
        self, base: dict[str, Any], *, paginate: bool, limit: int, offset: int
    ) -> dict:
        if not paginate:
            body = self._request(self._url(API_REPORTS, dict(base, limit=limit, offset=offset)))
            spend.record(
                SOURCE,
                "report",
                cost=1,
                unit="requests",
                items=len((body or {}).get("data") or []),
                extra={"metrics": base.get("metrics")},
            )
            return body

        page_size = min(max(limit, 100), 1000)
        first: dict | None = None
        rows: list = []
        cursor = offset
        pages = 0
        try:
            while True:
                page = self._request(
                    self._url(API_REPORTS, dict(base, limit=page_size, offset=cursor))
                )
                pages += 1
                if first is None:
                    first = page
                chunk = page.get("data") or []
                rows.extend(chunk)
                collected = offset - 1 + len(rows)
                # A response without ``data`` has nothing else to aggregate; stop cleanly.
                if len(chunk) < page_size:
                    break
                if collected >= ROW_CAP:
                    break
                total = (first or {}).get("total_rows")
                if total and collected >= total:
                    break
                cursor += page_size
                time.sleep(PAGE_PAUSE)
        except MetrikaError:
            # The page that raised still consumed a request against quota, even though it
            # never returned rows, so it counts alongside the pages that already succeeded.
            # Losing this entry would make an interrupted collection look like it never
            # touched the API at all, hiding exactly the usage a diagnosis needs.
            spend.record(
                SOURCE,
                "report.paginated",
                cost=pages + 1,
                unit="requests",
                items=len(rows),
                extra={"metrics": base.get("metrics"), "pages": pages, "outcome": "failed"},
            )
            raise

        spend.record(
            SOURCE,
            "report.paginated",
            cost=pages,
            unit="requests",
            items=len(rows),
            extra={"metrics": base.get("metrics"), "pages": pages},
        )
        result = dict(first or {})
        result["data"] = rows
        result["query"] = dict((first or {}).get("query", {}), limit=limit, offset=offset)
        result["capped"] = len(rows) >= ROW_CAP
        return result

    def _split_report(
        self,
        base: dict[str, Any],
        *,
        paginate: bool,
        limit: int,
        offset: int,
        cause: MetrikaError,
    ) -> dict:
        """Collect a refused period in calendar-month slices and merge the bodies."""
        period = _resolve_period(base.get("date1"), base.get("date2"))
        # An unresolvable or single-day period cannot be sliced: it is retried as-is and
        # only the accuracy ladder can still help.
        spans = _month_slices(*period) if period else [None]
        bodies: list[dict] = []
        slices: list[dict] = []
        for index, span in enumerate(spans):
            chunk = dict(base)
            if span is not None:
                chunk["date1"], chunk["date2"] = span[0].isoformat(), span[1].isoformat()
            body, accuracy = self._fetch_slice(chunk, limit=limit)
            bodies.append(body)
            slices.append(
                {
                    "date1": chunk.get("date1"),
                    "date2": chunk.get("date2"),
                    "accuracy": accuracy,
                }
            )
            if index + 1 < len(spans):
                time.sleep(PAGE_PAUSE)
        rows, totals = _merge_slice_rows(bodies)
        _sort_rows(rows, base.get("sort"), base.get("metrics"))
        accuracy = _accuracy_used([s["accuracy"] for s in slices])
        result = dict(bodies[0])
        result["data"] = rows if paginate else rows[offset - 1 : offset - 1 + limit]
        result["totals"] = totals
        result["total_rows"] = len(rows)
        result["query"] = dict(
            (bodies[0].get("query") or {}),
            date1=base.get("date1"),
            date2=base.get("date2"),
            limit=limit,
            offset=offset,
            accuracy=accuracy,
        )
        result["sampled"] = accuracy != "full" or any(b.get("sampled") for b in bodies)
        result["accuracy_used"] = accuracy
        result["capped"] = any(b.get("capped") for b in bodies) or len(rows) >= ROW_CAP
        shares = [
            b["sample_share"] for b in bodies if isinstance(b.get("sample_share"), int | float)
        ]
        if shares:
            result["sample_share"] = min(shares)
        elif isinstance(accuracy, int | float) and not isinstance(accuracy, bool):
            result["sample_share"] = float(accuracy)
        result["split"] = {
            "reason": cause.message,
            "periods": slices,
            "note": (
                "metrics are summed across the slices, so distinct-visitor and ratio "
                "metrics are approximations of the unsplit answer"
            ),
        }
        spend.record(
            SOURCE,
            "report.split",
            cost=0,
            unit="requests",
            items=len(rows),
            extra={
                "metrics": base.get("metrics"),
                "slices": len(slices),
                "accuracy": accuracy,
            },
        )
        return result

    def _fetch_slice(self, chunk: dict[str, Any], *, limit: int) -> tuple[dict, Any]:
        """Fetch one period slice, retrying the complexity error then degrading accuracy.

        Returns ``(body, accuracy)`` so the merged report can state the sampling actually
        used. The slice is always collected paginated: merging needs every row of the
        slice, not just the caller's window.
        """
        ladder = [chunk.get("accuracy", "full")]
        if ladder[0] == "full":
            ladder.append(SAMPLED_ACCURACY)
        last: MetrikaError | None = None
        for accuracy in ladder:
            for attempt in range(COMPLEXITY_ATTEMPTS):
                try:
                    return (
                        self._fetch_report(
                            dict(chunk, accuracy=accuracy),
                            paginate=True,
                            limit=limit,
                            offset=1,
                        ),
                        accuracy,
                    )
                except MetrikaError as exc:
                    if not _is_too_complicated(exc):
                        raise
                    last = exc
                    if attempt + 1 < COMPLEXITY_ATTEMPTS:
                        time.sleep(min(COMPLEXITY_PAUSE * (attempt + 1), COMPLEXITY_PAUSE_MAX))
        if last is not None:
            raise last
        raise MetrikaError(0, "report slice failed without an API error")

    def by_time(self, params: dict[str, Any], *, limit: int = 100, offset: int = 1) -> dict:
        """Return a time trend from ``stat/v1/data/bytime`` rather than a point-in-time slice."""
        body = self._request(
            self._url(
                f"{API_REPORTS}/bytime", dict(params, accuracy="full", limit=limit, offset=offset)
            )
        )
        spend.record(
            SOURCE,
            "report.bytime",
            cost=1,
            unit="requests",
            items=len((body or {}).get("data") or []),
        )
        return body

    # --- raw logs (Logs API) -----------------------------------------------

    def create_log_request(
        self, counter_id: str | int, source: str, date1: str, date2: str, fields: list[str]
    ) -> dict:
        """Request a raw-log export; ``source`` is either ``visits`` or ``hits``.

        ⚠️ Fields may include ``ym:s:clientID``, which is personal data. Never commit the result
        or expose it in a client-facing report.
        """
        spend.record(
            SOURCE,
            "logs.create",
            cost=1,
            unit="requests",
            items=len(fields),
            extra={"source": source, "period": f"{date1}..{date2}"},
        )
        body = self._request(
            self._url(
                f"{API_MANAGEMENT}/counter/{counter_id}/logrequests",
                {"source": source, "date1": date1, "date2": date2, "fields": ",".join(fields)},
            ),
            method="POST",
        )
        return (body or {}).get("log_request", body)

    def log_requests(self, counter_id: str | int) -> list[dict]:
        return (
            self._request(self._url(f"{API_MANAGEMENT}/counter/{counter_id}/logrequests")) or {}
        ).get("requests", [])

    def log_request(self, counter_id: str | int, request_id: int) -> dict:
        """Find one request by ID; the API provides no dedicated single-request endpoint."""
        for item in self.log_requests(counter_id):
            if item.get("request_id") == request_id:
                return item
        raise MetrikaError(404, f"log request {request_id} not found")

    def download_log_part(self, counter_id: str | int, request_id: int, part: int) -> str:
        """Download one completed export part as raw TSV text.

        The caller decides where to persist it, and that path must be covered by ``.gitignore``.
        """
        return self._request(
            self._url(
                f"{API_MANAGEMENT}/counter/{counter_id}/logrequest/{request_id}"
                f"/part/{part}/download"
            ),
            raw=True,
        )

    def cancel_log_request(self, counter_id: str | int, request_id: int) -> dict:
        """Cancel a request to release one of the limited Logs API request slots."""
        body = self._request(
            self._url(
                f"{API_MANAGEMENT}/counter/{counter_id}/logrequest/{request_id}/cancel",
                {"request_id": request_id},
            ),
            method="POST",
        )
        return (body or {}).get("log_request", body)


def _api_message(text: str) -> str:
    """Extract a readable API message from Metrica's ``message`` or ``errors`` fields."""
    try:
        body = json.loads(text)
    except ValueError:
        return text[:300] or "empty response"
    if isinstance(body, dict):
        if body.get("message"):
            return str(body["message"])
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("message") or first)
            return str(first)
    return text[:300]


def _is_too_complicated(exc: MetrikaError) -> bool:
    """Match the API's workload refusal in either response language."""
    message = (exc.message or "").lower()
    return any(marker in message for marker in _COMPLEXITY_MARKERS)


def _resolve_date(value: Any, today: date) -> date | None:
    """Map an absolute or relative API date onto a calendar day; ``None`` when unknown."""
    if not isinstance(value, str):
        return None
    if _ISO_DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    match = _DAYS_AGO_RE.match(value)
    return today - timedelta(days=int(match.group(1))) if match else None


def _resolve_period(
    date1: Any, date2: Any, *, today: date | None = None
) -> tuple[date, date] | None:
    """Resolve the requested period to calendar dates; ``None`` when it cannot be split."""
    today = today or date.today()
    start, end = _resolve_date(date1, today), _resolve_date(date2, today)
    if start is None or end is None or start >= end:
        return None
    return start, end


def _month_slices(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar-month-aligned slices covering ``start``..``end`` inclusive."""
    slices = []
    cursor = start
    while cursor <= end:
        month_end = date(
            cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]
        )
        slice_end = min(month_end, end)
        slices.append((cursor, slice_end))
        cursor = slice_end + timedelta(days=1)
    return slices


def _metric_value(row: dict, index: int) -> float:
    values = row.get("metrics") or []
    value = values[index] if index < len(values) else None
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _sum_metrics(left: list, right: list) -> list:
    out = []
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        numeric = (
            isinstance(a, int | float)
            and not isinstance(a, bool)
            and isinstance(b, int | float)
            and not isinstance(b, bool)
        )
        out.append(a + b if numeric else (a if b is None else b))
    return out


def _merge_slice_rows(bodies: list[dict]) -> tuple[list[dict], list | None]:
    """Sum the metrics of rows that share a dimension key across slices."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    totals: list | None = None
    for body in bodies:
        for row in body.get("data") or []:
            key = json.dumps(row.get("dimensions"), sort_keys=True, ensure_ascii=False)
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(row, metrics=list(row.get("metrics") or []))
                order.append(key)
            else:
                existing["metrics"] = _sum_metrics(existing["metrics"], row.get("metrics") or [])
        slice_totals = body.get("totals")
        if isinstance(slice_totals, list):
            totals = slice_totals if totals is None else _sum_metrics(totals, slice_totals)
    return [merged[key] for key in order], totals


def _sort_rows(rows: list[dict], sort: Any, metrics: Any) -> None:
    """Re-apply the caller's metric sort after merging; slices arrive sorted only locally."""
    names = [m.strip() for m in str(metrics or "").split(",") if m.strip()]
    tokens = [t.strip() for t in str(sort or "").split(",") if t.strip()]
    for token in reversed(tokens):
        descending = token.startswith("-")
        name = token.lstrip("-")
        if name not in names:
            continue
        index = names.index(name)
        rows.sort(key=lambda row, i=index: _metric_value(row, i), reverse=descending)


def _accuracy_used(accuracies: list) -> Any:
    """The least precise accuracy any slice needed — the honest bound for the whole period."""
    numeric = [a for a in accuracies if isinstance(a, int | float) and not isinstance(a, bool)]
    if numeric:
        return min(numeric)
    return "full" if all(a == "full" for a in accuracies) else accuracies[-1]


def rows_to_records(report: dict) -> list[dict]:
    """Flatten a report into ``{dimension: name, metric: number}`` records.

    Metrica returns dimensions and metrics as parallel arrays rather than pairs. Centralizing the
    mapping prevents a caller from shifting columns during one-off parsing.
    """
    query = report.get("query") or {}
    dimensions = [d.split(":")[-1] for d in (query.get("dimensions") or [])]
    metrics = [m.split(":")[-1] for m in (query.get("metrics") or [])]
    records = []
    for row in report.get("data") or []:
        record: dict[str, Any] = {}
        for index, dimension in enumerate(row.get("dimensions") or []):
            key = dimensions[index] if index < len(dimensions) else f"dimension_{index}"
            record[key] = dimension.get("name") if isinstance(dimension, dict) else dimension
        for index, value in enumerate(row.get("metrics") or []):
            key = metrics[index] if index < len(metrics) else f"metric_{index}"
            record[key] = value
        records.append(record)
    return records
