"""MOBILE_ALTERNATE_LINK (#1016): a media-qualified rel=alternate that names no URL.

Only a declaration with a media attribute and an empty or missing href fires. A valid
mobile alternate, and a plain hreflang alternate, must stay silent.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from seohead.checks.parser import mobile_alternate_broken_count
from seohead.crawl.collect import collect_urls
from seohead.crawl.evidence import build_evidence
from seohead.sf.config import load_config
from seohead.sf.core.context import AuditContext
from seohead.sf.core.loader import LoadedExports
from seohead.sf.core.rules import run_rules


def _count(head: str) -> int:
    soup = BeautifulSoup(f"<html><head>{head}</head><body></body></html>", features="lxml")
    return mobile_alternate_broken_count(soup)


def test_media_alternate_without_href_counts():
    assert _count('<link rel="alternate" media="only screen and (max-width: 640px)">') == 1


def test_media_alternate_with_blank_href_counts():
    assert _count('<link rel="alternate" media="(max-width: 640px)" href="  ">') == 1


def test_media_alternate_with_href_is_valid():
    tag = '<link rel="alternate" media="(max-width: 640px)" href="https://m.example.com/">'
    assert _count(tag) == 0


def test_alternate_without_media_is_not_a_mobile_alternate():
    assert _count('<link rel="alternate" hreflang="de">') == 0


def test_media_on_a_non_alternate_link_is_ignored():
    assert _count('<link rel="stylesheet" media="print" href="/print.css">') == 0


# -- registry check, through the native crawl -> evidence -> rules pipeline --


class _FakeResponse:
    def __init__(self, text: str, headers: dict[str, str]):
        self.text = text
        self.status_code = 200
        self.headers = headers


def _page(head_extra: str) -> str:
    body = "Enough body text to be a real page. " * 40
    return f"<html><head><title>Page</title>{head_extra}</head><body>{body}</body></html>"


_BROKEN = _page('<link rel="alternate" media="only screen and (max-width: 640px)">')
_VALID = _page(
    '<link rel="alternate" media="only screen and (max-width: 640px)" href="https://m.example.com/">'
)


def _run_crawl(mapping):
    crawl_result = collect_urls(
        list(mapping),
        fetcher=lambda url: mapping[url],
        sleeper=lambda _s: None,
    )
    evidence = build_evidence(crawl_result)
    exports = LoadedExports()
    exports.frames.update(evidence["frames"])
    exports.found = list(evidence["found"])
    exports.missing = list(evidence["missing"])
    ctx = AuditContext(exports, load_config(None))
    ctx.skip_unsupported(set(exports.frames))
    run_rules(ctx)
    return ctx


def test_mobile_alternate_fires_only_for_the_hrefless_declaration():
    mapping = {
        "https://example.com/broken": _FakeResponse(_BROKEN, {"content-type": "text/html"}),
        "https://example.com/valid": _FakeResponse(_VALID, {"content-type": "text/html"}),
    }
    ctx = _run_crawl(mapping)
    fired = {i.target_url for i in ctx.issues if i.check == "MOBILE_ALTERNATE_LINK"}
    assert fired == {"https://example.com/broken"}


def test_mobile_alternate_skips_honestly_on_a_plain_sf_export(result):
    skipped = {s.id for s in result.skipped}
    assert "MOBILE_ALTERNATE_LINK" in skipped
    assert "MOBILE_ALTERNATE_LINK" not in {i.check for i in result.issues}
