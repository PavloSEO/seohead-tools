"""A complete source-bound group companion replaces repeated large JSON cells."""

from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from seohead.reports.bi import BIExportError, _OutputBudget, export_bi
from seohead.reports.bi_index import (
    GroupIndex,
    copy_group_members_companion,
    verify_group_member_references,
    verify_group_members_companion,
    write_group_members_companion,
)
from tests.test_audit_v2_ledger_groups import _population_scan


def test_large_bi_groups_have_complete_companions_and_explicit_source_bound_references(tmp_path):
    scan, urls = _population_scan(tmp_path / "scan.sqlite", 200)
    out = tmp_path / "bi"
    export_bi(scan=scan, out_dir=out, max_rows_per_file=31)
    manifest = json.loads((out / "manifest.json").read_text())
    assert len(manifest["datasets"]) == 6
    companion = manifest["group_members"]
    assert verify_group_members_companion(out, companion)["members"] == len(urls)
    assert companion["groups"]["row_count"] == 1
    assert companion["members"]["row_count"] == len(urls)
    assert verify_group_member_references(out, manifest)["members"] == len(urls)
    without_companion = {key: value for key, value in manifest.items() if key != "group_members"}
    with pytest.raises(BIExportError, match="lacks its complete companion"):
        verify_group_member_references(out, without_companion)
    wrong_source = {**manifest, "run": {**manifest["run"], "audit_sha256": "b" * 64}}
    with pytest.raises(BIExportError, match="bind"):
        verify_group_member_references(out, wrong_source)
    from seohead.storage.audit_v2 import AuditV2Reader

    with AuditV2Reader(scan) as reader:
        assert companion["source_audit_sha256"] == reader.sha256
    import csv

    for part in manifest["datasets"]["findings"]["partitions"]:
        with (out / part["path"]).open(newline="") as stream:
            for row in csv.DictReader(stream):
                reference = json.loads(row["group_urls_json"])
                assert reference["schema"] == "bi-group-members.v1"
                assert reference["member_count"] == 200
                assert reference["companion_sha256"] == companion["sha256"]
                assert reference["source_audit_sha256"] == companion["source_audit_sha256"]
                assert row["group_url_count"] == "200"
    copied = tmp_path / "copy"
    copied.mkdir()
    copy_group_members_companion(out, copied, companion, _OutputBudget(10_000_000))
    assert verify_group_members_companion(copied, companion) == verify_group_members_companion(
        out, companion
    )
    member_file = copied / companion["members"]["partitions"][-1]["path"]
    member_file.write_bytes(member_file.read_bytes()[:-1])
    with pytest.raises((BIExportError, ValueError), match=r"digest|counts|checksum"):
        verify_group_members_companion(copied, companion)


def test_bi_index_retains_members_once_and_keeps_only_a_fixed_inline_population(tmp_path):
    sizes = []
    for count in (100, 200):
        with sqlite3.connect(":memory:") as con:
            urls = [f"https://example.test/{index:06d}" for index in range(count)]
            index = GroupIndex(con, [{"group_id": "GROUP", "count": count, "urls": urls}])
            directory = tmp_path / str(count)
            directory.mkdir()
            companion = write_group_members_companion(
                index,
                directory,
                run_id="test-run",
                source_audit_sha256="a" * 64,
                max_rows_per_file=50,
                max_bytes_per_file=1_000_000,
                budget=_OutputBudget(10_000_000),
            )
            for _ in range(count):
                assert isinstance(index.get("GROUP")["urls"], dict)
            assert companion["members"]["row_count"] == count
            sizes.append(
                con.execute("SELECT SUM(length(value_json)) FROM group_members").fetchone()[0]
            )
    assert sizes[1] == sizes[0] * 2


@pytest.mark.parametrize(
    "field,value", [("group_id", "OTHER"), ("run_id", "OTHER"), ("group_url_count", "29")]
)
def test_finding_reference_must_agree_with_its_row_identity_and_count(tmp_path, field, value):
    scan, _urls = _population_scan(tmp_path / "scan.sqlite", 30)
    directory = tmp_path / "bi"
    export_bi(scan=scan, out_dir=directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    part = directory / manifest["datasets"]["findings"]["partitions"][0]["path"]
    with part.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    rows[0][field] = value
    with part.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(BIExportError, match="row differs"):
        verify_group_member_references(directory, manifest)
