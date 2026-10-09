"""Destination and selection adapters retain complete local group companions."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from seohead.reports.bi import _OutputBudget
from seohead.reports.bi_destinations import (
    BIDestinationCommitUncertain,
    BIDestinationError,
    _manifest,
    apply_with_client,
    bigquery_plan,
    destination_preview,
    export_bi_xlsx,
    filter_package,
    sheets_plan,
)
from seohead.reports.bi_index import (
    GroupIndex,
    verify_group_member_references,
    write_group_members_companion,
)
from tests.test_google_sheets import SheetsDouble, _client


@pytest.fixture
def package(tmp_path):
    source = Path(__file__).resolve().parents[1] / "docs/examples/reporting-pack/worksheets"
    package = tmp_path / "package"
    shutil.copytree(source, package)
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["run"]["audit_sha256"] = "a" * 64
    urls = [f"https://example.test/member-{number}" for number in range(30)]
    with sqlite3.connect(":memory:") as con:
        index = GroupIndex(con, [{"group_id": "complete-group", "count": 30, "urls": urls}])
        companion = write_group_members_companion(
            index,
            package,
            run_id=manifest["run"]["run_id"],
            source_audit_sha256="a" * 64,
            max_rows_per_file=7,
            max_bytes_per_file=1_000_000,
            budget=_OutputBudget(10_000_000),
        )
        reference = index.get("complete-group")["urls"]
    dataset = manifest["datasets"]["findings"]
    part = dataset["partitions"][0]
    path = package / part["path"]
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows[:2]:
        row.update(
            group_id="complete-group", group_url_count="30", group_urls_json=json.dumps(reference)
        )
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    dataset["bytes"] += path.stat().st_size - part["bytes"]
    part.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    manifest["group_members"] = companion
    (package / "manifest.json").write_text(json.dumps(manifest))
    assert verify_group_member_references(package, manifest)["members"] == 30
    return package


def _companion_files(package):
    manifest = json.loads((package / "manifest.json").read_text())
    return {
        part["path"]: (package / part["path"]).read_bytes()
        for kind in ("groups", "members")
        for part in manifest["group_members"][kind]["partitions"]
    }


def _assert_local_only(result, companion):
    refs = result["external_companion_refs"]
    assert len(refs) == 1
    assert refs[0]["publication"] == "local_only" and refs[0]["published"] is False
    assert (refs[0]["groups"], refs[0]["members"]) == (1, 30)
    assert refs[0]["sha256"] == companion["sha256"]
    assert refs[0]["source_audit_sha256"] == companion["source_audit_sha256"]


@pytest.mark.parametrize("columns", [None, ["finding_id", "url"]])
def test_filtered_findings_keep_the_complete_hashed_companion_and_source_binding(
    package, tmp_path, columns
):
    manifest = json.loads((package / "manifest.json").read_text())
    selected = tmp_path / "selected"
    result = filter_package(
        package,
        dataset="findings",
        out_dir=selected,
        where={"group_id": ["complete-group"]},
        columns=columns,
    )
    assert result["row_count"] == 2
    assert _companion_files(selected) == _companion_files(package)
    assert result["group_members"] == manifest["group_members"]
    root, normalized = _manifest(selected)
    assert normalized["run"] == manifest["run"]
    assert verify_group_member_references(root, normalized)["members"] == 30
    _assert_local_only(result, manifest["group_members"])
    _assert_local_only(sheets_plan(selected), manifest["group_members"])
    twice = tmp_path / "twice"
    filter_package(selected, dataset="findings", out_dir=twice)
    assert _companion_files(twice) == _companion_files(package)
    assert sheets_plan(twice)["state"] == "ready"


@pytest.mark.parametrize(
    "corruption", ["missing_descriptor", "missing_file", "corrupt_file", "wrong_source"]
)
def test_invalid_companions_fail_before_authentication_or_remote_write(package, corruption):
    path = package / "manifest.json"
    manifest = json.loads(path.read_text())
    companion = manifest["group_members"]
    if corruption == "missing_descriptor":
        manifest.pop("group_members")
    elif corruption == "wrong_source":
        manifest["run"]["audit_sha256"] = "b" * 64
    else:
        member = package / companion["members"]["partitions"][0]["path"]
        if corruption == "missing_file":
            member.unlink()
        else:
            member.write_bytes(member.read_bytes() + b"\n")
    path.write_text(json.dumps(manifest))

    class NeverAuthorized:
        def authorize_target(self, _target):
            pytest.fail("invalid source must fail before target authorization")

    with pytest.raises(BIDestinationError, match="companion"):
        apply_with_client(
            package, target="synthetic", operation="replace", client=NeverAuthorized(), apply=True
        )


def test_google_publication_discloses_local_only_members_and_uploads_only_main_datasets(
    package, tmp_path, monkeypatch
):
    manifest = json.loads((package / "manifest.json").read_text())
    companion = manifest["group_members"]
    mapping = {
        name: {"worksheet_id": index, "worksheet_title": name}
        for index, name in enumerate(manifest["datasets"])
    }
    config = tmp_path / "destinations.json"
    config.write_text(
        json.dumps(
            {
                "sheets": {
                    "targets": {
                        "synthetic": {
                            "enabled": True,
                            "kind": "google_sheets_service_account",
                            "spreadsheet_id": "sheet-id",
                            "worksheets": mapping,
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SEOHEAD_BI_DESTINATIONS_FILE", str(config))
    preview = destination_preview(
        package, target="synthetic", destination="sheets", operation="replace"
    )
    _assert_local_only(preview, companion)
    assert set(preview["datasets"]) == set(manifest["datasets"])
    _assert_local_only(bigquery_plan(package, dataset="synthetic"), companion)
    remote = SheetsDouble(mapping)
    client = _client(mapping, remote)
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert result["state"] == "committed"
    _assert_local_only(result, companion)
    assert set(result["rows"]) == set(remote.values) == set(manifest["datasets"])
    assert not any(
        "group-members" in str(request["body"]) and "addSheet" in str(request["body"])
        for request in remote.requests
    )
    calls = len(remote.requests)
    replay = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    _assert_local_only(replay, companion)
    assert len(remote.requests) == calls


def test_pending_publication_also_discloses_unpublished_companion(package):
    companion = json.loads((package / "manifest.json").read_text())["group_members"]

    class Pending:
        def authorize_target(self, _target):
            return True

        def begin(self, **_kwargs):
            return {}

        def write(self, *_args):
            raise BIDestinationCommitUncertain("synthetic lost response")

    result = apply_with_client(
        package, target="synthetic", operation="replace", client=Pending(), apply=True
    )
    assert result["state"] == "reconciliation_required"
    _assert_local_only(result, companion)


def test_workbook_index_keeps_the_explicit_local_companion_reference(package, tmp_path):
    companion = json.loads((package / "manifest.json").read_text())["group_members"]
    result = export_bi_xlsx(package, dataset="findings", out=tmp_path / "findings.xlsx")
    _assert_local_only(result, companion)
    _assert_local_only(json.loads(Path(result["index"]).read_text()), companion)


def test_companion_copy_respects_the_combined_filter_partition_budget(
    package, tmp_path, monkeypatch
):
    from seohead.reports import bi

    monkeypatch.setattr(bi, "MAX_OUTPUT_PARTITIONS", 5)
    destination = tmp_path / "selected"
    with pytest.raises(BIDestinationError, match="partition"):
        filter_package(package, dataset="findings", out_dir=destination)
    assert not destination.exists()
    assert _companion_files(package)
