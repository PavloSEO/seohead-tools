"""BI qualification keeps unknown provider facts out of measured quadrants."""

from __future__ import annotations

import csv
import hashlib
import json

import pytest

from seohead.data_sources.evidence_import import normalize_inline
from seohead.reports.bi import export_bi


def evidence(provider, metric, value):
    return normalize_inline(
        [{"url": "https://example.test/", metric: value}],
        manifest={
            "format": "seohead.evidence-mapping.v1",
            "source": {
                "provider": provider,
                "operation": "synthetic",
                "reporting_identity": f"synthetic-{provider}",
                "privacy": "supplied",
                "timezone": "UTC",
                "attribution": "last-click",
                "search_engine": "google" if provider == "gsc" else None,
                "search_type": "web" if provider == "gsc" else None,
            },
            "url": {"field": "url", "kind": "absolute"},
            "row_shape": "flat",
            "dimensions": [],
            "metrics": [{"name": metric, "type": "number", "unit": "count"}],
            "period": {"start_date": "2026-01-01", "end_date": "2026-01-07"},
            "collection": {
                "state": "complete",
                "sampled": False,
                "thresholded": False,
                "truncated": False,
            },
        },
    )


def project(tmp_path, *documents):
    paths = []
    for index, document in enumerate(documents):
        path = tmp_path / f"provider-{index}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        paths.append(path)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    audit = {
        "schema": "seohead.site-audit/1",
        "url": "https://example.test/",
        "site": {},
        "pages": [{"url": "https://example.test/", "status_code": 200}],
        "findings": [],
        "summary": {"pages_checked": 1, "findings_total": 0, "tools_run": [], "tools_failed": []},
    }
    package = tmp_path / "package"
    export_bi(audit=audit, provider_joins=paths, search_metric="clicks", out_dir=package)
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] == hashes
    manifest = json.loads((package / "manifest.json").read_text())
    datasets = {}
    for name, dataset in manifest["datasets"].items():
        rows = []
        for part in dataset["partitions"]:
            with (package / part["path"]).open(newline="", encoding="utf-8") as stream:
                rows.extend(csv.DictReader(stream))
        assert len(rows) == dataset["row_count"]
        datasets[name] = rows
    quadrant = next(
        row for row in datasets["cohorts"] if row["cohort_id"] == "search_visibility_vs_sessions"
    )
    return quadrant, datasets, manifest


@pytest.mark.parametrize(
    "clicks,sessions,label",
    [
        (2, 3, "positive_search_positive_sessions"),
        (2, 0, "positive_search_zero_sessions"),
        (0, 3, "zero_search_positive_sessions"),
        (0, 0, "zero_search_zero_sessions"),
    ],
)
def test_explicit_measurements_keep_all_four_quadrants_and_axis_context(
    tmp_path, clicks, sessions, label
):
    gsc, ga4 = evidence("gsc", "clicks", clicks), evidence("ga4", "sessions", sessions)
    row, datasets, manifest = project(tmp_path, gsc, ga4)
    assert (row["membership"], row["state"], row["value_label"]) == ("member", "available", label)
    axes = json.loads(row["source_metric_observations_json"])
    for axis in axes:
        metric = next(item for item in datasets["metrics"] if item["provider"] == axis["provider"])
        assert axis["provider_source_id"] == metric["provider_source_id"]
        assert axis["reporting_identity"] == metric["reporting_identity"]
        assert axis["attribution"] == "last-click"
        assert axis["metric_unit"] == "count"
        assert axis["collection"]["state"] == "complete"
        assert all(
            axis["collection"][flag] is False for flag in ("sampled", "thresholded", "truncated")
        )
        assert axis["boundary_policy"] == "strict"
        assert axis["cross_source_policy"] == "juxtapose"
    assert axes[0]["search_engine"] == "google" and axes[1]["search_engine"] is None
    assert {source["state"] for source in manifest["provider_sources"]} == {"complete"}


