"""Objective authorship, date and trust-page evidence (issue #823).

The acceptance bar these tests pin is observability, not judgement: a finding
names the markup carrier, the crawl state and the evidence it stands on, and a
page whose body was never parsed is unmeasured -- never "no signals". Nothing
here asserts an E-E-A-T score, and the YMYL flag is a deterministic review
candidate, not a classification.

Three layers, mirroring the pipeline itself: the parser facts a page's DOM
carries, the checks over a synthetic native crawl (where the Trust Signals
column exists and holds objects), and the same checks over Screaming Frog
shaped exports (where it either does not exist or is a Custom Extraction
string). Storage keeps the absent-key / present-null distinction a scan needs
to tell "written before the field existed" from "never measured".
"""

from __future__ import annotations

import csv
import json

import pytest

from seohead.crawl.evidence import build_evidence
from seohead.crawl.spider import crawl_site
from seohead.sf.config import load_config
from seohead.sf.core.audit import run_audit
from seohead.sf.core.context import AuditContext
from seohead.sf.core.eeat import run_eeat
from seohead.sf.core.loader import LoadedExports
from seohead.storage import ScanError, import_run, open_scan
from seohead.storage.exports import export_run
from seohead.tools.parser import extract_trust_signals, parse_html
from tests.test_scan_artifact import BUILD
from tests.test_scan_artifact import legacy_run as legacy_run

# ---------------------------------------------------------------------------
# Parser facts: what a page's own markup declares.
# ---------------------------------------------------------------------------

_RICH_PAGE = """<html><head>
<title>A thoroughly declared article</title>
<meta name="author" content="Jane Writer">
<meta property="article:published_time" content="2026-09-01T10:00:00Z">
<meta property="article:modified_time" content="2026-09-20T11:00:00Z">
<meta property="og:type" content="article">
<script type="application/ld+json">
{"@type": "NewsArticle", "headline": "Declared",
 "author": {"@type": "Person", "name": "JSON Author"},
 "datePublished": "2026-09-01", "dateModified": "2026-09-20"}
</script>
</head><body>
<article>
<span class="byline">By the desk</span>
<a href="/about/jane" rel="author">Jane's page</a>
<span itemprop="author" content="Micro Author">micro</span>
<time datetime="2026-09-21">yesterday</time>
</article>
</body></html>"""


def _signals(html: str, url: str = "https://example.com/blog/post") -> dict:
    return parse_html(html, url)["trust_signals"]


def test_every_declared_carrier_is_recorded_with_its_own_name_and_confidence():
    signals = _signals(_RICH_PAGE)
    author = {item["signal"]: item for item in signals["author"]}
    assert author["meta_author"]["value"] == "Jane Writer"
    assert author["meta_author"]["confidence"] == "high"
    assert author["jsonld_author"]["value"] == "JSON Author"
    assert author["microdata_author"]["confidence"] == "high"
    # rel=author records the href as written -- a carrier's declared value,
    # not a resolved link.
    assert author["rel_author"]["value"] == "/about/jane"
    # A CSS class is a convention, not a declaration -- medium, never high.
    assert author["byline_class"]["confidence"] == "medium"
    dates = {item["signal"]: item for item in signals["dates"]}
    assert dates["meta_article_published"]["confidence"] == "high"
    assert dates["meta_article_modified"]["value"] == "2026-09-20T11:00:00Z"
    assert dates["jsonld_date:datePublished"]["value"] == "2026-09-01"
    assert dates["jsonld_date:dateModified"]["value"] == "2026-09-20"
    assert dates["time_element"]["confidence"] == "medium"
    assert set(signals["article"]) == {"og_type", "article_element", "jsonld_type:NewsArticle"}


def test_a_page_without_markup_records_a_measured_absence():
    signals = _signals("<html><head><title>Plain</title></head><body><p>x</p></body></html>")
    assert signals == {"author": [], "dates": [], "article": []}


