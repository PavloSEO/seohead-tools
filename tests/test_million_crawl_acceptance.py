"""Regression coverage for the manual one-million native crawl acceptance gate."""

from __future__ import annotations

import pytest

from scripts.accept_million_crawl import run_stage
from seohead.crawl.settings import MAX_REQUESTS_CEILING, MAX_URLS_CEILING, ConfigError, load
from seohead.storage import read_audit


def test_stable_capacity_configuration_keeps_url_gate_and_request_headroom():
    settings = load(overrides={"limits.max_urls": 1_000_000, "limits.max_requests": 2_000_000})
    assert MAX_URLS_CEILING == 1_000_000
    assert MAX_REQUESTS_CEILING == 2_000_000
    assert settings["limits"] == {
        **settings["limits"],
        "max_urls": 1_000_000,
        "max_requests": 2_000_000,
    }
    for requested in (1_000_001, 2_000_000):
        with pytest.raises(ConfigError, match="ceiling"):
            load(overrides={"limits.max_urls": requested})


def test_owned_mock_origin_runs_real_native_route_and_recovers(tmp_path):
    outcome = run_stage(
        tmp_path / "capacity",
        pages=64,
        shard_size=32,
        interrupt_after=24,
        consumers=False,
    )
    assert outcome["status"] == "passed"
    assert 0 < outcome["checkpoint"]["pages"] <= 24
    assert outcome["conservation"]["pages"] == outcome["conservation"]["sitemap_members"] == 64
    assert outcome["collector"]["audit_available"] is True


def test_representative_group_rich_fixture_conserves_capture_and_consumers(tmp_path):
    outcome = run_stage(
        tmp_path / "representative",
        pages=128,
        shard_size=64,
        interrupt_after=32,
        consumers=True,
        links_per_page=3,
        forms_per_page=1,
        body_padding_bytes=2048,
        body_profile="catalogue-v1",
        h1_families=1,
        comparison_compression="gzip",
    )
    counts = outcome["conservation"]
    assert counts["links"] == 384 and counts["forms"] == 129
    assert counts["smallest_body_bytes"] >= 2048
    consumers = outcome["consumers"]
    assert consumers["audit_v2"]["/groups"] > 0
    assert consumers["group_members"] >= 128
    assert consumers["csv_readback"] == consumers["xlsx_readback"] == consumers["report_readback"]
    assert consumers["task_coverage"]["source_findings"] == consumers["audit_v2"]["/issues"]


@pytest.mark.parametrize("extra", [["--skip-consumers"], ["--loopback-only"]])
def test_incompatible_consumer_retry_flags_fail_before_starting_worker(tmp_path, capsys, extra):
    from scripts.accept_million_crawl import main

    output = tmp_path / "absent"
    with pytest.raises(SystemExit) as error:
        main(["--out", str(output), "--input-scan", "retained.sqlite", *extra])
    assert error.value.code == 2
    assert not output.exists()


def test_standard_native_handler_bounds_deep_discovery_paths(tmp_path):
    outcome = run_stage(
        tmp_path / "bounded-paths",
        pages=64,
        shard_size=32,
        interrupt_after=24,
        consumers=False,
    )
    audit = read_audit(outcome["collector"]["scan"])
    finding = next(
        issue
        for issue in audit["issues"]
        if issue["check"] == "DEEP_DISCOVERY_PATH" and issue["target_url"].endswith("/p/63")
    )
    assert finding["details"] == {
        "hops": 63,
        "path_start": ["https://million-crawl.test/p/0"],
        "path_end": ["https://million-crawl.test/p/63"],
        "path_truncated": True,
    }
    trace = outcome["discovery_path_trace"]
    assert trace == [
        {
            "indexable_pages": 64,
            "resolved_depths": 64,
            "missing_depths": 0,
            "deep_depths": 43,
            "path_for_calls": 15,
            "longest_path_for_result": 20,
        }
    ]


def test_discovery_path_trace_preserves_short_standard_handler_paths(tmp_path):
    outcome = run_stage(
        tmp_path / "short-path-trace",
        pages=12,
        shard_size=6,
        interrupt_after=6,
        consumers=False,
    )
    audit = read_audit(outcome["collector"]["scan"])
    finding = next(
        issue
        for issue in audit["issues"]
        if issue["check"] == "DEEP_DISCOVERY_PATH" and issue["target_url"].endswith("/p/11")
    )
    assert finding["details"]["hops"] == 11
    assert len(finding["details"]["path"]) == 12
    assert outcome["discovery_path_trace"] == [
        {
            "indexable_pages": 12,
            "resolved_depths": 12,
            "missing_depths": 0,
            "deep_depths": 0,
            "path_for_calls": 7,
            "longest_path_for_result": 12,
        }
    ]


def test_owned_loopback_transport_smoke_uses_real_network_guarded_collector(tmp_path):
    """Separate smoke: exact HTTP/parser/storage scope and unchanged real pacing."""
    from scripts.accept_million_crawl import run_loopback

    result = run_loopback(tmp_path / "loopback")
    assert result["status"] == "passed"
    assert result["conservation"]["pages"] == result["conservation"]["sitemap_members"] == 8
    assert result["private_refused_before_allowance"] is True
    assert result["effective_max_requests_per_second"] == 2
    assert result["minimum_observed_request_interval_seconds"] >= 0.45


