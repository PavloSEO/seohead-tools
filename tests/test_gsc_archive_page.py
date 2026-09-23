"""Offline checks for the single-page Search Console archive primitive."""

import io
import json
import urllib.error

import pytest

from seohead.data_sources import gsc

TOKEN = "synthetic-secret-for-offline-tests"
DATES = {"start_date": "2025-05-01", "end_date": "2026-08-31"}


def fetch(body, **kwargs):
    raw = body if isinstance(body, str) else json.dumps(body)
    return gsc.search_analytics_page(
        "sc-domain:example.test",
        **DATES,
        token=TOKEN,
        transport=lambda *args: raw,
        **kwargs,
    )


@pytest.mark.parametrize("dimensions", [None, [], ["date", "page"]])
@pytest.mark.parametrize("search_type", ["web", "image", "video", "news", "discover", "googleNews"])
def test_exact_payload_and_fixed_endpoint(dimensions, search_type):
    calls = []
    groups = [
        {
            "groupType": "and",
            "filters": [{"dimension": "country", "operator": "equals", "expression": "usa"}],
        }
    ]

    def transport(method, url, payload, token):
        calls.append((method, url, payload, token))
        return '{"rows": [], "responseAggregationType": "byPage"}'

    result = gsc.search_analytics_page(
        "https://example.test/a?b=c",
        **DATES,
        dimensions=dimensions,
        search_type=search_type,
        aggregation_type="byPage",
        data_state="all",
        row_limit=123,
        start_row=25000,
        dimension_filter_groups=groups,
        token=TOKEN,
        transport=transport,
    )
    assert result["ok"] is True
    assert result["rows"] == []
    assert result["response_aggregation_type"] == "byPage"
    assert len(calls) == 1
    method, url, payload, token = calls[0]
    assert method == "POST"
    assert url == (
        "https://www.googleapis.com/webmasters/v3/sites/"
        "https%3A%2F%2Fexample.test%2Fa%3Fb%3Dc/searchAnalytics/query"
    )
    assert payload == {
        "startDate": DATES["start_date"],
        "endDate": DATES["end_date"],
        "dimensions": dimensions or [],
        "type": search_type,
        "aggregationType": "byPage",
        "dataState": "all",
        "rowLimit": 123,
        "startRow": 25000,
        "dimensionFilterGroups": groups,
    }
    assert token == TOKEN
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("body", [{}, {"rows": []}, {"responseAggregationType": "auto"}])
def test_valid_empty_response(body):
    result = fetch(body)
    assert result["ok"] is True
    assert result["rows"] == []
    assert result["metadata"] == {}
    assert result["error"] is None
    assert result["reason"] is None
    assert result["status"] == 200


@pytest.mark.parametrize("search_type", ["discover", "googleNews"])
def test_missing_optional_metrics_stay_null(search_type):
    result = fetch(
        {
            "rows": [{"keys": ["2026-08-01"], "clicks": 1, "impressions": 42}],
            "responseAggregationType": "byPage",
            "metadata": {"firstIncompleteDate": "2026-08-31"},
        },
        dimensions=["date"],
        search_type=search_type,
    )
    assert result["rows"] == [
        {
            "keys": ["2026-08-01"],
            "clicks": 1,
            "impressions": 42,
            "ctr": None,
            "position": None,
        }
    ]
    assert result["metadata"] == {"firstIncompleteDate": "2026-08-31"}


