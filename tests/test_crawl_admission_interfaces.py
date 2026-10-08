"""Admission parity is distinct from collecting the declared maximum population."""

from __future__ import annotations

import asyncio
import json

import pytest

from scripts.accept_million_crawl import SyntheticOrigin, _synthetic_transport
from seohead import cli
from seohead.crawl.collect import collect_urls
from seohead.crawl.settings import DEFAULTS, MAX_URLS_CEILING, checked_url_budget, load
from seohead.crawl.sqlite_adapter import crawl_to_scan
from seohead.job_contracts import MAX_REMOTE_CRAWL_SECONDS, ScanOptions
from seohead.remote_api.backend import RemoteProjectLimits, SQLiteJobBackend
from seohead.servers import handlers
from seohead.servers.mcp_server import build_server
from tests.test_crawl_list_mode import FakeResponse
from tests.test_remote_backend import SCANS_A, SITE, _api, _headers, _network


@pytest.mark.parametrize("pages", [50_000, 100_000, 1_000_000])
@pytest.mark.parametrize("interface", ["cli", "mcp"])
@pytest.mark.parametrize("seconds", [7 * 24 * 60 * 60, MAX_REMOTE_CRAWL_SECONDS])
def test_explicit_native_ceiling_reaches_shared_handler_unchanged(
    tmp_path, monkeypatch, capsys, pages, interface, seconds
):
    from seohead.servers import scan_handlers

    accepted = []

    def native(_url, *, settings, **_kwargs):
        accepted.append(settings)
        return {"ok": True, "urls_collected": 0, "partial": True, "finish_reason": "test_admission"}

    monkeypatch.setattr(scan_handlers, "crawl_site_scan", native)
    options = {
        "url": "https://example.test/",
        "max_urls": pages,
        "scan_out": str(tmp_path / "not-created.sqlite"),
        "overrides": {"limits.max_requests": 2_000_000, "limits.max_crawl_seconds": seconds},
        "producer_build": "a" * 40,
    }
    if interface == "cli":
        assert (
            cli.main(
                [
                    "crawl-site",
                    "--url",
                    options["url"],
                    "--max-urls",
                    str(pages),
                    "--scan-out",
                    options["scan_out"],
                    "--producer-build",
                    "a" * 40,
                    "--set",
                    "limits.max_requests=2000000",
                    "--set",
                    f"limits.max_crawl_seconds={seconds}",
                ]
            )
            == 0
        )
    else:
        tool = build_server()._tool_manager.get_tool("seo_crawl_site")
        asyncio.run(tool.run(options))
    assert accepted[0]["limits"]["max_urls"] == pages
    assert accepted[0]["limits"]["max_requests"] == 2_000_000
    assert accepted[0]["limits"]["max_crawl_seconds"] == seconds
    assert accepted[0]["speed"] == DEFAULTS["speed"]
    assert accepted[0]["rendering"] == DEFAULTS["rendering"]
    assert accepted[0]["resources"] == DEFAULTS["resources"]
    assert not (tmp_path / "not-created.sqlite").exists()


def test_above_ceiling_fails_shared_handler_before_artifact_or_network(tmp_path):
    target = tmp_path / "absent.sqlite"
    with pytest.raises(ValueError, match="ceiling"):
        handlers.crawl_site(url="https://example.test/", max_urls=1_000_001, scan_out=str(target))
    assert not target.exists()


def test_direct_native_capture_keeps_large_declaration_on_small_complete_origin(tmp_path):
    from seohead.storage import open_scan

    origin = SyntheticOrigin(3, 2)
    config = load(
        overrides={
            "limits.max_urls": MAX_URLS_CEILING,
            "limits.max_requests": 1_100_000,
            "speed.min_delay_seconds": 0,
        }
    )
    path = tmp_path / "scan.sqlite"
    with _synthetic_transport(origin):
        result = crawl_to_scan(
            "https://million-crawl.test/p/0",
            scan_out=str(path),
            settings=config,
            producer_version="test",
            producer_revision="a" * 40,
            runtime_versions={
                name: "test" for name in ("python", "sqlite", "httpx", "lxml", "beautifulsoup4")
            },
        )
    assert result.pages == 3 and not result.partial
    with open_scan(path, require_audit=False) as con:
        recorded = json.loads(con.execute("SELECT config_json FROM scan").fetchone()[0])
        assert recorded["limits"]["max_urls"] == MAX_URLS_CEILING
        assert recorded["limits"]["max_requests"] == 1_100_000


