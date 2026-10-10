"""Validation of closed page-action specs for rendered crawls (issue #1011).

A page action is data, never code: the closed set is ``click``, ``scroll``,
``wait_for_selector`` and ``wait_ms``. Each step carries only its declared
fields, selectors must compile as CSS, and the static time budget of a whole
action list is bounded up front. Nothing here runs a browser; the renderer
consumes the canonical output. ``extract`` and raw JavaScript are not part of
this slice and are rejected until the owner decides them (see docs/DECISIONS.md).
"""

from __future__ import annotations

from typing import Any

from soupsieve import SelectorSyntaxError
from soupsieve import compile as compile_selector

VERSION = "page_actions.v1"
MAX_STEPS = 20
MAX_SELECTOR_CHARS = 512
MAX_STEP_TIMEOUT_MS = 30_000
MAX_WAIT_MS = 10_000
MAX_SCROLL_PIXELS = 20_000
MAX_TOTAL_BUDGET_MS = 60_000
# A click has no declared wait, so it is charged this fixed budget.
CLICK_BUDGET_MS = 5_000
_FIELDS = {
    "click": {"action", "selector"},
    "scroll": {"action", "pixels"},
    "wait_for_selector": {"action", "selector", "timeout_ms"},
    "wait_ms": {"action", "ms"},
}


def _selector(value: Any) -> str:
    if type(value) is not str or not 0 < len(value) <= MAX_SELECTOR_CHARS:
        raise ValueError("page action selector is invalid")
    try:
        compile_selector(value)
    except SelectorSyntaxError as exc:
        raise ValueError("page action CSS selector is invalid") from exc
    return value


def _integer(value: Any, low: int, high: int, message: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(message)
    return value


def validate_actions(value: Any) -> tuple[list[dict[str, Any]], int]:
    """Return ``(canonical_steps, static_budget_ms)`` or raise ``ValueError``."""
    if not isinstance(value, list) or len(value) > MAX_STEPS:
        raise ValueError("page actions must be a bounded list")
    steps: list[dict[str, Any]] = []
    budget = 0
    for step in value:
        if not isinstance(step, dict) or type(step.get("action")) is not str:
            raise ValueError("page action must be an object with an action name")
        kind = step["action"]
        if kind not in _FIELDS:
            raise ValueError("page action is not in the closed set")
        if set(step) != _FIELDS[kind]:
            raise ValueError("page action has unsupported fields")
        if kind == "click":
            canonical = {"action": kind, "selector": _selector(step["selector"])}
            budget += CLICK_BUDGET_MS
        elif kind == "scroll":
            pixels = _integer(
                step["pixels"], 1, MAX_SCROLL_PIXELS, "scroll pixels must be 1..20000"
            )
            canonical = {"action": kind, "pixels": pixels}
        elif kind == "wait_for_selector":
            timeout = _integer(
                step["timeout_ms"], 1, MAX_STEP_TIMEOUT_MS, "step timeout is out of range"
            )
            canonical = {
                "action": kind,
                "selector": _selector(step["selector"]),
                "timeout_ms": timeout,
            }
            budget += timeout
        else:
            ms = _integer(step["ms"], 0, MAX_WAIT_MS, "wait_ms must be 0..10000")
            canonical = {"action": kind, "ms": ms}
            budget += ms
        steps.append(canonical)
    if budget > MAX_TOTAL_BUDGET_MS:
        raise ValueError("page actions exceed the static time budget")
    return steps, budget
