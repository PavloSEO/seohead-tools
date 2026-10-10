"""Unit tests for the shared CSV/XLSX formula guard in seohead.core.tabular."""

from __future__ import annotations

import pytest

from seohead.core.tabular import neutralize_formula


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_leads_are_prefixed_with_apostrophe(lead):
    assert neutralize_formula(f"{lead}SUM(A1)") == f"'{lead}SUM(A1)"


@pytest.mark.parametrize("value", ["plain", "", "a=b", " =leading space"])
def test_safe_strings_pass_through_unchanged(value):
    assert neutralize_formula(value) == value


def test_non_strings_pass_through_unchanged():
    assert neutralize_formula(None) is None
    assert neutralize_formula(3) == 3
