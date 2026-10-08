"""Disposable disk-backed BI lookups; never modify retained source artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections.abc import Sequence
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


@contextmanager
def projection_index(parent: Path, max_bytes: int):
    """Bound SQLite's main file, cache and temporary work in a private directory."""
    from seohead.reports.bi import MIN_FREE_DISK_BYTES, BIExportError

    if shutil.disk_usage(parent).free < MIN_FREE_DISK_BYTES:
        raise BIExportError("insufficient free disk for the BI projection index")
    with TemporaryDirectory(prefix=".seohead-bi-index-", dir=parent) as temporary:
        con = sqlite3.connect(Path(temporary) / "index.sqlite")
        try:
            con.execute("PRAGMA page_size=4096")
            con.execute(f"PRAGMA max_page_count={max_bytes // 4096}")
            con.execute("PRAGMA cache_size=-2048")
            con.execute("PRAGMA temp_store=FILE")
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            yield con
        except sqlite3.Error as exc:
            raise BIExportError(
                "BI projection index exceeded its disk bound or became unavailable"
            ) from exc
        finally:
            con.close()


class GroupIndex:
    """Keep finding groups on disk and mark references while findings stream."""

    def __init__(self, con, groups):
        from seohead.reports.bi import MAX_CELL_BYTES, MAX_FINDINGS, BIExportError

        self.con = con
        self.source_count = 0
        self.reference = {}
        con.execute(
            "CREATE TABLE groups (id TEXT PRIMARY KEY, ordinal INTEGER, value TEXT, member_count INTEGER, members_sha256 TEXT DEFAULT '', linked INTEGER DEFAULT 0)"
        )
        con.execute(
            "CREATE TABLE group_members (group_id TEXT, ordinal INTEGER, value_json TEXT, PRIMARY KEY(group_id,ordinal))"
        )
        for group in groups:
            self.source_count += 1
            if self.source_count > MAX_FINDINGS:
                raise BIExportError("finding groups exceed the source row bound")
            if not isinstance(group, dict):
                raise BIExportError("finding group must be an object")
            group_id = group.get("group_id") or group.get("id")
            if not isinstance(group_id, str) or not group_id:
                raise BIExportError("finding group has no stable identity")
            urls = group.get("urls")
            if not isinstance(urls, Sequence) or isinstance(urls, (str, bytes)):
                raise BIExportError("finding group has no ordered member array")
            value = json.dumps(
                {key: value for key, value in group.items() if key != "urls"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(value.encode("utf-8")) > MAX_CELL_BYTES:
                raise BIExportError("one finding group exceeds the BI cell bound")
            try:
                con.execute(
                    "INSERT INTO groups(id,ordinal,value,member_count) VALUES (?,?,?,?)",
                    (group_id, self.source_count - 1, value, len(urls)),
                )
            except sqlite3.IntegrityError as exc:
                raise BIExportError(f"duplicate finding group id {group_id!r}") from exc
            digest = hashlib.sha256()
            for ordinal, url in enumerate(urls):
                member = json.dumps(url, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if len(member.encode("utf-8")) > MAX_CELL_BYTES:
                    raise BIExportError("one finding group member exceeds the BI cell bound")
                con.execute("INSERT INTO group_members VALUES(?,?,?)", (group_id, ordinal, member))
                encoded = member.encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
            con.execute(
                "UPDATE groups SET members_sha256=? WHERE id=?", (digest.hexdigest(), group_id)
            )

    def get(self, key):
        row = self.con.execute(
            "SELECT value,member_count,members_sha256 FROM groups WHERE id=?", (key,)
        ).fetchone()
        if row is None:
            return None
        self.con.execute("UPDATE groups SET linked=1 WHERE id=? AND linked=0", (key,))
        group = json.loads(row[0])
        group["member_count"] = row[1]
        group["urls"] = (
            [
                json.loads(member[0])
                for member in self.con.execute(
                    "SELECT value_json FROM group_members WHERE group_id=? ORDER BY ordinal", (key,)
                )
            ]
            if row[1] <= 25
            else {
                **self.reference,
                "schema": "bi-group-members.v1",
                "group_id": key,
                "member_count": row[1],
                "members_sha256": row[2],
            }
        )
        return group

    def companion_rows(self, run_id, kind):
        if kind == "groups":
            for row in self.con.execute(
                "SELECT id,ordinal,value,member_count,members_sha256 FROM groups ORDER BY ordinal"
            ):
                group = json.loads(row[2])
                yield {
                    "run_id": run_id,
                    "group_id": row[0],
                    "group_ordinal": row[1],
                    "member_count": row[3],
                    "declared_count": group.get("count"),
                    "metadata_json": group,
                    "members_sha256": row[4],
                }
        else:
            for row in self.con.execute(
                "SELECT m.group_id,m.ordinal,m.value_json FROM group_members m JOIN groups g ON g.id=m.group_id ORDER BY g.ordinal,m.ordinal"
            ):
                yield {
                    "run_id": run_id,
                    "group_id": row[0],
                    "member_ordinal": row[1],
                    "member_json": json.loads(row[2]),
                }

    def coverage(self):
        counts = self.con.execute("SELECT COUNT(*),COALESCE(SUM(linked),0) FROM groups").fetchone()
        return counts[0], counts[1]

    def unlinked(self):
        for row in self.con.execute("SELECT id FROM groups WHERE linked=0 ORDER BY id"):
            yield row[0]


def _group_fields(kind):
    from seohead.reports.bi import Field

    shared = (Field("run_id", "string", False), Field("group_id", "string", False))
    if kind == "groups":
        return (
            *shared,
            Field("group_ordinal", "integer", False),
            Field("member_count", "integer", False),
            Field("declared_count", "integer"),
            Field("metadata_json", "json", False),
            Field("members_sha256", "string", False),
        )
    return (*shared, Field("member_ordinal", "integer", False), Field("member_json", "json", False))


def _companion_digest(descriptor):
    return hashlib.sha256(
        json.dumps(
            {key: value for key, value in descriptor.items() if key != "sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def write_group_members_companion(
    index, directory, *, run_id, source_audit_sha256, max_rows_per_file, max_bytes_per_file, budget
):
    """Write every group and member once, separately from the six main datasets."""
    from seohead.reports.bi import _PartitionWriter, _schema_fields

    descriptor = {
        "format": "bi-group-members.v1",
        "run_id": run_id,
        "source_audit_sha256": source_audit_sha256,
        "inline_member_limit": 25,
        "publication": "local companion; not included in the six published datasets",
    }
    for kind in ("groups", "members"):
        fields = _group_fields(kind)
        writer = _PartitionWriter(
            directory,
            f"group-{kind}",
            fields,
            max_rows_per_file=max_rows_per_file,
            max_bytes_per_file=max_bytes_per_file,
            budget=budget,
        )
        try:
            for row in index.companion_rows(run_id, kind):
                writer.write(row)
            descriptor[kind] = {**writer.finish(), "fields": _schema_fields(fields)}
        finally:
            writer.close()
    descriptor["sha256"] = _companion_digest(descriptor)
    verify_group_members_companion(directory, descriptor)
    index.reference = {
        "run_id": run_id,
        "source_audit_sha256": source_audit_sha256,
        "companion_sha256": descriptor["sha256"],
    }
    return descriptor


def _companion_rows(root, descriptor, kind):
    from seohead.reports.bi import BIExportError

    for part in descriptor[kind]["partitions"]:
        with (root / part["path"]).open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if None in row or any(value is None for value in row.values()):
                    raise BIExportError("group companion has an invalid CSV row")
                yield row


@contextmanager
def _group_csv_limit():
    from seohead.reports.bi import MAX_CELL_BYTES

    previous = csv.field_size_limit(MAX_CELL_BYTES)
    try:
        yield
    finally:
        csv.field_size_limit(previous)


def _group_partition_stats(path, fields):
    from seohead.reports.bi import MAX_CELL_BYTES, BIExportError

    digest, size, count = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream)
            if next(rows) != fields:
                raise BIExportError("group companion CSV header differs")
            for row in rows:
                if len(row) != len(fields) or any(
                    len(cell.encode("utf-8")) > MAX_CELL_BYTES for cell in row
                ):
                    raise BIExportError("group companion row exceeds its cell or field bound")
                count += 1
    except (csv.Error, UnicodeError, StopIteration) as exc:
        raise BIExportError("group companion CSV is invalid") from exc
    return digest.hexdigest(), size, count


@_group_csv_limit()
def verify_group_members_companion(root, descriptor):
    """Validate ordered identities, exact counts, source binding and complete file hashes."""
    from seohead.reports.bi import (
        MAX_OUTPUT_PARTITIONS,
        MAX_PROJECTION_INDEX_BYTES,
        BIExportError,
        _format_cell,
        _schema_fields,
    )

    if descriptor is None:
        return None
    root = Path(root)
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("format") != "bi-group-members.v1"
        or descriptor.get("sha256") != _companion_digest(descriptor)
    ):
        raise BIExportError("group companion identity or manifest digest differs")
    if not isinstance(descriptor.get("run_id"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", str(descriptor.get("source_audit_sha256"))
    ):
        raise BIExportError("group companion lacks its source audit binding")
    files = set()
    for kind in ("groups", "members"):
        data = descriptor.get(kind)
        fields = _schema_fields(_group_fields(kind))
        if (
            not isinstance(data, dict)
            or data.get("fields") != fields
            or not isinstance(data.get("partitions"), list)
            or not 1 <= len(data["partitions"]) <= MAX_OUTPUT_PARTITIONS
        ):
            raise BIExportError("group companion schema or partitions are invalid")
        row_count = byte_count = 0
        for part in data["partitions"]:
            relative = part.get("path") if isinstance(part, dict) else None
            if (
                not isinstance(relative, str)
                or not re.fullmatch(rf"group-{kind}-[0-9]{{4,}}\.csv", relative)
                or relative in files
            ):
                raise BIExportError("group companion partition path is invalid")
            files.add(relative)
            path = root / relative
            if path.is_symlink() or not path.is_file():
                raise BIExportError("group companion partition is unavailable")
            digest, size, rows = _group_partition_stats(path, [field["name"] for field in fields])
            if (digest, size, rows) != (part.get("sha256"), part.get("bytes"), part.get("rows")):
                raise BIExportError("group companion partition digest or counts differ")
            row_count += rows
            byte_count += size
        if (row_count, byte_count, len(data["partitions"])) != (
            data.get("row_count"),
            data.get("bytes"),
            data.get("partition_count"),
        ):
            raise BIExportError("group companion totals differ")
    with projection_index(root, MAX_PROJECTION_INDEX_BYTES) as con, ExitStack() as streams:
        con.execute("CREATE TABLE seen_groups (id TEXT PRIMARY KEY)")
        members = streams.enter_context(closing(_companion_rows(root, descriptor, "members")))
        groups = streams.enter_context(closing(_companion_rows(root, descriptor, "groups")))
        consumed = 0
        for ordinal, group in enumerate(groups):
            try:
                metadata = json.loads(group["metadata_json"])
                group_id = metadata.get("group_id") or metadata.get("id")
                count = int(group["member_count"])
                declared = "" if metadata.get("count") is None else str(metadata["count"])
                if (
                    not isinstance(group_id, str)
                    or not group_id
                    or count < 0
                    or int(group["group_ordinal"]) != ordinal
                    or group["declared_count"] != declared
                ):
                    raise ValueError
                if (
                    group["group_id"] != _format_cell(group_id, "string", "group_id")[0]
                    or group["run_id"] != _format_cell(descriptor["run_id"], "string", "run_id")[0]
                ):
                    raise ValueError
                con.execute("INSERT INTO seen_groups VALUES(?)", (group_id,))
                digest = hashlib.sha256()
                for member_ordinal in range(count):
                    member = next(members)
                    if (
                        member["group_id"] != group["group_id"]
                        or member["run_id"] != group["run_id"]
                        or int(member["member_ordinal"]) != member_ordinal
                    ):
                        raise ValueError
                    encoded = json.dumps(
                        json.loads(member["member_json"]),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    digest.update(len(encoded).to_bytes(8, "big"))
                    digest.update(encoded)
                    consumed += 1
                if digest.hexdigest() != group["members_sha256"]:
                    raise ValueError
            except (
                AttributeError,
                TypeError,
                ValueError,
                StopIteration,
                sqlite3.IntegrityError,
            ) as exc:
                raise BIExportError("group companion members, order or identity differ") from exc
        if next(members, None) is not None or consumed != descriptor["members"]["row_count"]:
            raise BIExportError("group companion contains orphan members")
    return {
        "groups": descriptor["groups"]["row_count"],
        "members": consumed,
        "sha256": descriptor["sha256"],
    }


def copy_group_members_companion(root, destination, descriptor, budget):
    """Preserve the immutable complete companion with a selected findings package."""
    if descriptor is None:
        return None
    verify_group_members_companion(root, descriptor)
    for kind in ("groups", "members"):
        for part in descriptor[kind]["partitions"]:
            budget.reserve_bytes(part["bytes"])
            budget.reserve_partition()
            with (
                (Path(root) / part["path"]).open("rb") as source,
                (Path(destination) / part["path"]).open("xb") as target,
            ):
                shutil.copyfileobj(source, target, 1024 * 1024)
            os.chmod(Path(destination) / part["path"], 0o600)
    verify_group_members_companion(destination, descriptor)
    return descriptor


@_group_csv_limit()
def verify_group_member_references(root, manifest):
    """Bind every versioned finding reference to the exact complete local companion."""
    from seohead.reports.bi import MAX_PROJECTION_INDEX_BYTES, BIExportError, _format_cell

    root = Path(root)
    descriptor = manifest.get("group_members")
    result = verify_group_members_companion(root, descriptor)
    run = manifest.get("run") or {}
    if descriptor is not None and (
        descriptor["run_id"] != run.get("run_id")
        or descriptor["source_audit_sha256"] != run.get("audit_sha256")
    ):
        raise BIExportError("group companion does not bind to this package audit")
    with projection_index(root, MAX_PROJECTION_INDEX_BYTES) as con:
        con.execute("CREATE TABLE member_refs (id TEXT PRIMARY KEY, count INTEGER, sha256 TEXT)")
        if descriptor is not None:
            with closing(_companion_rows(root, descriptor, "groups")) as groups:
                for row in groups:
                    metadata = json.loads(row["metadata_json"])
                    con.execute(
                        "INSERT INTO member_refs VALUES(?,?,?)",
                        (
                            metadata.get("group_id") or metadata["id"],
                            int(row["member_count"]),
                            row["members_sha256"],
                        ),
                    )
        dataset = (manifest.get("datasets") or {}).get("findings") or {}
        for part in dataset.get("partitions") or []:
            relative = part.get("path")
            if (
                not isinstance(relative, str)
                or (root / relative).parent != root
                or (root / relative).is_symlink()
            ):
                raise BIExportError("finding reference partition is invalid")
            with (root / relative).open(encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream):
                    raw = row.get("group_urls_json")
                    if not raw:
                        continue
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        continue
                    if descriptor is None or value.get("schema") != "bi-group-members.v1":
                        raise BIExportError("finding group reference lacks its complete companion")
                    if (
                        not isinstance(value.get("group_id"), str)
                        or type(value.get("member_count")) is not int
                    ):
                        raise BIExportError("finding group reference has invalid identity or count")
                    for name, expected_value in (
                        ("group_id", _format_cell(value["group_id"], "string", "group_id")[0]),
                        ("run_id", _format_cell(value.get("run_id"), "string", "run_id")[0]),
                        ("group_url_count", str(value["member_count"])),
                    ):
                        if name in row and row[name] != expected_value:
                            raise BIExportError(
                                "finding row differs from its group member reference"
                            )
                    expected = con.execute(
                        "SELECT count,sha256 FROM member_refs WHERE id=?", (value.get("group_id"),)
                    ).fetchone()
                    if (
                        expected is None
                        or type(value.get("member_count")) is not int
                        or (value.get("member_count"), value.get("members_sha256"))
                        != tuple(expected)
                        or value.get("companion_sha256") != descriptor["sha256"]
                        or value.get("source_audit_sha256") != descriptor["source_audit_sha256"]
                        or value.get("run_id") != descriptor["run_id"]
                    ):
                        raise BIExportError(
                            "finding group reference differs from its source companion"
                        )
    return result


class QuadrantLookup:
    """Fetch one candidate pair or its refusal reason without a URL dictionary."""

    def __init__(self, con, *, blocked=False):
        self.con = con
        self.blocked = blocked

    def get(self, url, default=None):
        refused = self.con.execute("SELECT reason FROM blocked WHERE url=?", (url,)).fetchone()
        if self.blocked:
            return refused[0] if refused else default
        if refused:
            return default
        row = self.con.execute(
            "SELECT search_json,sessions_json FROM candidates "
            "WHERE url=? AND search_count=1 AND sessions_count=1 LIMIT 1",
            (url,),
        ).fetchone()
        return (json.loads(row[0]), json.loads(row[1])) if row else default


class InlinkIndex:
    """Count unique eligible source pages, independently of link occurrences."""

    def __init__(self, con, run):
        self.con = con
        con.execute("CREATE TABLE eligible (url TEXT PRIMARY KEY, complete INTEGER)")
        for page in run.pages_factory():
            media = str(page.get("content_type") or "").split(";", 1)[0].strip().casefold()
            complete = (
                page.get("document_id") is not None
                and not page.get("body_unavailable")
                and media in {"text/html", "application/xhtml+xml"}
            )
            con.execute("INSERT INTO eligible VALUES (?,?)", (page["url"], int(complete)))
        self.denominator = con.execute("SELECT COUNT(*) FROM eligible WHERE complete=1").fetchone()[
            0
        ]
        con.execute("CREATE TABLE inlinks (target TEXT, source TEXT, PRIMARY KEY(target,source))")
        for link in run.links_factory():
            con.execute(
                "INSERT OR IGNORE INTO inlinks SELECT ?,? "
                "WHERE EXISTS(SELECT 1 FROM eligible WHERE url=? AND complete=1) "
                "AND EXISTS(SELECT 1 FROM eligible WHERE url=?)",
                (
                    link["destination_url"],
                    link["source_url"],
                    link["source_url"],
                    link["destination_url"],
                ),
            )

    def numerator(self, url):
        return self.con.execute("SELECT COUNT(*) FROM inlinks WHERE target=?", (url,)).fetchone()[0]


class PrimarySegmentIndex:
    """Disk-backed primary segments using the shared segment rule engine.

    A selected findings export may need the same ``analysis.segments`` result
    as a saved project view while its audit.v2 source is too large to
    materialize.  This temporary index keeps the source rows and membership
    relations in SQLite; it never changes the retained scan or BI package.
    """

    def __init__(self, con, definitions):
        from seohead.sf.core.segments import required_fields, resolve_order

        self.con = con
        self.order = resolve_order(definitions)
        self.required_fields = required_fields(self.order)
        self._order_index = {segment.name: index for index, segment in enumerate(self.order)}
        con.execute("CREATE TABLE segment_pages (url TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
        con.execute(
            "CREATE TABLE segment_memberships (segment TEXT NOT NULL, url TEXT NOT NULL, "
            "PRIMARY KEY(segment,url))"
        )
        con.execute("CREATE TABLE primary_segments (url TEXT PRIMARY KEY, segment TEXT NOT NULL)")
        con.execute(
            "CREATE TABLE segment_unknown (segment TEXT NOT NULL, url TEXT NOT NULL, "
            "fields_json TEXT NOT NULL, PRIMARY KEY(segment,url))"
        )

    def build(self, pages):
        for page in pages:
            url = page.get("url") if isinstance(page, dict) else None
            if not isinstance(url, str) or not url:
                continue
            record = dict(page)
            parts = urlsplit(url)
            record["url"] = url
            record.setdefault("path", parts.path)
            record.setdefault("host", (parts.hostname or "").lower())
            self.con.execute(
                "INSERT INTO segment_pages(url,record_json) VALUES (?,?)",
                (
                    url,
                    json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
        memberships = _SegmentMemberships(self.con)
        from seohead.sf.core.segments import _rule_matches

        for segment in self.order:
            for url, raw in self.con.execute(
                "SELECT url,record_json FROM segment_pages ORDER BY url"
            ):
                state, missing = _segment_state(
                    segment,
                    json.loads(raw),
                    memberships,
                    _rule_matches,
                    self.required_fields[segment.name],
                )
                if state == "matched":
                    self.con.execute(
                        "INSERT INTO segment_memberships(segment,url) VALUES (?,?)",
                        (segment.name, url),
                    )
                    self.con.execute(
                        "INSERT OR IGNORE INTO primary_segments(url,segment) VALUES (?,?)",
                        (url, segment.name),
                    )
                elif state == "unknown":
                    self.con.execute(
                        "INSERT INTO segment_unknown(segment,url,fields_json) VALUES (?,?,?)",
                        (segment.name, url, json.dumps(sorted(missing))),
                    )

    def primary(self, url):
        row = self.con.execute(
            "SELECT segment FROM primary_segments WHERE url=?", (url,)
        ).fetchone()
        if row is not None:
            primary_index = self._order_index[row[0]]
            missing = set()
            for segment, index in self._order_index.items():
                if index >= primary_index:
                    continue
                unknown = self.con.execute(
                    "SELECT fields_json FROM segment_unknown WHERE url=? AND segment=?",
                    (url, segment),
                ).fetchone()
                if unknown is not None:
                    missing.update(json.loads(unknown[0]))
            if missing:
                from seohead.reports.bi import BIExportError

                raise BIExportError(
                    "segment selection is unavailable because retained page fields are missing: "
                    + ", ".join(sorted(missing))
                )
            return row[0]
        unknown = self.con.execute(
            "SELECT fields_json FROM segment_unknown WHERE url=? ORDER BY segment", (url,)
        ).fetchall()
        if unknown:
            missing = sorted({field for value in unknown for field in json.loads(value[0])})
            from seohead.reports.bi import BIExportError

            raise BIExportError(
                "segment selection is unavailable because retained page fields are missing: "
                + ", ".join(missing)
            )
        from seohead.sf.core.segments import UNSEGMENTED, assign_segments

        parts = urlsplit(url)
        isolated = assign_segments(
            [{"url": url, "path": parts.path, "host": (parts.hostname or "").lower()}],
            self.order,
        )["primary"].get(url)
        return "default" if isolated in (None, UNSEGMENTED) else isolated


class _SegmentMemberships:
    """Mapping-shaped SQLite membership lookup for the shared rule evaluator."""

    def __init__(self, con):
        self.con = con

    def get(self, name, default=None):
        return _SegmentMembership(self.con, name) if name is not None else default

    def unknown_fields(self, name, url):
        row = self.con.execute(
            "SELECT fields_json FROM segment_unknown WHERE segment=? AND url=?", (name, url)
        ).fetchone()
        return set(json.loads(row[0])) if row is not None else set()


class _SegmentMembership:
    def __init__(self, con, segment):
        self.con = con
        self.segment = segment

    def __contains__(self, url):
        return (
            self.con.execute(
                "SELECT 1 FROM segment_memberships WHERE segment=? AND url=?", (self.segment, url)
            ).fetchone()
            is not None
        )


def _field_present(record, field):
    value = record
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def _segment_state(segment, record, memberships, rule_matches, required):
    """Evaluate shared OR rules while preserving unavailable retained fields."""
    unknown = set()
    url = record.get("url")
    for rule in segment.rules:
        if rule.op == "segment":
            if url in memberships.get(rule.value, ()):
                return "matched", set()
            unknown.update(memberships.unknown_fields(rule.value, url))
            continue
        if not _field_present(record, rule.field):
            unknown.add(rule.field)
            continue
        if rule_matches(rule, record, memberships):
            return "matched", set()
    unknown.intersection_update(required)
    return ("unknown", unknown) if unknown else ("not_matched", set())
