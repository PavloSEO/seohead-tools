"""Deterministic content normalization and language evidence for semantic analysis.

The semantic-similarity work (#765) needs its input defined exactly: which bytes
were read, which region of the document was measured, what the normalizer did to
the text, and what language evidence accompanies it. This module is that
definition (issue #801). It reuses the same content-area resolution as word
counts, Markdown extraction, and duplicate detection
(``content_area.resolve_content_area`` + ``extract_area_text``), so the
navigation/boilerplate exclusion policy is one shared policy, not a second list
that can silently drift.

Three hashes delimit the transformation. ``body_sha256`` is the retained
evidence identity (the bytes the corpus stored); ``input_sha256`` is the SHA-256
of the exact HTML string the normalizer consumed (identical to the body's when
the decoded text round-trips through UTF-8, distinct when it does not);
``normalized_sha256`` is the SHA-256 of the normalized output text. A consumer
can therefore reject an input that does not reproduce, without ever seeing the
text itself — public manifests carry hashes and counts only.

The normalizer pipeline is fixed and versioned as ``content_normalizer.v1``:
Unicode NFC, removal of invisible categories (control ``Cc``, format ``Cf`` —
which covers zero-width spaces, bidi marks and the soft hyphen — and surrogate
``Cs``), ``str.casefold``, and whitespace-run collapse. No statistical model and
no locale are involved, so the same input always yields the same output on any
platform.

Language is reported as evidence, not as a guess presented as fact: the
document's own ``<html lang>`` declaration is recorded as written, and the
detected side counts letter-script shares over the normalized text, which is
deterministic and dependency-free. A script share can distinguish Cyrillic from
Latin but cannot name a language (Latin text is not necessarily English), so the
field is named ``script`` and never coerced into a language code.

This module is pure and performs no network access.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from bs4 import BeautifulSoup, Tag

from seohead.checks.content_area import (
    AUTO_STRATEGIES,
    DEFAULT_EXCLUDE_TAGS,
    TEXT_EXCLUDED_TAGS,
    extract_area_text,
    resolve_content_area,
)

NORMALIZER_VERSION = "content_normalizer.v1"

# Unicode general categories whose characters carry no visible text: control
# (Cc), format (Cf — zero-width spaces, bidi controls, soft hyphen) and
# surrogate (Cs). Removing them is what makes two visually identical documents
# normalize identically even when one hides a U+200B in a heading.
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})

# Letter ranges per script family, checked only after str.isalpha() so a
# codepoint like U+00D7 (multiplication sign, inside the Latin-1 supplement)
# never counts as a letter.
_LATIN_RANGES = (
    (0x0041, 0x007A),  # Basic Latin letters
    (0x00C0, 0x024F),  # Latin-1 supplement letters, Latin Extended-A and -B
    (0x1E00, 0x1EFF),  # Latin Extended Additional
    (0x2C60, 0x2C7F),  # Latin Extended-C
    (0xA720, 0xA7FF),  # Latin Extended-D
    (0xAB30, 0xAB6F),  # Latin Extended-E
)
_CYRILLIC_RANGES = (
    (0x0400, 0x052F),  # Cyrillic and the Cyrillic Supplement
    (0x1C80, 0x1C88),  # Cyrillic Extended-C
    (0x2DE0, 0x2DFF),  # Cyrillic Extended-A
    (0xA640, 0xA69F),  # Cyrillic Extended-B
)

# Fewer letters than this cannot support a script claim; a dominant share below
# this threshold reports "mixed" rather than the plurality winner.
MIN_SCRIPT_LETTERS = 20
DOMINANT_SCRIPT_SHARE = 0.85

_TOKEN_RE = re.compile(r"\w{2,}", flags=re.UNICODE)


def normalize_text(text: str) -> str:
    """Canonical normalized form under ``content_normalizer.v1``.

    NFC, invisible-category removal, casefold, then whitespace-run collapse —
    a fixed pipeline, so the recorded ``normalizer_version`` in a manifest is
    enough to reproduce or reject an output hash.
    """
    if not isinstance(text, str):
        raise ValueError("text to normalize must be a string")
    text = unicodedata.normalize("NFC", text)
    # Whitespace controls (\t, \n, \x0b, ...) survive this pass: they are word
    # boundaries, so the collapse step folds them into spaces rather than the
    # removal merging words across them.
    text = "".join(
        ch for ch in text if unicodedata.category(ch) not in _REMOVED_CATEGORIES or ch.isspace()
    )
    return " ".join(text.casefold().split())


def _script_of(char: str) -> str:
    codepoint = ord(char)
    for low, high in _LATIN_RANGES:
        if low <= codepoint <= high:
            return "latin"
    for low, high in _CYRILLIC_RANGES:
        if low <= codepoint <= high:
            return "cyrillic"
    return "other"


def script_evidence(text: str) -> dict[str, Any]:
    """Letter-script profile of already-normalized text.

    ``script`` is "latin", "cyrillic", "mixed", "other" or "undetermined".
    "undetermined" covers texts too short to support a claim at all — a five-
    letter page is "undetermined", not "latin", because the sample cannot carry
    the conclusion. A strong "other" share (CJK, Arabic, ...) is named rather
    than folded into "mixed", which is reserved for genuinely blended
    Latin/Cyrillic text.
    """
    counts = {"latin": 0, "cyrillic": 0, "other": 0}
    for char in text:
        if char.isalpha():
            counts[_script_of(char)] += 1
    letters = sum(counts.values())
    shares = (
        {name: round(counts[name] / letters, 4) for name in counts}
        if letters
        else {"latin": 0.0, "cyrillic": 0.0, "other": 0.0}
    )
    if letters < MIN_SCRIPT_LETTERS:
        script = "undetermined"
    else:
        # max() over a fixed-order dict breaks ties toward the first name,
        # which is arbitrary but deterministic.
        dominant = max(counts, key=counts.get)
        if counts[dominant] / letters >= DOMINANT_SCRIPT_SHARE:
            script = dominant
        elif shares["other"] >= 0.5:
            script = "other"
        else:
            script = "mixed"
    return {"script": script, "script_letters": letters, "script_shares": shares}


def declared_language(soup: BeautifulSoup) -> dict[str, str]:
    """The document's own ``<html lang>`` claim, kept as written plus its primary subtag.

    The raw attribute is evidence: a malformed or unnormalized declaration is
    itself worth seeing, so only whitespace is trimmed. ``declared_primary`` is
    the casefolded first subtag ("en-US" -> "en") for grouping, derived rather
    than trusted.
    """
    raw = ""
    html_tag = soup.find("html")
    if isinstance(html_tag, Tag):
        value = html_tag.get("lang")
        if isinstance(value, list):
            value = " ".join(str(part) for part in value)
        raw = str(value or "").strip()
    primary = raw.split("-", 1)[0].strip().casefold() if raw else ""
    return {"declared": raw, "declared_primary": primary}


def normalize_document(html: str, content_area: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize one decoded HTML document's content region for semantic analysis.

    The region is resolved by the shared content-area policy: a configured
    ``include_selector``/``root_selector`` wins, otherwise ``<main>``,
    ``[role="main"]`` then ``<article>``, otherwise the whole body; the
    resolved strategy is named per document so a wrong selector is visible
    rather than silently widened. ``content_area`` is the same config mapping
    ``markdown_extract`` and the corpus readers accept.
    """
    if not isinstance(html, str):
        raise ValueError("html must be a decoded string")
    soup = BeautifulSoup(html, features="lxml")
    root, strategy = resolve_content_area(soup, content_area)
    normalized = normalize_text(extract_area_text(root))
    return {
        "text": normalized,
        "content_area_strategy": strategy,
        "language": {**declared_language(soup), **script_evidence(normalized)},
        "input_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "text_chars": len(normalized),
        "text_tokens": len(_TOKEN_RE.findall(normalized)),
    }


