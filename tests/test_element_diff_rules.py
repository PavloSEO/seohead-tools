"""Offline tests for mapping element diffs onto crawl finding rules (#1014, slice 2)."""

from __future__ import annotations

from seohead.checks.element_diff import classify_diffs, diff_elements


def _rules(raw: dict, rendered: dict, *, rendered_ok: bool = True) -> list[str]:
    return [
        rule for rule, _ in classify_diffs(diff_elements(raw, rendered, rendered_ok=rendered_ok))
    ]


def test_noindex_only_in_raw_is_a_finding():
    assert _rules({"meta_robots": "noindex, follow"}, {}) == ["noindex_nofollow_raw_only"]


def test_nofollow_only_in_raw_is_a_finding():
    assert _rules({"meta_robots": "NOFOLLOW"}, {}) == ["noindex_nofollow_raw_only"]


def test_robots_directive_added_by_js_is_not_this_rule():
    assert _rules({}, {"meta_robots": "noindex"}) == []


def test_other_robots_directive_only_in_raw_is_not_a_finding():
    assert _rules({"meta_robots": "max-snippet:-1"}, {}) == []


def test_canonical_changed_by_render_is_a_mismatch():
    assert _rules({"canonical": "https://a.test/x"}, {"canonical": "https://a.test/y"}) == [
        "canonical_mismatch"
    ]


def test_canonical_only_after_render_is_its_own_rule():
    assert _rules({}, {"canonical": "https://a.test/x"}) == ["canonical_only_after_render"]


def test_title_h1_description_changed_or_added_by_js():
    raw = {"title": "Raw", "h1": "", "description": "Old"}
    rendered = {"title": "After", "h1": "Added", "description": "Old"}
    assert _rules(raw, rendered) == ["element_changed_by_js", "element_changed_by_js"]


def test_title_removed_by_render_is_not_a_finding():
    assert _rules({"title": "Raw"}, {}) == []


def test_unmeasured_render_yields_no_findings():
    assert _rules({"canonical": "https://a.test/x"}, {}, rendered_ok=False) == []
