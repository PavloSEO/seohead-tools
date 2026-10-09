"""Dual-crawl cross-validation: diff what two evidence-gathering methods saw.

Any single crawl method has blind spots specific to *how* it gathers evidence,
and those blind spots don't announce themselves: a static-HTML parse simply
does not see what only exists after rendering, and reports nothing about it --
which looks identical to "there was nothing to see". Running two methods over
the same page at the same time and reporting exactly where they disagree turns
that silence into a finding. The flagship case is CSS ``background-image``,
which a static-HTML parse can already see when declared inline or in a
``<style>`` block (``parser.extract_url_sources``), but not when only an
external stylesheet declares it -- only a live browser's computed style
resolves that.

This is deliberately not #21's crawl-to-crawl regression compare
(``seohead.sf.core.compare``): that module assumes the *site* changed between
two runs of the same method. This module assumes the site is unchanged and the
*method* is what differs, so it reports a different question and uses a
different schema/keys, to keep tooling from mistaking one kind of diff for
the other.
"""

from __future__ import annotations

from typing import Any

from seohead.checks.parser import image_url_sources, parse_html


def build_page_evidence(
    html: str, url: str, *, extra_images: frozenset[str] | set[str] = frozenset()
) -> dict[str, set[str]]:
    """Evidence extracted from one HTML document, keyed by evidence type.

    ``extra_images`` folds in URLs observed by a means this function cannot
    reach on its own -- computed ``background-image`` from a live browser,
    which resolves an external stylesheet that a parse of the HTML text never
    sees, because the declaring CSS rule lives in a different resource.
    """
    if not html:
        return {"images": set(extra_images), "links": set()}
    parsed = parse_html(html, url, {"url_sources": True, "links": True, "text": False})
    images = {s["url"] for s in image_url_sources(parsed["url_sources"])}
    images |= set(extra_images)
    links = {link["href"] for link in parsed["links"]}
    return {"images": images, "links": links}


def compare_evidence(
    evidence_a: dict[str, dict[str, set[str]]],
    evidence_b: dict[str, dict[str, set[str]]],
    *,
    method_a: str = "static",
    method_b: str = "rendered",
) -> dict[str, Any]:
    """Diff two evidence-gathering passes over the same set of pages.

    Each argument maps ``url -> {evidence_type: {item, ...}}``. Only URLs and
    evidence types with an actual difference appear in ``urls``, so a caller
    that finds a URL missing from the result knows the two methods agreed on
    it completely -- silence still means agreement here, the opposite of what
    a single crawl's silence means.
    """
    by_url: dict[str, dict[str, Any]] = {}
    only_in_a_total = 0
    only_in_b_total = 0
    for url in sorted(set(evidence_a) | set(evidence_b)):
        types_a = evidence_a.get(url, {})
        types_b = evidence_b.get(url, {})
        url_diff: dict[str, Any] = {}
        for evidence_type in sorted(set(types_a) | set(types_b)):
            items_a = set(types_a.get(evidence_type) or ())
            items_b = set(types_b.get(evidence_type) or ())
            only_a = sorted(items_a - items_b)
            only_b = sorted(items_b - items_a)
            if only_a or only_b:
                url_diff[evidence_type] = {"only_in_a": only_a, "only_in_b": only_b}
                only_in_a_total += len(only_a)
                only_in_b_total += len(only_b)
        if url_diff:
            by_url[url] = url_diff

    return {
        "schema_version": "dualcrawl.v1",
        "methods": {"a": method_a, "b": method_b},
        "urls": by_url,
        "summary": {
            "urls_with_differences": len(by_url),
            "only_in_a": only_in_a_total,
            "only_in_b": only_in_b_total,
        },
    }
