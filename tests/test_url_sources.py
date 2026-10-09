"""Test URL-bearing attributes beyond ``<a href>``."""

from seohead.checks.parser import parse_html


def _sources(html: str) -> list[dict]:
    r = parse_html(
        html, "https://example.com/", {"url_sources": True, "links": False, "text": False}
    )
    return r["url_sources"]


def test_srcset_yields_multiple_urls():
    src = _sources('<img src="/a.jpg" srcset="/a.jpg 1x, /b.jpg 2x">')
    urls = {s["url"] for s in src if s["tag"] == "img"}
    assert "https://example.com/a.jpg" in urls
    assert "https://example.com/b.jpg" in urls


def test_form_and_button_formaction():
    src = _sources('<form action="/submit"><button formaction="/alt">go</button></form>')
    urls = {s["url"] for s in src}
    assert "https://example.com/submit" in urls
    assert "https://example.com/alt" in urls


def test_a_ping_splits_into_multiple_urls():
    src = _sources('<a ping="https://tracker.example/p1 https://tracker.example/p2">x</a>')
    ping_urls = [s["url"] for s in src if s["attr"] == "ping"]
    assert "https://tracker.example/p1" in ping_urls
    assert "https://tracker.example/p2" in ping_urls


def test_cite_on_blockquote_and_meta_refresh():
    src = _sources(
        '<blockquote cite="https://q.example/orig">q</blockquote>'
        '<meta http-equiv="refresh" content="0;url=/moved">'
    )
    urls = {s["url"] for s in src}
    assert "https://q.example/orig" in urls
    assert "https://example.com/moved" in urls


def test_itemtype_collected():
    src = _sources('<div itemtype="https://schema.org/Product">p</div>')
    assert any(s["url"] == "https://schema.org/Product" and s["attr"] == "itemtype" for s in src)


def test_data_and_fragment_and_mailto_skipped():
    src = _sources(
        '<img src="data:image/png;base64,xx"><a href="#frag">x</a><img src="mailto:a@b.c">'
    )
    urls = {s["url"] for s in src}
    assert not any("data:" in u for u in urls)
    assert not any(u.endswith("#frag") for u in urls)


def test_url_sources_off_by_default():
    r = parse_html('<img src="/a.jpg">', "https://example.com/")
    assert "url_sources" not in r  # The disabled option preserves backward compatibility.


# ── inert <template> content (issue #140) ───────────────────────────────────


def test_template_image_is_not_a_url_source():
    """<template> is a DocumentFragment: a browser never requests what is inside it."""
    src = _sources('<template><img src="/template-image.jpg"></template>')
    assert src == []


def test_noscript_image_is_still_a_url_source():
    """Unlike <template>, <noscript> is a real fallback a JS-disabled client does load."""
    src = _sources('<noscript><img src="/fallback.jpg"></noscript>')
    assert {"url": "https://example.com/fallback.jpg", "tag": "img", "attr": "src"} in src


# ── CSS-referenced resources ────────────────────────────────────────────────


def test_css_urls_are_extracted_from_inline_style_attributes():
    """A page whose images are CSS backgrounds was invisible to every image check."""
    from seohead.checks.parser import parse_html

    html = "<html><body><div style=\"background: url('/hero.png') no-repeat\"></div></body></html>"
    found = parse_html(html, "https://e.com/", {"url_sources": True})["url_sources"]
    assert {"url": "https://e.com/hero.png", "tag": "div", "attr": "style"} in found


def test_css_urls_are_extracted_from_style_blocks():
    from seohead.checks.parser import parse_html

    html = "<html><head><style>.hero{background-image:url(/bg.webp)}</style></head><body></body></html>"
    found = parse_html(html, "https://e.com/", {"url_sources": True})["url_sources"]
    assert {"url": "https://e.com/bg.webp", "tag": "style", "attr": "css"} in found


def test_css_extraction_covers_properties_beyond_background_image():
    """border-image, mask-image and content fetch resources the same way."""
    from seohead.checks.parser import extract_css_urls

    css = "a{border-image:url(b.png)} b{mask-image:url('m.svg')} c{content:url(\"i.gif\")}"
    assert extract_css_urls(css) == ["b.png", "m.svg", "i.gif"]


def test_css_url_quoting_variants_all_parse():
    from seohead.checks.parser import extract_css_urls

    css = "a{background:url(bare.png)} b{background:url('single.png')} c{background:url(\"double.png\")}"
    assert extract_css_urls(css) == ["bare.png", "single.png", "double.png"]


def test_css_url_whitespace_is_tolerated():
    from seohead.checks.parser import extract_css_urls

    assert extract_css_urls("a{background:url(  spaced.png  )}") == ["spaced.png"]


def test_data_uris_in_css_are_skipped_like_everywhere_else():
    from seohead.checks.parser import parse_html

    html = (
        '<html><body><div style="background:url(data:image/gif;base64,R0lGOD)"></div></body></html>'
    )
    found = parse_html(html, "https://e.com/", {"url_sources": True})["url_sources"]
    assert found == []


def test_css_text_without_urls_yields_nothing():
    from seohead.checks.parser import extract_css_urls

    assert extract_css_urls("body{color:red}") == []
    assert extract_css_urls("") == []


def test_a_css_background_and_an_img_are_both_reported():
    """The point of the change: neither source hides the other."""
    from seohead.checks.parser import parse_html

    html = (
        "<html><head><style>.h{background-image:url(/css.png)}</style></head>"
        '<body><img src="/tag.png"></body></html>'
    )
    urls = {
        u["url"] for u in parse_html(html, "https://e.com/", {"url_sources": True})["url_sources"]
    }
    assert urls == {"https://e.com/css.png", "https://e.com/tag.png"}
