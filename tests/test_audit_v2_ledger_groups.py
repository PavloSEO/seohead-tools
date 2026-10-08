"""Exact audit group scope and failure cleanup at the ledger consumer boundary."""

from __future__ import annotations

import sqlite3

import pytest

from seohead.storage.audit_v2 import AuditV2Reader
from seohead.storage.ledger import LedgerError, ingest_scan, open_ledger, remediation_summary
from seohead.storage.native_scan import NativeScan
from tests.test_remediation_ledger import A, B, _document, _issue, _ledger
from tests.test_remediation_ledger import _scan as _legacy_scan
from tests.test_scan_native import _metadata


def _scan(path, groups, *, streamed=True, schema_version="2.0"):
    issues = [_issue("ISSUE-000001", "TITLE_DUPLICATE", target=A, group_id="GROUP")]
    groups = [{"check": "TITLE_DUPLICATE", **group} for group in groups]
    if not streamed:
        return _legacy_scan(path, issues=issues, page_urls=[A, B], groups=groups)
    meta = _metadata()
    with NativeScan.create(path, **meta) as scan:
        document = _document(
            scan,
            meta,
            issues=issues,
            page_urls=[A, B],
            groups=groups,
            generated_at="2026-10-08T00:00:00Z",
            partial=False,
        )
        document["schema_version"] = schema_version
        header, collections = scan._audit_v2_parts(document)
        scan.save_audit_v2(header, collections)
        scan.finish_without_audit("synthetic audit.v2")
    return path


@pytest.mark.parametrize(
    "streamed,groups",
    [
        (False, []),
        (False, [{"group_id": "GROUP", "count": 2, "urls": [A]}]),
        (True, []),
        (True, [{"group_id": "GROUP", "count": 2, "urls": [A]}]),
        (True, [{"group_id": "GROUP", "count": 1}]),
        (True, [{"group_id": "GROUP", "count": 1, "urls": [None]}]),
        (True, [{"group_id": "GROUP", "count": 2, "urls": [A, A]}]),
    ],
)
def test_incomplete_group_scope_is_persisted_and_reingestion_repairs_prior_state(
    tmp_path, streamed, groups
):
    ledger = _ledger(tmp_path)
    scan = _scan(tmp_path / "scan.sqlite", groups, streamed=streamed)
    result = ingest_scan(ledger, scan)
    assert result["recorded"]["group_memberships_state"] == "partial"
    with open_ledger(ledger) as con:
        assert (
            con.execute("SELECT group_memberships_state FROM source_scan").fetchone()[0]
            == "partial"
        )
    assert remediation_summary(ledger)["scope"]["state"] != "complete"
    replay = ingest_scan(ledger, scan)
    assert replay["already_recorded"]
    assert replay["ledger_revision"] == result["ledger_revision"]

    # Older readers persisted their provisional state before resolving groups.
    with sqlite3.connect(ledger) as con:
        con.execute("UPDATE source_scan SET group_memberships_state='complete'")
    repaired = ingest_scan(ledger, scan)
    assert repaired["ledger_revision"] == result["ledger_revision"] + 1
    assert repaired["recorded"]["findings"] == repaired["recorded"]["observations"] == 0
    assert repaired["recorded"]["group_memberships_state"] == "partial"
    assert ingest_scan(ledger, scan)["already_recorded"]


@pytest.mark.parametrize("failure", ["duplicate_group", "schema", "missing_ledger"])
def test_failed_streamed_ingest_closes_reader_and_removes_group_spool(
    tmp_path, monkeypatch, failure
):
    ledger = _ledger(tmp_path)
    group = {"group_id": "GROUP", "count": 2, "urls": [A, B]}
    scan = _scan(
        tmp_path / "scan.sqlite",
        [group, group] if failure == "duplicate_group" else [group],
        schema_version="unsupported" if failure == "schema" else "2.0",
    )
    if failure == "missing_ledger":
        ledger = tmp_path / "missing.sqlite"
    readers, closed = [], []
    original_init, original_close = AuditV2Reader.__init__, AuditV2Reader.close

    def initialize(reader, *args, **kwargs):
        original_init(reader, *args, **kwargs)
        readers.append(reader)

    def close(reader):
        closed.append(reader)
        original_close(reader)

    monkeypatch.setattr(AuditV2Reader, "__init__", initialize)
    monkeypatch.setattr(AuditV2Reader, "close", close)
    with pytest.raises(LedgerError):
        ingest_scan(ledger, scan)
    assert readers and readers == closed
    for reader in readers:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            reader.con.execute("SELECT 1")
    assert not list(tmp_path.glob(".ledger-groups-*"))


