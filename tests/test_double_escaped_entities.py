"""collapse_whitespace must not decode entities a second time (issue #138).

BeautifulSoup's lxml parser already decodes character references exactly once while building
the tree, for both text nodes and attribute values -- the same number of times a browser's
HTML tokenizer does. A second decode is invisible on ordinary single-escaped markup (decoding
is idempotent past the first pass), but a page whose CMS or import pipeline already
double-escaped its entities (a real, common artifact: ``htmlspecialchars()``/``esc_html()``
applied twice, a JSON-escaped field rendered without unescaping) has it turn visibly-broken
entity soup into clean-looking text -- shortening the reported length below what a browser
tab or a Google SERP snippet actually shows, and flipping length-based checks in both
directions.
"""

from seohead.checks.parser import collapse_whitespace, parse_html

_DOUBLE_ESCAPED_TITLE = (
    "Nuts &amp;amp; Bolts &amp;amp; Screws &amp;amp; Washers &amp;amp; Rivets Store"
)
_DOUBLE_ESCAPED_DESC = (
    "Shop nuts &amp;amp; bolts &amp;amp; screws online today, fast shipping guaranteed."
)

# The 60/70-char thresholds TITLE_TOO_LONG/DESC_TOO_SHORT (seohead/sf/core/rules.py) use --
# that rule engine runs over a Screaming Frog export's own title/description columns, not over
# parse_html() output, so the fix is verified here the way the issue's acceptance criteria
# state it: against the length parse_html reports for the same source markup.
_TITLE_MAX_CHARS = 60
_DESC_MIN_CHARS = 70


def test_double_escaped_title_is_decoded_only_once():
    html = f"<html><head><title>{_DOUBLE_ESCAPED_TITLE}</title></head><body><p>x</p></body></html>"
    r = parse_html(html, "https://example.com/page")
    assert r["title"] == "Nuts &amp; Bolts &amp; Screws &amp; Washers &amp; Rivets Store"
    assert len(r["title"]) == 62  # what a browser tab / SERP snippet actually renders
    assert len(r["title"]) > _TITLE_MAX_CHARS  # TITLE_TOO_LONG should fire on this length


def test_double_escaped_description_is_decoded_only_once():
    html = (
        '<html><head><meta name="description" content="'
        f'{_DOUBLE_ESCAPED_DESC}"></head><body><p>x</p></body></html>'
    )
    r = parse_html(html, "https://example.com/page")
    assert len(r["meta_description"]) == 74
    assert len(r["meta_description"]) >= _DESC_MIN_CHARS  # DESC_TOO_SHORT should not fire


def test_single_escaped_markup_is_unaffected():
    """Ordinary, single-escaped source must report exactly as before."""
    html = (
        "<html><head><title>Tom &amp; Jerry &mdash; Caf&eacute;&nbsp;Shop</title>"
        '<meta name="description" content="Rock &amp; roll since 1990."></head>'
        "<body><p>x</p></body></html>"
    )
    r = parse_html(html, "https://example.com/page")
    assert r["title"] == "Tom & Jerry — Café Shop"
    assert r["meta_description"] == "Rock & roll since 1990."


def test_collapse_whitespace_no_longer_decodes_entities():
    """Direct unit test of the helper: it only collapses whitespace now."""
    assert collapse_whitespace("Tom &amp;amp; Jerry") == "Tom &amp;amp; Jerry"
    assert collapse_whitespace("  a   b  ") == "a b"
    assert collapse_whitespace(None) == ""
