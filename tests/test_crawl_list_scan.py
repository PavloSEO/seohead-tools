"""Explicit-list native storage preserves list semantics without global evidence lists."""

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from seohead.crawl import collect, list_scan
from seohead.mcp import handlers
from seohead.storage.audit_v2 import AuditV2Reader

REVISION = "c" * 40
HTML = '<html><head><title>Stored</title></head><body><a href="/never-follow">Link</a><form action="/never-submit"><input type="password"></form></body></html>'


class Response:
    def __init__(self, html=HTML, code=200, headers=None):
        self.text = html
        self.content = html.encode()
        self.status_code = code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


def install_fetcher(monkeypatch, fetcher):
    original = collect.collect_urls

    def injected(*args, **kwargs):
        return original(*args, fetcher=fetcher, sleeper=lambda _: None, **kwargs)

    monkeypatch.setattr(list_scan, "collect_urls", injected)


def run(path, urls=None, **kwargs):
    overrides = {
        "speed.min_delay_seconds": 0,
        "speed.adaptive": False,
        "robots.policy": "ignore",
        "cache.mode": "off",
        "sitemaps.auto_discover": False,
        "limits.max_urls": 1000000,
        "storage.format_version": "scan.v2",
    }
    overrides.update(kwargs.pop("overrides", {}))
    return handlers.crawl_site(
        urls=urls, scan_out=str(path), overrides=overrides, producer_build=REVISION, **kwargs
    )


def rows(path):
    with sqlite3.connect(path) as con:
        return con.execute(
            "SELECT u.url FROM pages p JOIN urls u USING(url_id) ORDER BY p.page_ordinal"
        ).fetchall()


def test_exact_multihost_list_keeps_order_deduplicates_and_never_discovers(tmp_path, monkeypatch):
    urls = [
        "https://one.example.test/a",
        "https://two.example.test/b?z=1&y=2",
        "https://one.example.test/a",
        "https://one.example.test/a/",
    ]
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    path = tmp_path / "list.seohead"
    result = run(path, urls)
    assert result["audit_available"] and not result["partial"]
    assert calls == [urls[0], urls[1], urls[3]]
    assert rows(path) == [(url,) for url in calls]
    assert result["input_coverage"] == {
        "accepted": 3,
        "duplicate": 1,
        "blank": 0,
        "url_too_long": 0,
    }
    with AuditV2Reader(path) as audit:
        assert audit.header["run"]["input_mode"] == "crawl-list"
        assert audit.header["run"]["source"] == "url-list"
        skipped = {item["id"] for item in audit.header["run"]["checks_skipped"]}
        assert {
            "OUTLINK_TO_LOCALHOST",
            "FOLLOW_AND_NOFOLLOW_INLINKS",
            "FORM_URL_INSECURE",
            "FORM_ON_HTTP_URL",
        } <= skipped
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM forms").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3


def test_url_file_is_not_consumed_by_robots_warning(tmp_path, monkeypatch, capsys):
    source = tmp_path / "urls.txt"
    source.write_text("https://one.example.test/a\nhttps://two.example.test/b\n")
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    result = run(tmp_path / "list.seohead", urls_file=str(source))
    assert result["urls_collected"] == len(calls) == 2
    assert "robots.txt bypass enabled" in capsys.readouterr().err