def test_totals_may_omit_keys_and_zero_is_preserved():
    result = fetch({"rows": [{"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}]})
    assert result["ok"] is True
    assert result["rows"] == [{"keys": [], "clicks": 0, "impressions": 0, "ctr": 0, "position": 0}]


@pytest.mark.parametrize(
    "body",
    [
        "not JSON",
        "null",
        "[]",
        "42",
        '"text"',
        {"rows": None},
        {"rows": {}},
        {"rows": [None]},
        {"rows": [{}]},
        {"rows": [{"clicks": True, "impressions": 1}]},
        {"rows": [{"clicks": 1, "impressions": "1"}]},
        {"rows": [{"clicks": 1, "impressions": -1}]},
        {"rows": [{"clicks": 1, "impressions": 1, "position": float("nan")}]},
        {"rows": [{"clicks": 1, "impressions": 1, "keys": ["unexpected"]}]},
        {"metadata": []},
        {"metadata": None},
        {"responseAggregationType": "unknown"},
    ],
)
def test_malformed_is_not_successful_empty(body):
    result = fetch(body)
    assert result["ok"] is False
    assert result["reason"] == "malformed_response"
    assert result["rows"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 403, "message": TOKEN}},
        {"error": None},
        {"errors": []},
    ],
)
def test_json_error_envelope_cannot_be_empty_success(body):
    result = fetch(body)
    assert result["ok"] is False
    assert result["reason"] == "api_error"
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"row_limit": 0},
        {"row_limit": 25001},
        {"row_limit": True},
        {"row_limit": 1.0},
        {"start_row": -1},
        {"start_row": 50001},
        {"start_row": False},
        {"start_row": 1.0},
        {"search_type": "books"},
        {"aggregation_type": "other"},
        {"data_state": "hourly_all"},
        {"search_type": "discover", "aggregation_type": "byProperty"},
        {"search_type": "googleNews", "aggregation_type": "byProperty"},
        {"dimensions": ["date", "date"]},
        {"dimensions": ["hour"]},
        {"dimensions": "date"},
        {"dimensions": [None]},
        {"dimensions": [{"date": True}]},
        {"dimension_filter_groups": "[]"},
        {"dimension_filter_groups": [None]},
        {"dimension_filter_groups": [{"groupType": "or", "filters": []}]},
        {"dimension_filter_groups": [{"filters": [], "url": "https://example.test"}]},
        {
            "dimension_filter_groups": [
                {"filters": [{"dimension": "page", "operator": "equals", "expression": []}]}
            ]
        },
        {
            "dimension_filter_groups": [
                {"filters": [{"dimension": "page", "operator": "unknown", "expression": "x"}]}
            ]
        },
    ],
)
def test_invalid_arguments_do_not_acquire_credentials_or_contact_network(monkeypatch, kwargs):
    def forbidden(*args):
        pytest.fail("Validation must happen before authentication or transport")

    monkeypatch.setattr(gsc, "_acquire_token", forbidden)
    result = gsc.search_analytics_page(
        "sc-domain:example.test", **DATES, transport=forbidden, **kwargs
    )
    assert result["ok"] is False
    assert result["reason"] == "invalid_argument"
    assert result["status"] is None


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-02-30", "2026-03-01"),
        ("2026-09-01", "2026-08-01"),
        ("today", "2026-08-01"),
        (None, "2026-08-01"),
    ],
)
def test_date_validation(start, end):
    result = gsc.search_analytics_page("sc-domain:example.test", start_date=start, end_date=end)
    assert result["reason"] == "invalid_argument"


@pytest.mark.parametrize("start_row", [0, 50000])
@pytest.mark.parametrize("row_limit", [1, 25000])
def test_inclusive_limits(start_row, row_limit):
    assert fetch({}, start_row=start_row, row_limit=row_limit)["ok"] is True


def test_403_quota_reason_and_redaction():
    def transport(*args):
        body = {
            "error": {
                "message": "Authorization: Bearer " + TOKEN,
                "errors": [{"reason": "quotaExceeded", "message": TOKEN}],
            }
        }
        raise urllib.error.HTTPError(
            "https://example.test/" + TOKEN,
            403,
            TOKEN,
            {"Authorization": TOKEN},
            io.BytesIO(json.dumps(body).encode()),
        )

    result = gsc.search_analytics_page(
        "sc-domain:example.test", **DATES, token=TOKEN, transport=transport
    )
    assert result["status"] == 403
    assert result["reason"] == "quotaExceeded"
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize(
    "exception", [urllib.error.URLError(TOKEN), TimeoutError(TOKEN), ValueError(TOKEN)]
)
def test_transport_errors_do_not_echo_token(exception):
    def transport(*args):
        raise exception

    result = gsc.search_analytics_page(
        "sc-domain:example.test", **DATES, token=TOKEN, transport=transport
    )
    assert result["reason"] == "transport_error"
    assert TOKEN not in json.dumps(result)


def test_auth_errors_do_not_echo_token(monkeypatch):
    monkeypatch.setattr(gsc, "_acquire_token", lambda value: (None, "Bearer " + TOKEN))
    result = gsc.search_analytics_page("sc-domain:example.test", **DATES)
    assert result["reason"] == "not_configured"
    assert TOKEN not in json.dumps(result)


def test_unknown_provider_reason_is_not_echoed():
    result = fetch({"error": {"code": 401, "errors": [{"reason": TOKEN}]}})
    assert result["status"] == 401
    assert result["reason"] == "api_error"
    assert TOKEN not in json.dumps(result)
