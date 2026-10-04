import json

from seohead.data_sources import yandex_webmaster as wm


def test_url_query_route_uses_post_filters_and_keeps_rows_separate():
    calls = []

    def send(method, url, payload, token):
        calls.append((method, url, payload, token))
        if payload["text_indicator"] == "URL":
            return json.dumps(
                {
                    "text_indicator_to_statistics": [
                        {"text_indicator": {"value": "https://example.test/a"}}
                    ]
                }
            )
        return json.dumps(
            {
                "text_indicator_to_statistics": [
                    {
                        "text_indicator": {"value": "pump"},
                        "statistics": [{"date": "2026-10-01", "field": "IMPRESSIONS", "value": 4}],
                    }
                ]
            }
        )

    result = wm.url_queries(
        "https:example.test:443",
        url="https://example.test/a",
        token="synthetic",
        user_id="7",
        transport=send,
    )
    assert result["ok"] and result["rows"][0]["query"] == "pump"
    assert [call[0] for call in calls] == ["POST", "POST"]
    assert all("query-analytics/list" in call[1] for call in calls)
    assert calls[0][2]["filters"]["text_filters"][0]["operation"] == "TEXT_MATCH"


def test_url_query_failures_do_not_echo_token():
    result = wm.url_queries("h", token="canary", user_id="7", transport=lambda *_: "not json")
    assert result["ok"] is False and "canary" not in json.dumps(result)
