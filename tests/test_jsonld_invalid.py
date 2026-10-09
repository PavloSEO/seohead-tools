"""A page whose JSON-LD does not compile is not a page without JSON-LD.

Reporting the second is the more damaging error of the two: an operator reads
"no structured data", writes it in a report, and the real finding — that the
site ships markup on every page and every search engine discards it — is lost.
"""

from __future__ import annotations

from seohead.checks.parser import parse_html
from seohead.checks.schema_org import _extract_blocks, _findings

# A stray comment, which JSON does not allow, voids the whole block. This is
# the shape a hand-filled template arrives in.
COMMENTED = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "FurnitureStore",
 "logo": "https://example.com/logo.png", /* put the logo path here */
 "telephone": "+7 000 000"}
</script></head><body>x</body></html>"""

TRAILING_COMMA = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Organization", "name": "Example",}
</script></head><body>x</body></html>"""

NO_MARKUP = "<html><head></head><body>x</body></html>"

VALID = """<html><head><script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Organization", "name": "Example"}
</script></head><body>x</body></html>"""


def _report(html: str) -> list[str]:
    blocks, errors, found = _extract_blocks(html)
    return _findings(
        {
            "blocks": len(blocks),
            "blocks_found": found,
            "blocks_invalid": found - len(blocks),
            "parse_errors": errors,
            "entities": [],
            "graph": {
                "nodes": 0,
                "is_graph": False,
                "islands": [],
                "with_id": 0,
                "top_level": 0,
            },
            "rich_results": [],
            "other_markup": {"microdata": False, "rdfa": False},
        }
    )


def test_an_invalid_block_is_not_reported_as_no_markup():
    findings = _report(COMMENTED)
    assert not any("contains no JSON-LD blocks" in line for line in findings)
    assert any("cannot be parsed" in line for line in findings)


def test_an_empty_page_still_reports_no_markup():
    assert "The page contains no JSON-LD blocks" in _report(NO_MARKUP)


def test_a_valid_block_reports_neither():
    findings = _report(VALID)
    assert not any("JSON-LD" in line for line in findings)


def test_the_cause_is_named_for_comments():
    assert any("permits no comments" in line for line in _report(COMMENTED))


def test_the_cause_is_named_for_a_trailing_comma():
    assert any("permits no trailing comma" in line for line in _report(TRAILING_COMMA))


# --- the parse tool ---------------------------------------------------------
def test_parse_reports_the_invalid_block_rather_than_dropping_it():
    parsed = parse_html(COMMENTED, "https://example.com/")
    assert parsed["jsonld"] == []
    assert len(parsed["jsonld_invalid"]) == 1
    invalid = parsed["jsonld_invalid"][0]
    assert invalid["index"] == 1
    assert invalid["error"]
    assert "FurnitureStore" in invalid["excerpt"]


def test_parse_keeps_jsonld_to_blocks_that_parsed():
    parsed = parse_html(VALID, "https://example.com/")
    assert len(parsed["jsonld"]) == 1
    assert parsed["jsonld_invalid"] == []


def test_parse_reports_an_empty_block():
    html = '<html><head><script type="application/ld+json"></script></head></html>'
    assert (
        parse_html(html, "https://example.com/")["jsonld_invalid"][0]["error"] == "block is empty"
    )
