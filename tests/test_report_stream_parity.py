"""Identical source facts from retained collections and materialized audits."""

from __future__ import annotations

from copy import deepcopy

import pytest

from seohead.reports import build_report
from seohead.storage.audit_v2 import AuditV2Reader, write_audit_v2
from tests.test_scan_artifact_office import frozen_office_clock as frozen_office_clock
from tests.test_scan_audit_v2 import _scan


@pytest.mark.parametrize("fmt", ["md", "csv", "xlsx", "docx"])
def test_stream_preserves_scope_suppression_and_trailing_notes(
    tmp_path, monkeypatch, frozen_office_clock, fmt
):
    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    issue = {
        "id": "one",
        "check": "TITLE_MISSING",
        "severity": "warning",
        "target_url": "https://example.test/page",
        "message": "Saved title observation.",
        "occurrences_count": 1,
        "locations": [{"source_url": "https://example.test/", "link_path": "/html/body/main/a"}],
    }
    suppressed = {
        **issue,
        "id": "excluded",
        "target_url": "https://example.test/excluded",
        "suppression": {
            "rule_id": "approved",
            "reason": "Intentional sample",
            "pattern": "*/excluded",
        },
    }
    audit = {
        "schema_version": "2.0",
        "run": {
            "collector": "seohead.crawl",
            "generated_at": "2026-10-06T00:00:00Z",
            "crawl_partial": True,
            "crawl_finish_reason": "page budget",
            "checks_disabled": [{"id": "TITLE_SHORT", "reason": "Operator choice"}],
            "checks_skipped": [{"id": "H1_MISSING", "reason": "Saved input unavailable"}],
            "finding_exclusion_policy": [
                {"id": "approved", "pattern": "*/excluded", "reason": "Intentional sample"}
            ],
        },
        "summary": {
            "totals": {"urls_crawled": 1, "issues_total": 1},
            "by_severity": {"warning": 1},
            "health_score_basis": "Saved coverage basis remains visible.",
            "health_score_scope": "One retained page only.",
            "finding_exclusions": {"suppressed_total": 1, "rules_configured": 1},
            "evidence_contract": {
                "scan_identity_state": "unavailable",
                "scan_identity_reason": "Not retained",
            },
        },
        "issues": [issue],
        "groups": [],
        "pages": [
            {"url": "https://example.test/page", "status_code": 200, "metrics": {"title": "Page"}}
        ],
        "suppressed_issues": [suppressed],
    }
    header = deepcopy(audit)
    collections = {f"/{key}": header.pop(key) for key in ("issues", "pages", "suppressed_issues")}
    header.update({key[1:]: [] for key in collections})
    write_audit_v2(scan, header, collections, binding)
    monkeypatch.setattr(
        AuditV2Reader, "materialize_legacy", lambda *_args, **_kwargs: pytest.fail("materialized")
    )
    streamed = tmp_path / f"stream.{fmt}"
    materialized = tmp_path / f"materialized.{fmt}"
    result = build_report(scan, fmt, str(streamed))
    assert result["ok"], result
    result = build_report(audit, fmt, str(materialized))
    assert result["ok"], result
    assert streamed.read_bytes() == materialized.read_bytes()
    if fmt == "csv":
        for suffix in (".pages.csv", ".scope.csv"):
            assert (
                streamed.with_suffix(suffix).read_bytes()
                == materialized.with_suffix(suffix).read_bytes()
            )
    if fmt == "md":
        text = streamed.read_text()
        for saved in (
            "Saved coverage basis remains visible.",
            "One retained page only.",
            "Saved input unavailable",
            "Operator choice",
            "Intentional sample",
            "https://example.test/excluded",
            "/html/body/main/a",
        ):
            assert saved in text


def test_csv_suppression_export_does_not_retain_rendered_rows():
    import tracemalloc
    from collections.abc import Sequence

    from seohead.reports.csvfile import _scope_rows

    class Suppressed(Sequence):
        def __len__(self):
            return 5000

        def __getitem__(self, index):
            if not 0 <= index < len(self):
                raise IndexError(index)
            return {
                "id": f"suppressed-{index}",
                "check": "TITLE_MISSING",
                "severity": "warning",
                "target_url": f"https://example.test/{index}",
                "suppression": {"rule_id": "approved", "reason": f"{index}:" + "x" * 2000},
            }

    # Warm the check registry/import path outside the allocation measurement.
    list(_scope_rows({}, []))
    tracemalloc.start()
    try:
        count = 0
        last = None
        for row in _scope_rows({}, Suppressed()):
            if row[0] == "suppressed finding":
                count += 1
                last = row
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert count == 5000
    assert last[1] == "suppressed-4999"
    assert "4999:" in last[3]
    # Retaining the formatted reasons alone would consume over 10 MB.
    assert peak < 2 * 1024 * 1024
