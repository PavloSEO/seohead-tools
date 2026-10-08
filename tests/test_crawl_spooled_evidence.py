"""Legacy JSONL collection must keep evidence complete without retaining it twice."""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

import seohead.crawl.spider as spider
from seohead import cli
from seohead.servers import handlers


class Response:
    status_code = 200

    def __init__(self, text: str):
        self.text = text
        self.headers = {"content-type": "text/html"}


PAGES = {
    "https://example.test/": (
        '<html><body><a href="/a">A</a><a href="/b">B</a>'
        '<form action="/send"><input type="password"></form></body></html>'
    ),
    "https://example.test/a": '<html><body><a href="/b">B again</a></body></html>',
    "https://example.test/b": "<html><body>Last page</body></html>",
}


def _fetcher(*, interrupt_b_once: bool = False):
    interrupted = False

    def fetch(url):
        nonlocal interrupted
        if url == "https://example.test/b" and interrupt_b_once and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return Response(PAGES[url])

    return fetch


def _crawl(tmp_path, *, spooled: bool, interrupt_b_once: bool = False):
    tmp_path.mkdir(exist_ok=True)
    kwargs = (
        {
            "out_path": str(tmp_path / "pages.jsonl"),
            "links_path": str(tmp_path / "links.jsonl"),
            "forms_path": str(tmp_path / "forms.jsonl"),
            "state_path": str(tmp_path / "state.json"),
            "spool_evidence": True,
        }
        if spooled
        else {}
    )
    return spider.crawl_site(
        "https://example.test/",
        fetcher=_fetcher(interrupt_b_once=interrupt_b_once),
        robots_policy="ignore",
        min_delay=0,
        max_urls=3,
        **kwargs,
    )


def test_spooled_legacy_evidence_matches_the_direct_collector(tmp_path):
    direct = _crawl(tmp_path / "direct", spooled=False)
    path = tmp_path / "spooled"
    spooled = _crawl(path, spooled=True)

    assert spooled.spooled_evidence is True
    assert (spooled.page_count, spooled.link_count, spooled.form_count) == (
        len(direct.pages),
        len(direct.links),
        len(direct.forms),
    )
    assert (spooled.pages, spooled.links, spooled.forms) == ([], [], [])
    assert [p.url for p in spider._read_pages_jsonl(str(path / "pages.jsonl"))] == [
        p.url for p in direct.pages
    ]
    assert [asdict(link) for link in spider._read_links_jsonl(str(path / "links.jsonl"))] == [
        asdict(link) for link in direct.links
    ]
    assert [asdict(form) for form in spider._read_forms_jsonl(str(path / "forms.jsonl"))] == [
        asdict(form) for form in direct.forms
    ]


def test_spooled_resume_preserves_page_link_and_form_populations(tmp_path):
    path = tmp_path / "resumed"
    interrupted = _crawl(path, spooled=True, interrupt_b_once=True)
    assert interrupted.finish_reason == "interrupted"
    assert (path / "state.json").exists()

    resumed = _crawl(path, spooled=True)
    uninterrupted_path = tmp_path / "uninterrupted"
    uninterrupted = _crawl(uninterrupted_path, spooled=True)

    assert resumed.resumed is True
    assert resumed.finish_reason == uninterrupted.finish_reason == "finished"
    assert (resumed.page_count, resumed.link_count, resumed.form_count) == (
        uninterrupted.page_count,
        uninterrupted.link_count,
        uninterrupted.form_count,
    )
    for filename in ("pages.jsonl", "links.jsonl", "forms.jsonl"):
        assert list(spider._jsonl_rows(str(path / filename))) == list(
            spider._jsonl_rows(str(uninterrupted_path / filename))
        )


def test_spooled_resume_refuses_missing_or_shortened_form_evidence(tmp_path):
    path = tmp_path / "resumed"
    _crawl(path, spooled=True, interrupt_b_once=True)
    forms = path / "forms.jsonl"
    saved = forms.read_text(encoding="utf-8")

    forms.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="sidecar counts disagree"):
        _crawl(path, spooled=True)
    forms.write_text(saved, encoding="utf-8")
    forms.unlink()
    with pytest.raises(ValueError, match="sidecar is missing"):
        _crawl(path, spooled=True)


