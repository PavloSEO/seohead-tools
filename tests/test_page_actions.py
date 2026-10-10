"""Offline tests for the closed page-action validator (issue #1011). No browser."""

import pytest

from seohead.checks.page_actions import MAX_STEPS, validate_actions


def test_closed_set_is_canonicalized_and_budgeted():
    steps, budget = validate_actions(
        [
            {"action": "click", "selector": "button.more"},
            {"action": "scroll", "pixels": 800},
            {"action": "wait_for_selector", "selector": ".item", "timeout_ms": 2000},
            {"action": "wait_ms", "ms": 500},
        ]
    )
    assert steps[0] == {"action": "click", "selector": "button.more"}
    assert steps[3] == {"action": "wait_ms", "ms": 500}
    assert budget == 5000 + 2000 + 500


@pytest.mark.parametrize(
    "bad",
    [
        "click",
        [{"action": "evaluate", "script": "1"}],
        [{"action": "click", "selector": "a", "extra": True}],
        [{"action": "click", "selector": "a:::bad"}],
        [{"action": "click", "selector": ""}],
        [{"action": "scroll", "pixels": 0}],
        [{"action": "scroll", "pixels": True}],
        [{"action": "wait_ms", "ms": 10_001}],
        [{"action": "wait_for_selector", "selector": "a", "timeout_ms": 30_001}],
        [{"action": "click", "selector": "a", "regex": "x"}],
        [{"action": "click"}],
        [{"selector": "a"}],
        [{"action": "click", "selector": "a"}] * (MAX_STEPS + 1),
    ],
)
def test_rejects_out_of_contract_input(bad):
    with pytest.raises(ValueError):
        validate_actions(bad)


def test_rejects_when_static_budget_exceeds_limit():
    steps = [{"action": "wait_ms", "ms": 10_000} for _ in range(7)]
    with pytest.raises(ValueError, match="static time budget"):
        validate_actions(steps)


def test_empty_list_is_valid():
    assert validate_actions([]) == ([], 0)
