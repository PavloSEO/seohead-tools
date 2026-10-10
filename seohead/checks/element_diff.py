"""Element-level diff between a raw HTML snapshot and its rendered DOM.

Pure function, no browser and no I/O. The caller decides whether the rendered
snapshot is trustworthy (see ``seohead.checks.render.incomplete_render_reason``)
and passes ``rendered_ok=False`` when it is not, so an unmeasured page yields no
diffs rather than false "changed by JavaScript" findings.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

ELEMENT_FIELDS: tuple[str, ...] = ("title", "h1", "description", "canonical", "meta_robots")


class ElementDiff(TypedDict):
    field: str
    kind: Literal["changed", "raw_only", "rendered_only"]
    raw: str
    rendered: str


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split())


def diff_elements(
    raw: Mapping[str, Any],
    rendered: Mapping[str, Any],
    *,
    rendered_ok: bool = True,
) -> list[ElementDiff]:
    """Return one ``ElementDiff`` per SEO element whose value differs after rendering.

    Values are compared after collapsing whitespace. An empty value on one side
    with a non-empty value on the other is ``raw_only`` or ``rendered_only``;
    two non-empty, different values are ``changed``. ``rendered_ok=False`` means
    the rendered snapshot was not a real capture and nothing is compared.
    """
    if not rendered_ok:
        return []
    diffs: list[ElementDiff] = []
    for field in ELEMENT_FIELDS:
        before = _norm(raw.get(field))
        after = _norm(rendered.get(field))
        if before == after:
            continue
        if not before:
            kind: Literal["changed", "raw_only", "rendered_only"] = "rendered_only"
        elif not after:
            kind = "raw_only"
        else:
            kind = "changed"
        diffs.append({"field": field, "kind": kind, "raw": before, "rendered": after})
    return diffs


ElementRule = Literal[
    "noindex_nofollow_raw_only",
    "canonical_mismatch",
    "canonical_only_after_render",
    "element_changed_by_js",
]

_ROBOTS_DIRECTIVES = ("noindex", "nofollow")
_JS_SENSITIVE_FIELDS = ("title", "description", "h1")


def classify_diffs(diffs: list[ElementDiff]) -> list[tuple[ElementRule, ElementDiff]]:
    """Map element diffs onto the crawl finding rules of #1014.

    A diff that matches no rule is dropped: it is measured but not a finding. The
    result is pure data; severity and presentation belong to the caller.
    """
    out: list[tuple[ElementRule, ElementDiff]] = []
    for diff in diffs:
        field, kind = diff["field"], diff["kind"]
        if field == "meta_robots" and kind == "raw_only":
            if any(token in diff["raw"].lower() for token in _ROBOTS_DIRECTIVES):
                out.append(("noindex_nofollow_raw_only", diff))
        elif field == "canonical":
            if kind == "changed":
                out.append(("canonical_mismatch", diff))
            elif kind == "rendered_only":
                out.append(("canonical_only_after_render", diff))
        elif field in _JS_SENSITIVE_FIELDS and kind in ("rendered_only", "changed"):
            out.append(("element_changed_by_js", diff))
    return out
