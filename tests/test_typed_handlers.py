"""Guard the two return shapes typed for issue #31.

``parse_html``/``parse_url`` and ``parse_robots``/``check_robots`` all build
their return value as a plain dict and ``cast(...)`` it to the declared
TypedDict at the end (see the comments at each cast site) -- ``cast`` is a
type checker no-op, so mypy verifies the *signature*, not that the dict built
above actually matches it. These tests close that gap at runtime, for the
handler surface (``parse``, ``robots_check``) and the core functions they
call. They also guard the two converted handlers against quietly reverting
to the untyped ``dict[str, Any]`` every handler used to return.

This covers the two shapes this issue's first slice typed -- see the other
42 handlers for what is intentionally not yet covered.
"""

from __future__ import annotations

import types
from typing import Any, Union, get_args, get_origin, get_type_hints

from seohead.checks import parser, robots
from seohead.core.models import ParsedPage, ParsedRobots, RobotsGroup
from seohead.mcp.handlers import HANDLERS

_SAMPLE_HTML = """
<html><head>
<title>Sample</title>
<meta name="description" content="A sample page">
<link rel="canonical" href="https://site.tld/page">
</head><body>
<h1>Hello</h1>
<a href="/other" rel="nofollow">Other</a>
</body></html>
"""

_SAMPLE_ROBOTS_TXT = (
    "User-agent: *\nDisallow: /api/\nAllow: /api/public\nCrawl-delay: 2\n\n"
    "Sitemap: https://site.tld/sitemap.xml"
)


# A PEP 604 union (``A | B``) reports types.UnionType as its origin, while
# typing.Union[A, B] reports typing.Union. Python 3.14 unified the two; every
# version this package supports below that does not, so both must be accepted
# or this helper silently treats a union as a single type.
_UNION_ORIGINS = (Union, types.UnionType)


def _variants(annotation: Any) -> list[Any]:
    """Flatten ``A | B`` into ``[A, B]``; a bare TypedDict into ``[A]``."""
    if get_origin(annotation) in _UNION_ORIGINS:
        return list(get_args(annotation))
    return [annotation]


def _allowed_keys(*typed_dicts: Any) -> set[str]:
    """Every key any of the given TypedDicts (or their bases) declares."""
    allowed: set[str] = set()
    for typed_dict in typed_dicts:
        allowed |= set(get_type_hints(typed_dict))
    return allowed


# --- the two functions that build the shape (cast bypasses static checking) ---


def test_parse_html_only_returns_keys_parsed_page_declares():
    result = parser.parse_html(_SAMPLE_HTML, "https://site.tld/page")
    assert set(result) <= _allowed_keys(ParsedPage)
    assert result["title"] == "Sample"


def test_parse_robots_only_returns_keys_parsed_robots_declares():
    result = robots.parse_robots(_SAMPLE_ROBOTS_TXT)
    assert set(result) <= _allowed_keys(ParsedRobots)
    assert result["sitemaps"] == ["https://site.tld/sitemap.xml"]
    for group in result["groups"]:
        assert set(group) <= _allowed_keys(RobotsGroup)


# --- the handler surface: still the untyped-return guard + a wiring check ---


def test_typed_handlers_do_not_declare_a_bare_any_dict():
    for name in ("parse", "robots_check"):
        return_type = get_type_hints(HANDLERS[name])["return"]
        assert return_type != dict[str, Any], (
            f"{name}'s return type regressed to the untyped dict[str, Any] this issue replaced"
        )


def test_parse_handler_forwards_only_declared_keys(monkeypatch):
    # Stub the network call so this stays deterministic and offline; the shape
    # asserted is exactly what parser.parse_url promises to return on failure.
    monkeypatch.setattr(
        parser,
        "parse_url",
        lambda url, options=None: {"url": url, "ok": False, "error": "stubbed"},
    )
    result = HANDLERS["parse"](url="https://site.tld")

    return_type = get_type_hints(HANDLERS["parse"])["return"]
    page_type = get_type_hints(return_type)["results"]
    (page_element,) = get_args(page_type)

    assert set(result) <= _allowed_keys(return_type)
    assert result["count"] == 1
    assert set(result["results"][0]) <= _allowed_keys(*_variants(page_element))


def test_robots_check_handler_forwards_only_declared_keys(monkeypatch):
    monkeypatch.setattr(
        robots,
        "check_robots",
        lambda url, user_agent="*", paths=None: {
            "ok": False,
            "robots_url": url.rstrip("/") + "/robots.txt",
            "error": "stubbed",
        },
    )
    result = HANDLERS["robots_check"](url="https://site.tld")

    return_type = get_type_hints(HANDLERS["robots_check"])["return"]
    assert set(result) <= _allowed_keys(*_variants(return_type))
    assert result["ok"] is False