def test_a_malformed_jsonld_block_contributes_nothing_and_is_named_beside_it():
    html = """<html><head>
<script type="application/ld+json">{"@type": "Article", "author": </script>
</head><body><article><p>content</p></article></body></html>"""
    parsed = parse_html(html, "https://example.com/blog/post")
    signals = parsed["trust_signals"]
    assert parsed["jsonld_invalid"]
    assert not any(item["signal"].startswith("jsonld_") for item in signals["author"])
    assert not any(item["signal"].startswith("jsonld_") for item in signals["dates"])
    # The <article> element is still real markup -- malformed JSON-LD hides
    # only its own declarations, not the rest of the document.
    assert signals["article"] == ["article_element"]


def test_signals_inside_a_template_are_not_in_the_rendered_document():
    html = """<html><body>
<template><meta name="author" content="Ghost"><time datetime="2026-01-01">x</time></template>
<p>visible</p></body></html>"""
    signals = _signals(html)
    assert signals["author"] == []
    assert signals["dates"] == []


def test_a_date_written_into_the_url_path_is_a_medium_confidence_convention():
    signals = _signals("<html><body><p>x</p></body></html>", "https://example.com/2024/05/report")
    url_dates = [d for d in signals["dates"] if d["signal"] == "url_path_date"]
    assert len(url_dates) == 1
    assert url_dates[0]["confidence"] == "medium"
    assert url_dates[0]["value"] == "2024/05"


def test_the_signal_inventory_is_bounded_per_family():
    from seohead.tools.parser import _TRUST_SIGNAL_CAP

    many = "".join(f'<span class="byline">by{i}</span>' for i in range(_TRUST_SIGNAL_CAP + 4))
    signals = _signals(f"<html><body>{many}</body></html>")
    assert len(signals["author"]) == _TRUST_SIGNAL_CAP


def test_extract_trust_signals_is_callable_on_its_own_tree():
    from bs4 import BeautifulSoup

    jsonld = parse_html(_RICH_PAGE, "https://example.com/blog/post")["jsonld"]
    soup = BeautifulSoup(_RICH_PAGE, features="lxml")
    signals = extract_trust_signals(soup, jsonld, "https://example.com/blog/post")
    assert {item["signal"] for item in signals["author"]} >= {"meta_author", "jsonld_author"}


# ---------------------------------------------------------------------------
# The checks, through a synthetic native crawl (Trust Signals as objects).
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


def _fetcher(mapping):
    def fetch(url):
        value = mapping.get(url)
        if value is None:
            return _FakeResponse("", status_code=404)
        return value

    return fetch


def _page(body: str, head: str = "") -> _FakeResponse:
    return _FakeResponse(f"<html><head>{head}</head><body>{body}</body></html>")


def _native_ctx(mapping, **crawl_kw):
    crawl_kw.setdefault("sleeper", lambda _s: None)
    crawl_kw.setdefault("min_delay", 0)
    result = crawl_site("https://example.com/", fetcher=_fetcher(mapping), **crawl_kw)
    evidence = build_evidence(result)
    exports = LoadedExports()
    exports.frames.update(evidence["frames"])
    exports.found = list(evidence["found"])
    exports.missing = list(evidence["missing"])
    ctx = AuditContext(exports, load_config(None))
    run_eeat(ctx)
    return ctx


def _fired(ctx) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for issue in ctx.issues:
        out.setdefault(issue.check, set()).add(issue.target_url)
    return out


def _issue(ctx, check_id, url=None):
    return next(
        i for i in ctx.issues if i.check == check_id and (url is None or i.target_url == url)
    )


def _skipped(ctx) -> dict[str, str]:
    return {s.id: s.reason for s in ctx.skipped}


_UNDATED_POST = "<article><h1>Undated</h1><p>copy</p></article>"
_DATED_POST = """<article><h1>Dated</h1>
<meta itemprop="author" content="Jane">
<time datetime="2026-09-01">Sep 1</time><p>copy</p></article>"""


