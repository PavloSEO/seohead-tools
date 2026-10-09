"""A gzip-bomb sitemap is bounded during decompression, not after (#148).

``gzip.decompress()`` has no size limit of its own: a payload well under ``MAX_XML_BYTES``
compressed can expand to gigabytes before anything checks the result. The fix reads the
stream incrementally and aborts as soon as the *decompressed* output crosses the same
ceiling the pre-decompression check already enforces on the *compressed* body.
"""

from __future__ import annotations

import gzip
import time

import httpx

from seohead.checks import sitemap as S


def _bomb(decompressed_size: int) -> bytes:
    """A small, highly compressible payload that expands past MAX_XML_BYTES.

    Real-world gzip bombs use exactly this shape (a hostile or misconfigured origin
    doesn't need a large transfer to force a large allocation) -- deflate's own ratio
    ceiling is documented around 1032:1, so a run of zeros comfortably clears
    MAX_XML_BYTES from a payload of only a few tens of kilobytes.
    """
    return gzip.compress(b"\0" * decompressed_size, compresslevel=9)


def test_maybe_gunzip_bounds_the_decompressed_size_not_the_compressed_one():
    payload = _bomb(S.MAX_XML_BYTES * 2)
    assert len(payload) < S.MAX_XML_BYTES, "the compressed body must pass the transfer-size check"

    start = time.monotonic()
    try:
        S._maybe_gunzip("https://example.com/sitemap.xml.gz", payload)
        raise AssertionError("expected ValueError: ratio bomb was not bounded")
    except ValueError as exc:
        elapsed = time.monotonic() - start
        assert str(exc) == S._TOO_LARGE_MSG, "same message the pre-decompression check raises"
        # Materialising the full 2x-over-ceiling buffer would itself take a beat; bailing out
        # after roughly one MAX_XML_BYTES chunk should be near-instant.
        assert elapsed < 5.0, f"took {elapsed:.2f}s -- looks like the full buffer was built first"


def test_a_gzip_child_inside_a_sitemap_index_is_one_named_error_not_a_crash():
    """Requirement (2): the same bound reached through a nested sitemap-index child."""
    bomb_payload = _bomb(S.MAX_XML_BYTES * 2)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://example.com/sitemap-index.xml":
            body = (
                b'<?xml version="1.0"?>'
                b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b"<sitemap><loc>https://example.com/child.xml.gz</loc></sitemap>"
                b"</sitemapindex>"
            )
            return httpx.Response(200, content=body, headers={"content-type": "application/xml"})
        if url == "https://example.com/child.xml.gz":
            return httpx.Response(
                200, content=bomb_payload, headers={"content-type": "application/gzip"}
            )
        return httpx.Response(404)

    def fake_http_client(*_args, **_kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), True

    original = S.http_client
    S.http_client = fake_http_client
    try:
        result = S.crawl("https://example.com/sitemap-index.xml")
    finally:
        S.http_client = original

    assert result["ok"] is True, "the index itself parsed fine; only its child failed"
    assert result["count"] == 0, "no URLs recovered from the bomb -- not a silent truncation"
    assert result["errors"] == [
        {"url": "https://example.com/child.xml.gz", "error": S._TOO_LARGE_MSG}
    ]
