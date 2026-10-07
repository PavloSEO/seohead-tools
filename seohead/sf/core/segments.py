"""Segment declaration and assignment: named subsets of a crawl, reported on
separately rather than folded into one averaged number.

Ninety-six checks over forty thousand URLs is a list nobody reads. A segment
turns it into "the blog is fine and the catalogue is broken" -- a table a
developer can act on template by template. Two things make this a distinct
diagnostic rather than a filter a caller could build inline:

- Membership is per page and non-exclusive: a page may belong to more than
  one segment, or none, because a URL pattern and a directory and a template
  are independent lenses that overlap in practice.
- A segment's rules may reference another, earlier segment (an "is a member
  of" rule), which turns the segment list into a dependency graph rather than
  a flat filter set. That graph is validated -- an unknown reference or a
  cycle is rejected by name -- rather than assumed safe because the caller
  happened to list things in a workable order.

For the exclusive breakdown every report needs (so a segmented table's counts
sum to the same totals the ungrouped report already showed), each page also
gets a single ``primary`` segment: the first one that matches, in a
deterministic evaluation order derived from the dependency graph -- not from
whatever order the caller listed the definitions in, so shuffling that list
without changing the dependency structure never changes the result. Anything
matching no segment lands in the explicit ``unsegmented`` bucket rather than
being dropped, which is what makes the totals add up exactly.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "segments.v1"
UNSEGMENTED = "unsegmented"

_VALID_OPS = {"prefix", "contains", "eq", "regex", "in", "segment"}


class SegmentError(ValueError):
    """A segment set cannot be evaluated as declared."""


@dataclass(frozen=True)
class SegmentRule:
    """One condition. A segment matches a page if any of its rules do (OR).

    ``field`` is looked up on the page record itself first, falling back to
    a ``metrics`` sub-dict -- so the same rule works against a flat list-mode
    ``PageRecord``-shaped dict and against a Screaming Frog ``Page.to_json()``,
    whose CSV-sourced fields (word count, canonical, status...) live under
    ``metrics``. This is what "any crawl field, including post-crawl results"
    means in practice: nothing here is restricted to the URL alone.

    op="segment" ignores ``field`` and instead checks membership in the
    earlier segment named by ``value`` -- the mechanism that turns the segment
    list into a dependency graph.
    """

    op: str
    field: str = "url"
    value: Any = None

    def __post_init__(self) -> None:
        if self.op not in _VALID_OPS:
            raise SegmentError(
                f"unknown segment rule op {self.op!r}; expected one of {sorted(_VALID_OPS)}"
            )
        if self.op == "regex":
            re.compile(str(self.value))  # fail at definition time, not at first match


@dataclass(frozen=True)
class Segment:
    name: str
    rules: tuple[SegmentRule, ...]

    @property
    def depends_on(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.value for r in self.rules if r.op == "segment"))


def _coerce_rule(raw: Mapping[str, Any] | SegmentRule) -> SegmentRule:
    if isinstance(raw, SegmentRule):
        return raw
    return SegmentRule(
        op=raw.get("op", "prefix"), field=raw.get("field", "url"), value=raw.get("value")
    )


def _coerce_segment(raw: Mapping[str, Any] | Segment) -> Segment:
    if isinstance(raw, Segment):
        return raw
    name = raw.get("name")
    if not name:
        raise SegmentError("a segment definition needs a non-empty 'name'")
    rules_raw = raw.get("rules") or []
    if not rules_raw:
        raise SegmentError(f"segment {name!r} has no rules; it would never match anything")
    return Segment(name=name, rules=tuple(_coerce_rule(r) for r in rules_raw))


def _field_value(record: Mapping[str, Any], field_path: str) -> Any:
    if "." in field_path:
        value: Any = record
        for part in field_path.split("."):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            else:
                return None
        return value
    if field_path in record:
        return record[field_path]
    metrics = record.get("metrics")
    if isinstance(metrics, Mapping):
        return metrics.get(field_path)
    return None


def _rule_matches(
    rule: SegmentRule, record: Mapping[str, Any], resolved: Mapping[str, set[str]]
) -> bool:
    if rule.op == "segment":
        url = record.get("url")
        return url in resolved.get(rule.value, ())

    value = _field_value(record, rule.field)
    if rule.op == "in":
        container = (
            rule.value if isinstance(rule.value, list | tuple | set | frozenset) else (rule.value,)
        )
        return value in container

    text = "" if value is None else str(value)
    if rule.op == "prefix":
        return text.startswith(str(rule.value))
    if rule.op == "contains":
        return str(rule.value) in text
    if rule.op == "eq":
        return value == rule.value
    if rule.op == "regex":
        return re.search(str(rule.value), text) is not None
    raise SegmentError(
        f"unknown segment rule op {rule.op!r}"
    )  # unreachable: validated at definition


def _segment_matches(
    segment: Segment, record: Mapping[str, Any], resolved: Mapping[str, set[str]]
) -> bool:
    return any(_rule_matches(rule, record, resolved) for rule in segment.rules)


def resolve_order(segments: Iterable[Mapping[str, Any] | Segment]) -> list[Segment]:
    """The evaluation order for a segment set.

    Segments are topologically sorted by their ``segment`` references, so a
    dependency is always resolved before anything that references it,
    regardless of the order the caller listed the definitions in. Ties (two
    segments with no dependency relationship to each other) are broken
    alphabetically by name, which is what makes the whole order deterministic
    under reordering the input rather than merely "one of several valid
    orders": the same segment set always produces the same order.

    Raises ``SegmentError`` naming an unknown reference, a duplicate name, or
    -- spelling out every segment on the cycle -- a circular dependency.
    """
    resolved_segments = [_coerce_segment(s) for s in segments]
    by_name: dict[str, Segment] = {}
    for seg in resolved_segments:
        if seg.name in by_name:
            raise SegmentError(f"duplicate segment name {seg.name!r}")
        by_name[seg.name] = seg

    for seg in resolved_segments:
        for dep in seg.depends_on:
            if dep not in by_name:
                raise SegmentError(f"segment {seg.name!r} references unknown segment {dep!r}")

    in_degree = dict.fromkeys(by_name, 0)
    dependents: dict[str, list[str]] = {name: [] for name in by_name}
    for seg in resolved_segments:
        for dep in seg.depends_on:
            in_degree[seg.name] += 1
            dependents[dep].append(seg.name)

    ready = sorted(name for name, degree in in_degree.items() if degree == 0)
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        order.append(name)
        for dependent in sorted(dependents[name]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                heapq.heappush(ready, dependent)

    if len(order) != len(by_name):
        remaining = sorted(set(by_name) - set(order))
        raise SegmentError(
            f"circular segment dependency: {' -> '.join(_find_cycle(remaining, by_name))}"
        )

    return [by_name[name] for name in order]


def required_fields(segments: Iterable[Mapping[str, Any] | Segment]) -> dict[str, frozenset[str]]:
    """Return each compiled segment's direct and dependency-required fields.

    Consumers that only retain a projection of source pages use this metadata to
    distinguish a field that was explicitly null from one never retained.  The
    same resolved order and dependency graph as ``assign_segments`` applies.
    """
    required: dict[str, frozenset[str]] = {}
    for segment in resolve_order(segments):
        fields = {rule.field for rule in segment.rules if rule.op != "segment"}
        for dependency in segment.depends_on:
            fields.update(required[dependency])
        required[segment.name] = frozenset(fields)
    return required


def _find_cycle(remaining: list[str], by_name: Mapping[str, Segment]) -> list[str]:
    """Name one concrete cycle among the nodes Kahn's algorithm could not
    place, instead of just listing the segments involved."""
    remaining_set = set(remaining)
    path: list[str] = []
    on_path: set[str] = set()

    def dfs(name: str) -> list[str] | None:
        path.append(name)
        on_path.add(name)
        for dep in by_name[name].depends_on:
            if dep not in remaining_set:
                continue
            if dep in on_path:
                return [*path[path.index(dep) :], dep]
            found = dfs(dep)
            if found:
                return found
        path.pop()
        on_path.discard(name)
        return None

    for name in sorted(remaining_set):
        found = dfs(name)
        if found:
            return found
    return remaining  # unreachable if Kahn's algorithm left anything behind


def assign_segments(
    pages: Iterable[Mapping[str, Any]], segments: Iterable[Mapping[str, Any] | Segment]
) -> dict[str, Any]:
    """Evaluate every segment over every page.

    Returns the resolved evaluation ``order``, the full non-exclusive
    ``memberships`` (every segment a URL matches, in evaluation order), and
    ``primary`` (the first matching segment per URL, or ``None`` if it
    matches none) -- the exclusive view every summed report is built from.
    """
    order = resolve_order(segments)
    pages = list(pages)
    resolved: dict[str, set[str]] = {}
    for seg in order:
        resolved[seg.name] = {
            page["url"]
            for page in pages
            if page.get("url") and _segment_matches(seg, page, resolved)
        }

    memberships: dict[str, list[str]] = {}
    primary: dict[str, str | None] = {}
    for page in pages:
        url = page.get("url")
        if not url:
            continue
        member_of = [seg.name for seg in order if url in resolved[seg.name]]
        memberships[url] = member_of
        primary[url] = member_of[0] if member_of else None

    return {"order": [seg.name for seg in order], "memberships": memberships, "primary": primary}


def segment_report(
    pages: Iterable[Mapping[str, Any]],
    issues: Iterable[Mapping[str, Any]],
    segments: Iterable[Mapping[str, Any] | Segment],
) -> dict[str, Any]:
    """Break an audit's pages and issues down by segment.

    Every page and every issue is attributed to exactly one bucket -- its
    primary segment, or the explicit ``unsegmented`` bucket -- so
    ``sum(pages_by_segment.values()) == len(pages)`` and the same holds for
    issues, always. A segmented report that did not sum to the ungrouped
    total would be silently dropping pages or issues somewhere, exactly the
    failure this toolkit's other diffs (compare.py, reconcile.py) exist to
    make impossible.
    """
    pages = list(pages)
    issues = list(issues)
    assignment = assign_segments(pages, segments)
    primary: dict[str, str | None] = assignment["primary"]
    names = [*assignment["order"], UNSEGMENTED]

    pages_by_segment = dict.fromkeys(names, 0)
    for page in pages:
        pages_by_segment[primary.get(page.get("url")) or UNSEGMENTED] += 1

    issues_by_segment: dict[str, dict[str, int]] = {name: {} for name in names}
    for issue in issues:
        row = issues_by_segment[primary.get(issue.get("target_url")) or UNSEGMENTED]
        check = issue.get("check", "")
        row[check] = row.get(check, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "order": assignment["order"],
        "memberships": assignment["memberships"],
        "pages_by_segment": pages_by_segment,
        "issues_by_segment": issues_by_segment,
        "totals": {"pages": len(pages), "issues": len(issues)},
    }
