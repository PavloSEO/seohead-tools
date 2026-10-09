"""Offline contracts for publication and branded-search cohort projections."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from seohead.mcp import handlers
from seohead.reports.cohorts import CohortProjectionError, gsc_progress, publication_cohorts

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_publication_projection_preserves_dates_sources_and_measured_zero(tmp_path):
    document = _fixture("publication_cohort_input.json")
    document["age_windows"] = [{"id": "first-month", "start_day": 0, "end_day": 30}]
    document["as_of_date"] = "2026-02-28"
    result = publication_cohorts(document=document, out_dir=str(tmp_path / "publication"))
    assert result["format"] == "seohead.publication-cohort-result.v1"
    inventory = _csv(tmp_path / "publication" / "publication_inventory.csv")
    uncertain = next(row for row in inventory if row["content_id"] == "content-1")
    assert uncertain["publication_raw"] == "Spring 2026"
    assert uncertain["publication_state"] == "ambiguous"
    assert uncertain["author_state"] == "missing"
    authors = _csv(tmp_path / "publication" / "publication_authors.csv")
    assert {row["author"] for row in authors} == {"Ada", "Bea"}
    observations = _csv(tmp_path / "publication" / "provider_observations.csv")
    gsc_zero = next(
        row for row in observations if row["provider"] == "gsc" and row["metric"] == "clicks"
    )
    assert gsc_zero["metric_value"] == "0.0"
    assert gsc_zero["metric_state"] == "measured"
    assert {row["provider"] for row in observations} == {"gsc", "ga4"}
    age_rows = _csv(tmp_path / "publication" / "age_window_observations.csv")
    assert age_rows and age_rows[0]["history_state"] == "partial"
    manifest = json.loads((tmp_path / "publication" / "manifest.json").read_text())
    assert manifest["policy"]["causality"].startswith("publication timing")


def test_publication_projection_does_not_join_normalization_collisions(tmp_path):
    document = _fixture("publication_cohort_input.json")
    duplicate = dict(document["content"][0])
    duplicate["url"] = "https://EXAMPLE.test/guide/"
    document["content"].append(duplicate)
    publication_cohorts(document=document, out_dir=str(tmp_path / "collision"))
    observations = _csv(tmp_path / "collision" / "provider_observations.csv")
    guide = [row for row in observations if row["url"] == "https://example.test/guide"]
    assert guide and {row["join_state"] for row in guide} == {"collision"}
    assert all(row["content_ids_json"] == "[]" for row in guide)


def test_publication_projection_requires_a_new_output_directory(tmp_path):
    with pytest.raises(CohortProjectionError, match="must not already exist"):
        publication_cohorts(
            document=_fixture("publication_cohort_input.json"), out_dir=str(tmp_path)
        )


def test_gsc_progress_preserves_classification_scope_and_weighted_ctr(tmp_path):
    result = gsc_progress(
        document=_fixture("gsc_progress_input.json"), out_dir=str(tmp_path / "gsc")
    )
    assert result["format"] == "seohead.gsc-progress-result.v1"
    contributions = _csv(tmp_path / "gsc" / "query_contributions.csv")
    assert {row["classification"] for row in contributions} == {
        "branded",
        "non_branded",
        "ambiguous",
    }
    assert any(
        row["clicks"] == "0.0" and row["clicks_state"] == "measured" for row in contributions
    )
    summaries = _csv(tmp_path / "gsc" / "period_summaries.csv")
    non_brand = next(row for row in summaries if row["classification"] == "non_branded")
    assert non_brand["ctr"] == "0.2"
    assert non_brand["scope_id"]
    changes = _csv(tmp_path / "gsc" / "period_changes.csv")
    assert any(row["percent_change_state"] == "new_from_zero" for row in changes)
    positions = _csv(tmp_path / "gsc" / "position_bands.csv")
    assert any(row["movement"] == "improved" for row in positions)
    assert all("not a tracked rank" in row["interpretation"] for row in positions)
    manifest = json.loads((tmp_path / "gsc" / "manifest.json").read_text())
    assert "unknown" in manifest["policy"]["absence"]


def test_gsc_progress_refuses_non_gsc_evidence(tmp_path):
    document = _fixture("gsc_progress_input.json")
    document["evidence"]["mapping"]["source"]["provider"] = "ga4"
    with pytest.raises(CohortProjectionError, match="only normalized GSC"):
        gsc_progress(document=document, out_dir=str(tmp_path / "gsc"))


def test_handlers_expose_both_offline_projections(tmp_path):
    publication = handlers.publication_cohorts(
        document=_fixture("publication_cohort_input.json"), out_dir=str(tmp_path / "publication")
    )
    progress = handlers.gsc_progress(
        document=_fixture("gsc_progress_input.json"), out_dir=str(tmp_path / "gsc")
    )
    assert publication["ok"] is True
    assert progress["ok"] is True
