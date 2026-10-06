"""Keep the synthetic Looker blueprint tied to the published BI schema."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from seohead.reports.bi import DATASET_SPECS
from seohead.reports.looker_link import LookerLinkError, build_looker_copy_link, load_blueprint
from seohead.reports.reporting_pack import build_worksheets

PACK_DIR = Path("examples/reporting-pack")
WORKSHEETS_DIR = PACK_DIR / "worksheets"


def test_looker_blueprint_uses_only_published_bi_datasets_and_fields():
    path = Path("examples/reporting-pack/looker-studio-blueprint.json")
    blueprint = json.loads(path.read_text(encoding="utf-8"))
    assert blueprint["format"] == "seohead.looker-studio-blueprint.v1"
    fields = {name: {field.name for field in spec[0]} for name, spec in DATASET_SPECS.items()}
    for page in blueprint["pages"]:
        assert page["datasets"]
        for dataset in page["datasets"]:
            assert dataset in fields
        assert all(
            any(field in fields[dataset] for dataset in page["datasets"])
            for field in page["fields"]
        )
    for control in blueprint["global_controls"]:
        datasets = control.get("datasets") or [control["dataset"]]
        assert all(control["field"] in fields[dataset] for dataset in datasets)


def test_reporting_pack_preview_is_valid_svg():
    path = Path("examples/reporting-pack/layout-preview.svg")
    assert ElementTree.parse(path).getroot().tag.endswith("svg")
    assert "SEOHEAD Evidence Reporting Pack" in path.read_text(encoding="utf-8")


def _sources():
    return [
        {
            "alias": "coverage_sheet",
            "dataset": "coverage",
            "kind": "sheets",
            "data_source_name": "Coverage worksheet",
            "spreadsheet_id": "1Qs8BdfxZXALh6vX4zrE7ZyGnR3h5k",
            "worksheet_id": 2,
            "worksheet_title": "coverage",
        },
        {
            "alias": "pages_sheet",
            "dataset": "pages",
            "kind": "sheets",
            "data_source_name": "Pages worksheet",
            "spreadsheet_id": "1Qs8BdfxZXALh6vX4zrE7ZyGnR3h5k",
            "worksheet_id": 3,
            "worksheet_title": "pages",
        },
        {
            "alias": "findings_table",
            "dataset": "findings",
            "kind": "bigquery",
            "data_source_name": "Findings table",
            "project_id": "sample-project",
            "dataset_id": "seohead_reporting",
            "table_id": "findings_v1",
        },
        {
            "alias": "links_table",
            "dataset": "link_occurrences",
            "kind": "bigquery",
            "data_source_name": "Links table",
            "project_id": "sample-project",
            "dataset_id": "seohead_reporting",
            "table_id": "links_v1",
        },
        {
            "alias": "metrics_table",
            "dataset": "metrics",
            "kind": "bigquery",
            "data_source_name": "Metrics table",
            "project_id": "sample-project",
            "dataset_id": "seohead_reporting",
            "table_id": "metrics_v1",
            "billing_project_id": "sample-project",
        },
        {
            "alias": "cohorts_table",
            "dataset": "cohorts",
            "kind": "bigquery",
            "data_source_name": "Cohorts table",
            "project_id": "sample-project",
            "dataset_id": "seohead_reporting",
            "table_id": "cohorts_v1",
        },
    ]


def test_copy_link_requires_an_original_template_and_exact_six_source_mappings():
    blueprint = load_blueprint("examples/reporting-pack/looker-studio-blueprint.json")
    result = build_looker_copy_link(
        blueprint=blueprint,
        original_report_id="0B_U5RNpwhcE6SF85TENURnc4UjA",
        original_report_confirmed=True,
        report_name="Synthetic SEOHEAD Evidence Reporting Pack",
        data_sources=_sources(),
    )
    assert result["network"] is False and result["writes"] is False
    assert result["template_state"] == "operator_confirmed_original_report_id_unverified_locally"
    assert result["dataset_mappings"] == sorted(DATASET_SPECS)
    assert [page["id"] for page in result["five_page_import_checklist"]] == [
        "overview",
        "technical",
        "links",
        "content",
        "performance",
    ]
    assert "c.reportId=0B_U5RNpwhcE6SF85TENURnc4UjA" in result["copy_link"]
    assert "ds.pages_sheet.connector=googleSheets" in result["copy_link"]
    assert "ds.metrics_table.connector=bigQuery" in result["copy_link"]


def test_copy_link_refuses_missing_template_and_incomplete_source_mapping():
    blueprint = load_blueprint("examples/reporting-pack/looker-studio-blueprint.json")
    with pytest.raises(LookerLinkError, match="original report ID"):
        build_looker_copy_link(
            blueprint=blueprint,
            original_report_id="",
            original_report_confirmed=False,
            report_name="Synthetic SEOHEAD Evidence Reporting Pack",
            data_sources=_sources(),
        )
    with pytest.raises(LookerLinkError, match="cover every"):
        build_looker_copy_link(
            blueprint=blueprint,
            original_report_id="0B_U5RNpwhcE6SF85TENURnc4UjA",
            original_report_confirmed=True,
            report_name="Synthetic SEOHEAD Evidence Reporting Pack",
            data_sources=_sources()[:-1],
        )
    with pytest.raises(LookerLinkError, match="missing or unconfirmed"):
        build_looker_copy_link(
            blueprint=blueprint,
            original_report_id="0B_U5RNpwhcE6SF85TENURnc4UjA",
            original_report_confirmed=False,
            report_name="Synthetic SEOHEAD Evidence Reporting Pack",
            data_sources=_sources(),
        )


def _worksheet_rows(dataset: str) -> list[dict[str, str]]:
    path = WORKSHEETS_DIR / f"{dataset}.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_committed_worksheets_match_published_bi_schema():
    manifest = json.loads((WORKSHEETS_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "seohead.bi-manifest.v1"
    for name, spec in DATASET_SPECS.items():
        path = WORKSHEETS_DIR / f"{name}.csv"
        assert path.is_file(), f"missing worksheet for dataset {name}"
        header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
        published = [field.name for field in spec[0]]
        assert header[: len(published)] == published
        declared = manifest["datasets"][name]
        assert declared["schema_version"] == f"seohead.bi.{name}.v1"
        assert len(_worksheet_rows(name)) == declared["row_count"]


def test_committed_worksheets_demonstrate_required_states():
    metrics = _worksheet_rows("metrics")
    assert any(
        row["value_number"] == "0" and row["value_state"] == "measured" for row in metrics
    ), "fixture must show a measured numeric zero distinct from missing"
    assert any(
        row["value_number"] == "" and row["value_state"] == "unavailable" for row in metrics
    ), "fixture must show a missing/suppressed value that must not plot as zero"
    assert {row["provider"] for row in metrics} == {"gsc", "ga4"}
    assert {row["population_state"] for row in metrics} >= {
        "matched",
        "external_only",
        "unkeyable",
    }

    cohorts = _worksheet_rows("cohorts")
    quadrants = [row for row in cohorts if row["cohort_id"] == "search_visibility_vs_sessions"]
    assert any(
        row["membership"] == "member"
        and row["search_value_state"] == "measured"
        and row["sessions_value_state"] == "measured"
        for row in quadrants
    ), "fixture must contain at least one complete search-versus-sessions quadrant"
    assert any(
        row["membership"] == "unclassified" and row["state"] == "incomplete" for row in quadrants
    ), "fixture must show the incomplete-pair cohort state explicitly"
    assert any(row["value_label"] == "zero_search_positive_sessions" for row in quadrants)

    coverage = _worksheet_rows("coverage")
    assert any(
        row["dataset"] == "link_occurrences" and row["state"] == "unavailable" for row in coverage
    ), "the unavailable dataset state must be visible, not an empty chart"
    assert any(row["state"] == "skipped" for row in coverage)
    assert _worksheet_rows("link_occurrences") == []
    assert len(_worksheet_rows("pages")) > 0
    assert len(_worksheet_rows("findings")) > 0


def test_committed_worksheets_are_reproducible_from_sources(tmp_path):
    """The fixture stays byte-bound to the versioned BI projection."""
    out_dir = tmp_path / "worksheets"
    result = build_worksheets(
        audit=Path("examples/audit.json"),
        provider_inputs=sorted((PACK_DIR / "sources").glob("*.json")),
        search_metric="clicks",
        out_dir=out_dir,
    )
    committed = {
        path.name: path.read_bytes() for path in sorted(WORKSHEETS_DIR.iterdir()) if path.is_file()
    }
    fresh = {path.name: path.read_bytes() for path in sorted(out_dir.iterdir()) if path.is_file()}
    assert fresh == committed, (
        "committed worksheets are stale; run python scripts/generate_reporting_pack_worksheets.py"
    )
    assert result["network"] is False
    assert result["run_id"] == "audit:73121f1fdbf0e2fed6d03e92"
