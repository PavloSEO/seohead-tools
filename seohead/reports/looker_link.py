"""Build a local-only Looker Studio Linking API URL for the reporting pack.

The builder cannot publish or inspect a Looker report.  It requires a supplied
original report ID and returns that ID as externally unverified: the native
five-page template remains a separate, user-authorized Google step.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from seohead.reports.bi import BI_SCHEMA_VERSION, DATASET_SPECS, MANIFEST_FORMAT

LINK_FORMAT = "seohead.looker-copy-link.v1"
LINK_BASE = "https://datastudio.google.com/reporting/create"
BLUEPRINT_FORMAT = "seohead.looker-studio-blueprint.v1"
PAGE_IDS = ("overview", "technical", "links", "content", "performance")
_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_REPORT_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_BQ_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")


class LookerLinkError(ValueError):
    """A local reporting-pack copy link lacks an exact, safe input contract."""


def load_blueprint(path: str | Path) -> dict[str, Any]:
    """Load one small regular reporting-pack blueprint without a network call."""
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise LookerLinkError("blueprint must be an existing regular JSON file")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise LookerLinkError("blueprint could not be read") from exc
    if len(raw) > 1_000_000:
        raise LookerLinkError("blueprint exceeds the 1 MiB local safety bound")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LookerLinkError("blueprint must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise LookerLinkError("blueprint must be a JSON object")
    return value


def _published_fields() -> dict[str, list[dict[str, Any]]]:
    return {
        name: [
            {"name": field.name, "type": field.type, "nullable": field.nullable}
            for field in spec[0]
        ]
        for name, spec in DATASET_SPECS.items()
    }


def _validate_blueprint(
    blueprint: dict[str, Any],
) -> tuple[set[str], dict[str, list[dict[str, Any]]]]:
    if blueprint.get("format") != BLUEPRINT_FORMAT:
        raise LookerLinkError("unsupported Looker reporting-pack blueprint format")
    if blueprint.get("source_contract") != MANIFEST_FORMAT:
        raise LookerLinkError("blueprint does not consume the supported BI manifest schema")
    pages = blueprint.get("pages")
    if (
        not isinstance(pages, list)
        or tuple(page.get("id") for page in pages if isinstance(page, dict)) != PAGE_IDS
    ):
        raise LookerLinkError(
            "blueprint must contain the original five reporting-pack pages in order"
        )
    published = _published_fields()
    used: set[str] = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("datasets"), list):
            raise LookerLinkError("blueprint page datasets are invalid")
        datasets = page["datasets"]
        if not datasets or any(dataset not in published for dataset in datasets):
            raise LookerLinkError("blueprint references an unsupported BI dataset")
        fields = page.get("fields")
        if not isinstance(fields, list) or not all(
            any(field["name"] == name for dataset in datasets for field in published[dataset])
            for name in fields
        ):
            raise LookerLinkError("blueprint page fields do not match the published BI schema")
        used.update(datasets)
    if used != set(published):
        raise LookerLinkError("blueprint must cover every declared BI dataset")
    return used, published


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LookerLinkError(f"{label} is required")
    return value.strip()


def _source_parameters(
    source: dict[str, Any], datasets: set[str]
) -> tuple[str, list[tuple[str, str]]]:
    alias = _require_text(source.get("alias"), "data-source alias")
    if not _ALIAS.fullmatch(alias):
        raise LookerLinkError("data-source alias must be a closed identifier, not a wildcard")
    dataset = _require_text(source.get("dataset"), "BI dataset")
    if dataset not in datasets:
        raise LookerLinkError("data-source mapping names an unsupported BI dataset")
    name = _require_text(source.get("data_source_name"), "data-source name")
    kind = source.get("kind")
    prefix = f"ds.{alias}."
    params = [(prefix + "datasourceName", name), (prefix + "refreshFields", "false")]
    if kind == "sheets":
        spreadsheet_id = _require_text(source.get("spreadsheet_id"), "Sheets spreadsheet ID")
        worksheet_id = source.get("worksheet_id")
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{10,256}", spreadsheet_id)
            or type(worksheet_id) is not int
            or worksheet_id < 0
        ):
            raise LookerLinkError(
                "Sheets mapping requires a valid spreadsheet ID and non-negative worksheet ID"
            )
        _require_text(source.get("worksheet_title"), "Sheets worksheet title")
        params.extend(
            [
                (prefix + "connector", "googleSheets"),
                (prefix + "spreadsheetId", spreadsheet_id),
                (prefix + "worksheetId", str(worksheet_id)),
                (prefix + "hasHeader", "true"),
            ]
        )
    elif kind == "bigquery":
        project_id = _require_text(source.get("project_id"), "BigQuery project ID")
        dataset_id = _require_text(source.get("dataset_id"), "BigQuery dataset ID")
        table_id = _require_text(source.get("table_id"), "BigQuery table ID")
        if (
            not _PROJECT_ID.fullmatch(project_id)
            or not _BQ_ID.fullmatch(dataset_id)
            or not _BQ_ID.fullmatch(table_id)
        ):
            raise LookerLinkError(
                "BigQuery mapping contains an invalid project, dataset, or table ID"
            )
        params.extend(
            [
                (prefix + "connector", "bigQuery"),
                (prefix + "type", "TABLE"),
                (prefix + "projectId", project_id),
                (prefix + "datasetId", dataset_id),
                (prefix + "tableId", table_id),
            ]
        )
        if source.get("billing_project_id") is not None:
            billing = _require_text(source.get("billing_project_id"), "BigQuery billing project ID")
            if not _PROJECT_ID.fullmatch(billing):
                raise LookerLinkError("BigQuery billing project ID is invalid")
            params.append((prefix + "billingProjectId", billing))
    else:
        raise LookerLinkError("data-source kind must be 'sheets' or 'bigquery'")
    return dataset, params


def build_looker_copy_link(
    *,
    blueprint: dict[str, Any],
    original_report_id: str,
    original_report_confirmed: bool,
    report_name: str,
    data_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a copy URL from a verified local blueprint and explicit source aliases.

    ``original_report_id`` is syntactically validated only.  This local code cannot
    prove it refers to a published, viewable native report, so its state stays
    explicit in the result rather than claiming template availability.
    """
    datasets, fields = _validate_blueprint(blueprint)
    report_id = _require_text(original_report_id, "original report ID")
    if not _REPORT_ID.fullmatch(report_id):
        raise LookerLinkError(
            "original report ID must be a real report identifier; blank/default templates are refused"
        )
    if original_report_confirmed is not True:
        raise LookerLinkError(
            "original report template is missing or unconfirmed; confirm the supplied real report ID"
        )
    name = _require_text(report_name, "report name")
    if not isinstance(data_sources, list) or not data_sources:
        raise LookerLinkError("one explicit data-source mapping is required for every BI dataset")
    parameters: list[tuple[str, str]] = [
        ("c.reportId", report_id),
        ("c.mode", "edit"),
        ("c.explain", "true"),
        ("r.reportName", name),
    ]
    mapped: set[str] = set()
    aliases: set[str] = set()
    for source in data_sources:
        if not isinstance(source, dict):
            raise LookerLinkError("data-source mappings must be objects")
        dataset, source_params = _source_parameters(source, datasets)
        alias = source["alias"].strip()
        if dataset in mapped or alias in aliases:
            raise LookerLinkError(
                "each BI dataset and data-source alias must be mapped exactly once"
            )
        mapped.add(dataset)
        aliases.add(alias)
        parameters.extend(source_params)
    if mapped != datasets:
        raise LookerLinkError("data-source mappings must cover every reporting-pack BI dataset")
    return {
        "format": LINK_FORMAT,
        "network": False,
        "writes": False,
        "source_contract": MANIFEST_FORMAT,
        "source_schema_version": BI_SCHEMA_VERSION,
        "template_state": "operator_confirmed_original_report_id_unverified_locally",
        "template_verification_required": [
            "Confirm the supplied original report ID is a real five-page reporting-pack template.",
            "Confirm the user has view access to that report and its configured data sources.",
            "Do not use a blank or Google default report as a substitute for this pack.",
        ],
        "original_report_id": report_id,
        "copy_link": f"{LINK_BASE}?{urlencode(parameters)}",
        "data_source_aliases": sorted(aliases),
        "dataset_mappings": sorted(mapped),
        "field_schema": fields,
        "five_page_import_checklist": blueprint["pages"],
        "global_controls": blueprint.get("global_controls", []),
        "date_controls": blueprint.get("date_controls", []),
        "calculated_fields": blueprint.get("calculated_fields", []),
        "filter_rules": blueprint.get("filter_rules", []),
    }
