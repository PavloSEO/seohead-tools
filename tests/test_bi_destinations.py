from __future__ import annotations

import pytest

from seohead.reports.bi import export_bi
from seohead.reports.bi_destinations import (
    BIDestinationError,
    apply_with_client,
    bigquery_plan,
    filter_package,
    sheets_plan,
)
from seohead.servers import handlers


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


def test_injected_destination_client_is_explicit_transactional_and_streamed(tmp_path):
    package = tmp_path / "package"
    export_bi(audit=_audit(), out_dir=package)

    class Client:
        def __init__(self):
            self.calls = []

        def authorize_target(self, target):
            self.calls.append(("authorize", target))
            return True

        def begin(self, **kwargs):
            self.calls.append(("begin", kwargs))
            return "tx"

        def write(self, transaction, dataset, rows):
            self.calls.append(("write", transaction, dataset, len(rows)))

        def commit(self, transaction):
            self.calls.append(("commit", transaction))

    client = Client()
    with pytest.raises(BIDestinationError, match="apply=True"):
        apply_with_client(package, target="synthetic", operation="replace", client=client)
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert result["rows"]["pages"] == 1
    assert client.calls[0] == ("authorize", "synthetic")
    assert client.calls[-1] == ("commit", "tx")


def test_filtered_bi_export_is_exact_and_partitioned(tmp_path):
    package = tmp_path / "package"
    export_bi(audit=_audit(), out_dir=package)
    filtered = filter_package(
        package,
        dataset="cohorts",
        out_dir=tmp_path / "filtered",
        where={"cohort_id": ["observed_status", "crawl_relative_depth"]},
        columns=["run_id", "cohort_id", "value_label", "state"],
        max_rows_per_file=1,
    )
    assert filtered["row_count"] == 2
    assert [part["rows"] for part in filtered["partitions"]] == [1, 1]
    assert (tmp_path / "filtered" / "manifest.json").is_file()


def test_shared_handler_requires_injected_authorized_client(tmp_path):
    package = tmp_path / "package"
    export_bi(audit=_audit(), out_dir=package)
    with pytest.raises(ValueError, match="injected authorized client"):
        handlers.bi_destination_apply(
            package=str(package),
            target="synthetic",
            destination="sheets",
            operation="replace",
            apply=True,
        )

    class Client:
        def authorize_target(self, target):
            return target == "synthetic"

        def begin(self, **_kwargs):
            return "tx"

        def write(self, *_args):
            pass

        def commit(self, _transaction):
            pass

    result = handlers.bi_destination_apply(
        package=str(package),
        target="synthetic",
        destination="sheets",
        operation="replace",
        apply=True,
        client=Client(),
    )
    assert result["destination"] == "sheets" and result["rows"]["pages"] == 1
