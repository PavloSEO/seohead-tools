"""Objective authorship, date and trust-page evidence for scoped audits (issue #823).

The E-E-A-T gap-map rows this module answers are deliberately kept to what a
crawl can *observe*: whether a content-shaped page carries byline markup and
date declarations, whether About/Contact/privacy/terms URLs were discovered in
the crawl's scope, and whether outbound links sit in the body content. Nothing
here produces an E-E-A-T score, and a detected signal never reads as proof of
trustworthiness -- the findings name the markup, the state and the evidence
behind them so a specialist reviews facts, not a verdict.

Scope is a first-class output:

- Author/date checks run only on *article-scope* pages -- a page that declared
  itself content-shaped (JSON-LD article ``@type``, ``og:type=article`` or an
  ``<article>`` element) or that sits under a conventional content path. The
  scope basis is reported per finding.
- Trust-page checks separate the states the issue requires: ``found_indexable``
  (silent), ``found_non_indexable`` and ``found_error`` (a page exists but does
  not serve), ``discovered_not_crawled`` (linked but outside the fetched set),
  and ``not_discovered`` (no matching URL inside the crawled set -- which does
  not prove the site lacks one).
- Unmeasured is always distinct from absent: a page whose body was never parsed
  keeps ``trust_signals`` NULL and is counted in the skip reason, and a page
  with external links but no link-position evidence is named unmeasured rather
  than reported as having no citations.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .context import AuditContext
from .inlinks import _all_inlink_records, _site_host
from .models import Page
from .normalize import norm_url
from .rules import _decoded_path, _has_column, _rec

# The four ids check_trust_pages emits through a loop over _TRUST_PAGES --
# read by tests/test_check_producer_gate.py so the registry gate still sees them.
TRUST_PAGE_CHECKS = (
    "MISSING_ABOUT_PAGE",
    "MISSING_CONTACT_PAGE",
    "MISSING_PRIVACY_POLICY",
    "MISSING_TERMS_PAGE",
)

# Every id this module emits, so the crawl-coverage gate can attribute
# fired/skipped/silent to run_eeat rather than to a borrowed pipeline spy.
EEAT_CHECK_IDS = frozenset(
    {"NO_AUTHOR_BYLINE", "NO_CONTENT_DATES", "FEW_CITATIONS", "YMYL_REVIEW_CANDIDATE"}
    | set(TRUST_PAGE_CHECKS)
)

# URL path segments conventionally carrying editorial content. This is a
# reviewable scope heuristic, not a page-type verdict: the check reports which
# segment matched, and a page outside the vocabulary is simply never evaluated.
_CONTENT_SEGMENTS = frozenset(
    {
        "blog",
        "blogs",
        "news",
        "article",
        "articles",
        "post",
        "posts",
        "stories",
        "press",
        "insights",
        "journal",
        "journals",
        "guide",
        "guides",
        "learn",
        "resources",
        "wiki",
        "publication",
        "publications",
        "materialy",
        "stati",
        "novosti",
        "blogi",
        "publikacii",
    }
)

# Trust-page discovery vocabulary: whole path segments and whole anchor texts,
# English plus the Russian forms a domestic site actually writes. The matched
# path/anchor is always reported, so a site whose pages live outside this
# vocabulary produces a "not discovered" finding a reviewer can discount by
# reading the finding's own evidence -- not a silent miss.
_TRUST_PAGES: dict[str, dict[str, Any]] = {
    "MISSING_ABOUT_PAGE": {
        "kind": "about",
        "paths": {"about", "about-us", "about_us", "company", "who-we-are", "o-nas", "o-kompanii"},
        "anchors": {"about", "about us", "our story", "who we are", "о нас", "о компании"},  # noqa: RUF001 - Russian anchor vocabulary
    },
    "MISSING_CONTACT_PAGE": {
        "kind": "contact",
        "paths": {"contact", "contacts", "contact-us", "contact_us", "kontakty", "kontakt"},
        "anchors": {
            "contact",
            "contact us",
            "contacts",
            "get in touch",
            "контакты",
            "связаться с нами",  # noqa: RUF001 - Russian anchor vocabulary
        },
    },
    "MISSING_PRIVACY_POLICY": {
        "kind": "privacy",
        "paths": {
            "privacy",
            "privacy-policy",
            "privacy_policy",
            "privacy-notice",
            "personal-data",
            "politika-konfidentsialnosti",
            "politika-konfidencialnosti",
            "personalnye-dannye",
        },
        "anchors": {
            "privacy",
            "privacy policy",
            "privacy notice",
            "политика конфиденциальности",
            "обработка персональных данных",
        },
    },
    "MISSING_TERMS_PAGE": {
        "kind": "terms",
        "paths": {
            "terms",
            "terms-of-service",
            "terms-of-use",
            "terms-and-conditions",
            "tos",
            "oferta",
            "usloviya",
            "user-agreement",
        },
        "anchors": {
            "terms",
            "terms of service",
            "terms of use",
            "terms and conditions",
            "user agreement",
            "оферта",
            "публичная оферта",
            "условия использования",
            "пользовательское соглашение",
        },
    },
}

# YMYL-adjacent vocabulary for the review candidate: whole word matches on the
# decoded path and the title only. This cannot classify -- it names candidate
# pages a specialist may want to inspect with the trust signals above.
_YMYL_VOCAB: dict[str, frozenset[str]] = {
    "health": frozenset(
        {
            "health",
            "medical",
            "clinic",
            "symptom",
            "symptoms",
            "treatment",
            "medication",
            "therapy",
            "diagnosis",
            "pharmacy",
            "dental",
        }
    ),
    "finance": frozenset(
        {
            "finance",
            "financial",
            "insurance",
            "mortgage",
            "loan",
            "loans",
            "credit",
            "invest",
            "investing",
            "investment",
            "tax",
            "taxes",
            "banking",
            "pension",
            "debt",
        }
    ),
    "legal": frozenset(
        {
            "legal",
            "lawyer",
            "lawyers",
            "attorney",
            "attorneys",
            "lawsuit",
            "solicitor",
            "visa",
            "visas",
            "immigration",
            "divorce",
        }
    ),
}

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]+")  # noqa: RUF001 - Cyrillic token range
_EXTENSION_RE = re.compile(r"\.(?:html?|php|aspx?|jsp)$", re.IGNORECASE)
_MAX_DISCOVERED_URLS = 5


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _TOKEN_RE.findall(text))


def _path_segments(url: str) -> frozenset[str]:
    """Whole decoded path segments, file extensions folded away."""
    segments = set()
    for segment in _decoded_path(url).split("/"):
        segment = _EXTENSION_RE.sub("", segment).lower()
        if segment:
            segments.add(segment)
    return frozenset(segments)


def _signals(rec: dict[str, Any]) -> dict[str, Any] | None:
    """The page's trust-signal object, or ``None`` when it was never measured.

    A native crawl stores the parser's ``{author, dates, article}`` object.
    A non-empty *string* cell -- reachable only through a Screaming Frog Custom
    Extraction column the operator named "Trust Signals" -- cannot say which
    family it observed, so it counts once, as a medium-confidence author signal
    whose own name says where it came from; it never fabricates a date signal.
    """
    raw = rec.get("trust_signals")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        return {
            "author": [{"signal": "custom_column", "confidence": "medium", "value": raw.strip()}],
            "dates": [],
            "article": [],
        }
    return None


def _article_scope(page: Page, signals: dict[str, Any] | None) -> str | None:
    """Why this page is inside the authorship scope, or ``None`` when it is not.

    ``declared:`` names the markup markers the page itself carries (a JSON-LD
    article type, ``og:type=article``, an ``<article>`` element); ``url_path:``
    names the conventional content segment its URL sits under. A page matching
    neither is outside the check's scope and stays silent rather than being
    evaluated as a clean content page.
    """
    if isinstance(signals, dict):
        markers = [m for m in (signals.get("article") or []) if isinstance(m, str)]
        if markers:
            return "declared:" + ",".join(markers)
    segments = sorted(_path_segments(page.url) & _CONTENT_SEGMENTS)
    if segments:
        return "url_path:" + ",".join(segments)
    return None


def _invalid_jsonld_blocks(rec: dict[str, Any]) -> int | None:
    """JSON-LD blocks found minus blocks parsed -- the malformed count, when both
    columns exist. An export carrying only "Structured Data" cannot state it."""
    found, parsed = rec.get("structured_data"), rec.get("structured_data_parsed")
    if isinstance(found, (int, float)) and isinstance(parsed, (int, float)):
        return max(int(found) - int(parsed), 0)
    return None


def _signal_evidence(signals: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """The verbatim signal inventory a finding stands on: which carriers fired,
    what they declared, and whether a malformed JSON-LD block may have hidden
    evidence. A reviewer reads this instead of trusting the finding's verdict."""
    evidence: dict[str, Any] = {
        "author_signals": signals.get("author") or [],
        "date_signals": signals.get("dates") or [],
        "article_markers": signals.get("article") or [],
    }
    malformed = _invalid_jsonld_blocks(rec)
    if malformed:
        evidence["jsonld_blocks_malformed"] = malformed
    if rec.get("last_modified"):
        evidence["last_modified_header"] = rec["last_modified"]
    return evidence


