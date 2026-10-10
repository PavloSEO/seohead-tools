"""Caller-executed AI custom column: plan, consent scope, validation and CSV output."""

from __future__ import annotations

import csv
import hashlib

import pytest

from seohead.checks import ai_column as core
from seohead.mcp import handlers
from tests.test_scan_corpus_inputs import _scan

HTML = """<html lang=en><head><title>Industrial pumps</title></head>
<body><main><h1>Industrial pumps</h1><p>Water pump equipment for factories.</p></main></body></html>"""


def _item(url: str, text: str = "Water pump equipment.") -> dict:
    return {
        "url": url,
        "text": text,
        "title": "Industrial pumps",
        "headings": ["Industrial pumps"],
        "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "body_sha256": "b" * 64,
    }


def test_plan_selects_urls_estimates_size_and_declares_consent_scope():
    items = [_item("https://example.test/a"), _item("https://example.test/b")]
    plan = core.prepare_ai_column_plan(
        items, "Classify search intent", urls=["https://example.test/a"]
    )

    assert plan["contract_version"] == core.CONTRACT_VERSION
    assert [page["url"] for page in plan["pages"]] == ["https://example.test/a"]
    assert plan["estimate"]["eligible"] == 1
    assert plan["estimate"]["pricing"] == "not_applied"
    assert plan["estimate"]["approx_input_tokens"] > 0
    assert plan["consent"]["data_sent_by_seohead"] is False
    assert plan["coverage"]["state"] == "complete"


def test_missing_and_empty_pages_are_unavailable_not_clean():
    plan = core.prepare_ai_column_plan(
        [_item("https://example.test/a", text="   ")],
        "Summarise",
        urls=["https://example.test/a", "https://example.test/missing"],
    )
    reasons = {page["url"]: page["reason"] for page in plan["pages"]}

    assert plan["coverage"]["state"] == "partial"
    assert plan["coverage"]["unavailable"] == 2
    assert reasons["https://example.test/missing"] == "url is not in the retained scan"


def test_plan_bounds_prompt_selection_and_excerpt():
    long_text = "x" * (core.MAX_EXCERPT_CHARS + 50)
    plan = core.prepare_ai_column_plan([_item("https://example.test/a", long_text)], "Summarise")
    payload = plan["pages"][0]["payload"]

    assert len(payload["excerpt"]) == core.MAX_EXCERPT_CHARS
    assert payload["excerpt_truncated"] is True
    with pytest.raises(ValueError, match="prompt is required"):
        core.prepare_ai_column_plan([], "   ")
    with pytest.raises(ValueError, match="exceeds"):
        core.prepare_ai_column_plan([], "x" * (core.MAX_PROMPT_CHARS + 1))
    with pytest.raises(ValueError, match="limit is 1"):
        core.prepare_ai_column_plan(
            [_item("https://example.test/a"), _item("https://example.test/b")],
            "Summarise",
            max_pages=1,
        )
    with pytest.raises(ValueError, match="column name"):
        core.prepare_ai_column_plan([], "Summarise", column="x" * (core.MAX_COLUMN_CHARS + 1))


def test_apply_accepts_matching_values_and_reports_missing_stale_and_failed_rows():
    items = [
        _item("https://example.test/a"),
        _item("https://example.test/b"),
        _item("https://example.test/c"),
    ]
    plan = core.prepare_ai_column_plan(items, "Draft a meta description")
    source = {page["url"]: page["source_reference"]["normalized_sha256"] for page in plan["pages"]}
    result = core.apply_ai_column_results(
        plan,
        [
            {
                "url": "https://example.test/a",
                "source_sha256": source["https://example.test/a"],
                "value": " Pumps ",
            },
            {"url": "https://example.test/b", "source_sha256": "stale", "value": "Pumps"},
            {"url": "https://example.test/unknown", "source_sha256": "x", "value": "Pumps"},
        ],
    )
    states = {row["url"]: (row["state"], row["column_value"]) for row in result["rows"]}

    assert states["https://example.test/a"] == ("completed", "Pumps")
    assert states["https://example.test/b"] == ("failed", None)
    assert states["https://example.test/c"] == ("missing", None)
    assert result["coverage"]["unmatched_rows"] == 1
    assert result["coverage"]["state"] == "partial"


def test_apply_rejects_empty_and_oversized_values_and_duplicates():
    plan = core.prepare_ai_column_plan([_item("https://example.test/a")], "Classify")
    digest = plan["pages"][0]["source_reference"]["normalized_sha256"]
    url = "https://example.test/a"
    empty = core.apply_ai_column_results(
        plan, [{"url": url, "source_sha256": digest, "value": " "}]
    )
    big = core.apply_ai_column_results(
        plan, [{"url": url, "source_sha256": digest, "value": "x" * (core.MAX_VALUE_CHARS + 1)}]
    )
    duplicate = core.apply_ai_column_results(
        plan,
        [
            {"url": url, "source_sha256": digest, "value": "a"},
            {"url": url, "source_sha256": digest, "value": "b"},
        ],
    )

    assert empty["rows"][0]["error"] == "value is missing"
    assert big["rows"][0]["error"].startswith("value exceeds")
    assert duplicate["rows"][0]["error"] == "duplicate row for page"
    assert duplicate["coverage"]["completed"] == 0


def test_csv_export_is_formula_safe_and_named_for_the_column(tmp_path):
    plan = core.prepare_ai_column_plan(
        [_item("https://example.test/a")], "Classify", column="intent"
    )
    digest = plan["pages"][0]["source_reference"]["normalized_sha256"]
    result = core.apply_ai_column_results(
        plan,
        [{"url": "https://example.test/a", "source_sha256": digest, "value": "=HYPERLINK('x')"}],
    )
    path = tmp_path / "column.csv"
    core.export_ai_column_csv(result, path)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["url", "state", "intent", "error"]
    assert rows[1][2] == "'=HYPERLINK('x')"


def test_handler_plans_from_retained_scan_and_applies_supplied_values(tmp_path):
    scan = _scan(tmp_path, [("https://example.test/a", HTML)])
    plan = handlers.ai_column(scan=str(scan), prompt="Classify search intent")

    assert plan["ok"] is True
    assert plan["plan"]["estimate"]["eligible"] == 1
    page = plan["plan"]["pages"][0]
    result = handlers.ai_column(
        scan=str(scan),
        prompt="Classify search intent",
        rows=[
            {
                "url": page["url"],
                "source_sha256": page["source_reference"]["normalized_sha256"],
                "value": "informational",
            }
        ],
        csv_path=str(tmp_path / "column.csv"),
    )

    assert result["result"]["rows"][0]["column_value"] == "informational"
    assert (tmp_path / "column.csv").exists()


def test_handler_requires_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        handlers.ai_column(prompt="Classify")
    with pytest.raises(ValueError, match="exactly one"):
        handlers.ai_column(items=[], scan="x.sqlite", prompt="Classify")
