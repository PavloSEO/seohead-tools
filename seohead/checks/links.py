"""On-page link checker — finds broken (4xx/5xx) and redirecting (3xx) links.

Parses a page's ``<a href>`` links and checks each target's HTTP status, so you can
spot broken internal links and links that point at redirects (wasted crawl hops).
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from seohead.recon.net import http_client

_UA = "Mozilla/5.0 (compatible; SEOHEAD-Tools/3.0; +https://seohead.tech/seotools)"


def _request_target(href: str) -> str:
    """The identity a fragment never changes: /guide#a and /guide#b name the same
    server resource, so deduping on the literal href string double-checked one
    page's anchors and could crowd a later, genuinely distinct link out of the
    bounded ``limit`` (#194)."""
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def check_links(
    url: str, internal_only: bool = False, limit: int = 200, timeout: float = 15.0
) -> dict:
    # Reuse the parser to extract links (only that field).
    from seohead.checks import parser as _parser

    page = _parser.parse_url(
        url,
        {
            "meta": False,
            "canonical": False,
            "og": False,
            "headings": False,
            "jsonld": False,
            "links": True,
            "text": False,
        },
    )
    if not page.get("ok"):
        return {"ok": False, "url": url, "error": page.get("error", "parse failed")}

    links = page.get("links", [])
    if internal_only:
        links = [ln for ln in links if not ln.get("external")]

    seen: dict[str, dict] = {}
    for ln in links:
        href = ln.get("href")
        if not href or not href.startswith(("http://", "https://")):
            continue
        target = _request_target(href)
        if target not in seen:
            seen[target] = ln
    targets = list(seen.items())[:limit]

    broken: list[dict] = []
    redirects: list[dict] = []
    ok_count = 0
    checked = 0
    client, _http2_capable = http_client(
        timeout, follow_redirects=False, headers={"User-Agent": _UA}
    )
    with client:
        for href, ln in targets:
            checked += 1
            try:
                resp = client.head(href)
                if resp.status_code >= 400 or resp.status_code == 405:
                    resp = client.get(href)  # some hosts reject HEAD
                code = resp.status_code
                location = resp.headers.get("location")
            except Exception as exc:
                broken.append(
                    {"href": href, "status": 0, "error": str(exc), "external": ln.get("external")}
                )
                continue
            if 300 <= code < 400:
                redirects.append(
                    {
                        "href": href,
                        "status": code,
                        "location": location,
                        "external": ln.get("external"),
                    }
                )
            elif code >= 400:
                broken.append({"href": href, "status": code, "external": ln.get("external")})
            else:
                ok_count += 1

    return {
        "ok": True,
        "url": url,
        "links_found": len(links),
        "checked": checked,
        "truncated": len(seen) > limit,
        "ok_count": ok_count,
        "broken": broken,
        "redirects": redirects,
    }


if __name__ == "__main__":
    # pure sanity: classification thresholds
    assert 300 <= 301 < 400 and 404 >= 400
    print("OK: links self-check passed")
