"""Security- and structure-shaped findings computed straight from a crawl's own evidence.

Sibling to ``linkgraph.py``: pure, and never imports ``seohead.sf``. Two findings here read
only what a crawl always records (``LinkEdge.destination``, ``.nofollow``, ``FormEdge``);
two need ``LinkEdge.rel``/``.target``/``.raw_href``, which only exist when the crawl was run
with ``link_attributes.capture`` on — see that setting's own docstring in
``crawl/spider.py`` for why it defaults off. Each such function is a plain filter over the
edge/form list; the caller (``seohead.servers.handlers``) decides when the data exists to ask.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from seohead.crawl.spider import FormEdge, LinkEdge

# Reverse tabnabbing (the opened page gets a live handle to the opener via window.opener)
# is prevented by either token; a link naming just one is not a finding.
_SAFE_BLANK_REL = {"noopener", "noreferrer"}

# Scheme -> port a URL carries implicitly when none is written, so that
# "https://x/" and "https://x:443/" normalize to the same origin.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None]:
    """Normalized (scheme, hostname, effective port) triple for an origin comparison.

    This is deliberately not the crawler's scope predicate (``crawl/settings.py``'s
    host-based in-scope check): scope decides what to crawl, this decides what a
    browser would treat as the same origin, and the two must not be conflated.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(scheme)
    return (scheme, hostname, port)


def _is_localhost(host: str) -> bool:
    host = host.lower().rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # not a literal IP -- an ordinary hostname, not localhost


def outlinks_to_localhost(links: list[LinkEdge]) -> list[dict[str, Any]]:
    """Edges pointing at a loopback address -- a dev/staging reference leaked into
    production markup. Needs only ``destination``, so it runs on every crawl."""
    out = []
    for edge in links:
        if _is_localhost(urlsplit(edge.destination).hostname or ""):
            out.append({"target_url": edge.source, "destination": edge.destination})
    return out


def unsafe_cross_origin_links(links: list[LinkEdge]) -> list[dict[str, Any]]:
    """``target="_blank"`` links naming neither ``noopener`` nor ``noreferrer`` in rel.

    Requires ``capture_attributes``: an edge whose attributes were never captured has
    ``target == ""`` and never matches, which is the correct "not measured" behaviour
    rather than a false positive or a false clean result.

    Only a cross-origin new-tab link is a finding: reverse tabnabbing needs a
    ``window.opener`` handle back into a *different* origin's page. A same-origin
    ``target="_blank"`` link is compared by normalized (scheme, hostname, effective
    port) origin, not by the crawler's host-based scope predicate -- see ``_origin``.
    """
    out = []
    for edge in links:
        if edge.target.lower() != "_blank":
            continue
        if _origin(edge.source) == _origin(edge.destination):
            continue
        if _SAFE_BLANK_REL & {t.lower() for t in edge.rel}:
            continue
        out.append({"target_url": edge.source, "destination": edge.destination})
    return out


def protocol_relative_links(links: list[LinkEdge]) -> list[dict[str, Any]]:
    """Edges whose href was written in the ``//host/path`` form before resolution.

    Also gated by ``capture_attributes``: ``raw_href`` is only populated when it is on.
    """
    out = []
    for edge in links:
        if edge.raw_href.startswith("//"):
            out.append(
                {
                    "target_url": edge.source,
                    "destination": edge.destination,
                    "raw_href": edge.raw_href,
                }
            )
    return out


def http_links_on_https_pages(
    links: Iterable[LinkEdge], host: str, safely_upgraded: set[str]
) -> list[dict[str, Any]]:
    """Observed internal ``http://`` anchors on HTTPS pages (#832).

    A raw href is required because resolved targets alone cannot distinguish an
    explicit HTTP anchor from an ordinary relative URL. A fetched HTTP variant
    that demonstrably redirects to HTTPS is left out: it already converges.
    """
    host = host.lower()
    findings = []
    for edge in links:
        source = urlsplit(edge.source)
        destination = urlsplit(edge.destination)
        if (
            source.scheme.lower() != "https"
            or (source.hostname or "").lower() != host
            or (destination.hostname or "").lower() != host
            or not edge.raw_href.lower().startswith("http://")
            or edge.destination in safely_upgraded
        ):
            continue
        findings.append(
            {
                "target_url": edge.source,
                "destination": edge.destination,
                "raw_href": edge.raw_href,
            }
        )
    return findings


def follow_and_nofollow_inlinks(links: list[LinkEdge], host: str) -> list[str]:
    """Internal destinations linked both with and without ``nofollow``.

    A page reached one way from some source and the other way from another is inconsistently
    signalled to a crawler about the same URL. Uses only ``destination``/``nofollow``, which
    are always recorded, so it needs no ``capture_attributes``.
    """
    host = host.lower()
    by_dest: dict[str, set[bool]] = defaultdict(set)
    for edge in links:
        if (urlsplit(edge.destination).hostname or "").lower() != host:
            continue
        by_dest[edge.destination].add(edge.nofollow)
    return sorted(dest for dest, flags in by_dest.items() if flags == {True, False})


def follow_and_nofollow_inlink_details(
    links: Iterable[LinkEdge], host: str, max_sources: int = 20
) -> list[dict[str, Any]]:
    """Observed mixed internal rel states with bounded contributing sources (#832)."""
    host = host.lower()
    grouped: dict[str, dict[str, Any]] = {}
    for edge in links:
        if (urlsplit(edge.destination).hostname or "").lower() != host:
            continue
        item = grouped.setdefault(edge.destination, {"sources": set(), "follow": 0, "nofollow": 0})
        item["sources"].add(edge.source)
        item["nofollow" if edge.nofollow else "follow"] += 1
    return [
        {
            "target_url": destination,
            "sources": sorted(item["sources"])[:max_sources],
            "follow_occurrences": item["follow"],
            "nofollow_occurrences": item["nofollow"],
        }
        for destination, item in sorted(grouped.items())
        if item["follow"] and item["nofollow"]
    ]


def form_url_insecure(forms: list[FormEdge]) -> list[dict[str, Any]]:
    """Forms whose action submits over plain HTTP, regardless of the hosting page's own
    scheme -- data leaves the browser unencrypted the moment the form is submitted."""
    return [
        {"target_url": f.page, "action": f.action, "method": f.method}
        for f in forms
        if f.action.lower().startswith("http://")
    ]


def forms_on_http_pages_with_password(forms: list[FormEdge]) -> list[dict[str, Any]]:
    """Password forms served from a plain-HTTP page: the credentials themselves travel
    unencrypted to reach the form, before the action URL is ever involved."""
    return [
        {"target_url": f.page, "action": f.action}
        for f in forms
        if f.has_password and f.page.lower().startswith("http://")
    ]
