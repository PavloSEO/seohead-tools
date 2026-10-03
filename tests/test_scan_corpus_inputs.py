"""Issue #669: corpus analyzers may read retained scan bodies without fetching."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import sqlite3

from seohead import cli
from seohead.servers import handlers
from seohead.storage import corpus_inputs
from seohead.storage.corpus_inputs import _indexable
from seohead.storage.native_scan import NativeScan
from seohead.tools.markdown_extract import extract_markdown
from tests.test_scan_native import _metadata, _record, _runtime
from tests.test_scan_resource_integration import _event


def _scan(tmp_path, pages, *, retain_bodies=True, finish=True):
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / "corpus.sqlite"
    metadata = _metadata(**({} if retain_bodies else {"storage.body_mode": "off"}))
    with NativeScan.create(path, **metadata) as scan:
        for index, (url, html) in enumerate(pages):
            scan.enqueue([(url, index)])
            lease = scan.claim(1)[0]
            record = _record(url)
            record["crawl_depth"] = lease.depth
            record["status_code"] = 200
            record["content_type"] = "text/html"
            if "noindex" in html:
                record["meta_robots"] = "noindex"
            runtime = _runtime()
            runtime["max_depth_reached"] = max(lease.depth, 1)
            scan.commit_page(
                lease,
                record,
                captures=[_event(url, html.encode(), "text/html; charset=utf-8")],
                runtime=runtime,
            )
        if finish:
            assert scan.finish_capture("fixture complete")
    return path


def test_scan_corpus_matches_inline_and_never_changes_source(tmp_path, monkeypatch):
    html = "<html><body><nav>menu</nav><main>same retained content words</main><footer>x</footer></body></html>"
    scan = _scan(tmp_path, [("https://example.test/a", html), ("https://example.test/b", html)])
    before = hashlib.sha256(scan.read_bytes()).hexdigest()
    network_attempts = []

    def refuse_network(*args, **kwargs):
        network_attempts.append((args, kwargs))
        raise AssertionError("saved corpus analysis must not access the network")

    monkeypatch.setattr("socket.getaddrinfo", refuse_network)
    monkeypatch.setattr("socket.socket.connect", refuse_network)

    expected = handlers.duplicate_check(
        items=[
            {"id": "https://example.test/a", "text": extract_markdown(html)["content_markdown"]},
            {"id": "https://example.test/b", "text": extract_markdown(html)["content_markdown"]},
        ]
    )
    actual = handlers.duplicate_check(scan=str(scan))
    boilerplate = handlers.boilerplate_report(scan=str(scan))

    assert {
        key: value for key, value in actual.items() if key not in {"source", "coverage"}
    } == expected
    expected_boilerplate = handlers.boilerplate_report(
        pages=[
            {"url": "https://example.test/a", "html": html},
            {"url": "https://example.test/b", "html": html},
        ]
    )
    assert {
        key: value for key, value in boilerplate.items() if key not in {"source", "coverage"}
    } == expected_boilerplate
    assert network_attempts == []
    assert actual["coverage"]["state"] == "complete"
    assert actual["source"]["representations"] == {"static": 2}
    assert boilerplate["count"] == 2
    assert hashlib.sha256(scan.read_bytes()).hexdigest() == before


def test_scan_corpus_keeps_nonindexable_and_missing_bodies_honest(tmp_path):
    html = "<html><body><main>same retained content words</main></body></html>"
    scan = _scan(
        tmp_path,
        [
            ("https://example.test/a", html),
            (
                "https://example.test/noindex",
                html.replace("<main>", "<meta name='robots' content='noindex'><main>"),
            ),
        ],
    )
    result = handlers.duplicate_check(scan=str(scan))
    assert result["excluded_non_indexable"] == 1

    no_bodies = _scan(
        tmp_path / "no-bodies", [("https://example.test/empty", html)], retain_bodies=False
    )
    missing = handlers.duplicate_check(scan=str(no_bodies))
    assert missing["ok"] is False
    assert missing["coverage"]["state"] == "unavailable"
    assert missing["coverage"]["omission_reasons"] == {
        "document unavailable: omitted/not_enabled": 1
    }


def test_scan_flag_is_a_stdin_safe_source_and_mcp_exposes_it(monkeypatch, capsys):
    class NeverRead:
        closed = False

        def isatty(self):
            return False

        def read(self):
            raise AssertionError("--scan must not consume stdin")

    monkeypatch.setattr(cli.sys, "stdin", NeverRead())
    monkeypatch.setitem(handlers.HANDLERS, "duplicate_check", lambda **kw: {"ok": True, "echo": kw})
    assert cli.main(["duplicate-check", "--scan", "saved.sqlite"]) == 0
    assert json.loads(capsys.readouterr().out)["echo"]["scan"] == "saved.sqlite"

    from seohead.servers.mcp_server import build_server

    tools = asyncio.run(build_server().list_tools())
    assert "scan" in {t.name: t for t in tools}["seo_duplicate_check"].inputSchema["properties"]
    assert "scan" in {t.name: t for t in tools}["seo_boilerplate_report"].inputSchema["properties"]


def test_scan_duplicate_work_budget_is_unavailable_not_a_clean_result(tmp_path, monkeypatch):
    html = "<html><body><main>same short repeated words</main></body></html>"
    scan = _scan(tmp_path, [("https://example.test/a", html), ("https://example.test/b", html)])
    monkeypatch.setattr("seohead.storage.corpus_inputs.MAX_DUPLICATE_CANDIDATE_COMPARISONS", 0)

    result = handlers.duplicate_check(scan=str(scan))

    assert result["ok"] is False
    assert result["reason"] == "duplicate candidate-comparison budget exceeded"
    assert "items" not in result
    assert "same short repeated words" not in json.dumps(result)


def test_scan_indexability_uses_the_crawler_policy_for_status_canonical_and_robots():
    page = {
        "url": "https://example.test/page",
        "status_code": 200,
        "canonical": "",
        "meta_robots": "",
        "x_robots": "",
        "error": "",
    }
    assert _indexable(page, False) is True
    assert _indexable({**page, "status_code": 404}, False) is False
    assert _indexable({**page, "canonical": "https://example.test/other"}, False) is False
    assert _indexable({**page, "meta_robots": "noindex,follow"}, False) is False
    assert _indexable(page, True) is False  # report_only robots evidence takes priority


def test_scan_boilerplate_result_keeps_raw_html_private(tmp_path):
    html = "<html><body><nav>private navigation words</nav><main>private page text</main></body></html>"
    result = handlers.boilerplate_report(
        scan=str(_scan(tmp_path, [("https://example.test/a", html)]))
    )

    assert result["coverage"]["analyzed_documents"] == 1
    assert "private navigation words" not in json.dumps(result)


def test_scan_coverage_names_a_running_capture_even_when_its_retained_body_is_complete(tmp_path):
    html = "<html><body><main>captured once</main></body></html>"
    result = handlers.duplicate_check(
        scan=str(_scan(tmp_path, [("https://example.test/a", html)], finish=False))
    )

    assert result["coverage"]["state"] == "partial"
    assert "scan lifecycle is running" in result["coverage"]["reason"]
    assert result["source"]["lifecycle"] == "running"


def test_scan_corpus_document_limit_streams_partial_coverage(tmp_path, monkeypatch):
    html = "<html><body><nav>menu</nav><main>content words</main><footer>x</footer></body></html>"
    scan = _scan(tmp_path, [(f"https://example.test/{i}", html) for i in range(3)])
    monkeypatch.setattr(corpus_inputs, "MAX_CORPUS_DOCUMENTS", 2)

    result = handlers.boilerplate_report(scan=str(scan))

    coverage = result["coverage"]
    assert coverage["state"] == "partial"
    assert coverage["prepared_documents"] == 2
    assert coverage["eligible_documents"] == 3
    assert coverage["omission_reasons"] == {"scan corpus document limit exceeded": 1}
    assert "scan corpus document limit exceeded" in coverage["reason"]
    assert result["count"] == 2


def test_scan_corpus_byte_budget_reports_partial_coverage(tmp_path, monkeypatch):
    html = "<html><body><main>body text {} enough words here</main></body></html>"
    pages = [(f"https://example.test/{i}", html.format(i)) for i in range(4)]
    scan = _scan(tmp_path, pages)
    # The retained input is the extracted Markdown; admit roughly half the corpus.
    retained = len(extract_markdown(pages[0][1])["content_markdown"].encode("utf-8"))
    monkeypatch.setattr(corpus_inputs, "MAX_CORPUS_INPUT_BYTES", retained * 2 + retained // 2)

    result = handlers.duplicate_check(scan=str(scan))

    coverage = result["coverage"]
    assert result["ok"] is True
    assert coverage["state"] == "partial"
    assert coverage["prepared_documents"] == 2
    assert coverage["eligible_documents"] == 4
    assert coverage["omission_reasons"] == {"scan corpus input-byte budget exceeded": 2}
    assert coverage["analyzed_documents"] == 2


def test_scan_boilerplate_meters_retained_digests_not_html_bytes(tmp_path, monkeypatch):
    html = (
        "<html><body><nav>menu</nav><main>"
        + "padding " * 400
        + "</main><footer>x</footer></body></html>"
    )
    scan = _scan(tmp_path, [(f"https://example.test/{i}", html) for i in range(5)])
    # Kilobytes of HTML per page, but only url + digest are retained input.
    monkeypatch.setattr(corpus_inputs, "MAX_CORPUS_INPUT_BYTES", 512)

    result = handlers.boilerplate_report(scan=str(scan))

    assert result["coverage"]["state"] == "complete"
    assert result["count"] == 5


def test_scan_corpus_read_interrupt_reports_partial_coverage(tmp_path, monkeypatch):
    html = "<html><body><nav>menu</nav><main>content words</main><footer>x</footer></body></html>"
    scan = _scan(tmp_path, [(f"https://example.test/{i}", html) for i in range(3)])
    real = corpus_inputs.read_document
    calls = []

    def interrupted_read(con, document_id, **kwargs):
        calls.append(document_id)
        if len(calls) > 1:
            raise sqlite3.OperationalError("interrupted")
        return real(con, document_id, **kwargs)

    monkeypatch.setattr(corpus_inputs, "read_document", interrupted_read)

    result = handlers.boilerplate_report(scan=str(scan))

    coverage = result["coverage"]
    assert coverage["state"] == "partial"
    assert coverage["prepared_documents"] == 1
    assert coverage["eligible_documents"] == 3
    assert coverage["omission_reasons"] == {
        "scan corpus read exceeded the per-document read budget": 2
    }
    assert result["count"] == 1


def test_scan_read_deadline_scales_with_artifact_size(tmp_path):
    from seohead.storage import (
        READ_TIMEOUT_BYTES_PER_SECOND,
        READ_TIMEOUT_SECONDS,
        _read_deadline_seconds,
    )

    small = tmp_path / "small.sqlite"
    small.write_bytes(b"x")
    assert _read_deadline_seconds(small) == READ_TIMEOUT_SECONDS
    large = tmp_path / "large.sqlite"
    with large.open("wb") as stream:
        stream.truncate(200 * 1024 * 1024)
    assert _read_deadline_seconds(large) == 200 * 1024 * 1024 / READ_TIMEOUT_BYTES_PER_SECOND


_LARGE_CORPUS_PAGE_COUNT = 30
# base64 of a seeded random stream: unique per page and nearly incompressible,
# so the artifact itself also exceeds the old 16 MiB retained-body ceiling
# (issue #710), which zlib-stored boilerplate-only pages would never reach.
_LARGE_SCRIPT_BYTES = 825_000


def _large_corpus_pages():
    """Yield distinct complete pages without holding the whole corpus in memory."""
    chrome = (
        "<header>Example masthead</header><nav>site menu</nav>"
        "<main><h1>Shared page</h1><p>Identical retained content words.</p></main>"
        "<footer>Example footer</footer>"
    )
    for index in range(_LARGE_CORPUS_PAGE_COUNT):
        payload = base64.b64encode(random.Random(index).randbytes(_LARGE_SCRIPT_BYTES)).decode(
            "ascii"
        )
        assert "noindex" not in payload  # _scan marks any "noindex" page non-indexable
        yield (
            f"https://example.test/page-{index}",
            "<html><head><title>Shared page</title><script>"
            + payload
            + "</script></head><body>"
            + chrome
            + "</body></html>",
        )


def _assert_complete_coverage(result, page_count=_LARGE_CORPUS_PAGE_COUNT):
    coverage = result["coverage"]
    assert coverage["state"] == "complete"
    assert coverage["eligible_documents"] == page_count
    assert coverage["prepared_documents"] == page_count
    assert coverage["analyzed_documents"] == page_count
    assert coverage["omitted_documents"] == 0
    assert coverage["omission_reasons"] == {}


def test_scan_corpus_streams_a_retained_corpus_past_the_old_16_mib_ceiling(tmp_path):
    """Both scan analyzers must complete on an artifact bigger than the former
    16 MiB input-byte budget, which metered raw HTML rather than retained input."""
    metered = {"count": 0, "bytes": 0}

    def pages():
        for url, html in _large_corpus_pages():
            metered["count"] += 1
            metered["bytes"] += len(html.encode("utf-8"))
            yield url, html

    scan = _scan(tmp_path, pages())
    urls = [f"https://example.test/page-{index}" for index in range(_LARGE_CORPUS_PAGE_COUNT)]

    # Pin the fixture above the old boundary so a later reduction cannot
    # silently stop exercising the regression.
    assert metered["count"] == _LARGE_CORPUS_PAGE_COUNT
    assert metered["bytes"] > 16 * 1024 * 1024
    assert scan.stat().st_size > 16 * 1024 * 1024

    boilerplate = handlers.boilerplate_report(scan=str(scan))
    _assert_complete_coverage(boilerplate)
    assert boilerplate["count"] == _LARGE_CORPUS_PAGE_COUNT
    assert len(boilerplate["groups"]) == 1
    group = boilerplate["groups"][0]
    assert group["dominant"] is True
    assert group["count"] == _LARGE_CORPUS_PAGE_COUNT
    assert group["urls"] == sorted(urls)
    assert boilerplate["minority_groups"] == []

    duplicates = handlers.duplicate_check(scan=str(scan))
    _assert_complete_coverage(duplicates)
    assert duplicates["count"] == _LARGE_CORPUS_PAGE_COUNT
    assert duplicates["clusters"] == []
    assert len(duplicates["exact_duplicates"]) == 1
    assert duplicates["exact_duplicates"][0]["members"] == sorted(urls)


def test_inline_duplicate_positional_arguments_keep_the_existing_contract():
    """Adding scan input must not reinterpret an inline caller's threshold as a path."""
    from seohead.servers.handlers import duplicate_check

    items = [
        {"id": "https://example.test/a", "text": "shared product description with details"},
        {"id": "https://example.test/b", "text": "shared product description with details"},
    ]
    positional = duplicate_check(items, 0.92, True, False)
    keyword = duplicate_check(
        items=items, threshold=0.92, with_fingerprints=True, only_indexable=False
    )
    assert positional == keyword
    assert positional["count"] == 2
