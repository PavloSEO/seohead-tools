"""A visited-page view integration must preserve its source grain and safety."""

from __future__ import annotations

import csv
import io
import json
import urllib.error

import pytest

from seohead.data_sources import ga4, providers
from seohead.data_sources.evidence_import import normalize_file
from seohead.data_sources.evidence_join_store import write as write_join
from seohead.data_sources.ga4_content import DIMENSIONS, METRICS, page_views
from seohead.reports.bi import export_bi
from tests.test_bi_export import _crawl_with_audit


def _body(rows, *, total=None, timezone="UTC", **metadata):
    return json.dumps(
        {
            "dimensionHeaders": [{"name": name} for name in DIMENSIONS],
            "metricHeaders": [{"name": name, "type": "TYPE_INTEGER"} for name in METRICS],
            "rows": [
                {
                    "dimensionValues": [{"value": value} for value in row[:3]],
                    "metricValues": [{"value": str(row[3])}],
                }
                for row in rows
            ],
            "rowCount": len(rows) if total is None else total,
            "metadata": {"timeZone": timezone, **metadata},
            "propertyQuota": {"tokensPerDay": {"consumed": 1, "remaining": 199999}},
        }
    )


def _request(**kwargs):
    return {
        "property_id": "123",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "site_origin": "https://example.test",
        **kwargs,
    }


def test_page_view_pagination_preserves_date_host_path_zero_and_metadata(monkeypatch):
    monkeypatch.setattr(ga4, "MAX_ROWS", 2)
    rows = [
        ("20261001", "example.test", "/a?x=1", 0),
        ("20261002", "other.test", "/a", 7),
        ("20261003", "example.test", "/b", 3),
    ]
    calls = []

    def send(url, payload, _token):
        calls.append(payload)
        offset, limit = int(payload["offset"]), int(payload["limit"])
        return _body(rows[offset : offset + limit], total=3)

    result = page_views(**_request(), token="synthetic", transport=send)
    assert result["ok"] and result["state"] == "complete"
    assert [call["offset"] for call in calls] == ["0", "2"]
    assert all(call["returnPropertyQuota"] is True for call in calls)
    assert result["rows"][0]["url"] == "https://example.test/a?x=1"
    assert result["rows"][0]["screenPageViews"] == 0
    assert result["rows"][0]["source_date"] == "20261001"
    assert result["rows"][1]["url"] is None
    assert result["page_metadata"][1]["property_quota"]["tokensPerDay"]["consumed"] == 1
    assert result["timezone"] == "UTC"
    assert result["sampled"] is result["thresholded"] is False


@pytest.mark.parametrize(
    "status,kind,attempts",
    [(403, "not_granted", 1), (429, "quota_exhausted", 3), (500, "collection_failed", 3)],
)
def test_provider_failure_is_bounded_and_never_echoes_secret(monkeypatch, status, kind, attempts):
    monkeypatch.setattr("seohead.data_sources.ga4_content.time.sleep", lambda _: None)
    calls = []

    def send(*_):
        calls.append(1)
        raise urllib.error.HTTPError(
            "https://example.test/?token=secret-canary",
            status,
            "secret-canary",
            {},
            io.BytesIO(b"{}"),
        )

    result = page_views(**_request(), token="secret-canary", transport=send)
    assert result["state"] == "failed" and result["failure_kind"] == kind
    assert len(calls) == attempts
    assert "secret-canary" not in json.dumps(result)


def test_sampling_thresholding_cap_and_empty_remain_distinct(monkeypatch):
    monkeypatch.setattr(ga4, "MAX_ROWS", 1)
    row = ("20261001", "example.test", "/a", 0)
    sampled = page_views(
        **_request(max_rows=1),
        token="t",
        transport=lambda *_: _body(
            [row],
            total=2,
            subjectToThresholding=True,
            samplingMetadatas=[{"samplesReadCount": "5", "samplingSpaceSize": "10"}],
        ),
    )
    assert (
        sampled["state"] == "partial"
        and sampled["truncated"]
        and sampled["sampled"]
        and sampled["thresholded"]
    )
    empty = page_views(**_request(), token="t", transport=lambda *_: _body([]))
    assert empty["state"] == "complete" and empty["result_state"] == "empty"
    assert empty["rows"] == []