def test_remote_api_queue_and_worker_preserve_explicit_ceiling(tmp_path, monkeypatch):
    requests = _network(monkeypatch)
    limits = RemoteProjectLimits(max_urls=1_000_000, max_requests=2_000_000)
    backend = SQLiteJobBackend(tmp_path / "queue", {"alpha": limits}, producer_build="a" * 40)
    api = _api(backend)
    sent = api.post(
        SCANS_A,
        headers=_headers(),
        json={
            "target_url": SITE,
            "options": {"max_urls": 1_000_000, "max_requests": 1_100_000},
        },
    )
    assert sent.status_code == 202, sent.text
    finished = backend.run_one("capacity-admission")
    assert finished.state == "finished"
    result = backend.get_result("alpha", sent.json()["job_id"])
    assert result.coverage == "complete" and result.audit_available
    assert len(requests) == 2  # robots + the only page, not a million-page benchmark
    scan = next(item for item in result.artifacts if item.kind == "scan")
    from seohead.storage import open_scan

    path = backend.artifact_path("alpha", finished.job_id, scan.artifact_id)
    with open_scan(path) as con:
        config = json.loads(con.execute("SELECT config_json FROM scan").fetchone()[0])
        assert config["limits"]["max_urls"] == 1_000_000
        assert config["limits"]["max_requests"] == 1_100_000
        assert config["speed"]["min_delay_seconds"] == 0.5
    refused = api.post(
        SCANS_A,
        headers=_headers(key="above-cap-synthetic"),
        json={
            "target_url": SITE,
            "options": {"max_urls": 1_000_001},
        },
    )
    assert refused.status_code == 422
    assert len(requests) == 2


def test_remote_project_defaults_and_global_bounds_stay_independent():
    assert RemoteProjectLimits().max_urls == 10_000
    assert RemoteProjectLimits().max_requests == 20_000
    assert RemoteProjectLimits().max_job_seconds == ScanOptions().max_crawl_seconds == 3_600
    for kwargs in ({"max_urls": 1_000_001}, {"max_requests": 2_000_001}):
        with pytest.raises(ValueError, match="ceiling"):
            RemoteProjectLimits(**kwargs)


@pytest.mark.parametrize("seconds", [7 * 24 * 60 * 60, MAX_REMOTE_CRAWL_SECONDS])
def test_remote_long_duration_requires_project_authorization_and_reaches_worker(
    tmp_path, monkeypatch, seconds
):
    requests = _network(monkeypatch)
    payload = {
        "target_url": SITE,
        "options": {
            "max_urls": 1_000_000,
            "max_requests": 1_100_000,
            "max_crawl_seconds": seconds,
        },
    }
    ordinary = SQLiteJobBackend(
        tmp_path / "ordinary",
        {
            "alpha": RemoteProjectLimits(max_urls=1_000_000, max_requests=2_000_000),
        },
        producer_build="a" * 40,
    )
    refused = _api(ordinary).post(SCANS_A, headers=_headers(), json=payload)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "budget_exceeded"
    assert ordinary.list_jobs("alpha", 0, 10) == [] and requests == []

    accepted = []
    from seohead.servers.scan_handlers import crawl_site_scan

    def runner(*args, **kwargs):
        accepted.append(kwargs["settings"])
        return crawl_site_scan(*args, **kwargs)

    authorized = SQLiteJobBackend(
        tmp_path / "authorized",
        {
            "alpha": RemoteProjectLimits(
                max_urls=1_000_000,
                max_requests=2_000_000,
                max_requests_per_origin=1_100_000,
                max_job_seconds=seconds,
            )
        },
        producer_build="a" * 40,
        runner=runner,
    )
    sent = _api(authorized).post(SCANS_A, headers=_headers(), json=payload)
    assert sent.status_code == 202
    outcome = authorized.run_one("explicit-duration")
    assert outcome.state == "finished" and len(requests) == 2
    assert accepted[0]["limits"]["max_crawl_seconds"] == seconds
    assert accepted[0]["limits"]["max_requests"] == 1_100_000
    assert accepted[0]["speed"]["min_delay_seconds"] == 0.5
    rendered = ScanOptions(max_crawl_seconds=seconds, rendering_mode="js").effective_config()
    assert rendered["rendering"]["escalation"]["max_render_seconds"] == 300


