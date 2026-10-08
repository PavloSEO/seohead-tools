"""Bing collection preserves response rows and honest page coverage, offline."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from seohead.data_sources import bing_webmaster, credentials, providers

LIST_OPERATIONS = ["sites", "crawl", "keywords", "search_performance"]


def _collect(operation, data, **kwargs):
    return bing_webmaster.collect(
        operation,
        site_url="https://example.test/",
        api_key="synthetic-key-canary",
        transport=lambda _url: json.dumps({"d": data}),
        **kwargs,
    )


@pytest.mark.parametrize("operation", LIST_OPERATIONS)
@pytest.mark.parametrize("data", [None, False, 0, "synthetic-error-canary", {}, [None], [0]])
def test_list_operations_refuse_missing_scalar_and_malformed_rows(operation, data):
    result = _collect(operation, data)
    assert result["ok"] is False and result["state"] == "failed"
    assert "rows" not in result and "returned" not in result
    assert "canary" not in json.dumps(result)


@pytest.mark.parametrize("operation", LIST_OPERATIONS)
def test_empty_array_is_measured_zero_rows(operation):
    result = _collect(operation, [])
    assert result["ok"] is True and result["state"] == "complete"
    assert result["rows"] == result["data"] == []
    assert result["returned"] == 0 and result["truncated"] is False


@pytest.mark.parametrize("operation", LIST_OPERATIONS)
def test_saved_collection_and_public_envelope_count_the_same_rows(tmp_path, operation):
    rows = [
        {"Url": "https://example.test/a", "Count": 0},
        {"Url": "https://example.test/b", "Count": None},
    ]
    public = providers.provider_collect(
        "bing_webmaster",
        operation,
        {"site_url": "https://example.test/", "api_key": "synthetic-key-canary"},
        transport=lambda _url: json.dumps({"d": rows}),
        artifact_dir=tmp_path,
    )
    stored = json.loads(next(tmp_path.glob("provider-*.json")).read_text())
    assert stored["result"]["rows"] == stored["result"]["data"] == rows
    assert stored["result"]["returned"] == public["evidence"]["row_counts"]["returned"] == 2
    assert public["evidence"]["pagination"] == {"returned": 2, "truncated": False}
    assert public["result"] is None
    assert "example.test" not in json.dumps(public) and "canary" not in json.dumps(public)
    assert stored["result"]["rows"][0]["Count"] == 0
    assert stored["result"]["rows"][1]["Count"] is None


@pytest.mark.parametrize(
    "data,page",
    [
        (None, 0),
        ([], 0),
        ({}, 0),
        ({"Links": None, "TotalPages": 1}, 0),
        ({"Links": [None], "TotalPages": 1}, 0),
        ({"Links": [], "TotalPages": None}, 0),
        ({"Links": [], "TotalPages": True}, 0),
        ({"Links": [], "TotalPages": -1}, 0),
        ({"Links": [], "TotalPages": "1"}, 0),
        ({"Links": [{"Count": 0}], "TotalPages": 0}, 0),
        ({"Links": [], "TotalPages": 0}, 1),
        ({"Links": [], "TotalPages": 1}, 1),
    ],
)
def test_link_envelope_refuses_invalid_rows_and_inconsistent_pages(data, page):
    result = _collect("links", data, page=page)
    assert result["ok"] is False and result["state"] == "failed"
    assert "returned" not in result


@pytest.mark.parametrize("page,next_page", [(0, 1), (1, 2), (2, None)])
def test_one_link_page_never_claims_the_whole_population(tmp_path, page, next_page):
    data = {"Links": [{"Url": "https://example.test/a", "Count": 0}], "TotalPages": 3}
    urls = []

    def transport(url):
        urls.append(url)
        return json.dumps({"d": data})

    public = providers.provider_collect(
        "bing_webmaster",
        "links",
        {"site_url": "https://example.test/", "page": page, "api_key": "synthetic"},
        transport=transport,
        artifact_dir=tmp_path,
    )
    result = json.loads(next(tmp_path.glob("provider-*.json")).read_text())["result"]
    assert len(urls) == 1
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)["page"] == [str(page)]
    assert result["data"] == data and result["rows"] == data["Links"]
    assert result["page"] == page and result["total_pages"] == 3
    assert result["next_page"] == next_page
    assert result["state"] == public["evidence"]["status"] == "partial"
    assert public["evidence"]["complete"] is False
    assert public["evidence"]["pagination"] == {"returned": 1, "truncated": True}


@pytest.mark.parametrize("total_pages,rows", [(0, []), (1, []), (1, [{"Count": 0}])])
def test_complete_single_link_response_preserves_zero(total_pages, rows):
    result = _collect("links", {"Links": rows, "TotalPages": total_pages})
    assert result["ok"] is True and result["state"] == "complete"
    assert result["rows"] == rows and result["returned"] == len(rows)
    assert result["next_page"] is None and result["truncated"] is False


def test_empty_page_of_multi_page_links_is_partial_not_empty_population():
    result = _collect("links", {"Links": [], "TotalPages": 3})
    assert result["state"] == "partial" and result["returned"] == 0
    assert result["truncated"] is True and result["next_page"] == 1


def test_missing_credentials_do_not_call_transport(monkeypatch):
    def missing():
        raise credentials.MissingCredential("synthetic missing credential")

    monkeypatch.setattr(credentials, "bing_webmaster_key", missing)
    result = bing_webmaster.collect(
        "sites", site_url="", transport=lambda _url: pytest.fail("unexpected provider call")
    )
    assert result["state"] == "not_configured" and result["verified"] is False


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_quota_failures_are_safe_failed_evidence(status):
    def failed(url):
        raise urllib.error.HTTPError(url, status, "synthetic-key-canary", {}, io.BytesIO(b""))

    result = providers.provider_collect(
        "bing_webmaster",
        "sites",
        {"site_url": "", "api_key": "synthetic-key-canary"},
        transport=failed,
    )
    assert result["evidence"]["status"] == "failed"
    assert result["evidence"]["complete"] is False
    assert "canary" not in json.dumps(result)
