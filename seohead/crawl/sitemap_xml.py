"""Select indexable URLs from page evidence and serialise them as XML sitemaps.

Pure functions: no network, no storage access. Callers pass one mapping per page with the fields
the crawler already keeps and receive the URLs to publish plus a reason for every URL left out.
The output follows the sitemaps.org protocol: one ``<urlset>`` per file, at most 50,000 URLs and
52,428,800 bytes uncompressed, with a ``<sitemapindex>`` when the set is split.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from xml.sax.saxutils import escape

from seohead.checks.parser import robots_directives
from seohead.crawl.evidence import _same_page

MAX_URLS_PER_FILE = 50_000
MAX_BYTES_PER_FILE = 52_428_800
NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
INDEX_NAME = "sitemap.xml"
_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'


def select_indexable(
    pages: Iterable[Mapping[str, object]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Return ``(urls to publish, [(url, reason)] excluded)``.

    A page is included only when it returned 200, is HTML, is not robots-blocked, carries no
    ``noindex`` directive, and is its own canonical. Each excluded URL gets the first reason that
    applies, so the caller can report what was left out and why.
    """
    included: list[str] = []
    excluded: list[tuple[str, str]] = []
    for page in pages:
        url = str(page.get("url") or "")
        reason = _exclusion_reason(page)
        if reason:
            excluded.append((url, reason))
        else:
            included.append(url)
    return included, excluded


def _exclusion_reason(page: Mapping[str, object]) -> str:
    if page.get("blocked_by_robots"):
        return "blocked_by_robots"
    if page.get("status_code") != 200:
        return "not_200"
    if "html" not in str(page.get("content_type") or "").lower():
        return "not_html"
    directives = robots_directives(
        str(page.get("meta_robots") or ""), str(page.get("x_robots") or "")
    )
    if "noindex" in directives:
        return "noindex"
    canonical = str(page.get("canonical") or "")
    if canonical and not _same_page(str(page.get("url") or ""), canonical):
        return "non_canonical"
    return ""


def render_sitemaps(
    urls: list[str],
    base_url: str,
    *,
    max_urls: int = MAX_URLS_PER_FILE,
    max_bytes: int = MAX_BYTES_PER_FILE,
) -> dict[str, bytes]:
    """Return ``{filename: xml bytes}`` for ``urls``, split across files when a limit is reached.

    A set that fits is one ``sitemap.xml``. A larger set yields ``sitemap-1.xml``,
    ``sitemap-2.xml``, … plus an index ``sitemap.xml`` whose ``<loc>`` values are ``base_url``
    joined with each part name.
    """
    chunks = _chunk(urls, max_urls=max_urls, max_bytes=max_bytes)
    if len(chunks) == 1:
        return {INDEX_NAME: _urlset(chunks[0])}
    base = base_url.rstrip("/")
    files = {f"sitemap-{n}.xml": _urlset(chunk) for n, chunk in enumerate(chunks, start=1)}
    index = [_XML_DECLARATION, f'<sitemapindex xmlns="{NAMESPACE}">']
    index += [f"  <sitemap><loc>{escape(f'{base}/{name}')}</loc></sitemap>" for name in files]
    index.append("</sitemapindex>")
    files[INDEX_NAME] = ("\n".join(index) + "\n").encode("utf-8")
    return files


def _chunk(urls: list[str], *, max_urls: int, max_bytes: int) -> list[list[str]]:
    chunks: list[list[str]] = [[]]
    size = _envelope_bytes()
    for url in urls:
        line = _url_line(url)
        if chunks[-1] and (len(chunks[-1]) >= max_urls or size + line > max_bytes):
            chunks.append([])
            size = _envelope_bytes()
        chunks[-1].append(url)
        size += line
    return chunks


def _envelope_bytes() -> int:
    return len(_urlset([]))


def _url_line(url: str) -> int:
    return len(f"  <url><loc>{escape(url)}</loc></url>\n".encode())


def _urlset(urls: list[str]) -> bytes:
    lines = [_XML_DECLARATION, f'<urlset xmlns="{NAMESPACE}">']
    lines += [f"  <url><loc>{escape(url)}</loc></url>" for url in urls]
    lines.append("</urlset>")
    return ("\n".join(lines) + "\n").encode("utf-8")
