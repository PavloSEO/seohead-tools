"""The identity User-Agent is the shared client default that SEO checks rely on.

Checks that do not pass their own User-Agent depend on ``http_client`` sending
``UA``. These tests pin that default so a future change cannot silently alter
what the crawled site sees.
"""

from __future__ import annotations

import httpx

from seohead.checks.sitemap import _fetch
from seohead.recon.net import UA, http_client


def test_default_client_sends_identity_user_agent():
    client, _http2_capable = http_client(5)
    with client:
        assert client.headers["user-agent"] == UA


def test_sitemap_fetch_keeps_identity_user_agent_and_sends_accept():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, request=request, content=b"<urlset/>")

    client = httpx.Client(headers={"User-Agent": UA}, transport=httpx.MockTransport(handler))
    with client:
        assert _fetch(client, "https://example.test/sitemap.xml") == b"<urlset/>"

    assert seen[0].headers["user-agent"] == UA
    assert seen[0].headers["accept"] == "application/xml,text/xml,application/gzip,*/*"
