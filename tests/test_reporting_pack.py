"""Keep the synthetic Looker blueprint tied to the published BI schema."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from seohead.reports.bi import DATASET_SPECS
from seohead.reports.looker_link import LookerLinkError, build_looker_copy_link, load_blueprint


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
