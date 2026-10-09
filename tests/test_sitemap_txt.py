"""Tests for plain-text (.txt) sitemaps in ``seohead.checks.sitemap``."""

from seohead.checks import sitemap as S


def test_parse_text_sitemap_basic():
    body = b"# header\nhttps://example.com/a\nhttps://example.com/b\n\n"
    r = S.parse_text_sitemap(body, "https://example.com/sitemap.txt")
    assert r["type"] == "text"
    assert [u["loc"] for u in r["urls"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_parse_text_sitemap_resolves_relative_and_skips_garbage():
    body = b"https://example.com/a\n/relative-b\nftp://example.org/y\nnot-a-url\n# comment\n"
    r = S.parse_text_sitemap(body, "https://example.com/sitemap.txt")
    locs = [u["loc"] for u in r["urls"]]
    assert "https://example.com/a" in locs
    assert "https://example.com/relative-b" in locs  # Resolved against the base URL.
    assert all(not loc.startswith("ftp") for loc in locs)  # FTP URLs are excluded.
    assert "not-a-url" not in locs


def test_parse_sitemap_detects_text_when_not_xml():
    body = b"https://example.com/1\nhttps://example.com/2\n"
    r = S.parse_sitemap(body, "https://example.com/sitemap.txt")
    assert r["type"] == "text"
    assert len(r["urls"]) == 2


def test_parse_sitemap_xml_still_takes_precedence():
    xml = (
        b'<?xml version="1.0"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://example.com/1</loc></url></urlset>"
    )
    r = S.parse_sitemap(xml, "https://example.com/s.xml")
    assert r["type"] == "urlset"
    assert len(r["urls"]) == 1
