"""Characterisation of the URL identity / host helpers in one part of the C08 cluster.

These helpers answer different questions (path-only key, fragment-free key, canonical
URL, host with or without a port, with or without ``www.``). The tests pin what each one
returns today so that any later merge or rename has to show, row by row, what changes.
Divergences that look like bugs are marked ``xfail(strict=True)``: they document the
defect without changing behaviour in this refactor.
"""

from __future__ import annotations

import pytest

from seohead.crawl import external
from seohead.data_sources import yandex_cloud
from seohead.recon import mirrors, net, tech
from seohead.sf.core import normalize, sitemap_coverage
from seohead.storage import fragment_links


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Example.com/", "https://www.Example.com/"),
        ("https://example.com:443/a/", "https://example.com:443/a/"),
        ("https://www.example.com:8443/x", "https://www.example.com:8443/x"),
        ("example.com/a/b/", "https://example.com/a/b/"),
        ("//www.example.com/x", ""),
        ("https://user:pw@www.example.com/p", ""),
    ],
)
def test_net_normalize_url_keeps_input_shape(url: str, expected: str) -> None:
    assert net.normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Example.com/", "https://www.example.com"),
        ("https://example.com:443/a/", "https://example.com:443/a"),
        ("HTTP://EXAMPLE.com:80/A/?q=1#f", "http://example.com:80/A?q=1#f"),
        ("//www.example.com/x", "//www.example.com/x"),
        ("example.com/a/b/", "example.com/a/b"),
    ],
)
def test_sf_norm_url_folds_scheme_host_and_trailing_slash_only(url: str, expected: str) -> None:
    assert normalize.norm_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Example.com/", "https://www.Example.com/"),
        ("HTTP://EXAMPLE.com:80/A/?q=1#f", "http://EXAMPLE.com:80/A/?q=1"),
        ("https://user:pw@www.example.com/p", "https://user:pw@www.example.com/p"),
    ],
)
def test_fragment_canonical_key_drops_only_fragment(url: str, expected: str) -> None:
    assert fragment_links._canonical_key(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Example.com/", "https://www.example.com/"),
        ("HTTP://EXAMPLE.com:80/A/", "http://example.com:80/A"),
        ("example.com/a/b/", "example.com/a/b"),
    ],
)
def test_mirrors_final_url_key(url: str, expected: str) -> None:
    assert mirrors._norm_final(url) == expected


@pytest.mark.parametrize(
    ("helper", "url", "expected"),
    [
        (tech._host, "https://www.example.com/x.js", "www.example.com"),
        (tech._host, "https://example.com:443/x.js", "example.com:443"),
        (tech._host, "example.com/x.js", "example.com"),
        (sitemap_coverage._host, "https://www.example.com:8443/x", "www.example.com:8443"),
        (sitemap_coverage._host, "example.com/a/b/", ""),
        (external._host, "https://www.example.com:8443/x", "www.example.com"),
        (external._host, "example.com/a/b/", ""),
        (yandex_cloud._host, "https://www.example.com/x", "example.com"),
        (yandex_cloud._host, "https://example.com:443/x", "example.com:443"),
        (yandex_cloud._host, "HTTP://EXAMPLE.com:80/A/", ""),
    ],
)
def test_host_helpers_keep_their_own_contract(helper, url: str, expected: str) -> None:
    assert helper(url) == expected


def test_yandex_host_does_not_lowercase_current_behaviour() -> None:
    # Pinned as-is: this helper never lowercases, unlike the others above.
    assert yandex_cloud._host("https://Example.COM/x") == "Example.COM"


@pytest.mark.xfail(strict=True, reason="yandex_cloud._host strips 'www.' unanchored (separate fix)")
def test_yandex_host_strips_only_leading_www() -> None:
    assert yandex_cloud._host("https://awww.foo.com/") == "awww.foo.com"


@pytest.mark.xfail(strict=True, reason="yandex_cloud._host strips 'www.' unanchored (separate fix)")
def test_yandex_host_strips_only_leading_www_when_www_is_a_substring() -> None:
    assert yandex_cloud._host("https://www.awww.foo.com/") == "awww.foo.com"
