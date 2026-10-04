from __future__ import annotations

from seohead.reports.bi import export_bi
from seohead.reports.bi_destinations import bigquery_plan, sheets_plan


def _audit():
    return {
        "schema": "seohead.site-audit/1",
        "url": "https://example.test/",
        "site": {},
        "pages": [{"url": "https://example.test/", "status_code": 200}],
        "findings": [],
        "summary": {"pages_checked": 1, "findings_total": 0, "tools_run": [], "tools_failed": []},
    }


def test_offline_destination_plans_reconcile_the_complete_local_package(tmp_path):
    package = tmp_path / "package"
    export_bi(audit=_audit(), out_dir=package)
    sheets = sheets_plan(package)
    assert sheets["network"] is False and sheets["apply"] is False
    assert sheets["state"] == "ready"
    assert {row["worksheet"] for row in sheets["worksheets"]} >= {"pages", "cohorts"}
    bigquery = bigquery_plan(package, dataset="synthetic_reporting")
    assert bigquery["billing_required"] is True
    assert all(row["table"].startswith("seohead_") for row in bigquery["tables"])
    assert sheets_plan(package, max_cells=1)["state"] == "unavailable"
