"""Large logical groups retain each ordered member without a whole-row JSON copy."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence

import pytest

from seohead.storage.audit_v2 import (
    AuditGroupMembers,
    AuditV2Error,
    AuditV2Reader,
    audit_v2_path,
    write_audit_v2,
)
from tests.test_scan_audit_v2 import _scan


class _URLs(Sequence):
    def __init__(self, count=25_000):
        self.count = count

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        return f"https://example.test/{index:06d}/" + "x" * 400


def test_large_group_streams_members_and_complete_json_above_atomic_row_limit(
    tmp_path, monkeypatch
):
    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    urls = _URLs()
    write_audit_v2(
        scan,
        {"groups": []},
        {"/groups": [{"group_id": "GROUP", "count": len(urls), "urls": urls}]},
        binding,
    )
    with AuditV2Reader(scan) as reader:
        assert reader.storage_version == 3
        group = reader.get_item("/groups", 0)
        assert isinstance(group["urls"], AuditGroupMembers)
        assert len(group["urls"]) == 25_000
        assert reader.group_members_page(0, offset=24_997)["rows"] == [
            urls[i] for i in range(24_997, 25_000)
        ]
        assert reader.con.execute("SELECT MAX(length(value_json)) FROM items").fetchone()[0] < 1024
        assert reader.con.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 25_000
        with pytest.raises(AuditV2Error, match="legacy JSON export exceeds"):
            reader.materialize_legacy(max_bytes=1024)

        def no_length_hint(_self):
            raise AssertionError("a streaming export tried to materialize all group members")

        monkeypatch.setattr(AuditGroupMembers, "__len__", no_length_hint)
        actual, expected = hashlib.sha256(), hashlib.sha256()
        size = 0
        for chunk in reader.document_chunks():
            actual.update(chunk.encode())
            size += len(chunk.encode())
        expected.update(b'{"groups":[{"group_id":"GROUP","count":25000,"urls":[')
        for index, url in enumerate(urls):
            expected.update((("," if index else "") + json.dumps(url)).encode())
        expected.update(b"]}]}")
        assert actual.digest() == expected.digest()
        assert size > 8 * 1024 * 1024


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM group_members WHERE ordinal=1",
        "UPDATE group_members SET ordinal=9 WHERE ordinal=1",
        "UPDATE group_members SET value_json='\"changed\"' WHERE ordinal=1",
        'UPDATE items SET value_json=\'{"urls":{"$audit_v2_group_members":1,"count":2}}\'',
    ],
)
def test_group_member_corruption_refuses_before_exposing_a_row(tmp_path, mutation):
    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    write_audit_v2(scan, {"groups": []}, {"/groups": [{"urls": ["a", "b"]}]}, binding)
    with sqlite3.connect(audit_v2_path(scan)) as con:
        con.execute(mutation)
    with pytest.raises(AuditV2Error):
        AuditV2Reader(scan)


def test_group_row_metadata_is_literal_and_supplied_members_marker_is_rejected(tmp_path):
    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    group = {"urls": ["a"], "metadata": {"$audit_v2_collection": "/issues", "count": 1}}
    write_audit_v2(
        scan, {"groups": [], "issues": []}, {"/groups": [group], "/issues": [{"n": 1}]}, binding
    )
    with AuditV2Reader(scan) as reader:
        assert reader.materialize_legacy()["groups"] == [group]
    prior = audit_v2_path(scan).read_bytes()
    with pytest.raises(AuditV2Error, match="reserved"):
        write_audit_v2(
            scan,
            {"groups": []},
            {"/groups": [{"urls": {"$audit_v2_group_members": 0, "count": 0}}]},
            binding,
        )
    assert audit_v2_path(scan).read_bytes() == prior


def test_group_slices_never_silently_shorten_on_a_byte_boundary(tmp_path):
    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    write_audit_v2(
        scan, {"groups": []}, {"/groups": [{"urls": ["x" * 600_000, "y" * 600_000]}]}, binding
    )
    with AuditV2Reader(scan) as reader:
        page = reader.group_members_page(0, limit=2)
        assert page["returned"] == 1 and page["has_more"]
        with pytest.raises(AuditV2Error, match="byte bound"):
            reader.get_item("/groups", 0)["urls"][:2]


def test_fractional_member_ordinal_refuses_even_with_a_valid_recomputed_digest(tmp_path):
    from seohead.storage.audit_v2 import _digest_item, _digest_start

    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    write_audit_v2(scan, {"groups": []}, {"/groups": [{"urls": ["a", "b", "c"]}]}, binding)
    with sqlite3.connect(audit_v2_path(scan)) as con:
        con.execute("UPDATE group_members SET ordinal=0.5 WHERE ordinal=1")
        meta = con.execute("SELECT binding_json,header_json FROM audit_meta").fetchone()
        digest = _digest_start(*meta, version=3)
        for pointer, ordinal, raw in con.execute(
            "SELECT pointer,ordinal,value_json FROM items ORDER BY pointer,ordinal"
        ):
            _digest_item(digest, pointer, ordinal, raw)
        for group, ordinal, raw in con.execute(
            "SELECT group_ordinal,ordinal,value_json FROM group_members ORDER BY group_ordinal,ordinal"
        ):
            _digest_item(digest, f"@group-members/{group}", ordinal, raw)
        con.execute("UPDATE audit_meta SET sha256=?", (digest.hexdigest(),))
    with pytest.raises(AuditV2Error, match="ordinals must be integers"):
        AuditV2Reader(scan)


def test_storage_revision_two_keeps_its_original_digest_and_complete_group_values(tmp_path):
    from seohead.storage.audit_v2 import APPLICATION_ID

    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    group = {
        "group_id": "OLD",
        "count": 2,
        "urls": ["https://example.test/й", "https://example.test/a/"],
    }
    header = {"groups": {"$audit_v2_collection": "/groups", "count": 1}}

    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    binding_json, header_json, row_json = encode(binding), encode(header), encode(group)
    digest = hashlib.sha256()
    for text in ("audit.v2", binding_json, header_json, "/groups", "0", row_json):
        raw = text.encode()
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    with sqlite3.connect(audit_v2_path(scan)) as con:
        con.executescript("""