def normalization_policy(content_area: dict[str, Any] | None = None) -> dict[str, Any]:
    """The recorded policy a normalized manifest was produced under.

    Everything here is what a reader needs to reproduce or reject the output:
    the normalizer version and its fixed steps, the content-area resolution
    order with the exclusion sets that actually applied, and the language
    evidence rules. ``configured`` echoes the caller's content-area config so a
    manifest can be re-derived rather than trusted.
    """
    return {
        "normalizer_version": NORMALIZER_VERSION,
        "text_pipeline": [
            "resolve_content_area",
            "extract_area_text",
            "unicode_nfc",
            "remove_categories_Cc_Cf_Cs",
            "casefold",
            "collapse_whitespace_runs",
        ],
        "content_area": {
            "configured": content_area or {},
            "selection_order": [
                "include_selector",
                "root_selector",
                *(strategy for _, strategy in AUTO_STRATEGIES),
                "body",
            ],
            "default_exclude_tags": list(DEFAULT_EXCLUDE_TAGS),
            "non_text_tags": list(TEXT_EXCLUDED_TAGS),
        },
        "language": {
            "declared": "the document's own <html lang> attribute, as written",
            "detected": "letter-script shares over the normalized text",
            "min_script_letters": MIN_SCRIPT_LETTERS,
            "dominant_script_share": DOMINANT_SCRIPT_SHARE,
        },
    }


def manifest_entry(item: dict[str, Any]) -> dict[str, Any]:
    """The public per-document manifest row: hashes and policy fields, never the text."""
    state = item.get("state") or ("normalized" if item.get("text") else "empty")
    return {
        "url": item.get("url") or item.get("id") or "",
        "state": state,
        "reason": str(item.get("reason") or ""),
        "representation": item.get("representation"),
        "indexable": item.get("indexable"),
        "content_area_strategy": item.get("content_area_strategy"),
        "language": item.get("language"),
        "body_sha256": item.get("body_sha256"),
        "input_sha256": item.get("input_sha256"),
        "normalized_sha256": item.get("normalized_sha256"),
        "text_chars": item.get("text_chars"),
        "text_tokens": item.get("text_tokens"),
    }


def prepare_items(
    items: list[dict[str, Any]], content_area: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Normalize inline ``{url, html}`` items into the same record shape a scan yields.

    An item without a usable ``html`` string is an explicit ``unavailable``
    entry, never a clean empty page — the same contract the retained-corpus
    reader keeps for bodies that were not stored.
    """
    prepared: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            prepared.append({"url": "", "state": "unavailable", "reason": "item is not an object"})
            continue
        url = str(item.get("url") or item.get("id") or "")
        html = item.get("html")
        if not isinstance(html, str) or not html.strip():
            prepared.append(
                {"url": url, "state": "unavailable", "reason": "item supplies no html body"}
            )
            continue
        indexable = item.get("indexable")
        prepared.append(
            {
                "id": url,
                "url": url,
                "state": None,  # manifest_entry derives normalized/empty from text
                "representation": item.get("representation"),
                "indexable": indexable if type(indexable) is bool else None,
                "body_sha256": item.get("body_sha256"),
                **normalize_document(html, content_area),
            }
        )
    return prepared