def _site(**overrides):
    mapping = {
        "https://example.com/robots.txt": _FakeResponse(
            "User-agent: *\nDisallow:\n", headers={"content-type": "text/plain"}
        ),
        "https://example.com/": _page(
            '<a href="/about">About us</a>'
            '<a href="/contact">Contact us</a>'
            '<a href="/privacy">Privacy policy</a>'
            '<a href="/terms">Terms of service</a>'
            '<a href="/blog/undated">u</a>'
            '<a href="/blog/dated">d</a>'
            '<a href="/blog/cited">c</a>'
            '<a href="/health/symptom-checker">h</a>'
        ),
        "https://example.com/about": _page("<h1>About</h1>"),
        "https://example.com/contact": _page(
            "<h1>Contact</h1>", head='<meta name="robots" content="noindex">'
        ),
        "https://example.com/privacy": _FakeResponse("", status_code=404),
        "https://example.com/terms": _page("<h1>Terms</h1>"),
        "https://example.com/blog/undated": _page(_UNDATED_POST),
        "https://example.com/blog/dated": _page(
            _DATED_POST + '<footer><a href="https://source.example/x">src</a></footer>'
        ),
        "https://example.com/blog/cited": _page(
            _UNDATED_POST + '<main><a href="https://source.example/y">src</a></main>'
        ),
        "https://example.com/health/symptom-checker": _page("<h1>Symptoms</h1>"),
    }
    mapping.update(overrides)
    return mapping


def test_attribution_fires_only_on_in_scope_pages_that_declared_nothing():
    ctx = _native_ctx(_site(), classify_links=True)
    fired = _fired(ctx)
    undated, dated = "https://example.com/blog/undated", "https://example.com/blog/dated"
    assert undated in fired.get("NO_AUTHOR_BYLINE", set())
    assert dated not in fired.get("NO_AUTHOR_BYLINE", set())
    assert undated in fired.get("NO_CONTENT_DATES", set())
    assert dated not in fired.get("NO_CONTENT_DATES", set())
    # A finding states why the page entered scope and which carriers it read --
    # a reviewer sees "no declared markup", never a trust verdict.
    details = _issue(ctx, "NO_AUTHOR_BYLINE", undated).details
    assert details["scope"] == "declared:article_element"
    assert details["declared_signals"] == 0
    # A page with no article marker and no content-path URL never enters scope.
    assert "https://example.com/" not in fired.get("NO_AUTHOR_BYLINE", set())
    assert "https://example.com/about" not in fired.get("NO_CONTENT_DATES", set())


def test_an_unparsed_body_is_unmeasured_not_a_measured_absence():
    # The article page's body exceeds the fetch cap: it is a real 200 HTML
    # page, but it was never parsed -- its trust signals are unmeasured, not
    # a measured absence. The homepage stays small enough to parse so the
    # crawl still discovers it.
    ctx = _native_ctx(
        {
            "https://example.com/robots.txt": _FakeResponse(
                "User-agent: *\nDisallow:\n", headers={"content-type": "text/plain"}
            ),
            "https://example.com/": _page('<a href="/blog/undated">u</a>'),
            "https://example.com/blog/undated": _page(_UNDATED_POST + "<p>" + "x " * 600),
        },
        max_response_bytes=900,
    )
    skipped = _skipped(ctx)
    assert "https://example.com/blog/undated" not in _fired(ctx).get("NO_AUTHOR_BYLINE", set())
    assert "NO_AUTHOR_BYLINE" in skipped
    assert "never measured" in skipped["NO_AUTHOR_BYLINE"]


def test_trust_page_states_keep_missing_distinct_from_undiscovered():
    ctx = _native_ctx(_site(), classify_links=True, scope={"exclude_patterns": [r"/terms"]})
    fired = _fired(ctx)
    trust = ctx.trust_evidence["trust_pages"]
    assert trust["about"]["state"] == "found_indexable"
    assert "MISSING_ABOUT_PAGE" not in {i.check for i in ctx.issues}
    assert "https://example.com/contact" in fired.get("MISSING_CONTACT_PAGE", set())
    assert trust["contact"]["state"] == "found_non_indexable"
    assert "https://example.com/privacy" in fired.get("MISSING_PRIVACY_POLICY", set())
    assert trust["privacy"]["state"] == "found_error"
    # Linked but never fetched is its own state -- the crawl saw the anchor,
    # not the page, and the finding says exactly that.
    terms = _issue(ctx, "MISSING_TERMS_PAGE")
    assert terms.details["state"] == "discovered_not_crawled"
    assert terms.details["discovered_not_crawled"] == ["https://example.com/terms"]
    assert trust["terms"]["state"] == "discovered_not_crawled"


