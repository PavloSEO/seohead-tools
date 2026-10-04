"""Issue #801: retained-body selection, language evidence, reproducible normalization."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import sqlite3

import pytest

from seohead import cli
from seohead.servers import handlers
from seohead.storage import corpus_inputs
from seohead.storage.native_scan import NativeScan
from seohead.tools.text_normalize import (
    NORMALIZER_VERSION,
    normalization_policy,
    normalize_document,
    normalize_text,
    prepare_items,
    script_evidence,
)
from tests.test_scan_corpus_inputs import _scan
from tests.test_scan_native import _metadata, _record, _runtime
from tests.test_scan_resource_integration import _event

_LATIN_HTML = (
    '<html lang="en"><body><nav>shared navigation words</nav>'
    "<main><h1>Catalog page</h1><p>The pump station catalogue page "
    "describes delivery terms and stock levels in plain English.</p></main>"
    "<footer>shared footer words</footer></body></html>"
)
_CYRILLIC_HTML = (
    '<html lang="ru"><body><nav>shared navigation words</nav>'
    "<main><h1>Страница каталога</h1><p>Страница каталога насосных "
    "станций описывает условия доставки и наличие на складе.</p></main>"
    "<footer>shared footer words</footer></body></html>"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_normalize_text_is_deterministic_under_unicode_variation():
    assert normalize_text("  Multiple   Spaces\tAnd\nNewlines ") == "multiple spaces and newlines"
    assert normalize_text("ÄBC") == "äbc"
    # NFC folds the decomposed accent into the precomposed codepoint, so both
    # spellings normalize to the same string rather than diverging by encoding.
    assert normalize_text("café") == normalize_text("café")
    # Invisible formatting (a zero-width space inside a token) is removed, not
    # turned into a word boundary: the policy removes, never injects, text.
    assert normalize_text("to​ken") == "token"
    assert normalize_text("") == ""


def test_script_evidence_names_latin_cyrillic_mixed_and_undetermined():
    latin = script_evidence("the quick brown fox jumps over the lazy dog")
    assert latin["script"] == "latin"
    assert latin["script_shares"] == {"latin": 1.0, "cyrillic": 0.0, "other": 0.0}

    cyrillic = script_evidence("быстрая рыжая лиса перепрыгивает через ленивого пса")
    assert cyrillic["script"] == "cyrillic"
    assert cyrillic["script_shares"]["cyrillic"] == 1.0

    blended = script_evidence("the quick brown fox " + "рыжая лиса и серая собака")
    assert blended["script"] == "mixed"
    assert blended["script_shares"]["latin"] > 0.0
    assert blended["script_shares"]["cyrillic"] > 0.0

    short = script_evidence("tiny")
    assert short["script"] == "undetermined"
    empty = script_evidence("1234 !!!")
    assert empty["script"] == "undetermined"
    assert empty["script_letters"] == 0


def test_normalize_document_records_region_language_and_hashes():
    record = normalize_document(_CYRILLIC_HTML)

    assert record["content_area_strategy"] == "auto_main"
    # Boilerplate exclusion is the shared content-area policy: nav/footer copy
    # repeated across pages never reaches the normalized input.
    assert "shared navigation words" not in record["text"]
    assert "shared footer words" not in record["text"]
    assert "страница каталога" in record["text"]
    assert record["language"]["declared"] == "ru"
    assert record["language"]["declared_primary"] == "ru"
    assert record["language"]["script"] == "cyrillic"
    # input_sha256 is the exact HTML string the normalizer consumed;
    # normalized_sha256 is its output. Both are reproducible from the inputs.
    assert record["input_sha256"] == _sha(_CYRILLIC_HTML)
    assert record["normalized_sha256"] == _sha(record["text"])
    assert normalize_text(record["text"]) == record["text"]  # idempotent
    assert record["text_chars"] == len(record["text"])
    assert record["text_tokens"] > 0


def test_repeated_template_blocks_are_excluded_by_policy_not_by_dedup():
    chrome = (
        "<header>Example masthead slogan</header><nav>Home Catalog Contacts</nav>"
        "<aside>Promo sidebar block</aside><footer>Example footer line</footer>"
    )
    pages = [
        {
            "url": "https://example.test/a",
            "html": f"<html><body>{chrome}<main>first article body text</main></body></html>",
        },
        {
            "url": "https://example.test/b",
            "html": f"<html><body>{chrome}<main>second article body text</main></body></html>",
        },
    ]
    prepared = prepare_items(pages)
    texts = [item["text"] for item in prepared]
    for shared in ("masthead slogan", "home catalog contacts", "promo sidebar", "footer line"):
        assert all(shared not in text for text in texts)
    assert texts[0] != texts[1]
    # A configured selector removes template blocks that are not semantic tags.
    promo_page = pages[0]["html"].replace(
        "<main>", '<main><div class="promo">repeated promo block</div>'
    )
    with_promo = prepare_items(
        [{"url": "https://example.test/a", "html": promo_page}],
        content_area={"exclude_selectors": [".promo"]},
    )
    assert "repeated promo block" not in with_promo[0]["text"]


def test_normalization_policy_documents_the_exclusion_and_language_rules():
    policy = normalization_policy({"root_selector": "main"})
    assert policy["normalizer_version"] == NORMALIZER_VERSION
    assert policy["content_area"]["configured"] == {"root_selector": "main"}
    assert policy["content_area"]["default_exclude_tags"] == ["nav", "header", "aside", "footer"]
    assert policy["content_area"]["selection_order"][:2] == [
        "include_selector",
        "root_selector",
    ]
    assert policy["language"]["min_script_letters"] > 0


def test_scan_semantic_inputs_stream_retained_bodies_without_refetch(tmp_path, monkeypatch):
    scan = _scan(
        tmp_path,
        [
            ("https://example.test/en", _LATIN_HTML),
            ("https://example.test/ru", _CYRILLIC_HTML),
        ],
    )
    before = hashlib.sha256(scan.read_bytes()).hexdigest()

    def refuse_network(*args, **kwargs):
        raise AssertionError("saved corpus analysis must not access the network")

    monkeypatch.setattr("socket.getaddrinfo", refuse_network)
    monkeypatch.setattr("socket.socket.connect", refuse_network)

    result = handlers.semantic_inputs(scan=str(scan))

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["coverage"]["state"] == "complete"
    assert result["coverage"]["analyzed_documents"] == 2
    assert result["normalization"]["normalizer_version"] == NORMALIZER_VERSION

    documents = {doc["url"]: doc for doc in result["documents"]}
    english = documents["https://example.test/en"]
    russian = documents["https://example.test/ru"]
    assert english["state"] == "normalized"
    assert english["language"]["declared"] == "en"
    assert english["language"]["script"] == "latin"
    assert russian["language"]["declared"] == "ru"
    assert russian["language"]["script"] == "cyrillic"
    # The retained body hash is the corpus's evidence identity; the input hash
    # is the exact decoded string — identical here because the fixture is
    # UTF-8 end to end. Both are recorded so a consumer can check the chain.
    con = sqlite3.connect(scan)
    try:
        stored = dict(
            con.execute(
                "SELECT u.url, d.body_sha256 FROM pages p "
                "JOIN urls u USING(url_id) JOIN documents d ON d.document_id=p.document_id"
            ).fetchall()
        )
    finally:
        con.close()
    for url, doc in documents.items():
        assert doc["body_sha256"] == stored[url]
        assert doc["input_sha256"] == stored[url]
        assert doc["normalized_sha256"] and doc["normalized_sha256"] != doc["input_sha256"]
        assert doc["content_area_strategy"] == "auto_main"
        assert doc["indexable"] is True

    # The manifest carries hashes and policy, never the normalized text itself.
    serialized = json.dumps(result, ensure_ascii=False)
    assert "the pump station catalogue page" not in serialized
    assert "страница каталога" not in serialized
    assert hashlib.sha256(scan.read_bytes()).hexdigest() == before


def test_scan_semantic_non_utf8_body_keeps_input_hash_distinct(tmp_path):
    body = (
        "<html lang=ru><body><main><p>Страница каталога насосных станций.</p></main></body></html>"
    )
    raw = body.encode("cp1251")
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/cp1251", 0)])
        lease = scan.claim(1)[0]
        record = _record(lease.url)
        record["crawl_depth"] = lease.depth
        record["status_code"] = 200
        record["content_type"] = "text/html"
        scan.commit_page(
            lease,
            record,
            captures=[_event(lease.url, raw, "text/html; charset=windows-1251")],
            runtime=_runtime(),
        )
        assert scan.finish_capture("fixture complete")

    result = handlers.semantic_inputs(scan=str(path))

    (doc,) = result["documents"]
    assert doc["state"] == "normalized"
    assert doc["language"]["script"] == "cyrillic"
    assert doc["body_sha256"] == hashlib.sha256(raw).hexdigest()
    # The decoded text re-encoded as UTF-8 differs from the stored cp1251
    # bytes, so input and body hashes differ — both are recorded rather than
    # pretending the normalizer consumed the raw entity.
    assert doc["input_sha256"] != doc["body_sha256"]
    assert doc["input_sha256"] == _sha(body)


def _scan_with_missing_body(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        for index, url in enumerate(["https://example.test/ok", "https://example.test/missing"]):
            scan.enqueue([(url, index)])
            lease = scan.claim(1)[0]
            record = _record(url)
            record["crawl_depth"] = lease.depth
            record["status_code"] = 200
            record["content_type"] = "text/html"
            if url.endswith("missing"):
                event = dataclasses.replace(
                    _event(lease.url, b"", "text/html; charset=utf-8"),
                    entity_bytes=None,
                    body_state="unavailable",
                    body_reason="fetch_failed",
                )
            else:
                event = _event(lease.url, _LATIN_HTML.encode(), "text/html; charset=utf-8")
            runtime = _runtime()
            runtime["max_depth_reached"] = max(lease.depth, 1)
            scan.commit_page(lease, record, captures=[event], runtime=runtime)
        assert scan.finish_capture("fixture complete")
    return path


def test_scan_semantic_partial_and_missing_bodies_stay_explicit(tmp_path):
    scan = _scan_with_missing_body(tmp_path / "mixed")

    result = handlers.semantic_inputs(scan=str(scan))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["documents"][0]["url"] == "https://example.test/ok"
    coverage = result["coverage"]
    assert coverage["state"] == "partial"
    assert coverage["eligible_documents"] == 2
    assert coverage["prepared_documents"] == 1
    assert coverage["omission_reasons"] == {"document unavailable: unavailable/fetch_failed": 1}

    no_bodies = _scan(
        tmp_path / "none",
        [("https://example.test/empty", _LATIN_HTML)],
        retain_bodies=False,
    )
    missing = handlers.semantic_inputs(scan=str(no_bodies))
    assert missing["ok"] is False
    assert missing["coverage"]["state"] == "unavailable"
    assert missing["coverage"]["omission_reasons"] == {
        "document unavailable: omitted/not_enabled": 1
    }
    assert "documents" not in missing


def test_scan_semantic_byte_budget_reports_partial_coverage(tmp_path, monkeypatch):
    pages = [(f"https://example.test/{i}", _LATIN_HTML) for i in range(4)]
    scan = _scan(tmp_path, pages)
    retained = len(normalize_document(_LATIN_HTML)["text"].encode("utf-8"))
    monkeypatch.setattr(corpus_inputs, "MAX_CORPUS_INPUT_BYTES", retained * 2 + retained // 2)

    result = handlers.semantic_inputs(scan=str(scan))

    coverage = result["coverage"]
    assert result["ok"] is True
    assert coverage["state"] == "partial"
    assert coverage["prepared_documents"] == 2
    assert coverage["eligible_documents"] == 4
    assert coverage["omission_reasons"] == {"scan corpus input-byte budget exceeded": 2}
    assert result["count"] == 2


def test_scan_semantic_measured_empty_content_area_is_named(tmp_path):
    html = "<html><body><nav>only navigation words</nav><main></main></body></html>"
    scan = _scan(tmp_path, [("https://example.test/empty", html)])

    result = handlers.semantic_inputs(scan=str(scan))

    assert result["ok"] is True
    (doc,) = result["documents"]
    assert doc["state"] == "empty"
    assert doc["normalized_sha256"] == _sha("")
    assert doc["text_chars"] == 0
    assert result["coverage"]["measured_empty_documents"] == 1
    assert result["coverage"]["state"] == "complete"


def test_semantic_inputs_inline_items_and_unavailable_entries():
    result = handlers.semantic_inputs(
        items=[
            {"url": "https://example.test/a", "html": _LATIN_HTML},
            {"url": "https://example.test/missing"},
            "not-an-object",
        ]
    )

    assert result["ok"] is True
    assert result["count"] == 3
    states = {doc["url"]: doc["state"] for doc in result["documents"]}
    assert states["https://example.test/a"] == "normalized"
    assert states["https://example.test/missing"] == "unavailable"
    coverage = result["coverage"]
    assert coverage["state"] == "partial"
    assert coverage["eligible_documents"] == 3
    assert coverage["prepared_documents"] == 1
    assert coverage["omitted_documents"] == 2
    serialized = json.dumps(result)
    assert "the pump station catalogue page" not in serialized


def test_semantic_inputs_rejects_ambiguous_arguments(tmp_path):
    scan = _scan(tmp_path, [("https://example.test/a", _LATIN_HTML)])
    with pytest.raises(ValueError, match="mutually exclusive"):
        handlers.semantic_inputs(items=[{"url": "x", "html": "<p>t</p>"}], scan=str(scan))
    with pytest.raises(ValueError, match="content_area"):
        handlers.semantic_inputs(scan=str(scan), content_area={"root_selector": "main"})
    with pytest.raises(ValueError, match="items"):
        handlers.semantic_inputs()


def test_semantic_inputs_cli_scan_flag_is_stdin_safe_and_mcp_exposes_it(monkeypatch, capsys):
    class NeverRead:
        def isatty(self):
            return False

        def read(self):
            raise AssertionError("--scan must not consume stdin")

    monkeypatch.setattr(cli.sys, "stdin", NeverRead())
    monkeypatch.setitem(handlers.HANDLERS, "semantic_inputs", lambda **kw: {"ok": True, "echo": kw})
    assert cli.main(["semantic-inputs", "--scan", "saved.sqlite"]) == 0
    assert json.loads(capsys.readouterr().out)["echo"]["scan"] == "saved.sqlite"

    from seohead.servers.mcp_server import build_server

    tools = asyncio.run(build_server().list_tools())
    schema = {t.name: t for t in tools}["seo_semantic_inputs"].inputSchema["properties"]
    assert {"items", "scan", "content_area"} <= set(schema)
