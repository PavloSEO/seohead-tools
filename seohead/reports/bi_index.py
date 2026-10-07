"""Disposable disk-backed BI lookups; never modify retained source artifacts."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
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
        con.execute(
            "CREATE TABLE groups (id TEXT PRIMARY KEY, value TEXT, linked INTEGER DEFAULT 0)"
        )
        for group in groups:
            self.source_count += 1
            if self.source_count > MAX_FINDINGS:
                raise BIExportError("finding groups exceed the source row bound")
            group_id = group.get("group_id") or group.get("id")
            if not isinstance(group_id, str) or not group_id:
                continue
            value = json.dumps(group, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(value.encode("utf-8")) > MAX_CELL_BYTES:
                raise BIExportError("one finding group exceeds the BI cell bound")
            try:
                con.execute("INSERT INTO groups(id,value) VALUES (?,?)", (group_id, value))
            except sqlite3.IntegrityError as exc:
                raise BIExportError(f"duplicate finding group id {group_id!r}") from exc

    def get(self, key):
        row = self.con.execute("SELECT value FROM groups WHERE id=?", (key,)).fetchone()
        if row is None:
            return None
        self.con.execute("UPDATE groups SET linked=1 WHERE id=? AND linked=0", (key,))
        return json.loads(row[0])

    def coverage(self):
        counts = self.con.execute("SELECT COUNT(*),COALESCE(SUM(linked),0) FROM groups").fetchone()
        return counts[0], counts[1]

    def unlinked(self):
        for row in self.con.execute("SELECT id FROM groups WHERE linked=0 ORDER BY id"):
            yield row[0]


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
