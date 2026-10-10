"""--max-urls is the page budget; a stopped-by-budget crawl is a finished capture."""

from __future__ import annotations

from seohead.crawl import settings as crawl_settings
from seohead.crawl.settings import load
from seohead.crawl.sqlite_adapter import crawl_to_scan
from seohead.projects.runtime import _candidate, admission
from seohead.projects.workspace import create_project
from seohead.storage.native_scan import NativeScan


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.text = self.content.decode("utf-8")
        self.headers = {"content-type": "text/html"}
        self.url = ""
        self.history = []


def test_page_budget_derives_request_and_time_budgets_for_small_crawls():
    settings = load(overrides={"limits.max_urls": 300})
    crawl_settings.bind_budgets_to_page_budget(settings, {"limits.max_urls"})
    assert settings["limits"]["max_requests"] == 900
    assert settings["limits"]["max_crawl_seconds"] == 600


def test_explicit_request_and_time_budgets_are_kept():
    settings = load(
        overrides={
            "limits.max_urls": 50,
            "limits.max_requests": 4000,
            "limits.max_crawl_seconds": 30,
        }
    )
    crawl_settings.bind_budgets_to_page_budget(
        settings,
        {"limits.max_urls", "limits.max_requests", "limits.max_crawl_seconds"},
    )
    assert settings["limits"]["max_requests"] == 4000
    assert settings["limits"]["max_crawl_seconds"] == 30


def test_small_crawls_are_admitted_without_approval_and_large_ones_are_not(tmp_path):
    project = tmp_path / "site"
    create_project(project, "https://example.test/")
    for pages, admitted in ((50, True), (300, True), (1000, False)):
        settings = load(overrides={"limits.max_urls": pages})
        crawl_settings.bind_budgets_to_page_budget(settings, {"limits.max_urls"})
        assert admission(str(project), settings)["ok"] is admitted, pages


def test_budget_stop_is_a_finished_capture_with_a_named_reason(tmp_path):
    scan_path = tmp_path / "budget.sqlite"
    page = b'<html><body><a href="/a">a</a><a href="/b">b</a></body></html>'

    def fetcher(url):
        if url.endswith("/robots.txt"):
            return _Response(404, "")
        return _Response(200, page)

    run = crawl_to_scan(
        "https://example.test/",
        scan_out=str(scan_path),
        settings=load(
            overrides={"limits.max_urls": 1, "speed.min_delay_seconds": 0, "speed.concurrency": 1}
        ),
        producer_version="3.0.0",
        producer_revision="a" * 40,
        runtime_versions={
            "python": "test",
            "sqlite": "test",
            "httpx": "test",
            "lxml": "test",
            "beautifulsoup4": "test",
        },
        fetcher=fetcher,
        sleeper=lambda _seconds: None,
        clock=lambda: 0.0,
    )
    assert run.finish_reason == "stopped_by_budget"
    assert run.partial is True
    with NativeScan.open(scan_path) as scan:
        assert scan.finish_capture(reason=run.finish_reason) is True
    header = NativeScan.inspect(str(scan_path))["scan"]
    assert header["lifecycle"] == "finished"
    assert header["finish_reason"] == "stopped_by_budget"
    assert header["finished_at"] is not None


def test_competitor_url_keeps_the_host_as_entered():
    assert _candidate("https://www.iana.org/")["url"] == "https://www.iana.org/"
    row = _candidate(
        {"url": "HTTPS://WWW.Example.test/shop", "source": "list", "observed_at": None}
    )
    assert row["url"] == "https://www.example.test/shop"