def test_list_resume_refuses_changed_input_before_fetching(tmp_path, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/b"):
            raise KeyboardInterrupt
        return Response()

    install_fetcher(monkeypatch, fetch)
    path = tmp_path / "list.seohead"
    urls = ["https://one.example.test/a", "https://two.example.test/b"]
    first = run(path, urls)
    assert first["partial"] and first["finish_reason"] == "interrupted"
    before = calls[:]
    with pytest.raises(ValueError, match="input changed"):
        run(path, urls[::-1])
    assert calls == before
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    resumed = handlers.crawl_site(resume=str(path), producer_build=REVISION)
    assert resumed["resumed"] and resumed["urls_collected"] == 2
    assert calls == [*before, urls[1]]
    assert rows(path) == [(url,) for url in urls]


def test_request_budget_counts_robots_and_persists_across_resume(tmp_path, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        return (
            Response("User-agent: *\n", headers={"content-type": "text/plain"})
            if url.endswith("robots.txt")
            else Response()
        )

    install_fetcher(monkeypatch, fetch)
    path = tmp_path / "list.seohead"
    result = run(
        path,
        ["https://one.example.test/a", "https://one.example.test/b", "https://two.example.test/c"],
        overrides={"robots.policy": "respect", "limits.max_requests": 3},
    )
    assert result["finish_reason"] == "request_limit" and result["requests_used"] == 3
    assert calls == [
        "https://one.example.test/robots.txt",
        "https://one.example.test/a",
        "https://one.example.test/b",
    ]
    count = len(calls)
    resumed = handlers.crawl_site(resume=str(path), producer_build=REVISION)
    assert resumed["finish_reason"] == "request_limit"
    assert resumed["requests_used"] == 3 and len(calls) == count


def test_killed_finite_elapsed_latch_cannot_be_cleared_by_repeated_resume(tmp_path, monkeypatch):
    urls = ["https://one.example.test/a", "https://two.example.test/b"]
    install_fetcher(monkeypatch, lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    path = tmp_path / "list.seohead"
    result = run(path, urls, overrides={"limits.max_crawl_seconds": 60})
    assert result["finish_reason"] == "interrupted"
    with sqlite3.connect(path) as con:
        con.execute(
            "UPDATE context_items SET payload_json=? WHERE kind='list_elapsed'",
            (json.dumps({"schema_version": "list_elapsed.v1", "seconds": 0.01, "active": True}),),
        )
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    for _ in range(2):
        result = handlers.crawl_site(resume=str(path), producer_build=REVISION)
        assert result["finish_reason"] == "duration_limit"
        with sqlite3.connect(path) as con:
            saved = json.loads(
                con.execute(
                    "SELECT payload_json FROM context_items WHERE kind='list_elapsed'"
                ).fetchone()[0]
            )
            assert saved["active"] is True
    assert calls == []


def test_offline_list_reanalysis_keeps_list_identity_and_unavailable_graph(tmp_path, monkeypatch):
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    path = tmp_path / "list.seohead"
    urls = ["https://one.example.test/a", "https://two.example.test/b"]
    run(path, urls)
    for name in ("derived", "derived-again"):
        target = tmp_path / f"{name}.seohead"
        result = handlers.scan_reanalyze(str(path), str(target), producer_build=REVISION)
        assert result.get("ok", True) and result.get("audit_available", True), result
        assert rows(target) == [(url,) for url in urls]
        with AuditV2Reader(target) as audit:
            assert audit.header["run"]["input_mode"] == "crawl-list"
            skipped = {item["id"] for item in audit.header["run"]["checks_skipped"]}
            assert {"OUTLINK_TO_LOCALHOST", "FORM_URL_INSECURE"} <= skipped
        with sqlite3.connect(target) as con:
            assert con.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM forms").fetchone()[0] == 0
        path = target
    assert calls == urls


def test_legacy_directory_resume_restores_the_full_jsonl_prefix(tmp_path, monkeypatch):
    directory = tmp_path / "legacy"
    urls = ["https://one.example.test/a", "https://two.example.test/b"]
    paused = False

    def fetch(url):
        nonlocal paused
        if url.endswith("/b") and not paused:
            paused = True
            raise KeyboardInterrupt
        return Response()

    install_fetcher(monkeypatch, fetch)
    first = handlers.crawl_site(
        urls=urls,
        out_dir=str(directory),
        max_urls=1000000,
        robots="ignore",
        min_delay=0,
        producer_build=REVISION,
    )
    assert first["partial"]
    path = directory / ".list.seohead"
    assert [
        json.loads(line)["url"] for line in (directory / "pages.jsonl").read_text().splitlines()
    ] == urls[:1]
    resumed = handlers.crawl_site(resume=str(path), producer_build=REVISION)
    assert resumed["out_dir"] == str(directory)
    assert [
        json.loads(line)["url"] for line in (directory / "pages.jsonl").read_text().splitlines()
    ] == urls
    assert json.loads((directory / "audit.json").read_text())["run"]["input_mode"] == "crawl-list"


def test_large_directory_list_retires_prior_clean_reports_without_deletion(tmp_path, monkeypatch):
    from seohead.mcp import scan_handlers

    monkeypatch.setattr(scan_handlers, "MAX_AUDIT_PAGES", 1)
    install_fetcher(monkeypatch, lambda _: Response())
    directory = tmp_path / "legacy"
    directory.mkdir()
    for name in ("audit.json", "tasks.json", "tasks.md"):
        (directory / name).write_text("prior report")
    result = handlers.crawl_site(
        urls=["https://one.example.test/a", "https://two.example.test/b"],
        out_dir=str(directory),
        max_urls=1000000,
        robots="ignore",
        min_delay=0,
        producer_build=REVISION,
        overrides={"output.write_pages_jsonl": False},
    )
    assert result["audit_available"] is False
    assert len((directory / ".pages_resume.jsonl").read_text().splitlines()) == 2
    for name, path in result["stale_reports"].items():
        assert not (directory / name).exists()
        assert Path(path).read_text() == "prior report"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"rendering.mode": "js"}, "static-only"),
        ({"resources.fetch": True}, "static-only"),
        ({"cache.mode": "live"}, "cache.mode"),
    ],
)
def test_unsupported_list_options_fail_before_network_or_output(
    tmp_path, monkeypatch, overrides, reason
):
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    path = tmp_path / "list.seohead"
    with pytest.raises(ValueError, match=reason):
        run(path, ["https://one.example.test/a"], overrides=overrides)
    assert not path.exists() and calls == []