def check_attribution(ctx: AuditContext) -> None:
    """NO_AUTHOR_BYLINE / NO_CONTENT_DATES -- objective markup presence (#823).

    The check fires only inside the article scope above, only when the signal
    column exists, and reports the signals it consulted. Presence is evidence,
    never a trustworthiness verdict; absence on a page is "no declared markup",
    which is what the finding says.
    """
    has_signals = _has_column(ctx, "trust_signals")
    has_lastmod = _has_column(ctx, "last_modified")
    if not has_signals:
        ctx.skip(
            "NO_AUTHOR_BYLINE",
            "no Trust Signals column (native crawl or a same-named custom "
            "extraction) -- no author markup evidence at all",
        )
    if not has_signals and not has_lastmod:
        ctx.skip(
            "NO_CONTENT_DATES",
            "no Trust Signals column and no Last Modified column -- no date evidence at all",
        )
        return
    applicable = unmeasured = 0
    for page in ctx.indexable_html_pages():
        rec = _rec(page)
        signals = _signals(rec)
        scope = _article_scope(page, signals)
        if scope is None:
            continue
        applicable += 1
        if has_signals and signals is None:
            # The column exists but this row holds none: an unparsed body,
            # never a measured absence. Counted, not passed as clean.
            unmeasured += 1
            continue
        signals = signals or {"author": [], "dates": [], "article": []}
        if has_signals and not signals.get("author"):
            ctx.add(
                "NO_AUTHOR_BYLINE",
                target_url=page.url,
                details={
                    "scope": scope,
                    "declared_signals": 0,
                },
                evidence=_signal_evidence(signals, rec),
            )
        if not signals.get("dates") and not rec.get("last_modified"):
            ctx.add(
                "NO_CONTENT_DATES",
                target_url=page.url,
                details={
                    "scope": scope,
                    "declared_signals": 0,
                },
                evidence=_signal_evidence(signals, rec),
            )
    if not applicable:
        # Nothing evaluated is not a clean result: the scope never applied,
        # which the run must say rather than count as silent coverage.
        if has_signals:
            ctx.skip(
                "NO_AUTHOR_BYLINE",
                "no article-scope pages (no declared article markers and no "
                "content-path URL) -- the check did not apply",
            )
        ctx.skip(
            "NO_CONTENT_DATES",
            "no article-scope pages (no declared article markers and no "
            "content-path URL) -- the check did not apply",
        )
    elif unmeasured:
        for check_id in ("NO_AUTHOR_BYLINE", "NO_CONTENT_DATES"):
            ctx.skip(
                check_id,
                f"{unmeasured} in-scope page(s) carry no trust-signal evidence "
                "-- their bodies were never measured",
            )


