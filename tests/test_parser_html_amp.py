"""#1021 slice 1: the <html amp> / <html ⚡> marker is captured as data by parse_html."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from seohead.checks.parser import document_html_amp, parse_html


@pytest.mark.parametrize(
    "open_tag, expected",
    [
        ("<html amp>", True),
        ("<html ⚡>", True),
        ('<html amp lang="en">', True),
        ("<html>", False),
        ('<html lang="en">', False),
    ],
)
def test_root_html_tag_amp_marker(open_tag, expected):
    html = f"{open_tag}<head><title>t</title></head><body><p>x</p></body></html>"
    assert parse_html(html, "https://example.com/a")["html_amp"] is expected


def test_amp_word_in_body_text_is_not_a_marker():
    html = "<html><head><title>t</title></head><body><p>amp ⚡ is a framework</p></body></html>"
    assert parse_html(html, "https://example.com/a")["html_amp"] is False


def test_amp_attribute_on_nested_element_is_not_a_marker():
    html = '<html><head></head><body><div amp="1">x</div></body></html>'
    assert document_html_amp(BeautifulSoup(html, "lxml")) is False
