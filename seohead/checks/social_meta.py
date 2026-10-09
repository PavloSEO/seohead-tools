"""Report missing required and recommended Open Graph/Twitter Card fields.

Open Graph and Twitter metadata control link previews in social networks and
messaging applications. Unlike Schema.org, these consumers do not share a
vocabulary: they expect a fixed set of fields, some of which are necessary for
a preview to render at all. This module applies a rule table to the
``parser.og`` and ``parser.twitter`` mappings extracted earlier.

The rules encode a practical minimum for a renderable preview (required) and a
high-quality preview (recommended). A rule may accept alternative keys such as
``og:image`` and ``og:image:src``; any populated alternative satisfies it.
"""

from __future__ import annotations

from typing import Any

# (alternative keys, level, label). Level is required or recommended.
OG_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("og:title",), "required", "og:title"),
    (("og:type",), "required", "og:type"),
    (("og:url",), "required", "og:url"),
    (("og:image", "og:image:src", "og:image:url"), "required", "og:image"),
    (("og:image:alt",), "required", "og:image:alt"),
    (("og:description",), "recommended", "og:description"),
    (("og:site_name",), "recommended", "og:site_name"),
    (("og:locale",), "recommended", "og:locale"),
]

TWITTER_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("twitter:card",), "required", "twitter:card"),
    (("twitter:title",), "required", "twitter:title"),
    (("twitter:description",), "required", "twitter:description"),
    (("twitter:image", "twitter:image:src"), "required", "twitter:image"),
    (("twitter:image:alt",), "required", "twitter:image:alt"),
    (("twitter:site",), "recommended", "twitter:site"),
    (("twitter:creator",), "recommended", "twitter:creator"),
]


def check_tags(
    tags: dict[str, str], rules: list[tuple[tuple[str, ...], str, str]]
) -> list[dict[str, Any]]:
    """Apply rules to a tag mapping and return only missing fields with severity."""
    out: list[dict[str, Any]] = []
    for keys, level, label in rules:
        if not any(tags.get(k) for k in keys):
            out.append({"tag": label, "level": level, "alternatives": list(keys)})
    return out


def check_social_meta(
    og: dict[str, str] | None = None, twitter: dict[str, str] | None = None
) -> dict[str, Any]:
    """Identify fields missing from renderable, high-quality social previews."""
    og = og or {}
    twitter = twitter or {}
    og_missing = check_tags(og, OG_RULES)
    tw_missing = check_tags(twitter, TWITTER_RULES)

    og_required = [m for m in og_missing if m["level"] == "required"]
    tw_required = [m for m in tw_missing if m["level"] == "required"]

    findings: list[str] = []
    if og_required:
        findings.append(
            f"Open Graph is missing required preview fields: "
            f"{', '.join(m['tag'] for m in og_required)}"
        )
    if tw_required:
        findings.append(
            f"Twitter Card is missing required fields: {', '.join(m['tag'] for m in tw_required)}"
        )
    if not og and not twitter:
        findings.append(
            "No Open Graph or Twitter tags were found; social link previews will not render"
        )
    elif not og:
        findings.append(
            "Open Graph tags are absent; previews may not render in Open Graph consumers"
        )
    elif not twitter:
        findings.append(
            "Twitter Card tags are absent; previews in X and compatible consumers may be limited"
        )

    return {
        "ok": True,
        "og_present": len(og),
        "twitter_present": len(twitter),
        "og_missing": og_missing,
        "twitter_missing": tw_missing,
        "og_complete": not og_required,
        "twitter_complete": not tw_required,
        "findings": findings,
    }
