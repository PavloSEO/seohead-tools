"""Score an ``llms.txt`` manifest for generative and answer-engine optimization.

``llms.txt`` is an ecosystem convention for a Markdown file at the site root. It
summarizes the project, identifies important pages, and points models to useful
documentation. The common structure uses one H1, a blockquote summary, H2 sections,
and Markdown link lists.

A useful manifest gives an AI system concise context instead of forcing it to
infer the product from arbitrary HTML pages. This module therefore scores practical
signals of model usefulness rather than treating mere file presence as success.

The nine binary checks produce a score of ``passed * 10 / 9``:
  1. An H1 heading is present; when ``brand`` is supplied, it must name the project.
  2. At least three H2 sections.
  3. At least three Markdown links.
  4. A project or brand mention when ``brand`` is supplied.
  5. A product, service, platform, tool, solution, or API category mention.
  6. A product or pricing page.
  7. Proof such as cases, testimonials, reviews, or customers.
  8. Documentation, API, or guide content.
  9. A size no greater than 60 KiB, beyond which the file becomes cumbersome for
     model context windows.
"""

from __future__ import annotations

import re
from typing import Any

from seohead.recon.net import normalize_url

_MAX_BYTES = 60 * 1024
_CATEGORY_HINTS = (
    "продукт",
    "product",
    "сервис",
    "service",
    "платформ",
    "platform",
    "инструмент",
    "tool",
    "решен",
    "solution",
    "saas",
    "api",
    "marketplace",
)


def score_llms_txt(content: str, brand: str | None = None) -> dict[str, Any]:
    """Score ``llms.txt`` content against nine checks without network access.

    ``brand`` supplies the project or brand name for checks one and four. When
    omitted, check four is deliberately reported as failed rather than inferred,
    and check one is scored as mere heading presence -- with no expected name
    to compare against, "the H1 names the project" is not something this
    function can measure, so its own name says only what it actually checked.
    """
    if not content:
        return {"ok": False, "error": "llms.txt is empty or missing"}

    text = content
    text_low = text.lower()
    lines = text.splitlines()

    h1_text = next(
        (
            ln.lstrip("# ").strip()
            for ln in lines
            if ln.lstrip().startswith("# ") and ln.strip("# ").strip()
        ),
        None,
    )
    if brand:
        h1_check_name = f"H1 heading names the project ({brand})"
        has_h1 = h1_text is not None and brand.lower() in h1_text.lower()
    else:
        h1_check_name = "H1 heading is present"
        has_h1 = h1_text is not None
    sections = [ln for ln in lines if ln.lstrip().startswith("## ")]
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text)

    mentions_brand = bool(brand) and brand.lower() in text_low
    mentions_category = any(h in text_low for h in _CATEGORY_HINTS)

    link_text_low = " ".join(anchor for anchor, _ in links).lower() + " " + text_low
    has_product = any(k in link_text_low for k in ("product", "pricing", "продукт", "тариф", "цен"))
    has_proof = any(
        k in link_text_low
        for k in ("case", "testimonial", "review", "customer", "кейс", "отзыв", "клиент")
    )
    has_docs = any(
        k in link_text_low
        for k in ("docs", "documentation", "api", "guide", "документац", "руководств")
    )

    size_ok = len(content.encode("utf-8")) <= _MAX_BYTES

    checks = [
        {"name": h1_check_name, "passed": has_h1},
        {"name": "At least 3 H2 sections", "passed": len(sections) >= 3},
        {"name": "At least 3 Markdown links", "passed": len(links) >= 3},
        {
            "name": f"Brand mention ({brand})" if brand else "Brand mention",
            "passed": mentions_brand,
        },
        {"name": "Product or service category mention", "passed": mentions_category},
        {"name": "Product or pricing page", "passed": has_product},
        {"name": "Proof: cases, testimonials, reviews, or customers", "passed": has_proof},
        {"name": "Documentation, API, or guide", "passed": has_docs},
        {"name": f"Size at most {_MAX_BYTES // 1024} KiB", "passed": size_ok},
    ]
    passed = sum(1 for c in checks if c["passed"])
    score = round(passed / len(checks) * 10, 1)

    grade = (
        "A"
        if score >= 8
        else "B"
        if score >= 6
        else "C"
        if score >= 4
        else "D"
        if score >= 2
        else "F"
    )

    return {
        "ok": True,
        "score": score,
        "grade": grade,
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "stats": {
            "h1": has_h1,
            "sections": len(sections),
            "links": len(links),
            "mentions_brand": mentions_brand,
            "size_bytes": len(content.encode("utf-8")),
            "size_ok": size_ok,
        },
    }


def check_llms_txt(url: str, brand: str | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """Fetch and score a site's ``/llms.txt``; this is the only network boundary."""
    base = normalize_url(url)
    if not base:
        return {"ok": False, "error": f"Not a valid HTTP(S) URL: {url!r}"}
    # llms.txt belongs at the origin root, so discard the supplied path.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(base)
    llms_url = urlunsplit((parts.scheme, parts.netloc, "/llms.txt", "", ""))
    try:
        from seohead.recon.net import http_client

        client, _ = http_client(timeout)
    except ImportError:
        return {"ok": False, "error": "httpx is required"}
    try:
        with client:
            resp = client.get(llms_url)
        if resp.status_code == 404:
            # A missing file is a measured result, not a tool failure. Returning
            # ok=False would prevent the orchestrator from distinguishing absence
            # from a network error that made the check unavailable.
            return {
                "ok": True,
                "url": llms_url,
                "status_code": resp.status_code,
                "exists": False,
                "score": 0,
                "grade": "F",
                "passed": 0,
                "findings": [
                    "llms.txt is missing, so the site provides no "
                    "curated context for AI systems; models must infer "
                    "the project from whichever pages they encounter"
                ],
            }
        if resp.status_code >= 400:
            # A 401/403/429/5xx answered the request without ever measuring whether
            # the file exists (an access gate, a rate limit, or an upstream fault
            # all withhold the body the same way a transport error does). Reporting
            # this as exists:false would be a confident claim about content the
            # server never actually served -- the same false-certainty mistake as
            # treating a network error like a real absence.
            return {
                "ok": False,
                "url": llms_url,
                "status_code": resp.status_code,
                "error": f"llms.txt could not be measured: server returned {resp.status_code}",
            }
        scored = score_llms_txt(resp.text, brand=brand)
        return {"url": llms_url, "status_code": resp.status_code, "exists": True, **scored}
    except Exception as exc:
        return {"ok": False, "url": llms_url, "error": str(exc)}