@pytest.mark.parametrize("filename", ["pages.jsonl", "links.jsonl", "forms.jsonl"])
@pytest.mark.parametrize("tail", [b'{"torn":', b"{}", b"[]\n", b"broken\n", b"\xff\n"])
def test_spooled_resume_refuses_corrupt_rows_without_changing_evidence(tmp_path, filename, tail):
    path = tmp_path / "resumed"
    _crawl(path, spooled=True, interrupt_b_once=True)
    sidecar = path / filename
    sidecar.write_bytes(sidecar.read_bytes() + tail)
    before = {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}

    with pytest.raises(ValueError, match="checkpoint evidence sidecar"):
        _crawl(path, spooled=True)

    assert {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()} == before


@pytest.mark.parametrize("filename", ["pages.jsonl", "links.jsonl"])
def test_spooled_resume_does_not_recreate_a_missing_sidecar(tmp_path, filename):
    path = tmp_path / "resumed"
    _crawl(path, spooled=True, interrupt_b_once=True)
    (path / filename).unlink()

    with pytest.raises(ValueError, match="checkpoint evidence sidecar is missing"):
        _crawl(path, spooled=True)

    assert not (path / filename).exists()


def test_spooled_resume_migrates_v4_inline_forms(tmp_path):
    path = tmp_path / "legacy"
    path.mkdir()
    spider.crawl_site(
        "https://example.test/",
        fetcher=_fetcher(interrupt_b_once=True),
        robots_policy="ignore",
        min_delay=0,
        max_urls=3,
        out_path=str(path / "pages.jsonl"),
        links_path=str(path / "links.jsonl"),
        state_path=str(path / "state.json"),
    )
    state_path = path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["forms"]) == 1
    state["schema_version"] = "crawl_state.v4"
    state.pop("spooled_evidence")
    state.pop("evidence_counts")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    forms_path = path / "forms.jsonl"
    forms_path.write_text(
        json.dumps(
            {
                "page": "https://example.test/wrong",
                "method": "get",
                "action": "",
                "has_password": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inline forms and form sidecar disagree"):
        _crawl(path, spooled=True)
    forms_path.unlink()

    resumed = _crawl(path, spooled=True)

    assert resumed.resumed is True
    assert resumed.form_count == 1
    assert len(spider._read_forms_jsonl(str(path / "forms.jsonl"))) == 1


def test_large_legacy_crawl_retains_evidence_with_named_unavailable_audit(
    tmp_path, monkeypatch, capsys
):
    from seohead.servers import scan_handlers

    monkeypatch.setattr(scan_handlers, "MAX_AUDIT_PAGES", 2)
    original = spider.crawl_site

    def injected(*args, **kwargs):
        return original(*args, fetcher=_fetcher(), sleeper=lambda _: None, **kwargs)

    monkeypatch.setattr(spider, "crawl_site", injected)
    out_dir = tmp_path / "legacy"
    out_dir.mkdir()
    (out_dir / "audit.json").write_text('{"old":"audit"}', encoding="utf-8")
    result = handlers.crawl_site(
        url="https://example.test/", out_dir=str(out_dir), robots="ignore", min_delay=0
    )

    assert result["urls_collected"] == 3
    assert result["audit_available"] is False
    assert "pages=3/2" in result["audit_reason"]
    assert not (out_dir / "audit.json").exists()
    assert (out_dir / result["stale_reports"]["audit.json"]).read_text(encoding="utf-8") == (
        '{"old":"audit"}'
    )
    assert len(list(spider._jsonl_rows(str(out_dir / "pages.jsonl")))) == 3
    assert result["discovery"]["links_seen"] == 3
    cli._print_crawl_outcome(result)
    assert (
        "audit unavailable: legacy audit materialization limit exceeded" in capsys.readouterr().err
    )


def test_robots_failure_does_not_turn_unread_prior_sidecars_into_a_clean_audit(
    tmp_path, monkeypatch
):
    original = spider.crawl_site

    def unavailable_robots(_url):
        response = Response("unavailable")
        response.status_code = 503
        return response

    def injected(*args, **kwargs):
        return original(*args, fetcher=unavailable_robots, sleeper=lambda _: None, **kwargs)

    monkeypatch.setattr(spider, "crawl_site", injected)
    out_dir = tmp_path / "legacy"
    out_dir.mkdir()
    (out_dir / "audit.json").write_text('{"old":"audit"}', encoding="utf-8")

    result = handlers.crawl_site(url="https://example.test/", out_dir=str(out_dir), min_delay=0)

    assert result["audit_available"] is False
    assert result["finish_reason"] == "robots_unavailable"
    assert "prior JSONL sidecars were not reconciled" in result["audit_reason"]
    assert not (out_dir / "audit.json").exists()
    assert (out_dir / result["stale_reports"]["audit.json"]).read_text(encoding="utf-8") == (
        '{"old":"audit"}'
    )


def test_public_legacy_resume_preserves_ordered_evidence_findings_and_coverage(
    tmp_path, monkeypatch
):
    pages = {
        "https://example.test/": Response(
            '<html><head><title>Root</title></head><body><nav><a href="/a" rel="nofollow">A</a></nav>'
            '<main><a href="/a">A</a><a href="/old">Old</a><a href="http://localhost/local">Local</a>'
            '<form action="http://example.test/send"><input type="password"></form></main></body></html>'
        ),
        "https://example.test/a": Response('<html><body><a href="/final">Final</a></body></html>'),
        "https://example.test/old": Response(""),
        "https://example.test/final": Response(
            "<html><head><title>Final</title></head><body>Done</body></html>"
        ),
    }
    pages["https://example.test/old"].status_code = 301
    pages["https://example.test/old"].headers["location"] = "/final"
    interrupt = True

    def fetch(url):
        nonlocal interrupt
        if url == "https://example.test/old" and interrupt:
            interrupt = False
            raise KeyboardInterrupt
        return pages[url]

    original = spider.crawl_site
    original_audit = handlers._audit_crawl_result
    evidence = []

    def injected(*args, **kwargs):
        return original(*args, fetcher=fetch, sleeper=lambda _: None, **kwargs)

    def audited(result, **kwargs):
        evidence.append(
            {
                "pages": [
                    {key: value for key, value in asdict(page).items() if key != "response_time"}
                    for page in result.pages
                ],
                "links": [asdict(link) for link in result.links],
                "forms": [asdict(form) for form in result.forms],
            }
        )
        return original_audit(result, **kwargs)

    monkeypatch.setattr(spider, "crawl_site", injected)
    monkeypatch.setattr(handlers, "_audit_crawl_result", audited)
    options = {
        "url": "https://example.test/",
        "robots": "ignore",
        "min_delay": 0,
        "overrides": {"sitemaps.auto_discover": False, "link_position.classify": True},
    }
    resumed_dir = tmp_path / "resumed"
    first = handlers.crawl_site(out_dir=str(resumed_dir), **options)
    assert first["partial"] and first["finish_reason"] == "interrupted"
    resumed = handlers.crawl_site(out_dir=str(resumed_dir), **options)
    full_dir = tmp_path / "full"
    full = handlers.crawl_site(out_dir=str(full_dir), **options)
    assert resumed["resumed"] and not full["resumed"]
    assert resumed["finish_reason"] == full["finish_reason"] == "finished"
    assert evidence[-2] == evidence[-1]
    assert len(evidence[-1]["pages"]) == 4
    assert any(page["redirect_url"] for page in evidence[-1]["pages"])
    assert evidence[-1]["forms"]
    assert {link["position"] for link in evidence[-1]["links"]} >= {"nav", "content"}
    resumed_audit = json.loads((resumed_dir / "audit.json").read_text())
    full_audit = json.loads((full_dir / "audit.json").read_text())
    assert resumed_audit["issues"] == full_audit["issues"]
    assert resumed_audit["summary"] == full_audit["summary"]
    assert resumed_audit["run"]["checks_skipped"] == full_audit["run"]["checks_skipped"]
    assert {issue["check"] for issue in full_audit["issues"]} >= {
        "OUTLINK_TO_LOCALHOST",
        "FORM_URL_INSECURE",
    }