def test_a_trust_page_nobody_linked_is_not_discovered_not_missing():
    ctx = _native_ctx(
        {
            "https://example.com/robots.txt": _FakeResponse(
                "User-agent: *\nDisallow:\n", headers={"content-type": "text/plain"}
            ),
            "https://example.com/": _page("<h1>Home</h1>"),
        }
    )
    fired = _fired(ctx)
    issue = _issue(ctx, "MISSING_ABOUT_PAGE")
    assert issue.details["state"] == "not_discovered"
    assert issue.target_url is None
    assert None in fired.get("MISSING_ABOUT_PAGE", set())
    assert ctx.trust_evidence["trust_pages"]["about"]["state"] == "not_discovered"


def test_citations_require_a_content_position_or_a_countable_absence():
    ctx = _native_ctx(_site(), classify_links=True)
    fired = _fired(ctx)
    # A footer-only external link is positioned but not content: measured "none".
    assert "https://example.com/blog/dated" in fired.get("FEW_CITATIONS", set())
    # A content-position external link is the only carrier that reads as a citation.
    assert "https://example.com/blog/cited" not in fired.get("FEW_CITATIONS", set())
    # No external links at all is the other measurable fact: zero outlinks.
    assert "https://example.com/blog/undated" in fired.get("FEW_CITATIONS", set())
    summary = ctx.trust_evidence["citations"]
    assert summary["applicable_pages"] == 3
    assert summary["with_content_citations"] == 1


def test_citations_without_position_evidence_are_unmeasured_not_absent():
    ctx = _native_ctx(
        {
            "https://example.com/robots.txt": _FakeResponse(
                "User-agent: *\nDisallow:\n", headers={"content-type": "text/plain"}
            ),
            "https://example.com/": _page('<a href="/blog/linked">l</a>'),
            "https://example.com/blog/linked": _page(
                _DATED_POST + '<a href="https://source.example/x">src</a>'
            ),
        }
    )
    # classify_links stayed off: the external link has no recorded position,
    # so citations are unmeasured -- the check names the population rather
    # than claiming no citations exist.
    fired = _fired(ctx)
    assert "https://example.com/blog/linked" not in fired.get("FEW_CITATIONS", set())
    assert "FEW_CITATIONS" in _skipped(ctx)
    assert ctx.trust_evidence["citations"]["unmeasured_pages"] == 1


def test_ymyl_is_a_review_candidate_never_a_classification():
    ctx = _native_ctx(_site(), classify_links=True)
    fired = _fired(ctx)
    assert "https://example.com/health/symptom-checker" in fired.get("YMYL_REVIEW_CANDIDATE", set())
    assert "https://example.com/blog/dated" not in fired.get("YMYL_REVIEW_CANDIDATE", set())
    issue = _issue(ctx, "YMYL_REVIEW_CANDIDATE", "https://example.com/health/symptom-checker")
    assert set(issue.details["matched"]["health"]) == {"health", "symptom"}
    assert "not a YMYL classification" in issue.details["rationale"]
    assert issue.severity == "notice"


def test_the_summary_block_reports_scope_and_anchor_evidence():
    ctx = _native_ctx(_site(), classify_links=True)
    evidence = ctx.trust_evidence
    assert evidence["scope"]["indexable_html_pages"] >= 4
    assert evidence["scope"]["basis"]
    assert evidence["anchor_evidence"] == "all_inlinks"


def test_a_crawl_with_no_article_scope_says_the_check_did_not_apply():
    ctx = _native_ctx(
        {
            "https://example.com/robots.txt": _FakeResponse(
                "User-agent: *\nDisallow:\n", headers={"content-type": "text/plain"}
            ),
            "https://example.com/": _page("<h1>Home</h1>"),
        }
    )
    skipped = _skipped(ctx)
    assert "did not apply" in skipped["NO_AUTHOR_BYLINE"]
    assert "did not apply" in skipped["NO_CONTENT_DATES"]
    assert "did not apply" in skipped["FEW_CITATIONS"]