def test_timezone_drift_and_malformed_rows_fail_without_partial_success(monkeypatch):
    monkeypatch.setattr(ga4, "MAX_ROWS", 1)

    def send(_url, payload, _token):
        zone = "UTC" if payload["offset"] == "0" else "Europe/Minsk"
        return _body([("20261001", "example.test", "/a", 1)], total=2, timezone=zone)

    assert page_views(**_request(), token="t", transport=send)["state"] == "failed"
    assert (
        page_views(
            **_request(), token="t", transport=lambda *_: _body([("bad", "example.test", "/a", 1)])
        )["state"]
        == "failed"
    )


def test_saved_provider_to_retained_join_to_bi_preserves_grain(tmp_path, monkeypatch):
    scan = _crawl_with_audit(tmp_path, monkeypatch)
    source = tmp_path / "provider"
    response = providers.provider_collect(
        "ga4",
        "page_views",
        _request(token="synthetic"),
        artifact_dir=source,
        transport=lambda *_: _body(
            [
                ("20261001", "example.test", "/", 0),
                ("20261002", "example.test", "/", 5),
                ("20261001", "example.test", "/outside", 2),
                ("20261001", "foreign.test", "/a", 4),
            ]
        ),
    )
    assert response["result"] is None  # raw rows remain restricted local evidence
    assert response["evidence"]["sampling"] is False
    assert response["evidence"]["timezone"] == "UTC"
    artifact = next(source.glob("provider-*.json"))
    document = normalize_file(artifact)
    assert document["mapping"]["row_shape"] == "flat"
    assert document["mapping"]["source"]["privacy"] == "restricted"
    joined = tmp_path / "joined.sqlite"
    write_join(scan, document, joined)
    package = tmp_path / "bi"
    export_bi(scan=scan, provider_joins=[joined], out_dir=package)
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["datasets"]["metrics"]["row_count"] == 4
    part = package / manifest["datasets"]["metrics"]["partitions"][0]["path"]
    with part.open() as f:
        metrics = list(csv.DictReader(f))
    assert {row["metric_name"] for row in metrics} == {"screenPageViews"}
    assert {row["dimension_date"] for row in metrics} == {"2026-10-01", "2026-10-02"}
    assert (
        next(row for row in metrics if row["dimension_date"] == "2026-10-02")["value_number"] == "5"
    )
    assert {row["population_state"] for row in metrics} == {"matched", "external_only", "unkeyable"}


def test_short_provider_pages_cannot_spend_unbounded_requests(monkeypatch):
    monkeypatch.setattr("seohead.data_sources.ga4_content.MAX_PAGE_REQUESTS", 2)
    monkeypatch.setattr(ga4, "MAX_ROWS", 1)
    calls = []

    def send(*_):
        calls.append(1)
        return _body([("20261001", "example.test", "/a", 1)], total=100)

    result = page_views(**_request(), token="t", transport=send)
    assert result["state"] == "failed"
    assert result["completed_pages"] == len(calls) == 2


def test_missing_credentials_never_constructs_provider_transport(monkeypatch):
    from seohead.data_sources.credentials import MissingCredential

    def missing():
        raise MissingCredential("synthetic missing credentials")

    monkeypatch.setattr("seohead.data_sources.credentials.ga4_access_token", missing)
    monkeypatch.setattr(
        "seohead.data_sources.credentials.gsc_service_account_available", lambda: False
    )
    result = page_views(
        **_request(), transport=lambda *_: pytest.fail("unexpected provider request")
    )
    assert result["state"] == "not_configured"
