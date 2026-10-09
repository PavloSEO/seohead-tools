"""Regression tests for issue #1051: media-qualified rel=alternate links are read."""

from __future__ import annotations

from bs4 import BeautifulSoup

from seohead.checks.parser import extract_mobile_alternates, parse_html

PAGE = """<html><head>
<title>Desktop</title>
<link rel="alternate" media="only screen and (max-width: 640px)" href="/m/page">
<link rel="alternate" hreflang="fr" href="/fr/page">
<link rel="alternate" media="print" href="https://other.example.com/print">
</head><body><h1>Hi</h1><template><link rel="alternate" media="all" href="/inert"></template></body></html>"""


def test_mobile_alternate_is_read_and_resolved_against_base():
    soup = BeautifulSoup(PAGE, "html.parser")
    assert extract_mobile_alternates(soup, "https://example.com/page") == [
        {
            "media": "only screen and (max-width: 640px)",
            "raw_href": "/m/page",
            "url": "https://example.com/m/page",
        },
        {
            "media": "print",
            "raw_href": "https://other.example.com/print",
            "url": "https://other.example.com/print",
        },
    ]


def test_hreflang_alternates_are_not_mobile_alternates():
    soup = BeautifulSoup(
        '<link rel="alternate" hreflang="fr" media="all" href="/fr">', "html.parser"
    )
    assert extract_mobile_alternates(soup, "https://example.com/") == []


def test_alternate_without_media_is_not_read():
    soup = BeautifulSoup('<link rel="alternate" href="/feed.xml">', "html.parser")
    assert extract_mobile_alternates(soup, "https://example.com/") == []


def test_parse_html_exposes_mobile_alternates():
    parsed = parse_html(PAGE, "https://example.com/page")
    assert [item["raw_href"] for item in parsed["mobile_alternates"]] == [
        "/m/page",
        "https://other.example.com/print",
    ]
    assert [item["lang"] for item in parsed["hreflang"]] == ["fr"]
