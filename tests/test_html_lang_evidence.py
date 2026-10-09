"""<html lang> evidence on parse_html (epic #1002): the declared language is kept as
written, and an absent or empty attribute is distinguishable from a declared one.
"""

from __future__ import annotations

from seohead.checks.parser import parse_html


def _lang(html: str) -> dict[str, str]:
    return parse_html(html, "https://example.com/")["html_lang"]


def test_declared_lang_is_kept_and_primary_subtag_derived():
    assert _lang('<html lang="en-US"><body>x</body></html>') == {
        "declared": "en-US",
        "declared_primary": "en",
    }


def test_missing_lang_attribute_is_empty_not_declared():
    assert _lang("<html><body>x</body></html>") == {"declared": "", "declared_primary": ""}


def test_empty_lang_attribute_is_empty_not_declared():
    assert _lang('<html lang=""><body>x</body></html>') == {"declared": "", "declared_primary": ""}
