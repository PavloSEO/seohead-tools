"""Yandex Webmaster API v4 adapter: user id discovery, query strings and paging."""

import json

from seohead.data_sources import yandex_webmaster as wm


def _transport(pages):
    calls = []

    def send(method, url, payload, token):
        calls.append(url)
        if url.endswith("/user"):
            return json.dumps({"user_id": 42})
        return json.dumps(pages(url))

    return send, calls


def test_user_id_is_resolved_and_list_params_repeat():
    send, calls = _transport(lambda url: {"indicators": {}})
    result = wm.collect(
        "search_history",
        host_id="https:example.com:443",
        params={"query_indicator": ["TOTAL_SHOWS", "TOTAL_CLICKS"], "date_from": "2025-01-01"},
        token="t",
        transport=send,
    )
    assert result["ok"] and calls[0].endswith("/user")
    assert "/user/42/hosts/https:example.com:443/search-queries/all/history?" in calls[1]
    assert "query_indicator=TOTAL_SHOWS&query_indicator=TOTAL_CLICKS" in calls[1]


def test_search_queries_are_paged_until_a_short_page():
    def pages(url):
        offset = int(url.split("offset=")[1].split("&")[0])
        size = 500 if offset == 0 else 7
        return {"queries": [{"query_id": str(offset + i)} for i in range(size)], "count": 507}

    send, calls = _transport(pages)
    result = wm.collect(
        "search_performance", user_id="1", host_id="h", paginate=True, token="t", transport=send
    )
    assert result["returned"] == 507 and result["truncated"] is False
    assert "order_by=TOTAL_SHOWS" in calls[0] and len(calls) == 2


def test_paging_stops_at_the_row_ceiling():
    send, _ = _transport(lambda url: {"queries": [{}] * 500})
    result = wm.collect(
        "search_performance",
        user_id="1",
        host_id="h",
        paginate=True,
        max_rows=600,
        token="t",
        transport=send,
    )
    assert result["returned"] == 600 and result["truncated"] is True


def test_indexing_reads_the_real_v4_samples_endpoint():
    send, calls = _transport(lambda url: {"samples": [], "count": 0})
    result = wm.collect("indexing", user_id="1", host_id="h", token="t", transport=send)
    assert result["ok"] and "/hosts/h/indexing/samples" in calls[0]


def test_indexing_samples_page_at_the_documented_cap():
    def pages(url):
        offset = int(url.split("offset=")[1].split("&")[0])
        size = 100 if offset < 200 else 30
        return {"samples": [{"url": f"https://example.com/{offset + i}"} for i in range(size)]}

    send, calls = _transport(pages)
    result = wm.collect(
        "indexing", user_id="1", host_id="h", paginate=True, token="t", transport=send
    )
    assert result["returned"] == 230 and result["truncated"] is False
    assert "limit=100" in calls[0] and len(calls) == 3


def test_broken_links_samples_collect_the_links_list():
    def pages(url):
        offset = int(url.split("offset=")[1].split("&")[0])
        size = 100 if offset == 0 else 5
        return {
            "links": [
                {"destination_url": f"https://example.com/gone/{offset + i}"} for i in range(size)
            ],
            "count": 105,
        }

    send, calls = _transport(pages)
    result = wm.collect(
        "broken_links_samples", user_id="1", host_id="h", paginate=True, token="t", transport=send
    )
    assert result["ok"] and "/hosts/h/links/internal/broken/samples" in calls[0]
    assert result["returned"] == 105 and len(result["data"]["links"]) == 105


def test_host_operation_without_host_is_refused():
    try:
        wm.collect("summary", user_id="1", token="t", transport=lambda *a: "{}")
    except ValueError as exc:
        assert "host_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_query_history_puts_the_query_id_in_the_path():
    send, calls = _transport(lambda url: {"indicators": {}})
    wm.collect("query_history", user_id="1", host_id="h", query_id="a/b", token="t", transport=send)
    assert calls[0].endswith("/user/1/hosts/h/search-queries/a%2Fb/history")