# ---------------------------------------------------------------------------
# The same checks over Screaming Frog shaped exports.
# ---------------------------------------------------------------------------

_EXPORT_COLS = [
    "Address",
    "Content Type",
    "Status Code",
    "Status",
    "Indexability",
    "Title 1",
    "External Outlinks",
]
_INLINK_COLS = [
    "Type",
    "Source",
    "Destination",
    "Anchor Text",
    "Status Code",
    "Follow",
    "Link Position",
]


def _export_dir(tmp_path, internal_rows, inlink_rows=None, extra_cols=(), extra_cells=()):
    d = tmp_path / "exports"
    d.mkdir(parents=True)
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(_EXPORT_COLS) + list(extra_cols))
        for row in internal_rows:
            w.writerow(list(row) + list(extra_cells))
    if inlink_rows is not None:
        with open(d / "all_inlinks.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(_INLINK_COLS)
            w.writerows(inlink_rows)
    return str(d)


def _export_row(url, *, indexability="Indexable", title="T", outlinks="0"):
    return [url, "text/html", "200", "OK", indexability, title, outlinks]


def _audit(tmp_path, internal_rows, inlink_rows=None, extra_cols=(), extra_cells=()):
    exports_dir = _export_dir(tmp_path, internal_rows, inlink_rows, extra_cols, extra_cells)
    return run_audit(input_mode="parse-exports", exports_dir=exports_dir, log=lambda m: None)


def _export_fired(res) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for issue in res.issues:
        out.setdefault(issue.check, set()).add(issue.target_url)
    return out


def test_an_export_without_signal_columns_skips_with_its_reason(tmp_path):
    res = _audit(tmp_path, [_export_row("https://example.com/blog/a")])
    skipped = {s.id for s in res.skipped}
    assert "NO_AUTHOR_BYLINE" in skipped
    assert "NO_CONTENT_DATES" in skipped


def test_a_custom_extraction_string_is_a_medium_confidence_author_signal(tmp_path):
    # The one thing an export can carry: a Custom Extraction the operator named
    # "Trust Signals". A non-empty cell is author evidence of unknown family --
    # it can never be a date signal, which is exactly what the fixture asserts.
    res = _audit(
        tmp_path,
        [_export_row("https://example.com/blog/a"), _export_row("https://example.com/blog/b")],
        extra_cols=("Trust Signals",),
        extra_cells=("a named person",),
    )
    fired = _export_fired(res)
    assert "https://example.com/blog/a" not in fired.get("NO_AUTHOR_BYLINE", set())
    assert "https://example.com/blog/a" in fired.get("NO_CONTENT_DATES", set())
    issue = next(i for i in res.issues if i.check == "NO_CONTENT_DATES")
    authors = issue.evidence["author_signals"]
    assert authors[0]["signal"] == "custom_column"
    assert authors[0]["confidence"] == "medium"

    # An empty cell is unmeasured, not a measured absence.
    res2 = _audit(
        tmp_path / "empty",
        [_export_row("https://example.com/blog/a")],
        extra_cols=("Trust Signals",),
        extra_cells=("",),
    )
    assert "NO_AUTHOR_BYLINE" in {s.id for s in res2.skipped}
    assert "https://example.com/blog/a" not in _export_fired(res2).get("NO_AUTHOR_BYLINE", set())


def test_export_trust_pages_report_discovered_and_index_states(tmp_path):
    res = _audit(
        tmp_path,
        [
            _export_row("https://example.com/"),
            _export_row("https://example.com/about"),
            _export_row("https://example.com/contact"),
            _export_row("https://example.com/privacy"),
            _export_row("https://example.com/terms"),
        ],
    )
    fired_checks = {i.check for i in res.issues}
    assert "MISSING_ABOUT_PAGE" not in fired_checks
    assert "MISSING_CONTACT_PAGE" not in fired_checks
    assert "MISSING_PRIVACY_POLICY" not in fired_checks
    assert "MISSING_TERMS_PAGE" not in fired_checks
    assert res.summary["trust_evidence"]["trust_pages"]["about"]["state"] == "found_indexable"

    states = _audit(
        tmp_path / "states",
        [
            _export_row("https://example.com/"),
            _export_row("https://example.com/privacy", indexability="Non-Indexable"),
        ],
    )
    fired = _export_fired(states)
    assert "https://example.com/privacy" in fired.get("MISSING_PRIVACY_POLICY", set())
    privacy = next(i for i in states.issues if i.check == "MISSING_PRIVACY_POLICY")
    assert privacy.details["state"] == "found_non_indexable"
    # Terms was never in the crawled set: not_discovered, a state that does
    # not claim the site lacks the page.
    terms = next(i for i in states.issues if i.check == "MISSING_TERMS_PAGE")
    assert terms.details["state"] == "not_discovered"
    assert states.summary["trust_evidence"]["trust_pages"]["terms"]["state"] == "not_discovered"


def test_export_trust_pages_discovered_only_by_anchor_stay_distinct(tmp_path):
    res = _audit(
        tmp_path,
        [_export_row("https://example.com/")],
        inlink_rows=[
            [
                "Hyperlink",
                "https://example.com/",
                "https://example.com/contact-us",
                "Contact us",
                "200",
                "true",
                "Content",
            ]
        ],
    )
    fired = _export_fired(res)
    issue = next(i for i in res.issues if i.check == "MISSING_CONTACT_PAGE")
    assert issue.details["state"] == "discovered_not_crawled"
    assert issue.details["discovered_not_crawled"] == ["https://example.com/contact-us"]
    assert None in fired.get("MISSING_CONTACT_PAGE", set())


def test_export_citations_read_positions_and_outlink_counts(tmp_path):
    cited, footer_only, none = (
        "https://example.com/blog/cited",
        "https://example.com/blog/footer",
        "https://example.com/blog/none",
    )
    res = _audit(
        tmp_path,
        [
            _export_row(cited, outlinks="1"),
            _export_row(footer_only, outlinks="1"),
            _export_row(none, outlinks="0"),
        ],
        inlink_rows=[
            ["Hyperlink", cited, "https://source.example/a", "src", "200", "true", "Content"],
            [
                "Hyperlink",
                footer_only,
                "https://source.example/b",
                "src",
                "200",
                "true",
                "Footer",
            ],
        ],
    )
    fired = _export_fired(res)
    assert cited not in fired.get("FEW_CITATIONS", set())
    assert footer_only in fired.get("FEW_CITATIONS", set())
    assert none in fired.get("FEW_CITATIONS", set())


def test_export_ymyl_flag_matches_whole_words_and_quotes_them(tmp_path):
    res = _audit(
        tmp_path,
        [
            _export_row("https://example.com/finance/mortgage-rates"),
            _export_row("https://example.com/blog/financed-the-van"),
        ],
    )
    fired = _export_fired(res)
    assert "https://example.com/finance/mortgage-rates" in fired.get("YMYL_REVIEW_CANDIDATE", set())
    # "financed" is not the word "finance" -- whole-word matching keeps a
    # partial hit out of the review queue.
    assert "https://example.com/blog/financed-the-van" not in fired.get(
        "YMYL_REVIEW_CANDIDATE", set()
    )
    issue = next(i for i in res.issues if i.check == "YMYL_REVIEW_CANDIDATE")
    assert set(issue.details["matched"]["finance"]) == {"finance", "mortgage"}
    assert "not a YMYL classification" in issue.details["rationale"]


def test_a_partial_crawl_qualifies_every_not_discovered_state(tmp_path):
    from seohead.sf.core.aggregate import aggregate
    from seohead.sf.core.loader import load_exports

    exports_dir = _export_dir(tmp_path, [_export_row("https://example.com/")])
    ctx = AuditContext(load_exports(exports_dir), load_config(None))
    run_eeat(ctx)
    res = aggregate(ctx, {"input_mode": "crawl", "crawl_partial": True}, {}, {})
    trust = res.summary["trust_evidence"]
    assert trust["trust_pages"]["about"]["state"] == "not_discovered"
    assert "partial crawl" in trust["crawl_scope"]


# ---------------------------------------------------------------------------
# Storage: the field is optional and its nulls are real states.
# ---------------------------------------------------------------------------


def _write_trust_signals(legacy_run, value, drop_key=False):
    pages = legacy_run / "pages.jsonl"
    rows = [json.loads(line) for line in pages.read_text().splitlines()]
    if drop_key:
        rows[0].pop("trust_signals", None)
    else:
        rows[0]["trust_signals"] = value
    pages.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return rows


def test_stored_trust_signals_survive_an_import_unchanged(legacy_run, tmp_path):
    value = {
        "author": [{"signal": "meta_author", "confidence": "high", "value": "Jane"}],
        "dates": [{"signal": "time_element", "confidence": "medium", "value": "2026-09-01"}],
        "article": ["article_element"],
    }
    _write_trust_signals(legacy_run, value)
    out = import_run(legacy_run, tmp_path / "scan.sqlite", producer_build=BUILD)
    con = open_scan(out)
    try:
        stored = con.execute(
            "SELECT trust_signals_json FROM pages WHERE page_ordinal=0"
        ).fetchone()[0]
        assert json.loads(stored) == value
    finally:
        con.close()


def test_an_absent_trust_signals_key_is_accepted_as_an_older_scan(legacy_run, tmp_path):
    _write_trust_signals(legacy_run, None, drop_key=True)
    out = import_run(legacy_run, tmp_path / "scan.sqlite", producer_build=BUILD)
    con = open_scan(out)
    try:
        assert (
            con.execute("SELECT trust_signals_json FROM pages WHERE page_ordinal=0").fetchone()[0]
            is None
        )
    finally:
        con.close()


def test_a_present_null_is_stored_as_unmeasured_not_as_empty_signals(legacy_run, tmp_path):
    _write_trust_signals(legacy_run, None)
    out = import_run(legacy_run, tmp_path / "scan.sqlite", producer_build=BUILD)
    con = open_scan(out)
    try:
        assert (
            con.execute("SELECT trust_signals_json FROM pages WHERE page_ordinal=0").fetchone()[0]
            is None
        )
    finally:
        con.close()


def test_duplicate_ids_survive_legacy_import_and_export(legacy_run, tmp_path):
    value = [{"id": "menu-item", "count": 2}]
    pages = legacy_run / "pages.jsonl"
    rows = [json.loads(line) for line in pages.read_text().splitlines()]
    rows[0]["duplicate_ids"] = value
    pages.write_text("".join(json.dumps(row) + "\n" for row in rows))
    scan = import_run(legacy_run, tmp_path / "scan.sqlite", producer_build=BUILD)
    export_run(scan, tmp_path / "export")
    exported = json.loads((tmp_path / "export" / "pages.jsonl").read_text().splitlines()[0])
    assert exported["duplicate_ids"] == value


@pytest.mark.parametrize(
    "value",
    [
        {"author": [], "dates": []},
        {"author": [], "dates": [], "article": [], "extra": 1},
        {"author": [{"signal": "meta_author", "confidence": "high"}], "dates": [], "article": []},
        {
            "author": [{"signal": "meta_author", "confidence": "high", "value": 3}],
            "dates": [],
            "article": [],
        },
        {"author": [], "dates": [], "article": [1]},
        "not an object at all",
    ],
)
def test_a_trust_signals_record_of_the_wrong_shape_is_refused(legacy_run, tmp_path, value):
    _write_trust_signals(legacy_run, value)
    with pytest.raises(ScanError, match="trust_signals"):
        import_run(legacy_run, tmp_path / "bad.sqlite", producer_build=BUILD)


def test_partially_positioned_external_links_cannot_prove_absent_citations(tmp_path):
    url = "https://example.com/blog/article"
    result = _audit(
        tmp_path,
        [_export_row(url, outlinks="2")],
        inlink_rows=[
            ["Hyperlink", url, "https://source.example/footer", "Source", "200", "true", "Footer"],
            ["Hyperlink", url, "https://source.example/unknown", "Source", "200", "true", ""],
        ],
    )
    assert not any(issue.check == "FEW_CITATIONS" for issue in result.issues)
    assert "FEW_CITATIONS" in {entry.id for entry in result.skipped}
