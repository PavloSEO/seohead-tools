"""Streaming workbook dimensions remain usable by read-only consumers."""

from __future__ import annotations

import gc
import sys

import pytest

from seohead.reports import xlsx
from tests.test_scan_artifact_office import frozen_office_clock as frozen_office_clock


def _document(count):
    return {
        "domain": "example.test",
        "findings": [
            {
                "severity": "warning",
                "url": f"https://example.test/{index}",
                "client_title": "Missing title",
            }
            for index in range(count)
        ],
        "pages": [
            {"url": f"https://example.test/{index}", "status": 200} for index in range(count)
        ],
        "site": {
            "tech_detect": {
                "technologies": [
                    {"category": "server", "name": "Fixture", "evidence": "Saved header"}
                ]
            }
        },
        "suppressed_issues": [
            {
                "id": "excluded",
                "check": "TITLE_MISSING",
                "suppression": {"rule_id": "rule", "reason": "Intentional"},
            }
        ],
        "summary": {
            "pages_checked": count,
            "findings_total": count,
            "findings_by_severity": {"warning": count},
            "severity_note": "Saved coverage basis",
            "checks_disabled": [{"id": "H1_MISSING", "reason": "Operator choice"}],
            "tools_failed": [{"tool": "TITLE_LONG", "error": "Input unavailable"}],
            "finding_exclusion_policy": [{"id": "rule", "reason": "Intentional"}],
            "finding_exclusions": {"suppressed_total": 1},
            "project_coverage": {
                "project": {"site": "example.test", "uuid": "fixture"},
                "status": {
                    "state": "partial",
                    "revision": 1,
                    "items": [{"id": "check", "title": "Saved check", "state": "not_run"}],
                },
            },
            "evidence_contract": {
                "scan_identity_state": "unavailable",
                "scan_identity_reason": "Not retained",
            },
        },
    }


class _Stream:
    def __init__(self, document):
        self.document = document

    def get(self, key, default=None):
        value = self.document.get(key, default)
        return iter(value) if key in {"findings", "pages"} else value


@pytest.mark.parametrize("count", [0, 3])
def test_all_sheet_dimensions_match_emitted_cells_without_read_only_recalculation(
    tmp_path, frozen_office_clock, count
):
    from openpyxl import load_workbook

    document = _document(count)
    materialized = tmp_path / "materialized.xlsx"
    streamed = tmp_path / "streamed.xlsx"
    xlsx.write(document, materialized)
    xlsx.write_stream(_Stream(document), streamed)
    assert materialized.read_bytes() == streamed.read_bytes()
    regular = load_workbook(streamed)
    read_only = load_workbook(streamed, read_only=True)
    try:
        assert set(read_only.sheetnames) == {
            "Summary",
            "Findings",
            "Pages",
            "Technologies",
            "Finding Exclusions",
            "Suppressed Findings",
            "Project Coverage",
            "Evidence coverage",
        }
        for sheet in regular:
            actual = read_only[sheet.title]
            assert actual.max_row == sheet.max_row, sheet.title
            assert actual.max_column == sheet.max_column, sheet.title
            assert actual.calculate_dimension() == sheet.calculate_dimension(), sheet.title
            assert list(actual.values) == list(sheet.values), sheet.title
        assert read_only["Findings"].max_row == read_only["Pages"].max_row == count + 1
        assert len(regular["Summary"]._charts) == 1
        assert read_only["Findings"].max_column == 10
        assert read_only["Pages"].max_column == 11
    finally:
        regular.close()
        read_only.close()


def test_failed_archive_open_closes_xml_generators_and_removes_tempfiles(tmp_path, monkeypatch):
    from openpyxl.worksheet._writer import ALL_TEMP_FILES

    baseline = set(ALL_TEMP_FILES)
    ignored = []
    monkeypatch.setattr(sys, "unraisablehook", ignored.append)
    with pytest.raises(OSError):
        xlsx.write(_document(1), tmp_path)
    gc.collect()
    assert not ignored
    assert set(ALL_TEMP_FILES) == baseline
