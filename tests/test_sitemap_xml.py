"""Tests for XML sitemap generation from page evidence (``seohead.crawl.sitemap_xml``)."""

import xml.etree.ElementTree as ET

from seohead.crawl.sitemap_xml import render_sitemaps, select_indexable

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _page(url, **overrides):
    base = {
        "url": url,
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "meta_robots": "",
        "x_robots": "",
        "canonical": url,
        "blocked_by_robots": False,
    }
    base.update(overrides)
    return base


def test_select_indexable_includes_self_canonical_html_200():
    included, excluded = select_indexable([_page("https://example.com/a")])
    assert included == ["https://example.com/a"]
    assert excluded == []


def test_select_indexable_reports_each_exclusion_reason():
    pages = [
        _page("https://example.com/blocked", blocked_by_robots=True),
        _page("https://example.com/missing", status_code=404),
        _page("https://example.com/redirect", status_code=301),
        _page("https://example.com/pdf", content_type="application/pdf"),
        _page("https://example.com/noindex", meta_robots="noindex, follow"),
        _page("https://example.com/none-directive", x_robots="none"),
        _page("https://example.com/other", canonical="https://example.com/canonical"),
        _page("https://example.com/ok"),
    ]
    included, excluded = select_indexable(pages)
    assert included == ["https://example.com/ok"]
    assert excluded == [
        ("https://example.com/blocked", "blocked_by_robots"),
        ("https://example.com/missing", "not_200"),
        ("https://example.com/redirect", "not_200"),
        ("https://example.com/pdf", "not_html"),
        ("https://example.com/noindex", "noindex"),
        ("https://example.com/none-directive", "noindex"),
        ("https://example.com/other", "non_canonical"),
    ]


def test_select_indexable_treats_scoped_noindex_as_not_global():
    included, _ = select_indexable([_page("https://example.com/a", x_robots="bingbot: noindex")])
    assert included == ["https://example.com/a"]


def test_select_indexable_empty_canonical_does_not_exclude():
    included, excluded = select_indexable([_page("https://example.com/a", canonical="")])
    assert included == ["https://example.com/a"]
    assert excluded == []


def test_render_single_file_is_a_valid_urlset():
    files = render_sitemaps(
        ["https://example.com/a", "https://example.com/b?x=1&y=2"], "https://example.com"
    )
    assert list(files) == ["sitemap.xml"]
    root = ET.fromstring(files["sitemap.xml"])
    assert root.tag == f"{NS}urlset"
    locs = [u.find(f"{NS}loc").text for u in root.findall(f"{NS}url")]
    assert locs == ["https://example.com/a", "https://example.com/b?x=1&y=2"]


def test_render_empty_set_is_an_empty_urlset():
    files = render_sitemaps([], "https://example.com")
    root = ET.fromstring(files["sitemap.xml"])
    assert root.tag == f"{NS}urlset"
    assert root.findall(f"{NS}url") == []


def test_render_splits_by_url_count_with_index():
    urls = [f"https://example.com/p{i}" for i in range(5)]
    files = render_sitemaps(urls, "https://example.com/", max_urls=2)
    assert sorted(files) == ["sitemap-1.xml", "sitemap-2.xml", "sitemap-3.xml", "sitemap.xml"]
    index = ET.fromstring(files["sitemap.xml"])
    assert index.tag == f"{NS}sitemapindex"
    locs = [s.find(f"{NS}loc").text for s in index.findall(f"{NS}sitemap")]
    assert locs == [
        "https://example.com/sitemap-1.xml",
        "https://example.com/sitemap-2.xml",
        "https://example.com/sitemap-3.xml",
    ]
    collected = []
    for name in ("sitemap-1.xml", "sitemap-2.xml", "sitemap-3.xml"):
        part = ET.fromstring(files[name])
        assert len(part.findall(f"{NS}url")) <= 2
        collected += [u.find(f"{NS}loc").text for u in part.findall(f"{NS}url")]
    assert collected == urls


def test_render_splits_by_byte_size():
    urls = [f"https://example.com/{'x' * 200}{i}" for i in range(20)]
    files = render_sitemaps(urls, "https://example.com", max_bytes=1500)
    parts = [name for name in files if name != "sitemap.xml"]
    assert len(parts) > 1
    for name in parts:
        assert len(files[name]) <= 1500


def test_render_escapes_xml_special_characters():
    files = render_sitemaps(["https://example.com/a?x=1&y=<2>"], "https://example.com")
    assert b"&amp;" in files["sitemap.xml"]
    assert b"&lt;2&gt;" in files["sitemap.xml"]
    ET.fromstring(files["sitemap.xml"])
