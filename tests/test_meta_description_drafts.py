from __future__ import annotations

import csv

from seohead.checks.meta_description_drafts import (
    DraftCheckpoint,
    StaticDraftExecutor,
    SuppliedDraftExecutor,
    export_draft_review,
    prepare_draft_plan,
    run_draft_plan,
)


def _pages():
    return [
        {
            "url": "https://example.test/pumps",
            "html": "<html lang=en><head><title>Pumps</title><meta name=description content='Old copy'></head><body><nav>menu</nav><main><h1>Industrial pumps</h1><p>Industrial water pumps for factories with delivery options.</p></main></body></html>",
        },
        {
            "url": "https://example.test/valves",
            "html": "<html lang=en><head><title>Valves</title></head><body><main><h1>Control valves</h1><p>Control valves for water systems and industrial maintenance.</p></main></body></html>",
        },
        {"url": "https://example.test/missing", "html": ""},
    ]


def _draft(page, text):
    return {
        "url": page["url"],
        "source_sha256": page["source_reference"]["normalized_sha256"],
        "proposed_description": text,
    }


def test_supplied_and_delegated_synthetic_executors_share_the_same_contract(tmp_path):
    plan = prepare_draft_plan(_pages(), {"min_chars": 10, "max_chars": 155}, batch_size=1)
    first, second = plan["batches"]
    drafts = [
        _draft(first[0], "Industrial pumps for factory water systems with delivery options."),
        _draft(second[0], "Control valves for water systems and industrial maintenance projects."),
    ]

    supplied = run_draft_plan(
        plan, SuppliedDraftExecutor(drafts), DraftCheckpoint(tmp_path / "supplied.sqlite")
    )
    delegated = run_draft_plan(
        plan, StaticDraftExecutor(drafts), DraftCheckpoint(tmp_path / "delegated.sqlite")
    )

    assert [row["generation_state"] for row in supplied["rows"]] == [
        "unavailable",
        "completed",
        "completed",
    ]
    assert [row["generation_state"] for row in delegated["rows"]] == [
        "unavailable",
        "completed",
        "completed",
    ]
    assert supplied["executor"]["kind"] == "calling_agent"
    assert delegated["executor"]["kind"] == "delegated_agent"


def test_checkpoint_resumes_completed_rows_and_invalidates_changed_context(tmp_path):
    plan = prepare_draft_plan(_pages()[:1], {"min_chars": 10})
    page = plan["batches"][0][0]
    executor = SuppliedDraftExecutor(
        [_draft(page, "Industrial pumps for factory water systems and planned delivery support.")]
    )
    checkpoint = DraftCheckpoint(tmp_path / "drafts.sqlite")
    first = run_draft_plan(plan, executor, checkpoint)
    second = run_draft_plan(plan, executor, checkpoint)
    changed = run_draft_plan(
        prepare_draft_plan(_pages()[:1], {"min_chars": 11}), executor, checkpoint
    )

    assert first["rows"][0]["resumed"] is False
    assert second["rows"][0]["resumed"] is True
    assert changed["rows"][0]["generation_state"] == "completed"
    assert changed["rows"][0]["resumed"] is False


def test_stale_malformed_and_repetitive_drafts_remain_reviewable(tmp_path):
    plan = prepare_draft_plan(
        _pages()[:2], {"min_chars": 120, "max_chars": 155, "prohibited_phrases": ["best"]}
    )
    first, second = plan["batches"][0]
    text = "Best industrial equipment for water systems, delivery planning and maintenance support for every project today."
    result = run_draft_plan(
        plan,
        SuppliedDraftExecutor([_draft(first, text), _draft(second, text)]),
        DraftCheckpoint(tmp_path / "drafts.sqlite"),
    )

    assert all(row["generation_state"] == "completed" for row in result["rows"])
    assert "duplicate_proposed_description" in result["rows"][0]["review_reasons"]
    assert "repetitive_opening" in result["rows"][0]["review_reasons"]
    assert "contains_prohibited_brand_phrase" in result["rows"][0]["review_reasons"]
    assert "marketing_claim_not_observed_in_page_evidence" in result["rows"][0]["review_reasons"]


def test_export_is_formula_safe_and_no_cms_write(tmp_path):
    plan = prepare_draft_plan(_pages()[:1], {"min_chars": 1})
    page = plan["batches"][0][0]
    result = run_draft_plan(
        plan,
        SuppliedDraftExecutor([_draft(page, "=not a spreadsheet formula")]),
        DraftCheckpoint(tmp_path / "drafts.sqlite"),
    )
    json_path, csv_path = tmp_path / "review.json", tmp_path / "review.csv"
    export_draft_review(result, json_path, csv_path)

    assert json_path.exists()
    with csv_path.open() as source:
        row = next(csv.DictReader(source))
    assert row["proposed_description"].startswith("'=")


def test_executor_interruption_is_checkpointed_and_a_later_run_resumes_completed_work(tmp_path):
    plan = prepare_draft_plan(_pages()[:2], {"min_chars": 1}, batch_size=1)
    first, second = (batch[0] for batch in plan["batches"])

    class InterruptingExecutor:
        def __init__(self):
            self.calls = 0

        def describe(self):
            return {
                "kind": "calling_agent",
                "contract_version": "meta_description_drafts.v1",
                "model_identity": None,
                "data_transfer": "caller_runtime",
            }

        def execute(self, records):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("caller interrupted")
            return [
                _draft(first, "Industrial pumps for factory water systems and delivery support.")
            ]

    checkpoint = DraftCheckpoint(tmp_path / "drafts.sqlite")
    interrupted = run_draft_plan(plan, InterruptingExecutor(), checkpoint)
    recovered = run_draft_plan(
        plan,
        SuppliedDraftExecutor(
            [
                _draft(
                    second, "Control valves for water systems and industrial maintenance projects."
                )
            ]
        ),
        checkpoint,
    )

    assert {row["generation_state"] for row in interrupted["rows"]} == {"completed", "failed"}
    assert recovered["rows"][0]["resumed"] is True
    assert recovered["rows"][1]["generation_state"] == "completed"