def test_density_fixture_declares_distinct_links_forms_and_body_padding():
    from bs4 import BeautifulSoup

    from scripts.accept_million_crawl import SyntheticOrigin

    origin = SyntheticOrigin(20, 10, links_per_page=8, forms_per_page=3, body_padding_bytes=2048)
    html = origin._page(1)
    soup = BeautifulSoup(html, "html.parser")
    assert len({anchor["href"] for anchor in soup.find_all("a")}) == 8
    assert len(soup.find_all("form")) == 3
    assert "x" * 2048 in soup.get_text()


@pytest.mark.parametrize("comparison_compression", ["none", "gzip"])
def test_small_consumer_route_streams_export_and_rechecks_real_retained_evidence(
    tmp_path, monkeypatch, comparison_compression
):
    from seohead.storage.audit_v2 import AuditV2Reader

    def refuse_materialization(*_args, **_kwargs):
        raise AssertionError("consumer materialized the full audit")

    monkeypatch.setattr(AuditV2Reader, "materialize_legacy", refuse_materialization)
    outcome = run_stage(
        tmp_path / "consumers",
        pages=32,
        shard_size=16,
        interrupt_after=8,
        consumers=True,
        comparison_compression=comparison_compression,
    )
    consumers = outcome["consumers"]
    assert consumers["source_sha256_before"] == consumers["source_sha256_after"]
    assert consumers["source_audit_sha256_before"] == consumers["source_audit_sha256_after"]
    assert consumers["export"]["counts"]["pages"] == 32
    assert consumers["consistency"]["read"]["pages"] == 32
    assert consumers["comparison"]["conservation"]["state"] == "complete"
    assert consumers["comparison_roundtrip"] == {
        name: item["rows"] for name, item in consumers["comparison"]["files"].items()
    }
    if comparison_compression == "gzip":
        assert all(
            item["compression"] == "gzip" for item in consumers["comparison"]["files"].values()
        )
    assert (
        consumers["comparison"]["conservation"]["before_issues"] == consumers["audit_v2"]["/issues"]
    )
    assert consumers["health"]["check_coverage"]["checks_total"] > 0
    assert consumers["recheck"]["same_observation"]["not_verifiable"] == 1
    assert consumers["recheck"]["fresh_observation"]["resolved"] == 1


def test_catalogue_body_profile_is_varied_reproducible_and_reports_entropy():
    import zlib

    from bs4 import BeautifulSoup

    from scripts.accept_million_crawl import SyntheticOrigin, _body_profile_summary

    varied = SyntheticOrigin(20, 10, body_padding_bytes=16384, body_profile="catalogue-v1")
    padding = SyntheticOrigin(20, 10, body_padding_bytes=16384)
    body = varied._page(2)
    assert body == varied._page(2)
    assert body != varied._page(3)
    assert len(body) >= 16384
    soup = BeautifulSoup(body, "html.parser")
    assert soup.select("section table") and soup.select('script[type="application/json"]')
    assert "not a commercial offer" in soup.get_text()
    assert len(zlib.compress(body)) > 3 * len(zlib.compress(padding._page(2)))
    sample = _body_profile_summary(varied)
    assert sample == _body_profile_summary(varied)
    assert sample["sampled_pages"] == 20
    assert sample["sample_byte_entropy_bits"] > 4


def test_consumer_failure_preserves_producer_checkpoint_and_failed_phase(tmp_path, monkeypatch):
    import json

    from seohead.mcp import handlers

    def fail(*_args, **_kwargs):
        raise RuntimeError("intentional consumer failure")

    monkeypatch.setattr(handlers, "scan_reanalyze", fail)
    output = tmp_path / "failed-consumer"
    with pytest.raises(RuntimeError, match="intentional consumer"):
        run_stage(output, pages=8, shard_size=4, interrupt_after=2, consumers=True)
    checkpoint = json.loads((output / "producer-result.json").read_text())
    assert checkpoint["status"] == "producer_passed_consumers_pending"
    assert checkpoint["conservation"]["pages"] == 8
    phases = json.loads((output / "consumers/progress.json").read_text())["phases"]
    assert phases["reanalysis"]["state"] == "failed"
    assert phases["export"]["state"] == "returned"
    assert not (output / "result.json").exists()


def test_consumer_retry_preserves_capture_and_only_fetches_fresh_selected_url(
    tmp_path, monkeypatch
):
    from scripts.accept_million_crawl import run_consumers_only
    from seohead.mcp import scan_handlers

    original = run_stage(
        tmp_path / "original", pages=8, shard_size=4, interrupt_after=2, consumers=False
    )
    call = scan_handlers.crawl_site_scan
    budgets = []

    def selected_only(*args, **kwargs):
        budgets.append(kwargs["settings"]["limits"]["max_urls"])
        assert budgets[-1] == 1
        return call(*args, **kwargs)

    monkeypatch.setattr(scan_handlers, "crawl_site_scan", selected_only)
    result = run_consumers_only(
        tmp_path / "original/native.sqlite", tmp_path / "retry", comparison_compression="gzip"
    )
    assert budgets == [1]
    assert result["mode"] == "consumers_only"
    assert result["capture_source_revision"] == original["source_revision"]
    consumers = result["consumers"]
    assert consumers["source_sha256_before"] == consumers["source_sha256_after"]
    assert consumers["source_audit_sha256_before"] == consumers["source_audit_sha256_after"]