CREATE TABLE audit_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_version TEXT NOT NULL CHECK (format_version = 'audit.v2'),
    binding_json TEXT NOT NULL,
    header_json TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE collections (
    pointer TEXT PRIMARY KEY,
    item_count INTEGER NOT NULL CHECK (item_count >= 0)
);
CREATE TABLE items (
    pointer TEXT NOT NULL REFERENCES collections(pointer),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    value_json TEXT NOT NULL,
    PRIMARY KEY (pointer, ordinal)
);
""")
        con.execute(f"PRAGMA application_id={APPLICATION_ID}")
        con.execute("PRAGMA user_version=2")
        con.execute(
            "INSERT INTO audit_meta VALUES(1,'audit.v2',?,?,?)",
            (binding_json, header_json, digest.hexdigest()),
        )
        con.execute("INSERT INTO collections VALUES('/groups',1)")
        con.execute("INSERT INTO items VALUES('/groups',0,?)", (row_json,))
    before = audit_v2_path(scan).read_bytes()
    with AuditV2Reader(scan) as reader:
        assert reader.storage_version == 2
        assert reader.sha256 == digest.hexdigest()
        assert reader.materialize_legacy() == {"groups": [group]}
        assert reader.group_members_page(0, offset=1)["rows"] == group["urls"][1:]
    assert audit_v2_path(scan).read_bytes() == before


def test_disk_group_producer_keeps_streamed_and_explicit_legacy_shapes(tmp_path):
    from seohead.sf.core.context import _DiskGroups
    from seohead.sf.core.models import AuditResult, Group

    groups = _DiskGroups()
    try:
        groups.append(Group("GROUP", "TITLE_DUPLICATE", "shared", ["a", "b"], 2))
        result = AuditResult(run={}, summary={}, groups=groups)
        _header, parts = result.audit_v2_parts()
        row = next(iter(parts["/groups"]))
        assert isinstance(row["urls"], Sequence) and not isinstance(row["urls"], list)
        assert list(row["urls"]) == ["a", "b"]
        assert result.to_json()["groups"][0]["urls"] == ["a", "b"]
    finally:
        groups.close()
