"""Offline tests for the Metrika traffic document, its HTML/PDF renderer, and their interfaces.

Every Metrika response comes from :class:`FakeMetrika`, a synthetic Reporting API that derives
each total from one deterministic daily curve, so a period total, a daily series and a breakdown
of the same period always agree. Counter ``100000000`` and ``example.com`` are synthetic.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from seohead import cli
from seohead.data_sources import metrika_traffic as core
from seohead.data_sources.metrika import MetrikaError
from seohead.mcp import handlers
from seohead.reports import chromium_pdf, svg_charts, traffic_dashboard

COUNTER = "100000000"
CURRENT = ("2026-09-01", "2026-09-30")

ENGINES = [
    ("yandex", "Yandex", 0.58),
    ("google", "Google", 0.37),
    ("bing", "Bing", 0.02),
    ("duckduckgo", "DuckDuckGo", 0.015),
    ("mail_ru", "Mail.ru", 0.01),
    ("other", "Other search engine", 0.005),
]
CITIES = [
    ("101", "Northport", 0.31),
    ("102", "Eastvale", 0.12),
    ("103", "Lakeside", 0.08),
    ("104", "Riverton", 0.07),
    ("105", "Hillcrest", 0.06),
    ("106", "Westfield", 0.05),
    ("107", "Fairview", 0.045),
    ("108", "Brookdale", 0.04),
    ("109", "Cedar Falls", 0.03),
    ("110", "Pinecrest", 0.025),
    ("111", "Oakridge", 0.02),
    ("112", "Maple Grove", 0.015),
]
COUNTRIES = [
    ("901", "Country A", 0.62),
    ("902", "Country B", 0.27),
    ("903", "Country C", 0.05),
    ("904", "Country D", 0.02),
    ("905", "Country E", 0.01),
    ("906", "Country F", 0.008),
]
DEVICES = [
    ("mobile", "Smartphones", 0.58),
    ("desktop", "PC", 0.40),
    ("tablet", "Tablets", 0.015),
    ("tv", "TV", 0.005),
]
AGE = [
    ("25", "25-34", 0.27),
    ("35", "35-44", 0.24),
    ("45", "45-54", 0.175),
    ("55", "55+", 0.134),
    ("18", "18-24", 0.11),
    ("17", "under 18", 0.02),
]
GENDER = [("female", "female", 0.66), ("male", "male", 0.34)]
CHANNELS = [
    ("organic", "Search engine traffic", 0.72),
    ("direct", "Direct traffic", 0.11),
    ("email", "Mailing traffic", 0.087),
    ("referral", "Link traffic", 0.026),
    ("ad", "Ad traffic", 0.019),
    ("social", "Social network traffic", 0.019),
    ("internal", "Internal traffic", 0.006),
    ("recommend", "Recommendation system traffic", 0.0013),
    ("messenger", "Messenger traffic", 0.0004),
]
PAGES = [
    (None, f"https://example.com/{path}", weight)
    for path, weight in (
        ("guide/choosing-a-plan/", 0.044),
        ("glossary/rate/", 0.023),
        ("guide/writing-a-cover-letter/", 0.022),
        ("glossary/allowance/", 0.0217),
        ("glossary/service-contract/", 0.0193),
        ("glossary/syllabus/", 0.0185),
        ("glossary/overtime-pay/", 0.0179),
        ("guide/side-income/", 0.016),
        ("glossary/career-ladder/", 0.0155),
        ("guide/languages-on-a-resume/", 0.0148),
        ("", 0.0147),
        ("glossary/unused-leave-compensation/", 0.0113),
        ("glossary/income-tax/", 0.0112),
        ("glossary/freelance-platform/", 0.0099),
        ("glossary/advance-payment/", 0.0097),
        ("glossary/shift-schedule/", 0.0095),
        ("glossary/notice-period/", 0.009),
    )
]
PHRASES = [
    (None, phrase, weight)
    for phrase, weight in (
        ("what is a career ladder", 0.0128),
        ("syllabus", 0.0049),
        ("what is a freelance platform", 0.0041),
        ("service contract", 0.0038),
        ("mental health", 0.0022),
        ("combination resume", 0.0019),
        ("interview dress code", 0.0016),
        ("overtime pay rules", 0.0016),
        ("employer reviews", 0.0013),
        ("resume template word", 0.0013),
        ("headcount optimization", 0.0013),
        ("internal work rules", 0.0011),
        ("languages on a resume", 0.001),
        ("salary", 0.001),
        ("notice period on resignation", 0.001),
        ("advance payment", 0.0009),
    )
]
BREAKDOWNS = {
    "ym:s:lastSignSearchEngineRoot": ENGINES,
    "ym:s:lastSearchEngineRoot": ENGINES,
    "ym:s:regionCity": CITIES,
    "ym:s:regionCountry": COUNTRIES,
    "ym:s:deviceCategory": DEVICES,
    "ym:s:ageInterval": AGE,
    "ym:s:gender": GENDER,
    "ym:s:startURL": PAGES,
    "ym:s:lastSignSearchPhrase": PHRASES,
    "ym:s:lastSearchPhrase": PHRASES,
    "ym:s:lastSignTrafficSource": CHANNELS,
    "ym:s:lastTrafficSource": CHANNELS,
}
# Determined-value coverage: Metrika leaves visits without a city/age out of a breakdown total.
COVERAGE = {"ym:s:regionCity": 0.93, "ym:s:ageInterval": 0.55, "ym:s:gender": 0.57}


def daily_organic(day: date) -> float:
    """Synthetic organic visits for one day: growth, a weekly rhythm, and a wobble."""
    months = (day.year - 2025) * 12 + day.month - 6 + day.day / 31
    growth = 210 * 1.17 ** max(months - 15, -30) if months > 0 else 0.0
    weekly = (1.0, 1.04, 1.02, 0.98, 0.9, 0.55, 0.6)[day.weekday()]
    wobble = 1 + 0.12 * math.sin(day.toordinal() / 3.1)
    return round(growth * weekly * wobble)


class FakeMetrika:
    """Synthetic Reporting API: ``report(params, *, limit, offset)`` like ``MetrikaClient``."""

    def __init__(self, *, scale: float = 1.0, fail: dict | None = None, empty: bool = False):
        self.calls: list[dict] = []
        self.scale = scale
        self.fail = fail or {}
        self.empty = empty

    def _period_visits(self, params: dict) -> float:
        if self.empty:
            return 0.0
        start = date.fromisoformat(params["date1"])
        end = date.fromisoformat(params["date2"])
        total = 0.0
        day = start
        while day <= end:
            total += daily_organic(day)
            day += timedelta(days=1)
        organic = "organic" in (params.get("filters") or "")
        return total * self.scale * (1.0 if organic else 1 / 0.72)

    def report(self, params: dict, *, limit: int = 100, offset: int = 0, paginate: bool = False):
        self.calls.append({"params": dict(params), "limit": limit, "offset": offset})
        if offset != 1:
            raise MetrikaError(400, "offset must be at least 1")
        for needle, error in self.fail.items():
            if needle in params.get("metrics", "") or needle in params.get("dimensions", ""):
                raise error
        dimension = params.get("dimensions")
        metrics = params["metrics"].split(",")
        visits = self._period_visits(params)
        if not dimension:
            return {"totals": [self._metric(m, visits) for m in metrics], "data": [{}]}
        if dimension in ("ym:s:date", "ym:s:startOfMonth"):
            return self._series(params, dimension)
        catalogue = BREAKDOWNS[dimension]
        organic_only = "organic" in (params.get("filters") or "")
        if dimension in ("ym:s:lastSignTrafficSource", "ym:s:lastTrafficSource") and organic_only:
            catalogue = [row for row in catalogue if row[0] == "organic"]
        base = visits * COVERAGE.get(dimension, 1.0)
        weights = sum(w for *_, w in catalogue)
        rows = []
        for row_id, name, weight in catalogue:
            value = round(base * weight / weights) if weights else 0
            if value <= 0:
                continue
            dimension_value = {"name": name} if row_id is None else {"id": row_id, "name": name}
            rows.append({"dimensions": [dimension_value], "metrics": [float(value)]})
        rows.sort(key=lambda r: -r["metrics"][0])
        total = sum(r["metrics"][0] for r in rows)
        return {"data": rows[:limit], "total_rows": len(rows), "totals": [total]}

    def _metric(self, metric: str, visits: float) -> float:
        fixed = {
            "ym:s:visits": visits,
            "ym:s:users": round(visits * 0.878),
            "ym:s:pageviews": round(visits * 1.317),
            "ym:s:pageDepth": 1.317 if visits else 0.0,
            "ym:s:bounceRate": 15.4 if visits else 0.0,
            "ym:s:avgVisitDurationSeconds": 69.5 if visits else 0.0,
            "ym:s:percentNewVisitors": 86.13 if visits else 0.0,
            "ym:s:upTo3VisitsPerUserPercentage": 8.74 if visits else 0.0,
            "ym:s:robotPercentage": 0.0,
        }
        return float(fixed[metric])

    def _series(self, params: dict, dimension: str) -> dict:
        start = date.fromisoformat(params["date1"])
        end = date.fromisoformat(params["date2"])
        buckets: dict[str, float] = {}
        day = start
        while day <= end:
            value = 0.0 if self.empty else daily_organic(day) * self.scale
            key = day.isoformat() if dimension == "ym:s:date" else day.replace(day=1).isoformat()
            buckets[key] = buckets.get(key, 0.0) + value
            day += timedelta(days=1)
        rows = [
            {"dimensions": [{"name": key}], "metrics": [value]}
            for key, value in sorted(buckets.items())
            if value > 0
        ]
        return {"data": rows, "total_rows": len(rows), "totals": [sum(buckets.values())]}


def build(client: FakeMetrika | None = None, **kwargs) -> dict:
    return core.build_traffic_document(
        kwargs.pop("counter_id", COUNTER),
        kwargs.pop("date1", CURRENT[0]),
        kwargs.pop("date2", CURRENT[1]),
        client=client or FakeMetrika(),
        **kwargs,
    )


# --- collection --------------------------------------------------------------


def test_document_has_every_block_and_kpi_with_comparisons():
    document = build(site_label="example.com")
    assert document["schema"] == core.SCHEMA
    assert document["ok"] is True
    assert document["period"] == {"date1": "2026-09-01", "date2": "2026-09-30", "days": 30}
    assert document["comparisons"]["previous"] == {
        "date1": "2026-08-02",
        "date2": "2026-08-31",
        "days": 30,
    }
    assert document["comparisons"]["year_ago"]["date1"] == "2025-09-01"
    blocks = document["blocks"]
    for key in (
        "summary",
        "daily",
        "windows",
        "search_engines",
        "cities",
        "countries",
        "devices",
        "age",
        "gender",
        "landing_pages",
        "search_phrases",
        "channels",
    ):
        assert blocks[key]["status"] == "ok", key
    assert blocks["gsc_queries"]["status"] == "skipped"
    assert blocks["gsc_queries"]["reason"]
    metrics = blocks["summary"]["metrics"]
    assert list(metrics) == list(core.KPI_ORDER)
    visits = metrics["visits"]
    assert visits["value"] > visits["previous"] > visits["year_ago"] > 0
    assert visits["previous_delta_pct"] == round(
        (visits["value"] - visits["previous"]) / visits["previous"] * 100, 2
    )
    assert metrics["visits_per_day"]["value"] == round(visits["value"] / 30, 2)
    assert metrics["bounce_rate"]["lower_is_better"] is True
    assert metrics["repeat_visitors_2_3_pct"]["api_metric"] == "ym:s:upTo3VisitsPerUserPercentage"


def test_every_reporting_request_uses_offset_one():
    client = FakeMetrika()
    document = build(client)
    assert client.calls, "the collector made no request"
    assert {call["offset"] for call in client.calls} == {1}
    assert document["methodology"]["api_offset"] == 1
    assert document["methodology"]["requests"] == len(client.calls)


def test_last_significant_attribution_is_the_default():
    client = FakeMetrika()
    document = build(client)
    dimensions = {call["params"].get("dimensions") for call in client.calls}
    assert "ym:s:lastSignSearchEngineRoot" in dimensions
    assert "ym:s:lastSignSearchPhrase" in dimensions
    assert "ym:s:lastSearchEngineRoot" not in dimensions
    filtered = [c["params"]["filters"] for c in client.calls if c["params"].get("filters")]
    assert set(filtered) == {"ym:s:lastSignTrafficSource=='organic'"}
    assert document["attribution"] == "last_significant"


def test_last_click_attribution_switches_every_attributed_dimension():
    client = FakeMetrika()
    document = build(client, attribution="last_click")
    dimensions = {call["params"].get("dimensions") for call in client.calls}
    assert {"ym:s:lastSearchEngineRoot", "ym:s:lastSearchPhrase", "ym:s:lastTrafficSource"} <= (
        dimensions
    )
    assert not any(d and "lastSign" in d for d in dimensions)
    assert document["methodology"]["traffic_filter"] == "ym:s:lastTrafficSource=='organic'"


def test_channels_cover_all_traffic_and_extra_filters_are_anded():
    client = FakeMetrika()
    document = build(client, filters="ym:s:isNewUser=='Yes'")
    channel_calls = [
        c for c in client.calls if c["params"].get("dimensions") == "ym:s:lastSignTrafficSource"
    ]
    assert channel_calls and all("filters" not in c["params"] for c in channel_calls)
    assert len(document["blocks"]["channels"]["rows"]) == len(CHANNELS)
    assert document["methodology"]["traffic_filter"] == (
        "ym:s:lastSignTrafficSource=='organic' AND (ym:s:isNewUser=='Yes')"
    )


def test_traffic_all_applies_no_source_filter():
    client = FakeMetrika()
    document = build(client, traffic="all")
    assert not any(c["params"].get("filters") for c in client.calls)
    assert document["methodology"]["traffic_filter"] is None


def test_breakdown_rows_carry_share_previous_and_delta():
    document = build()
    engines = document["blocks"]["search_engines"]
    first = engines["rows"][0]
    assert first["key"] == "yandex"
    assert first["share_pct"] == round(first["visits"] / engines["total"] * 100, 2)
    assert first["delta_status"] == "ok"
    assert first["delta_pct"] == core.delta_pct(first["visits"], first["previous"])
    cities = document["blocks"]["cities"]
    assert len(cities["rows"]) == 10
    assert cities["other"]["visits"] == cities["total"] - sum(r["visits"] for r in cities["rows"])
    assert cities["share_basis"]


def test_daily_points_align_the_comparison_periods_by_position():
    daily = build()["blocks"]["daily"]
    assert len(daily["points"]) == 30
    first = daily["points"][0]
    assert first["date"] == "2026-09-01"
    assert first["previous_date"] == "2026-08-02"
    assert first["year_ago_date"] == "2025-09-01"
    assert first["visits"] == daily_organic(date(2026, 9, 1))


def test_windows_compare_with_the_preceding_window():
    windows = build()["blocks"]["windows"]
    assert [item["months"] for item in windows["items"]] == [3, 6, 12]
    three, _, twelve = windows["items"]
    assert three["current"] == {"date1": "2026-07-01", "date2": "2026-09-30", "days": 92}
    assert three["previous"]["date2"] == "2026-06-30"
    assert three["granularity"] == "day" and twelve["granularity"] == "month"
    assert len(twelve["series"]["current"]) == 12
    assert three["metrics"]["visits"]["delta_status"] == "ok"


def test_empty_counter_is_reported_as_missing_not_as_zero_change():
    document = build(FakeMetrika(empty=True))
    blocks = document["blocks"]
    visits = blocks["summary"]["metrics"]["visits"]
    assert visits["value"] == 0
    assert visits["previous_delta_pct"] is None
    assert visits["previous_delta_status"] == "no_baseline"
    for key in ("search_engines", "cities", "landing_pages", "channels"):
        assert blocks[key]["status"] == "unavailable"
        assert "no rows" in blocks[key]["reason"]
    codes = {w["code"] for w in document["warnings"]}
    assert {"no_visits", "blocks_unavailable"} <= codes


def test_one_failed_breakdown_is_unavailable_and_the_rest_survive():
    client = FakeMetrika(fail={"ym:s:regionCity": MetrikaError(400, "synthetic rejection")})
    document = build(client)
    cities = document["blocks"]["cities"]
    assert cities["status"] == "unavailable"
    assert "synthetic rejection" in cities["reason"]
    assert document["blocks"]["countries"]["status"] == "ok"
    warning = next(w for w in document["warnings"] if w["code"] == "blocks_unavailable")
    assert warning["blocks"] == ["cities"]


def test_a_rejected_repeat_visit_metric_is_unavailable_with_its_reason():
    error = MetrikaError(400, "unknown metric")
    document = build(FakeMetrika(fail={"upTo3VisitsPerUserPercentage": error}))
    metrics = document["blocks"]["summary"]["metrics"]
    repeat = metrics["repeat_visitors_2_3_pct"]
    assert repeat["status"] == "unavailable"
    assert "unknown metric" in repeat["reason"]
    assert metrics["visits"]["status"] == "ok"


def test_an_authorization_failure_stops_collection_early():
    client = FakeMetrika(fail={"ym:s:users": MetrikaError(403, "invalid oauth_token")})
    document = build(client)
    assert document["ok"] is False
    assert "403" in document["error"]
    assert len(client.calls) <= 3


def test_a_high_robot_share_becomes_a_warning():
    class Robots(FakeMetrika):
        def _metric(self, metric, visits):
            return 12.5 if metric == "ym:s:robotPercentage" else super()._metric(metric, visits)

    document = build(Robots())
    assert document["methodology"]["robots"]["robot_pct"] == 12.5
    assert any(w["code"] == "robots_high" for w in document["warnings"])
    assert not any(w["code"] == "robots_high" for w in build()["warnings"])


def test_a_row_beyond_a_truncated_lookup_has_an_unknown_baseline():
    class Truncated(FakeMetrika):
        def report(self, params, *, limit=100, offset=0, paginate=False):
            body = super().report(params, limit=limit, offset=offset)
            if params.get("dimensions") == "ym:s:regionCity" and params["date1"] < "2026-09-01":
                body = dict(body, data=body["data"][:3], total_rows=5000)
            return body

    rows = build(Truncated())["blocks"]["cities"]["rows"]
    assert rows[0]["delta_status"] == "ok"
    assert rows[5]["delta_status"] == "unknown"
    assert rows[5]["delta_pct"] is None


def test_a_row_absent_from_a_complete_lookup_is_new():
    class Fresh(FakeMetrika):
        def report(self, params, *, limit=100, offset=0, paginate=False):
            body = super().report(params, limit=limit, offset=offset)
            if params.get("dimensions") == "ym:s:startURL" and params["date1"] < "2026-09-01":
                body = dict(body, data=body["data"][1:], total_rows=len(body["data"]) - 1)
            return body

    first = build(Fresh())["blocks"]["landing_pages"]["rows"][0]
    assert first["delta_status"] == "new"
    assert first["previous"] == 0.0


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"counter_id": "abc"}, "digits"),
        ({"counter_id": ""}, "counter_id required"),
        ({"date1": "30daysAgo"}, "YYYY-MM-DD"),
        ({"date1": "2026-02-30"}, "calendar date"),
        ({"date1": "2026-10-01", "date2": "2026-09-01"}, "after"),
        ({"date1": "2025-01-01", "date2": "2026-09-01"}, "at most"),
        ({"attribution": "first_click"}, "attribution"),
        ({"traffic": "paid"}, "traffic"),
        ({"lang": "de"}, "lang"),
        ({"top": 0}, "top"),
        ({"filters": "   "}, "filters"),
        ({"gsc_rows": [{"clicks": 1}]}, "query"),
    ],
)
def test_invalid_input_is_refused_before_any_request(kwargs, message):
    client = FakeMetrika()
    with pytest.raises(core.TrafficRequestError, match=message):
        build(client, **kwargs)
    assert client.calls == []


def test_counter_ids_accept_lists_and_numbers():
    assert core.parse_counter_ids([100000000, "100000001"]) == ["100000000", "100000001"]
    assert core.parse_counter_ids("100000000, 100000000") == ["100000000"]
    client = FakeMetrika()
    build(client, counter_id=[100000000, 100000001])
    assert {call["params"]["ids"] for call in client.calls} == {"100000000,100000001"}


def test_calendar_helpers_clamp_month_ends():
    assert core.add_months(date(2024, 2, 29), -12) == date(2023, 2, 28)
    assert core.add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    periods = core.comparison_periods(date(2024, 2, 1), date(2024, 2, 29))
    assert periods["year_ago"] == (date(2023, 2, 1), date(2023, 2, 28))
    assert periods["previous"] == (date(2024, 1, 3), date(2024, 1, 31))
    window = core.window_periods(date(2026, 9, 30), 6)
    assert window["current"] == (date(2026, 4, 1), date(2026, 9, 30))
    assert window["previous"] == (date(2025, 10, 1), date(2026, 3, 31))


def test_supplied_search_console_rows_become_a_sorted_block():
    rows = [
        {"keys": ["example query b"], "clicks": 3, "impressions": 90, "position": 7.2},
        {"query": "example query a", "clicks": 9, "impressions": 120, "ctr": 0.075, "position": 4},
    ]
    block = build(gsc_rows=rows)["blocks"]["gsc_queries"]
    assert block["status"] == "ok"
    assert [r["query"] for r in block["rows"]] == ["example query a", "example query b"]
    assert block["rows"][1]["ctr"] == pytest.approx(3 / 90)
    assert block["totals"] == {"clicks": 12.0, "impressions": 210.0}


def test_a_failing_search_console_fetcher_is_an_unavailable_block():
    def failing(start, end):
        return {"ok": False, "error": "not configured"}

    block = build(gsc_fetch=failing)["blocks"]["gsc_queries"]
    assert block == {"status": "unavailable", "reason": "not configured"}

    seen = []

    def working(start, end):
        seen.append((start, end))
        return {"ok": True, "rows": [{"keys": ["example"], "clicks": 1, "impressions": 2}]}

    assert build(gsc_fetch=working)["blocks"]["gsc_queries"]["status"] == "ok"
    assert seen == [CURRENT]


# --- rendering ---------------------------------------------------------------


@pytest.fixture(scope="module")
def document() -> dict:
    return build(site_label="example.com")


def test_rendered_html_is_static_and_self_contained(document):
    html = traffic_dashboard.render_html(document)
    lowered = html.lower()
    assert "<script" not in lowered
    assert " src=" not in lowered and "<link" not in lowered
    assert "@import" not in lowered and "url(" not in lowered
    assert "@page { size: 297mm 210mm" in html
    assert html.count('<section class="page') >= 12
    assert "Summary" in html and "Traffic channels" in html
    assert "<svg" in html


def test_rendered_html_formats_document_values(document):
    html = traffic_dashboard.render_html(document)
    visits = document["blocks"]["summary"]["metrics"]["visits"]
    assert f"{visits['value']:,.0f}" in html
    assert f"+{visits['previous_delta_pct']:.1f}%" in html
    assert "example.com/guide/choosing-a-plan/" in html


def test_unavailable_blocks_render_as_explicit_panels():
    client = FakeMetrika(fail={"ym:s:startURL": MetrikaError(400, "synthetic rejection")})
    html = traffic_dashboard.render_html(build(client))
    assert "Data unavailable" in html
    assert "synthetic rejection" in html


def test_russian_labels_and_number_format(document):
    html = traffic_dashboard.render_html(document, lang="ru")
    assert "Итоги" in html and "Каналы трафика" in html
    assert 'lang="ru"' in html
    visits = document["blocks"]["summary"]["metrics"]["visits"]["value"]
    assert f"{visits:,.0f}".replace(",", "\u00a0") in html


def test_brand_colours_and_name_are_applied(document, tmp_path):
    brand = {"name": "Example Co", "accent": "#B71C1C", "ink": "#0E2247", "logo_text": "EXAMPLE"}
    html = traffic_dashboard.render_html(document, brand=brand)
    assert "#B71C1C" in html and "#0E2247" in html and "EXAMPLE" in html
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(brand), encoding="utf-8")
    assert traffic_dashboard.load_brand(str(path)).accent == "#B71C1C"
    assert traffic_dashboard.load_brand(json.dumps(brand)).name == "Example Co"
    assert "#2F5BD3" in traffic_dashboard.render_html(document)


@pytest.mark.parametrize(
    "brand,message",
    [
        ({"accent": "red"}, "#RRGGBB"),
        ({"accent": "#B71C1C;}body{"}, "#RRGGBB"),
        ({"font_stack": "Arial; } * { color: red"}, "font_stack"),
        ({"colour": "#B71C1C"}, "unknown brand keys"),
        ("missing-brand-file.json", "not found"),
        ({"name": "x" * 81}, "80"),
    ],
)
def test_invalid_brand_is_refused(brand, message):
    with pytest.raises(ValueError, match=message):
        traffic_dashboard.load_brand(brand)


def test_renderer_refuses_other_documents():
    with pytest.raises(ValueError, match=r"not a seohead\.metrika-traffic/1"):
        traffic_dashboard.render_html({"schema": "seohead.site-audit/1"})


def test_search_console_rows_paginate_into_their_own_pages():
    rows = [
        {"query": f"example query {i}", "clicks": 100 - i, "impressions": 1000} for i in range(30)
    ]
    html = traffic_dashboard.render_html(build(gsc_rows=rows))
    assert html.count("Search Console queries</h2>") == 2
    assert "example query 29" in html


def test_bar_labels_thin_out_instead_of_overlapping():
    labels = [str(i) for i in range(90)]
    values = [10_000 + i * 37 for i in range(90)]
    svg = svg_charts.bar_chart(
        labels,
        [svg_charts.Series("v", values, "#2F5BD3")],
        width=600,
        height=200,
        fmt=lambda v: f"{v:,.0f}",
    )
    value_labels = svg.count('font-weight="600"')
    assert 0 < value_labels < 90
    assert "rect" in svg


def test_donut_skips_labels_on_slivers():
    svg = svg_charts.donut_chart(
        [97, 2, 1], ["#111111", "#222222", "#333333"], ["97%", "2%", "1%"], size=200
    )
    assert "97%" in svg and "2%" not in svg and "1%" not in svg


def test_line_chart_labels_peak_and_last_point():
    svg = svg_charts.line_chart(
        ["a", "b", "c", "d"],
        [svg_charts.Series("v", [5, 40, 12, 20], "#2F5BD3")],
        width=500,
        height=200,
        fmt=str,
    )
    assert ">40<" in svg and ">20<" in svg and ">5<" in svg


# --- PDF printing ------------------------------------------------------------


def test_browser_discovery_order():
    env = {"SEOHEAD_CHROME": "/opt/example/chrome"}
    found = chromium_pdf.find_browser(env=env, is_file=lambda p: True)
    assert found == {"path": str(Path("/opt/example/chrome")), "source": "SEOHEAD_CHROME"}
    missing = chromium_pdf.find_browser(env=env, is_file=lambda p: False)
    assert missing["path"] is None and "SEOHEAD_CHROME" in missing["reason"]
    linux = chromium_pdf.find_browser(
        env={}, platform="linux", is_file=lambda p: str(p) == str(Path("/usr/bin/chromium"))
    )
    assert linux["source"] == "standard location"
    on_path = chromium_pdf.find_browser(
        env={}, platform="linux", is_file=lambda p: False, which=lambda n: "/x/" + n
    )
    assert on_path["source"] == "PATH"
    none = chromium_pdf.find_browser(
        env={}, platform="linux", is_file=lambda p: False, which=lambda n: None
    )
    assert none["path"] is None and "SEOHEAD_CHROME" in none["reason"]


def test_browser_command_keeps_the_sandbox(tmp_path):
    command = chromium_pdf.browser_command(
        "chrome", tmp_path / "a.html", tmp_path / "a.pdf", tmp_path / "profile"
    )
    assert "--no-sandbox" not in command
    for flag in ("--headless=new", "--disable-gpu", "--no-pdf-header-footer"):
        assert flag in command
    assert any(arg.startswith("--user-data-dir=") for arg in command)
    assert command[-1].startswith("file:")


class _Browser:
    """A launched browser: ``poll`` answers ``code`` until it is stopped."""

    def __init__(self, code=None):
        self.code, self.returncode, self.stopped = code, code, False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.stopped, self.returncode = True, -15

    def wait(self, timeout=None):
        return self.returncode


def test_print_waits_for_a_pdf_written_after_the_command_returns(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<html></html>", encoding="utf-8")
    target = tmp_path / "report.pdf"
    state = {"command": None, "sleeps": 0}

    def launcher(command, **kwargs):
        state["command"] = command
        return _Browser(0)

    def sleep(_seconds):
        # The browser "finishes" writing only after the command has already returned.
        state["sleeps"] += 1
        partial = Path(state["command"][-2].split("=", 1)[1])
        if state["sleeps"] == 2:
            partial.write_bytes(b"%PDF-1.7\n" + b"x" * 2048)

    result = chromium_pdf.print_to_pdf(
        html, target, browser="chrome", launcher=launcher, sleep=sleep
    )
    assert result["status"] == "ok"
    assert target.read_bytes().startswith(b"%PDF-")
    assert not list(tmp_path.glob(".*partial*"))


def test_print_reports_failure_when_no_pdf_appears(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<html></html>", encoding="utf-8")

    def launcher(command, **kwargs):
        Path(command[-2].split("=", 1)[1]).write_bytes(b"<html>not a pdf</html>")
        return _Browser(0)

    result = chromium_pdf.print_to_pdf(
        html, tmp_path / "r.pdf", browser="chrome", launcher=launcher, sleep=lambda s: None
    )
    assert result["status"] == "failed" and "not a PDF" in result["reason"]
    assert not (tmp_path / "r.pdf").exists()

    timed = chromium_pdf.print_to_pdf(
        html,
        tmp_path / "t.pdf",
        browser="chrome",
        timeout=0.01,
        launcher=lambda command, **kw: _Browser(None),
        sleep=lambda s: None,
    )
    assert timed["status"] == "failed" and "timeout" in timed["reason"]

    crashed = chromium_pdf.print_to_pdf(
        html,
        tmp_path / "c.pdf",
        browser="chrome",
        launcher=lambda command, **kw: _Browser(1),
        sleep=lambda s: None,
    )
    assert crashed["status"] == "failed" and "exited without" in crashed["reason"]


def test_print_stops_a_browser_that_keeps_running_after_the_pdf(tmp_path):
    """Chrome on macOS writes the PDF and does not exit; the browser is stopped, not awaited."""
    html = tmp_path / "report.html"
    html.write_text("<html></html>", encoding="utf-8")
    browsers = []

    def launcher(command, **kwargs):
        Path(command[-2].split("=", 1)[1]).write_bytes(b"%PDF-1.7\n" + b"x" * 64)
        browsers.append(_Browser(None))
        return browsers[-1]

    result = chromium_pdf.print_to_pdf(
        html, tmp_path / "k.pdf", browser="chrome", launcher=launcher, sleep=lambda s: None
    )
    assert result["status"] == "ok" and browsers[0].stopped


# --- handler and CLI ---------------------------------------------------------


@pytest.fixture
def no_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("SEOHEAD_CHROME", str(tmp_path / "no-such-browser"))


@pytest.fixture
def fake_metrika(monkeypatch):
    from seohead.data_sources import metrika

    client = FakeMetrika()
    monkeypatch.setattr(metrika, "MetrikaClient", lambda *a, **k: client)
    return client


def test_handler_collects_and_renders_with_pdf_skipped_without_a_browser(
    tmp_path, no_browser, fake_metrika
):
    out = tmp_path / "report"
    result = handlers.metrika_traffic_pdf(
        counter_id=COUNTER, date1=CURRENT[0], date2=CURRENT[1], out_dir=str(out)
    )
    assert result["ok"] is True
    assert result["mode"] == "collect_and_render"
    assert result["pdf"]["status"] == "skipped"
    assert "SEOHEAD_CHROME" in result["pdf"]["reason"]
    assert result["files"]["pdf"] is None
    saved = json.loads((out / "metrika-traffic.json").read_text(encoding="utf-8"))
    assert saved["schema"] == core.SCHEMA
    assert (out / "metrika-traffic.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert sorted(p.name for p in out.iterdir()) == ["metrika-traffic.html", "metrika-traffic.json"]
    assert {call["offset"] for call in fake_metrika.calls} == {1}
    assert result["requests"] == len(fake_metrika.calls)


def test_handler_renders_an_existing_document_without_network(
    tmp_path, no_browser, monkeypatch, document
):
    from seohead.data_sources import metrika

    def forbidden(*args, **kwargs):
        raise AssertionError("render-only mode must not create a Metrika client")

    monkeypatch.setattr(metrika, "MetrikaClient", forbidden)
    source = tmp_path / "doc.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    result = handlers.metrika_traffic_pdf(
        document=str(source), out_dir=str(tmp_path / "out"), pdf=False, lang="ru"
    )
    assert result["ok"] is True and result["mode"] == "render"
    assert result["files"]["document"] is None
    assert result["pdf"] == {"status": "skipped", "reason": "pdf=false was requested"}
    assert "Итоги" in (tmp_path / "out" / "metrika-traffic.html").read_text(encoding="utf-8")


def test_handler_refuses_to_replace_files_unless_asked(tmp_path, no_browser, document):
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "metrika-traffic.html"
    existing.write_text("keep me", encoding="utf-8")
    refused = handlers.metrika_traffic_pdf(document=document, out_dir=str(out), pdf=False)
    assert refused["ok"] is False and refused["existing"] == [str(existing)]
    assert existing.read_text(encoding="utf-8") == "keep me"
    replaced = handlers.metrika_traffic_pdf(
        document=document, out_dir=str(out), pdf=False, overwrite=True
    )
    assert replaced["ok"] is True
    assert existing.read_text(encoding="utf-8") != "keep me"


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"counter_id": COUNTER, "date1": CURRENT[0], "date2": CURRENT[1]}, "out_dir required"),
        ({"counter_id": COUNTER, "date1": "2026-09", "date2": CURRENT[1], "out_dir": "x"}, "date1"),
        ({"counter_id": COUNTER, "document": {}, "out_dir": "x"}, "not both"),
        ({"document": {"schema": "other"}, "out_dir": "x"}, "not a seohead"),
        ({"document": "missing.json", "out_dir": "x"}, "not found"),
        ({"document": {}, "out_dir": "x", "brand": {"accent": "blue"}}, "#RRGGBB"),
        ({"document": {}, "out_dir": "x", "timeout": 0}, "timeout"),
    ],
)
def test_handler_validates_before_network_or_writes(tmp_path, monkeypatch, kwargs, message):
    from seohead.data_sources import metrika

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must happen before a Metrika client exists")

    monkeypatch.setattr(metrika, "MetrikaClient", forbidden)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=message):
        handlers.metrika_traffic_pdf(**kwargs)
    assert not (tmp_path / "x").exists()


def test_handler_reports_a_missing_token_as_a_result(tmp_path, monkeypatch):
    from seohead.data_sources import metrika
    from seohead.data_sources.credentials import MissingCredential

    def missing(*args, **kwargs):
        raise MissingCredential("Metrika token not configured")

    monkeypatch.setattr(metrika, "MetrikaClient", missing)
    result = handlers.metrika_traffic_pdf(
        counter_id=COUNTER, date1=CURRENT[0], date2=CURRENT[1], out_dir=str(tmp_path / "o")
    )
    assert result == {"ok": False, "error": "Metrika token not configured"}
    assert not (tmp_path / "o").exists()


def test_cli_renders_a_document_from_flags(tmp_path, no_browser, document, capsys):
    source = tmp_path / "doc.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    brand = tmp_path / "brand.json"
    brand.write_text(json.dumps({"name": "Example Co", "accent": "#B71C1C"}), encoding="utf-8")
    rc = cli.main(
        [
            "metrika-traffic-pdf",
            "--document",
            str(source),
            "--out-dir",
            str(tmp_path / "out"),
            "--brand",
            str(brand),
            "--no-pdf",
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "render"
    assert "#B71C1C" in (tmp_path / "out" / "metrika-traffic.html").read_text(encoding="utf-8")


def test_mcp_tool_declares_its_side_effects():
    pytest.importorskip("mcp")
    from seohead.mcp.mcp_server import build_server

    tools = {tool.name: tool for tool in build_server()._tool_manager.list_tools()}
    hints = tools["seo_metrika_traffic_pdf"].annotations
    assert hints.readOnlyHint is False
    assert hints.openWorldHint is True
    assert hints.destructiveHint is True
    assert hints.idempotentHint is False


def test_real_pdf_smoke(tmp_path, document):
    browser = chromium_pdf.find_browser()
    if not browser["path"]:
        pytest.skip(browser["reason"])
    html = tmp_path / "report.html"
    html.write_text(traffic_dashboard.render_html(document), encoding="utf-8")
    result = chromium_pdf.print_to_pdf(html, tmp_path / "report.pdf", timeout=180)
    assert result["status"] == "ok", result
    data = (tmp_path / "report.pdf").read_bytes()
    assert data.startswith(b"%PDF-")
    assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") >= 12