def _population_scan(path, count):
    meta = _metadata()
    urls = [f"https://example.test/{index:06d}" for index in range(count)]
    group = {"group_id": "GROUP", "check": "TITLE_DUPLICATE", "count": count, "urls": urls}
    with NativeScan.create(path, **meta) as scan:
        document = _document(
            scan,
            meta,
            issues=[
                _issue(f"ISSUE-{index:06d}", "TITLE_DUPLICATE", target=url, group_id="GROUP")
                for index, url in enumerate(urls)
            ],
            page_urls=urls,
            groups=[group],
            generated_at="2026-10-08T00:00:00Z",
            partial=False,
        )
        scan.save_audit_v2(*scan._audit_v2_parts(document))
        scan.finish_without_audit("synthetic group population")
    return path, urls


def test_ledger_member_storage_grows_linearly_and_pages_have_a_public_continuation(tmp_path):
    from seohead.servers import handlers
    from seohead.storage.ledger import ledger_summary, read_cases, read_group_members
    from tests.test_remediation_interfaces import _cli

    sizes = []
    for count in (100, 200):
        directory = tmp_path / str(count)
        directory.mkdir()
        ledger = _ledger(directory)
        scan, urls = _population_scan(directory / "scan.sqlite", count)
        result = ingest_scan(ledger, scan)
        assert result["recorded"]["findings"] == count
        assert result["recorded"]["group_memberships"] == count
        counts = ledger_summary(ledger)["counts"]
        assert counts["source_group"] == 1
        assert counts["source_group_member"] == count
        with open_ledger(ledger) as con:
            sizes.append(
                con.execute("SELECT SUM(length(value_json)) FROM source_group_member").fetchone()[0]
            )
            assert (
                con.execute("SELECT SUM(length(group_urls_json)) FROM finding_group").fetchone()[0]
                == count * 2
            )
        preview = read_cases(ledger, limit=1)["findings"][0]["group_memberships"][0]
        assert preview["group_urls"] == urls[:25]
        assert preview["group_members_page"]["total"] == count
        assert preview["group_members_page"]["next_offset"] == 25
        selection = dict(
            source_scan_id=result["source_scan_id"], group_ref="GROUP", offset=count - 3, limit=3
        )
        page = read_group_members(ledger, **selection)
        assert page["rows"] == urls[-3:]
        assert not page["has_more"]
        assert handlers.remediation_cases(str(ledger), **selection) == page
        assert (
            _cli(
                "remediation-cases",
                "--ledger",
                str(ledger),
                "--source-scan-id",
                str(result["source_scan_id"]),
                "--group-ref",
                "GROUP",
                "--offset",
                str(count - 3),
                "--limit",
                "3",
            )
            == page
        )
        assert ingest_scan(ledger, scan)["already_recorded"]
    assert sizes[1] == 2 * sizes[0]


@pytest.mark.parametrize("conflict", [False, True])
def test_v4_group_migration_preserves_identity_or_rolls_back_inconsistent_copies(
    tmp_path, conflict
):
    import hashlib
    import json

    ledger = _ledger(tmp_path)
    scan, urls = _population_scan(tmp_path / "scan.sqlite", 2)
    ingest_scan(ledger, scan)
    with sqlite3.connect(ledger) as con:
        identity = con.execute("SELECT ledger_uuid,ledger_revision FROM ledger").fetchone()
        con.execute("UPDATE finding_group SET group_urls_json=?", (json.dumps(urls),))
        if conflict:
            con.execute(
                "UPDATE finding_group SET group_urls_json='[]' WHERE finding_id=(SELECT MIN(finding_id) FROM finding_group)"
            )
        con.execute("DROP TABLE source_group_member")
        con.execute("DROP TABLE source_group")
        con.execute("PRAGMA user_version=4")
    original = hashlib.sha256(ledger.read_bytes()).hexdigest()
    with pytest.raises(LedgerError, match="user_version 4"):
        open_ledger(ledger)
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == original
    if conflict:
        with pytest.raises(LedgerError, match="conflicting group"):
            open_ledger(ledger, write=True)
        assert hashlib.sha256(ledger.read_bytes()).hexdigest() == original
    else:
        with open_ledger(ledger, write=True) as con:
            assert (
                tuple(con.execute("SELECT ledger_uuid,ledger_revision FROM ledger").fetchone())
                == identity
            )
            assert con.execute("SELECT COUNT(*) FROM source_group_member").fetchone()[0] == 2
        assert ingest_scan(ledger, scan)["already_recorded"]