@pytest.mark.parametrize("seconds", [0, True, MAX_REMOTE_CRAWL_SECONDS + 1])
def test_remote_duration_ceiling_and_types_fail_before_io(seconds):
    with pytest.raises(ValueError):
        ScanOptions(max_crawl_seconds=seconds)
    with pytest.raises(ValueError):
        RemoteProjectLimits(max_job_seconds=seconds)


def test_eager_legacy_population_is_refused_before_fetching():
    with pytest.raises(ValueError, match=r"materialized legacy.*50,000"):
        collect_urls(["https://example.test/"], max_urls=1_000_000)
    assert checked_url_budget(50_000, materialized=True) == 50_000


@pytest.mark.parametrize("chain_kind", ["redirect", "canonical"])
def test_list_budget_keeps_completed_hops_and_never_fetches_past_limit(chain_kind):
    calls = []

    def fetch(url):
        calls.append(url)
        target = {
            "https://example.test/a": "https://example.test/b",
            "https://example.test/b": "https://example.test/c",
        }[url]
        if chain_kind == "redirect":
            return FakeResponse("", 301, {"location": target})
        return FakeResponse(f'<html><head><link rel="canonical" href="{target}"></head></html>')

    result = collect_urls(
        ["https://example.test/a"],
        fetcher=fetch,
        max_requests=2,
        resolve_redirect_destination=chain_kind == "redirect",
        resolve_canonical_destination=chain_kind == "canonical",
    )
    assert calls == ["https://example.test/a", "https://example.test/b"]
    assert result.partial and result.finish_reason == "request_limit"
    page = result.pages[0]
    chain = page.redirect_chain if chain_kind == "redirect" else page.canonical_chain
    assert [hop["url"] for hop in chain] == ["https://example.test/b"]


def test_list_robots_uses_the_same_request_budget():
    calls = []
    result = collect_urls(
        ["https://example.test/a"],
        max_requests=1,
        robots_policy="respect",
        fetcher=lambda url: calls.append(url) or FakeResponse("User-agent: *\nAllow: /"),
    )
    assert calls == ["https://example.test/robots.txt"]
    assert result.partial and result.finish_reason == "request_limit"
    assert result.pages == []


def test_list_audit_refusal_keeps_private_evidence_when_public_export_is_off(tmp_path, monkeypatch):
    from seohead.crawl import collect
    from seohead.servers import scan_handlers

    original = collect.collect_urls
    monkeypatch.setattr(scan_handlers, "MAX_AUDIT_PAGES", 1)
    monkeypatch.setattr(
        collect,
        "collect_urls",
        lambda urls, **kwargs: original(
            urls,
            **kwargs,
            fetcher=lambda _url: FakeResponse("<html><title>Retained</title></html>"),
        ),
    )
    result = handlers.crawl_site(
        urls=["https://example.test/a", "https://example.test/b"],
        out_dir=str(tmp_path),
        min_delay=0,
        overrides={"output.write_pages_jsonl": False},
    )
    assert result["audit_available"] is False
    assert result["urls_collected"] == 2
    assert "materialization limit" in result["audit_reason"]
    assert not (tmp_path / "pages.jsonl").exists()
    rows = [
        json.loads(line) for line in (tmp_path / ".pages_resume.jsonl").read_text().splitlines()
    ]
    assert [row["title"] for row in rows] == ["Retained", "Retained"]
