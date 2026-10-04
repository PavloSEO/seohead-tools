from seohead.crawl.external import ExternalCheck
from seohead.storage.native_scan import NativeScan
from tests.test_scan_native import _metadata


def test_native_external_checks_are_typed_ordered_and_idempotent(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(
        path,
        format_version="scan.v2",
        **_metadata(**{"storage.format_version": "scan.v2"}),
    ) as scan:
        check = ExternalCheck(
            url="https://outside.example.test/", outcome="blocked", reason="private_target"
        )
        scan.record_external_check(0, check.as_dict())
        scan.record_external_check(0, check.as_dict())
        summary = {
            "enabled": True,
            "ran": True,
            "policy": {"max_targets": 1},
            "robots_policy": "not_evaluated",
            "destinations": 1,
            "records_carried": 0,
            "resumed": 0,
            "fetched": 0,
            "failed": 0,
            "blocked": 1,
            "skipped": 0,
            "skipped_reasons": {},
            "hosts_contacted": 0,
            "requests": 0,
            "finish_reason": "finished",
        }
        scan.record_external_checks_summary(summary)
        assert list(scan.external_checks()) == [check.as_dict()]
        assert scan.con.execute("SELECT payload_json FROM external_check_summary").fetchone()[0]


def test_v1_external_checks_remain_unavailable(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        assert list(scan.external_checks()) == []