class _StoredInlinks:
    """Reiterate the existing native graph without building an all-inlinks list."""

    def __init__(self, graph):
        self.graph = graph

    def __iter__(self):
        return self.graph.iter_evidence_links()


def _inlinks(ctx: AuditContext):
    if ctx.graph_access is not None:
        return _StoredInlinks(ctx.graph_access)
    return _all_inlink_records(ctx)


def check_trust_pages(ctx: AuditContext) -> None:
    """MISSING_{ABOUT,CONTACT,PRIVACY,TERMS} -- discovery and index state (#823).

    "Missing" is never the raw absence of a match: each finding names one of
    ``found_non_indexable`` / ``found_error`` / ``discovered_not_crawled`` /
    ``not_discovered``, and the matched pages or anchors are quoted back so a
    reviewer can see exactly what the vocabulary caught. The same states are
    mirrored into ``ctx.trust_evidence`` for the audit summary.
    """
    if not ctx.pages:
        for check_id in TRUST_PAGE_CHECKS:
            ctx.skip(check_id, "no pages in scope -- nothing was crawled or exported")
        return
    site_host = _site_host(ctx)
    inlinks = _inlinks(ctx)
    trust_pages: dict[str, Any] = {}
    for check_id, cfg in _TRUST_PAGES.items():
        # Candidate group order chooses the first representative, including a
        # later indexable spelling of an earlier normalized URL. Keep that
        # scalar order, but never retain the groups' full Page objects.
        candidate_order: dict[str, int] = {}
        first = first_indexable = None
        indexable_order = None
        has_2xx = False
        evidence_kinds: set[str] = set()
        matched: set[str] = set()

        def remember(
            key,
            page,
            evidence_kind,
            matched_values,
            *,
            orders=candidate_order,
            kinds=evidence_kinds,
            matches=matched,
        ):
            nonlocal first, first_indexable, indexable_order, has_2xx
            order = orders.setdefault(key, len(orders))
            summary = (page.url, page.status_code, page.indexability)
            if first is None:
                first = summary
            has_2xx = has_2xx or page.is_2xx
            if (
                page.is_2xx
                and page.is_indexable
                and (indexable_order is None or order < indexable_order)
            ):
                first_indexable, indexable_order = summary, order
            kinds.add(evidence_kind)
            matches.update(matched_values)

        for page in ctx.pages:
            matched_paths = _path_segments(page.url) & cfg["paths"]
            if matched_paths:
                remember(norm_url(page.url), page, "url_path", matched_paths)
        discovered_only: set[str] = set()
        if inlinks:
            for rec in inlinks:
                anchor = " ".join((rec.get("anchor") or "").split()).lower()
                destination = rec.get("destination_url")
                if not anchor or anchor not in cfg["anchors"] or not destination:
                    continue
                host = urllib.parse.urlparse(destination).hostname or ""
                if host.lower() != site_host:
                    continue
                key = norm_url(destination)
                page = ctx.page_by_norm.get(key)
                if page is not None:
                    remember(key, page, "anchor", (anchor,))
                else:
                    # Preserve exactly the existing lexicographically first
                    # five URL examples, without retaining all other targets.
                    discovered_only.add(destination)
                    if len(discovered_only) > _MAX_DISCOVERED_URLS:
                        discovered_only.remove(max(discovered_only))
        state = "not_discovered"
        representative = first_indexable or first
        if first is not None:
            if first_indexable is not None:
                state = "found_indexable"
            elif has_2xx:
                state = "found_non_indexable"
            else:
                state = "found_error"
        elif discovered_only:
            state = "discovered_not_crawled"
        representative_url, representative_status, representative_indexability = representative or (
            None,
            None,
            None,
        )
        evidence_kinds = sorted(evidence_kinds)
        matched = sorted(matched)
        trust_pages[cfg["kind"]] = {
            "state": state,
            "url": representative_url,
            "status_code": representative_status,
            "indexability": representative_indexability,
            "evidence": evidence_kinds,
            "matched": matched,
            "discovered_not_crawled": sorted(discovered_only),
        }
        if state == "found_indexable":
            continue
        ctx.add(
            check_id,
            target_url=representative_url,
            status_code=representative_status,
            details={
                "state": state,
                "crawl_pages": len(ctx.pages),
                "vocabulary": "whole path segments and anchor texts from a fixed EN/RU list",
                "matched": matched,
                "evidence": evidence_kinds,
                "indexability": representative_indexability,
                "discovered_not_crawled": sorted(discovered_only),
            },
        )
    ctx.trust_evidence["trust_pages"] = trust_pages
    ctx.trust_evidence["anchor_evidence"] = "all_inlinks" if inlinks is not None else "none"


