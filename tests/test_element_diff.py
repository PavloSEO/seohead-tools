"""Offline tests for the raw vs rendered element diff (#1014, slice 1)."""

from __future__ import annotations

from seohead.checks.element_diff import ELEMENT_FIELDS, diff_elements


def test_identical_values_produce_no_diff():
    snap = {"title": "Pumps", "h1": "Pumps", "canonical": "https://example.com/"}
    assert diff_elements(snap, dict(snap)) == []


def test_empty_on_both_sides_is_not_a_diff():
    assert diff_elements({}, {}) == []


def test_changed_value_is_reported_with_both_sides():
    diffs = diff_elements({"title": "Raw"}, {"title": "After JS"})
    assert diffs == [{"field": "title", "kind": "changed", "raw": "Raw", "rendered": "After JS"}]


def test_value_only_in_raw_is_raw_only():
    diffs = diff_elements({"meta_robots": "noindex"}, {})
    assert diffs == [{"field": "meta_robots", "kind": "raw_only", "raw": "noindex", "rendered": ""}]


def test_value_only_after_render_is_rendered_only():
    diffs = diff_elements({}, {"canonical": "https://example.com/a"})
    assert diffs == [
        {
            "field": "canonical",
            "kind": "rendered_only",
            "raw": "",
            "rendered": "https://example.com/a",
        }
    ]


def test_whitespace_differences_are_not_diffs():
    assert diff_elements({"h1": "  Pumps\n  CDM  "}, {"h1": "Pumps CDM"}) == []


def test_none_values_are_treated_as_empty():
    assert diff_elements({"description": None}, {"description": ""}) == []


def test_rendered_not_ok_yields_nothing_even_when_values_differ():
    assert diff_elements({"title": "A"}, {"title": "B"}, rendered_ok=False) == []


def test_only_known_elements_are_compared():
    diffs = diff_elements({"words": 10, "links": 1}, {"words": 500, "links": 40})
    assert diffs == []
    assert ELEMENT_FIELDS == ("title", "h1", "description", "canonical", "meta_robots")


def test_field_order_is_stable():
    raw = {f: "raw" for f in ELEMENT_FIELDS}
    rendered = {f: "rendered" for f in ELEMENT_FIELDS}
    assert [d["field"] for d in diff_elements(raw, rendered)] == list(ELEMENT_FIELDS)
