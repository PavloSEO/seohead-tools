"""Offline tests for link position classification (issue #20, part 3)."""

from bs4 import BeautifulSoup

from seohead.checks import link_position
from seohead.checks.content_area import find_content_root
from seohead.checks.parser import parse_html

_HTML = """<html><body>
<nav><a href="/nav1">Nav</a></nav>
<header><a href="/hdr1">Header</a></header>
<main>
  <p>Real body copy <a href="/content1">Content</a></p>
  <aside class="sidebar"><a href="/side1">Sidebar</a></aside>
</main>
<footer><a href="/foot1">Footer</a></footer>
</body></html>"""


def _links_by_href(soup: BeautifulSoup) -> dict[str, "BeautifulSoup"]:
    return {a["href"]: a for a in soup.find_all("a")}


def test_classify_link_assigns_nav_content_and_footer_correctly():
    """Acceptance criterion: nav, content, footer must be assigned correctly."""
    soup = BeautifulSoup(_HTML, features="lxml")
    content_root, _ = find_content_root(soup)
    links = _links_by_href(soup)

    assert link_position.classify_link(links["/nav1"], content_root) == "nav"
    assert link_position.classify_link(links["/hdr1"], content_root) == "header"
    assert link_position.classify_link(links["/content1"], content_root) == "content"
    assert link_position.classify_link(links["/side1"], content_root) == "sidebar"
    assert link_position.classify_link(links["/foot1"], content_root) == "footer"


def test_rules_are_ordered_first_match_wins():
    """A rule earlier in the list can reclassify what a later rule would also match."""
    soup = BeautifulSoup(
        '<html><body><nav><a class="promo" href="/x">x</a></nav></body></html>', features="lxml"
    )
    content_root, _ = find_content_root(soup)
    link = soup.find("a")

    rules = link_position.rules_from_config(
        [{"position": "promo", "selector": ".promo"}, {"position": "nav", "selector": "nav"}]
    )
    assert link_position.classify_link(link, content_root, rules=rules) == "promo"
    # Reversed order: the built-in-shaped nav rule wins instead.
    rules = link_position.rules_from_config(
        [{"position": "nav", "selector": "nav"}, {"position": "promo", "selector": ".promo"}]
    )
    assert link_position.classify_link(link, content_root, rules=rules) == "nav"


def test_site_specific_selector_catches_a_non_nav_menu():
    """Plenty of menus are not a <nav> element at all."""
    soup = BeautifulSoup(
        '<html><body><div class="mega-menu"><a href="/m">Menu</a></div></body></html>',
        features="lxml",
    )
    content_root, _ = find_content_root(soup)
    link = soup.find("a")
    assert link_position.classify_link(link, content_root) == "content"  # no built-in rule fires

    rules = link_position.rules_from_config([{"position": "nav", "selector": ".mega-menu"}])
    assert link_position.classify_link(link, content_root, rules=rules) == "nav"


def test_malformed_site_specific_selector_is_skipped_not_raised():
    soup = BeautifulSoup('<html><body><a href="/x">x</a></body></html>', features="lxml")
    content_root, _ = find_content_root(soup)
    link = soup.find("a")
    rules = link_position.rules_from_config([{"position": "broken", "selector": ":::not-css:::"}])
    assert link_position.classify_link(link, content_root, rules=rules) == "content"


def test_link_outside_a_narrowed_content_root_is_other_not_content():
    soup = BeautifulSoup(
        '<html><body><div class="promo"><a href="/x">x</a></div>'
        "<main><p>real content</p></main></body></html>",
        features="lxml",
    )
    content_root, _ = find_content_root(soup, {"include_selector": "main"})
    link = soup.find("a")
    assert link_position.classify_link(link, content_root) == "other"


def test_parse_html_classify_links_option_is_off_by_default():
    out = parse_html(_HTML, "https://example.com/")
    assert all("position" not in link for link in out["links"])


def test_parse_html_classify_links_option_wires_position_into_links():
    out = parse_html(_HTML, "https://example.com/", {"classify_links": True})
    by_href = {link["href"]: link["position"] for link in out["links"]}
    assert by_href["https://example.com/nav1"] == "nav"
    assert by_href["https://example.com/foot1"] == "footer"
    assert by_href["https://example.com/content1"] == "content"
