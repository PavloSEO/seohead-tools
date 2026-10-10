"""Durable cursor-backed evidence joins for million-page native scans.

Small joins remain JSON documents.  This artifact keeps a large crawl join in
SQLite: one row per retained page, normalized provider row, and provenance
edge.  Readers reopen cursors, so a consumer never needs a list of crawl pages
or matched URLs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from seohead.core.common import canonical_json
from seohead.data_sources.evidence_import import NORMALIZED_FORMAT
from seohead.data_sources.evidence_join import _evidence_header, _key_fn, _row_join_key
from seohead.storage import open_scan

FORMAT = "seohead.evidence-join-sqlite.v1"
MAX_PAGES = 1_000_000
MAX_EVIDENCE_ROWS = 100_000
# An explicit edge/output rail, not a site-size cap: one million distinct URLs
# joined to one evidence row each is valid.
MAX_MATCH_EDGES = 5_000_000


class EvidenceJoinStoreError(ValueError):
    """A durable evidence-join artifact is malformed or unsafe."""


def _update(digest: Any, *values: Any) -> None:
    for value in values:
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\0")


def _url_policy(value: dict[str, bool] | None) -> dict[str, bool]:
    policy = {"ignore_query": False, "ignore_scheme": False, "casefold_path": False}
    supplied = value or {}
    if not isinstance(supplied, dict) or set(supplied) - set(policy):
        raise EvidenceJoinStoreError("unsupported evidence-join URL policy")
    for key, enabled in supplied.items():
        if not isinstance(enabled, bool):
            raise EvidenceJoinStoreError(f"URL policy {key} must be boolean")
        policy[key] = enabled
    return policy


def _new_target(value: str | Path) -> Path:
    path = Path(value).absolute()
    if path.exists() or path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
        raise EvidenceJoinStoreError(
            "evidence join store must be a new file in an existing non-symlink directory"
        )
    return path


def _open_read(value: str | Path) -> sqlite3.Connection:
    path = Path(value).absolute()
    if path.is_symlink() or not path.is_file():
        raise EvidenceJoinStoreError("evidence join store must be a regular non-symlink file")
    try:
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA query_only=ON")
        return con
    except sqlite3.Error as exc:
        raise EvidenceJoinStoreError(f"cannot open evidence join store: {exc}") from exc


def _validate_metadata(metadata: Any) -> None:
    if not isinstance(metadata, dict) or metadata.get("format") != FORMAT:
        raise EvidenceJoinStoreError(f"expected {FORMAT} metadata")
    if any(not isinstance(metadata.get(name), dict) for name in ("crawl", "evidence", "summary")):
        raise EvidenceJoinStoreError("evidence join store metadata is incomplete")
    summary = metadata["summary"]
    names = (
        "pages",
        "rows",
        "matched_rows",
        "matched_pages",
        "crawl_only",
        "external_only",
        "unkeyable_rows",
        "unkeyable_pages",
        "candidate_pairs",
    )
    if any(type(summary.get(name)) is not int or summary[name] < 0 for name in names):
        raise EvidenceJoinStoreError("evidence join store has invalid population counts")
    if (
        summary["matched_rows"] + summary["external_only"] + summary["unkeyable_rows"]
        != summary["rows"]
    ):
        raise EvidenceJoinStoreError("evidence join store does not conserve evidence rows")
    if (
        summary["matched_pages"] + summary["crawl_only"] + summary["unkeyable_pages"]
        != summary["pages"]
    ):
        raise EvidenceJoinStoreError("evidence join store does not conserve crawl pages")


def is_store(value: str | Path) -> bool:
    try:
        with _open_read(value) as con:
            row = con.execute("SELECT value FROM meta WHERE key='document'").fetchone()
            return bool(row and json.loads(row[0]).get("format") == FORMAT)
    except (EvidenceJoinStoreError, json.JSONDecodeError, sqlite3.Error):
        return False


class EvidenceJoinStore:
    """Immutable metadata plus re-iterable row, URL-edge, and page cursors."""

    def __init__(self, value: str | Path) -> None:
        self.path = Path(value).absolute()
        with _open_read(self.path) as con:
            row = con.execute("SELECT value FROM meta WHERE key='document'").fetchone()
        if row is None:
            raise EvidenceJoinStoreError("evidence join store has no metadata")
        try:
            self.metadata = json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvidenceJoinStoreError("evidence join store metadata is invalid JSON") from exc
        _validate_metadata(self.metadata)
        summary = self.metadata["summary"]
        with _open_read(self.path) as con:
            actual = {
                "pages": int(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]),
                "rows": int(con.execute("SELECT COUNT(*) FROM evidence_rows").fetchone()[0]),
                "candidate_pairs": int(con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]),
                "matched_rows": int(
                    con.execute(
                        "SELECT COUNT(*) FROM evidence_rows WHERE population='matched'"
                    ).fetchone()[0]
                ),
                "matched_pages": int(
                    con.execute("SELECT COUNT(*) FROM pages WHERE population='matched'").fetchone()[
                        0
                    ]
                ),
                "crawl_only": int(
                    con.execute(
                        "SELECT COUNT(*) FROM pages WHERE population='crawl_only'"
                    ).fetchone()[0]
                ),
                "external_only": int(
                    con.execute(
                        "SELECT COUNT(*) FROM evidence_rows WHERE population='external_only'"
                    ).fetchone()[0]
                ),
                "unkeyable_rows": int(
                    con.execute(
                        "SELECT COUNT(*) FROM evidence_rows WHERE population='unkeyable_rows'"
                    ).fetchone()[0]
                ),
                "unkeyable_pages": int(
                    con.execute(
                        "SELECT COUNT(*) FROM pages WHERE population='unkeyable_pages'"
                    ).fetchone()[0]
                ),
            }
            dangling = con.execute(
                "SELECT 1 FROM matches m LEFT JOIN evidence_rows r "
                "ON r.row_index=m.row_index AND r.natural_key_sha256=m.natural_key_sha256 "
                "LEFT JOIN pages p ON p.page_ordinal=m.page_ordinal "
                "WHERE r.row_index IS NULL OR p.page_ordinal IS NULL LIMIT 1"
            ).fetchone()
        if dangling or any(summary[name] != value for name, value in actual.items()):
            raise EvidenceJoinStoreError("evidence join store rows disagree with its metadata")

    def iter_observations(self) -> Iterator[dict[str, Any]]:
        """Yield each provider row once with an exact matched page count."""
        con = _open_read(self.path)
        try:
            for item in con.execute(
                "SELECT r.row_index,r.natural_key_sha256,r.row_json,r.population,"
                "COUNT(m.page_ordinal) FROM evidence_rows r LEFT JOIN matches m "
                "ON m.row_index=r.row_index AND m.natural_key_sha256=r.natural_key_sha256 "
                "GROUP BY r.row_index,r.natural_key_sha256,r.row_json,r.population "
                "ORDER BY r.row_index,r.natural_key_sha256"
            ):
                try:
                    row = json.loads(item[2])
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise EvidenceJoinStoreError("evidence join store row JSON is invalid") from exc
                yield {
                    "identity": (item[0], item[1]),
                    "row": row,
                    "population": item[3],
                    "matched_page_count": int(item[4]),
                }
        finally:
            con.close()

    def iter_matches(self) -> Iterator[dict[str, Any]]:
        """Yield every provider-row to retained-URL provenance edge."""
        con = _open_read(self.path)
        try:
            for item in con.execute(
                "SELECT m.row_index,m.natural_key_sha256,p.page_ordinal,p.url,p.join_key "
                "FROM matches m JOIN pages p ON p.page_ordinal=m.page_ordinal "
                "ORDER BY m.row_index,m.natural_key_sha256,p.page_ordinal"
            ):
                yield {
                    "identity": (item[0], item[1]),
                    "page_ordinal": item[2],
                    "url": item[3],
                    "url_key": item[4],
                }
        finally:
            con.close()

    def iter_pages(self, population: str | None = None) -> Iterator[dict[str, Any]]:
        con = _open_read(self.path)
        try:
            cursor = con.execute(
                "SELECT page_ordinal,url,join_key,population FROM pages "
                + ("WHERE population=? " if population is not None else "")
                + "ORDER BY page_ordinal",
                (population,) if population is not None else (),
            )
            for item in cursor:
                yield {
                    "page_ordinal": item[0],
                    "url": item[1],
                    "url_key": item[2],
                    "population": item[3],
                }
        finally:
            con.close()


def open_store(value: str | Path) -> EvidenceJoinStore:
    return EvidenceJoinStore(value)


def write(
    scan: str | Path,
    document: dict[str, Any],
    out: str | Path,
    *,
    policy: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Atomically join one saved scan with bounded normalized evidence."""
    if not isinstance(document, dict) or document.get("format") != NORMALIZED_FORMAT:
        raise EvidenceJoinStoreError(f"expected a {NORMALIZED_FORMAT} document")
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_EVIDENCE_ROWS:
        raise EvidenceJoinStoreError(f"normalized evidence exceeds {MAX_EVIDENCE_ROWS} rows")
    target, url_policy = _new_target(out), _url_policy(policy)
    key_fn = _key_fn(url_policy)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".evidence-join-", suffix=".sqlite", dir=target.parent
    )
    os.close(descriptor)
    staged: Path | None = Path(temporary)
    try:
        con = sqlite3.connect(staged)
        try:
            con.executescript(
                """
                PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;
                CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE pages (page_ordinal INTEGER PRIMARY KEY,url TEXT NOT NULL,join_key TEXT,population TEXT NOT NULL);
                CREATE INDEX pages_join_key ON pages(join_key);
                CREATE TABLE evidence_rows (row_index INTEGER NOT NULL,natural_key_sha256 TEXT NOT NULL,join_key TEXT,row_json TEXT NOT NULL,population TEXT NOT NULL,PRIMARY KEY(row_index,natural_key_sha256));
                CREATE INDEX evidence_rows_join_key ON evidence_rows(join_key);
                CREATE TABLE matches (row_index INTEGER NOT NULL,natural_key_sha256 TEXT NOT NULL,page_ordinal INTEGER NOT NULL,PRIMARY KEY(row_index,natural_key_sha256,page_ordinal)) WITHOUT ROWID;
                CREATE INDEX matches_page_ordinal ON matches(page_ordinal);
                """
            )
            page_digest, row_digest = hashlib.sha256(), hashlib.sha256()
            with open_scan(scan, require_audit=False) as source:
                scan_row = source.execute("SELECT scan_uuid FROM scan WHERE singleton=1").fetchone()
                if scan_row is None or not isinstance(scan_row[0], str) or not scan_row[0]:
                    raise EvidenceJoinStoreError("saved scan has no scan UUID")
                scan_uuid = scan_row[0]
                pages, batch = 0, []
                for source_row in source.execute(
                    "SELECT p.page_ordinal,u.url FROM pages p JOIN urls u USING(url_id) ORDER BY p.page_ordinal"
                ):
                    pages += 1
                    if pages > MAX_PAGES:
                        raise EvidenceJoinStoreError(
                            f"saved scan exceeds {MAX_PAGES} retained pages"
                        )
                    ordinal, raw_url = int(source_row[0]), source_row[1]
                    key = key_fn(raw_url)
                    batch.append(
                        (ordinal, raw_url, key, "unkeyable_pages" if key is None else "crawl_only")
                    )
                    _update(page_digest, ordinal, raw_url, key or "")
                    if len(batch) == 10_000:
                        con.executemany("INSERT INTO pages VALUES (?,?,?,?)", batch)
                        batch.clear()
                if batch:
                    con.executemany("INSERT INTO pages VALUES (?,?,?,?)", batch)
            dimension_names: set[str] = set()
            batch = []
            for raw in rows:
                if not isinstance(raw, dict):
                    raise EvidenceJoinStoreError("normalized evidence contains a non-object row")
                index, digest = raw.get("row_index"), raw.get("natural_key_sha256")
                if type(index) is not int or index < 0 or not isinstance(digest, str):
                    raise EvidenceJoinStoreError(
                        "normalized evidence row lacks its stable identity"
                    )
                dimensions = raw.get("dimensions") or {}
                if not isinstance(dimensions, dict):
                    raise EvidenceJoinStoreError("normalized evidence dimensions must be an object")
                dimension_names.update(str(name) for name in dimensions)
                key, payload = _row_join_key(raw, key_fn), canonical_json(raw)
                batch.append(
                    (
                        index,
                        digest,
                        key,
                        payload,
                        "unkeyable_rows" if key is None else "external_only",
                    )
                )
                _update(row_digest, index, digest, key or "", payload)
                if len(batch) == 10_000:
                    con.executemany("INSERT INTO evidence_rows VALUES (?,?,?,?,?)", batch)
                    batch.clear()
            if batch:
                con.executemany("INSERT INTO evidence_rows VALUES (?,?,?,?,?)", batch)
            con.execute(
                "INSERT INTO matches SELECT r.row_index,r.natural_key_sha256,p.page_ordinal "
                "FROM evidence_rows r JOIN pages p ON p.join_key=r.join_key WHERE r.join_key IS NOT NULL"
            )
            candidate_pairs = int(con.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
            if candidate_pairs > MAX_MATCH_EDGES:
                raise EvidenceJoinStoreError(
                    f"provider join exceeds {MAX_MATCH_EDGES} durable page/evidence edges"
                )
            con.execute(
                "UPDATE evidence_rows SET population='matched' WHERE population='external_only' AND EXISTS (SELECT 1 FROM matches m WHERE m.row_index=evidence_rows.row_index AND m.natural_key_sha256=evidence_rows.natural_key_sha256)"
            )
            con.execute(
                "UPDATE pages SET population='matched' WHERE population='crawl_only' AND EXISTS (SELECT 1 FROM matches m WHERE m.page_ordinal=pages.page_ordinal)"
            )

            def count(query: str) -> int:
                return int(con.execute(query).fetchone()[0])

            summary = {
                "pages": pages,
                "rows": len(rows),
                "matched_rows": count(
                    "SELECT COUNT(*) FROM evidence_rows WHERE population='matched'"
                ),
                "matched_pages": count("SELECT COUNT(*) FROM pages WHERE population='matched'"),
                "crawl_only": count("SELECT COUNT(*) FROM pages WHERE population='crawl_only'"),
                "external_only": count(
                    "SELECT COUNT(*) FROM evidence_rows WHERE population='external_only'"
                ),
                "unkeyable_rows": count(
                    "SELECT COUNT(*) FROM evidence_rows WHERE population='unkeyable_rows'"
                ),
                "unkeyable_pages": count(
                    "SELECT COUNT(*) FROM pages WHERE population='unkeyable_pages'"
                ),
                "candidate_pairs": candidate_pairs,
                "page_key_collisions": count(
                    "SELECT COUNT(*) FROM (SELECT join_key FROM pages WHERE join_key IS NOT NULL GROUP BY join_key HAVING COUNT(*) > 1)"
                ),
                "row_key_collisions": count(
                    "SELECT COUNT(*) FROM (SELECT join_key FROM evidence_rows WHERE join_key IS NOT NULL GROUP BY join_key HAVING COUNT(*) > 1)"
                ),
                "multi_match_keys": count(
                    "SELECT COUNT(*) FROM (SELECT p.join_key FROM pages p JOIN evidence_rows r ON r.join_key=p.join_key WHERE p.join_key IS NOT NULL GROUP BY p.join_key HAVING COUNT(DISTINCT p.page_ordinal)>1 OR COUNT(DISTINCT r.row_index || ':' || r.natural_key_sha256)>1)"
                ),
                "ambiguous_rows": sum(1 for row in rows if row.get("ambiguous")),
            }
            metadata = {
                "format": FORMAT,
                "crawl": {
                    "source": "scan",
                    "scan_uuid": scan_uuid,
                    "page_digest_sha256": page_digest.hexdigest(),
                },
                "evidence": _evidence_header(document),
                "url_policy": {"version": "external_join.v1", **url_policy},
                "allocation_policy": {
                    "version": "evidence_join_store.v1",
                    "domain_totals": "never_allocate_to_urls",
                    "missing_or_partial": "preserve_availability_state",
                    "ambiguous_rows": "preserve_without_collapse",
                },
                "summary": summary,
                "dimension_names": sorted(dimension_names),
                "evidence_rows_digest_sha256": row_digest.hexdigest(),
            }
            _validate_metadata(metadata)
            content = hashlib.sha256(canonical_json(metadata).encode("utf-8"))
            for edge in con.execute(
                "SELECT row_index,natural_key_sha256,page_ordinal FROM matches ORDER BY row_index,natural_key_sha256,page_ordinal"
            ):
                _update(content, *edge)
            metadata["content_sha256"] = content.hexdigest()
            con.execute("INSERT INTO meta VALUES (?,?)", ("document", canonical_json(metadata)))
            con.commit()
        finally:
            con.close()
        os.chmod(staged, 0o600)
        os.replace(staged, target)
        staged = None
        return {**metadata, "path": str(target)}
    except sqlite3.Error as exc:
        raise EvidenceJoinStoreError(f"cannot write evidence join store: {exc}") from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
