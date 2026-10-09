"""Dual-crawl cross-validation: diff two evidence-gathering passes over the same page."""

from seohead.checks.dualcrawl import build_page_evidence, compare_evidence

# ── build_page_evidence ──────────────────────────────────────────────────────


def test_background_only_page_is_seen_without_any_img_tag():
    """The flagship case: no <img> at all, only CSS backgrounds."""
    html = (
        "<html><head><style>.hero{background-image:url(/hero.png)}</style></head>"
        '<body><div style="background:url(/icon.svg)"></div></body></html>'
    )
    evidence = build_page_evidence(html, "https://example.com/")
    assert evidence["images"] == {"https://example.com/hero.png", "https://example.com/icon.svg"}


def test_img_and_css_background_are_both_collected():
    html = (
        "<html><head><style>.h{background-image:url(/css.png)}</style></head>"
        '<body><img src="/tag.png"></body></html>'
    )
    evidence = build_page_evidence(html, "https://example.com/")
    assert evidence["images"] == {"https://example.com/css.png", "https://example.com/tag.png"}


def test_non_image_url_sources_are_excluded():
    """script src and form action are url_sources but not images."""
    html = '<script src="/app.js"></script><form action="/submit"></form>'
    evidence = build_page_evidence(html, "https://example.com/")
    assert evidence["images"] == set()


def test_links_are_collected_too():
    html = '<a href="/a">a</a><a href="https://other.example/b">b</a>'
    evidence = build_page_evidence(html, "https://example.com/")
    assert evidence["links"] == {"https://example.com/a", "https://other.example/b"}


def test_extra_images_fold_in_computed_backgrounds():
    """A live browser's computed style resolves external stylesheets; plain HTML text cannot."""
    evidence = build_page_evidence(
        "<body></body>",
        "https://example.com/",
        extra_images={"https://example.com/from-external.css.png"},
    )
    assert evidence["images"] == {"https://example.com/from-external.css.png"}


def test_empty_html_still_carries_extra_images():
    evidence = build_page_evidence("", "https://example.com/", extra_images={"https://e.com/x.png"})
    assert evidence == {"images": {"https://e.com/x.png"}, "links": set()}


# ── compare_evidence ─────────────────────────────────────────────────────────


def test_schema_is_distinct_from_the_sf_compare_module():
    """Different key from #21's crawl-to-crawl regression compare (compare.v1)."""
    result = compare_evidence({}, {})
    assert result["schema_version"] == "dualcrawl.v1"
    assert result["schema_version"] != "compare.v1"


def test_agreement_produces_no_url_entry():
    """Silence here means the two methods agreed -- the opposite of a single crawl's silence."""
    evidence = {"https://e.com/": {"images": {"https://e.com/a.png"}}}
    result = compare_evidence(evidence, evidence)
    assert result["urls"] == {}
    assert result["summary"] == {"urls_with_differences": 0, "only_in_a": 0, "only_in_b": 0}


def test_rendered_only_images_are_reported_keyed_by_url_and_evidence_type():
    """A rendered pass sees a background image a static pass cannot -- the finding itself."""
    static = {"https://e.com/page": {"images": {"https://e.com/tag.png"}, "links": set()}}
    rendered = {
        "https://e.com/page": {
            "images": {"https://e.com/tag.png", "https://e.com/external-bg.png"},
            "links": {"https://e.com/found-by-js"},
        }
    }
    result = compare_evidence(static, rendered, method_a="static", method_b="rendered")
    page_diff = result["urls"]["https://e.com/page"]
    assert page_diff["images"] == {"only_in_a": [], "only_in_b": ["https://e.com/external-bg.png"]}
    assert page_diff["links"] == {"only_in_a": [], "only_in_b": ["https://e.com/found-by-js"]}
    assert result["methods"] == {"a": "static", "b": "rendered"}
    assert result["summary"] == {"urls_with_differences": 1, "only_in_a": 0, "only_in_b": 2}


def test_a_new_url_reports_its_full_evidence_as_only_in_b():
    result = compare_evidence({}, {"https://e.com/new": {"images": {"https://e.com/x.png"}}})
    assert result["urls"]["https://e.com/new"]["images"]["only_in_b"] == ["https://e.com/x.png"]


def test_not_merged_silently_each_url_stays_its_own_entry():
    """Two different pages with different gaps must not collapse into one combined result."""
    static = {"https://e.com/a": {"images": set()}, "https://e.com/b": {"images": set()}}
    rendered = {
        "https://e.com/a": {"images": {"https://e.com/a-bg.png"}},
        "https://e.com/b": {"images": {"https://e.com/b-bg.png"}},
    }
    result = compare_evidence(static, rendered)
    assert set(result["urls"]) == {"https://e.com/a", "https://e.com/b"}
    assert result["urls"]["https://e.com/a"]["images"]["only_in_b"] == ["https://e.com/a-bg.png"]
    assert result["urls"]["https://e.com/b"]["images"]["only_in_b"] == ["https://e.com/b-bg.png"]
