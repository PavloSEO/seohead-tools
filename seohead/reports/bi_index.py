"""Disposable disk-backed BI lookups; never modify retained source artifacts."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


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