@pytest.mark.parametrize(
    "case,reason",
    [
        ("unknown_collection", "collection_state"),
        ("malformed_collection_origin", "collection_state"),
        ("unknown_flags", "explicitly"),
        ("unknown_flag_origin", "explicitly"),
        ("partial", "not complete"),
        ("failed", "not complete"),
        ("skipped", "not complete"),
        ("not_configured", "not complete"),
        ("sampled", "explicitly"),
        ("thresholded", "explicitly"),
        ("truncated", "explicitly"),
        ("missing_attribution", "unverified_scope"),
        ("unknown_attribution_origin", "unverified_scope"),
        ("negative_clicks", "nonnegative"),
        ("negative_sessions", "nonnegative"),
        ("null_clicks", "did not measure"),
        ("ratio_unit", "count units"),
        ("timezone_mismatch", "compatible"),
        ("period_mismatch", "compatible"),
    ],
)
def test_incomplete_or_incompatible_observations_do_not_enter_zero_quadrants(
    tmp_path, case, reason
):
    gsc, ga4 = evidence("gsc", "clicks", 0), evidence("ga4", "sessions", 2)
    collection = gsc["mapping"]["collection"]
    origins = gsc["provenance"]["fields"]
    if case == "unknown_collection":
        # The normalizer retains this default, but records that it is not evidence.
        origins["collection_state"] = {"value": None, "origin": "unknown"}
    elif case == "malformed_collection_origin":
        origins["collection_state"]["origin"] = {"not": "a source attestation"}
    elif case == "unknown_flags":
        for flag in ("sampled", "thresholded", "truncated"):
            collection[flag] = None
            origins[flag] = {"value": None, "origin": "unknown"}
    elif case == "unknown_flag_origin":
        origins["sampled"]["origin"] = "unknown"
    elif case in {"partial", "failed", "skipped", "not_configured"}:
        collection["state"] = case
    elif case in {"sampled", "thresholded", "truncated"}:
        collection[case] = True
    elif case == "missing_attribution":
        gsc["mapping"]["source"]["attribution"] = None
        origins["attribution"] = {"value": None, "origin": "unknown"}
    elif case == "unknown_attribution_origin":
        origins["attribution"]["origin"] = "unknown"
    elif case == "negative_clicks":
        gsc = evidence("gsc", "clicks", -1)
    elif case == "negative_sessions":
        ga4 = evidence("ga4", "sessions", -1)
    elif case == "null_clicks":
        gsc = evidence("gsc", "clicks", None)
    elif case == "ratio_unit":
        gsc["mapping"]["metrics"][0]["unit"] = "ratio"
    elif case == "timezone_mismatch":
        ga4["mapping"]["source"]["timezone"] = "Europe/Minsk"
    elif case == "period_mismatch":
        ga4["mapping"]["period"]["end_date"] = "2026-01-08"
    row, datasets, manifest = project(tmp_path, gsc, ga4)
    assert (row["membership"], row["state"]) == ("unclassified", "incomplete")
    assert reason in row["reason"]
    assert row["value_label"] == row["search_value"] == row["sessions_value"] == ""
    assert len(datasets["metrics"]) == 2
    if case == "unknown_collection":
        metric = next(item for item in datasets["metrics"] if item["provider"] == "gsc")
        assert metric["collection_state"] == "unknown"
        assert "collection_state" in metric["collection_reason"]
        source_id = metric["provider_source_id"]
        rows = [item for item in datasets["coverage"] if item["evidence_source_id"] == source_id]
        assert rows and {item["state"] for item in rows} == {"unknown"}
        assert all("collection_state" in item["reason"] for item in rows)
        source = next(
            item for item in manifest["provider_sources"] if item["provider_source_id"] == source_id
        )
        assert source["state"] == "unknown"
        assert source["evidence"]["collection"]["state"] == "complete"


def test_declared_cross_source_differences_are_retained_on_each_axis(tmp_path):
    gsc, ga4 = evidence("gsc", "clicks", 0), evidence("ga4", "sessions", 2)
    ga4["mapping"]["source"].update(attribution="data-driven", search_engine="bing")
    ga4["provenance"]["fields"]["search_engine"] = {"value": "bing", "origin": "declared"}
    row, _, _ = project(tmp_path, gsc, ga4)
    assert row["membership"] == "member"
    axes = json.loads(row["source_metric_observations_json"])
    assert [axis["attribution"] for axis in axes] == ["last-click", "data-driven"]
    assert [axis["search_engine"] for axis in axes] == ["google", "bing"]