def check_citations(ctx: AuditContext) -> None:
    """FEW_CITATIONS -- observable outbound-reference facts only (#823).

    A citation claim needs link positions: an outbound link inside the body
    content is the only carrier that reads as a citation. When positions were
    never classified the check cannot tell a content link from a footer link,
    so those pages are counted unmeasured. The two facts that do stand on
    their own -- the page declares zero external outlinks, or every positioned
    external link sits outside the content -- are the only two it reports.
    """
    has_outlinks = _has_column(ctx, "external_outlinks")
    inlinks = _inlinks(ctx)
    if not has_outlinks and inlinks is None:
        ctx.skip(
            "FEW_CITATIONS",
            "no External Outlinks column and no all_inlinks export -- "
            "nothing to count outbound citations from",
        )
        return
    site_host = _site_host(ctx)
    external_by_source: dict[str, list[dict[str, Any]]] = {}
    if inlinks:
        for rec in inlinks:
            source, destination = rec.get("source_url"), rec.get("destination_url")
            if not source or not destination:
                continue
            host = urllib.parse.urlparse(destination).hostname or ""
            if host.lower() and host.lower() != site_host:
                external_by_source.setdefault(norm_url(source), []).append(rec)
    applicable = unmeasured = cited = 0
    for page in ctx.indexable_html_pages():
        rec = _rec(page)
        scope = _article_scope(page, _signals(rec))
        if scope is None:
            continue
        applicable += 1
        rows = external_by_source.get(norm_url(page.url)) if inlinks is not None else None
        positioned = [r for r in (rows or []) if r.get("link_position")]
        content_cites = [
            r for r in positioned if str(r["link_position"]).strip().lower() == "content"
        ]
        if content_cites:
            cited += 1
            continue
        outlinks = rec.get("external_outlinks") if has_outlinks else None
        if rows and len(positioned) == len(rows):
            ctx.add(
                "FEW_CITATIONS",
                target_url=page.url,
                details={
                    "scope": scope,
                    "external_links_positioned": len(positioned),
                    "content_position_links": 0,
                    "positions_seen": sorted({str(r["link_position"]) for r in positioned}),
                },
                evidence={"measured": True, "citation_carrier": "link position = content"},
            )
        elif isinstance(outlinks, (int, float)) and int(outlinks) == 0:
            ctx.add(
                "FEW_CITATIONS",
                target_url=page.url,
                details={
                    "scope": scope,
                    "external_outlinks": 0,
                },
                evidence={
                    "measured": True,
                    "citation_carrier": "external outlinks (a page with none cites nothing)",
                },
            )
        else:
            unmeasured += 1
    ctx.trust_evidence["citations"] = {
        "applicable_pages": applicable,
        "with_content_citations": cited,
        "unmeasured_pages": unmeasured,
        "position_evidence": "all_inlinks" if inlinks is not None else "none",
    }
    if not applicable:
        ctx.skip(
            "FEW_CITATIONS",
            "no article-scope pages (no declared article markers and no "
            "content-path URL) -- the check did not apply",
        )
    elif unmeasured:
        ctx.skip(
            "FEW_CITATIONS",
            f"{unmeasured} in-scope page(s) have external links whose positions "
            "were never classified -- citations unmeasured, not absent",
        )