def test_native_list_inspect_and_csv_export_preserve_every_page(tmp_path, monkeypatch):
    install_fetcher(monkeypatch, lambda _: Response())
    path = tmp_path / "list.seohead"
    urls = ["https://one.example.test/a", "https://two.example.test/b"]
    run(path, urls)
    inspected = handlers.scan_inspect(str(path), limit=2)
    assert not inspected.get("error"), inspected
    exported = handlers.scan_export(
        str(path),
        str(tmp_path / "pages.csv"),
        format="csv",
        records=["pages"],
        fields={"pages": ["url", "title"]},
    )
    assert exported["ok"] and exported["counts"]["pages"] == len(urls)
    with (tmp_path / "pages.pages.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert [row["url"] for row in csv.DictReader(stream, delimiter=";")] == urls


def test_list_duration_is_cumulative_and_prevents_later_dispatches(tmp_path, monkeypatch):
    now = [0.0]
    original = list_scan.ListScan
    monkeypatch.setattr(
        list_scan,
        "ListScan",
        lambda *args, **kwargs: original(*args, clock=lambda: now[0], **kwargs),
    )
    calls = []

    def fetch(url):
        calls.append(url)
        now[0] += 2
        return Response()

    install_fetcher(monkeypatch, fetch)
    path = tmp_path / "list.seohead"
    result = run(
        path,
        [f"https://one.example.test/{n}" for n in range(3)],
        overrides={"limits.max_crawl_seconds": 3},
    )
    assert result["finish_reason"] == "duration_limit" and len(calls) == 2
    result = handlers.crawl_site(resume=str(path), producer_build=REVISION)
    assert result["finish_reason"] == "duration_limit" and len(calls) == 2


def test_oversized_robots_is_bounded_and_named_unavailable(tmp_path, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("robots.txt"):
            return Response(
                "User-agent: *\n" + "Disallow: /private\n" * 100,
                headers={"content-type": "text/plain"},
            )
        return Response()

    install_fetcher(monkeypatch, fetch)
    path = tmp_path / "list.seohead"
    result = run(
        path,
        ["https://one.example.test/a", "https://one.example.test/b"],
        overrides={"robots.policy": "respect", "limits.max_response_bytes": 1024},
    )
    assert result["urls_collected"] == 2
    assert calls.count("https://one.example.test/robots.txt") == 1
    assert any("robots policy unavailable" in note for note in result["limitations"])
    with AuditV2Reader(path) as audit:
        skipped = {entry["id"]: entry["reason"] for entry in audit.header["run"]["checks_skipped"]}
        assert "unavailable" in skipped["BLOCKED_BY_ROBOTS"]


def test_canonical_hops_rebind_credentials_and_do_not_add_list_pages(monkeypatch):
    calls = []

    class Client:
        def get(self, url, *, headers, extensions):
            calls.append((url, headers))
            return (
                Response(
                    '<html><head><link rel="canonical" href="https://other.example.test/target"></head></html>'
                )
                if len(calls) == 1
                else Response()
            )

        def close(self):
            pass

    monkeypatch.setenv("SEOHEAD_TEST_LIST_TOKEN", "test-value")
    monkeypatch.setattr(collect, "validate_url", lambda _: None)
    monkeypatch.setattr(collect, "pinned_target", lambda url: (url, {}, {}))
    monkeypatch.setattr(collect, "http_client", lambda *_a, **_kw: (Client(), False))
    result = collect.collect_urls(
        ["https://source.example.test/start"],
        resolve_canonical_destination=True,
        credential_headers=[
            {
                "host": "source.example.test",
                "headers": {"Authorization": "env:SEOHEAD_TEST_LIST_TOKEN"},
            }
        ],
    )
    assert len(result.pages) == 1
    assert calls[0][1]["Authorization"] == "test-value"
    assert "Authorization" not in calls[1][1]


@pytest.mark.parametrize("kind", ["redirect", "canonical"])
def test_native_optional_hop_budget_preserves_completed_evidence(tmp_path, monkeypatch, kind):
    root = "https://one.example.test/start"
    middle = "https://two.example.test/middle"
    final = "https://three.example.test/final"
    calls = []

    def fetch(url):
        calls.append(url)
        target = {root: middle, middle: final}[url]
        if kind == "redirect":
            return Response("", 301, {"location": target})
        return Response(f'<html><head><link rel="canonical" href="{target}"></head></html>')

    install_fetcher(monkeypatch, fetch)
    path = tmp_path / "list.seohead"
    result = run(
        path,
        [root],
        overrides={"limits.max_requests": 2, f"discovery.resolve_{kind}_destination": True},
    )
    assert result["partial"] and result["finish_reason"] == "request_limit"
    assert calls == [root, middle] and rows(path) == [(root,)]
    with sqlite3.connect(path) as con:
        chain = json.loads(con.execute(f"SELECT {kind}_chain_json FROM pages").fetchone()[0])
        assert len(chain) == 1 and chain[0]["url"] == middle
    resumed = handlers.crawl_site(resume=str(path), producer_build=REVISION)
    assert resumed["finish_reason"] == "request_limit" and calls == [root, middle]


def test_interrupt_after_native_commit_reconciles_counts_without_refetch(tmp_path, monkeypatch):
    from seohead.storage.native_scan import NativeScan

    original = NativeScan.commit_page
    paused = False

    def commit_then_pause(self, *args, **kwargs):
        nonlocal paused
        receipt = original(self, *args, **kwargs)
        if not paused:
            paused = True
            raise KeyboardInterrupt
        return receipt

    monkeypatch.setattr(NativeScan, "commit_page", commit_then_pause)
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    path = tmp_path / "list.seohead"
    urls = ["https://one.example.test/a", "https://two.example.test/b"]
    first = run(path, urls)
    assert first["partial"] and first["urls_collected"] == len(rows(path)) == 1
    second = handlers.crawl_site(resume=str(path), producer_build=REVISION)
    assert not second["partial"] and second["urls_collected"] == len(rows(path)) == 2
    assert calls == urls


def test_input_length_exclusions_are_counted_without_claiming_they_were_fetched(
    tmp_path, monkeypatch
):
    accepted = "https://one.example.test/a"
    calls = []
    install_fetcher(monkeypatch, lambda url: (calls.append(url), Response())[1])
    result = run(
        tmp_path / "list.seohead",
        ["", accepted, accepted, accepted + "x" * 100],
        overrides={"limits.max_url_length": 64},
    )
    assert calls == [accepted]
    assert result["input_coverage"] == {
        "accepted": 1,
        "duplicate": 1,
        "blank": 1,
        "url_too_long": 1,
    }
