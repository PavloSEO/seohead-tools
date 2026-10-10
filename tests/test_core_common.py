"""Behaviour pinned for the shared helpers in seohead.core.common and semantics.demand."""

from __future__ import annotations

import json
import re

from seohead.core.common import canonical_json, collapse_whitespace, utc_iso_z
from seohead.semantics.demand import has_observed_demand


def test_collapse_whitespace_joins_runs_and_trims():
    assert collapse_whitespace("  a \n\t b  ") == "a b"
    assert collapse_whitespace("") == ""


def test_canonical_json_is_sorted_compact_and_keeps_unicode():
    assert canonical_json({"b": 1, "a": "тест"}) == '{"a":"тест","b":1}'


def test_canonical_json_default_variant_accepts_nan_like_json_dumps():
    # The strict allow_nan=False variant lives in bi.py and compare_store.py on purpose.
    assert canonical_json({"x": float("nan")}) == json.dumps(
        {"x": float("nan")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_utc_iso_z_ends_with_z_and_has_no_offset():
    value = utc_iso_z()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", value)


def test_has_observed_demand_requires_a_positive_known_value():
    assert has_observed_demand(5, None, None)
    assert has_observed_demand(None, 0, 3)
    assert not has_observed_demand(None, None, None)
    assert not has_observed_demand(0, 0, 0)