def check_ymyl_review(ctx: AuditContext) -> None:
    """YMYL_REVIEW_CANDIDATE -- a keyword flag for human review only (#823).

    Deterministic whole-word matches on the decoded path and the title name
    candidate pages a specialist may want to check with the trust signals
    above. It is deliberately not a classifier: the finding's own message says
    so, and its details quote the matched terms so a reviewer can discard a
    false positive in one glance. No model is consulted.
    """
    pages = ctx.indexable_html_pages()
    if not pages:
        ctx.skip("YMYL_REVIEW_CANDIDATE", "no indexable HTML pages in scope")
        return
    for page in pages:
        rec = _rec(page)
        path_tokens = _tokens(_decoded_path(page.url))
        title_tokens = _tokens(str(rec.get("title") or ""))
        matched: dict[str, list[str]] = {}
        matched_in: set[str] = set()
        for group, vocab in _YMYL_VOCAB.items():
            hits = sorted((path_tokens | title_tokens) & vocab)
            if hits:
                matched[group] = hits
                if path_tokens & vocab:
                    matched_in.add("path")
                if title_tokens & vocab:
                    matched_in.add("title")
        if matched:
            ctx.add(
                "YMYL_REVIEW_CANDIDATE",
                target_url=page.url,
                details={
                    "matched": matched,
                    "matched_in": sorted(matched_in),
                    "rationale": "deterministic whole-word match on URL path and "
                    "title; a review candidate, not a YMYL classification",
                },
            )


def run_eeat(ctx: AuditContext) -> None:
    """The issue-#823 evidence pass -- runs beside run_rules/run_inlinks."""
    if ctx.internal_df is None:
        return
    ctx.trust_evidence.setdefault(
        "scope",
        {
            "indexable_html_pages": len(ctx.indexable_html_pages()),
            "crawl_pages": len(ctx.pages),
            "basis": "declared article markers or conventional content-path segments",
        },
    )
    check_attribution(ctx)
    check_trust_pages(ctx)
    check_citations(ctx)
    check_ymyl_review(ctx)
