"""Disk-backed comparison with complete, deterministic finding exports.

The temporary SQLite index bounds working memory independently of URL/finding
count. A new output directory contains immutable NDJSON streams and a compact
manifest; no inline sample stands in for the full comparison population.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import zlib
from collections.abc import Iterable, Mapping
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .compare import (
    CompareError,
    _header,
    _host_changed,
    _iter_rows,
    _key,
    _load_correspondence,
    _mapped_url,
    _measurement_gaps,
    _page_facts,
    _run,
    preflight,
)
from .evidence_contract import comparison_compatibility, comparison_warnings

_BUCKETS = ("entered", "left", "appeared", "disappeared", "unchanged")


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _index(con: sqlite3.Connection, source: Any, side: int) -> None:
    for page in _iter_rows(source, "pages"):
        if not isinstance(page, Mapping) or not isinstance(page.get("url"), str) or not page["url"]:
            raise CompareError("comparison page must contain a nonempty URL")
        try:
            con.execute(
                "INSERT INTO pages VALUES (?,?,?,?)", (side, page["url"], page["url"], _json(page))
            )
        except sqlite3.IntegrityError as exc:
            raise CompareError(f"comparison has duplicate page URL {page['url']!r}") from exc
    for issue in _iter_rows(source, "issues"):
        if (
            not isinstance(issue, Mapping)
            or not isinstance(issue.get("check"), str)
            or not issue["check"]
        ):
            raise CompareError("comparison finding must contain a nonempty check")
        if issue.get("target_url") is not None and not isinstance(issue["target_url"], str):
            raise CompareError("comparison finding target_url must be a string or null")
        check, url = _key(issue)
        try:
            con.execute(
                "INSERT INTO issues VALUES (?,?,?,?,?)", (side, check, url, url, _json(issue))
            )
        except sqlite3.IntegrityError as exc:
            raise CompareError(f"comparison has duplicate finding key {(check, url)!r}") from exc


def _pair(before_url: str, after_url: str, kind: str, state: str) -> dict[str, Any]:
    return {
        "before_url": before_url,
        "after_url": after_url,
        "correspondence_key": after_url,
        "kind": kind,
        "state": state,
        "before_origin": f"{urlsplit(before_url).scheme}://{urlsplit(before_url).netloc}",
        "after_origin": f"{urlsplit(after_url).scheme}://{urlsplit(after_url).netloc}",
        "host_changed": _host_changed(before_url, after_url),
    }


def _bind(con: sqlite3.Connection, declaration: Mapping[str, Any]) -> None:
    for (before_url,) in con.execute("SELECT url FROM pages WHERE side=0 ORDER BY url"):
        after_url, kind = _mapped_url(before_url, declaration)
        if after_url is None:
            continue
        matched = con.execute("SELECT 1 FROM pages WHERE side=1 AND url=?", (after_url,)).fetchone()
        state = "matched" if matched else "after_not_crawled"
        con.execute(
            "INSERT INTO pairs VALUES (?,?,?,?)",
            (before_url, after_url, state, _json(_pair(before_url, after_url, kind, state))),
        )
        if matched:
            con.execute(
                "UPDATE pages SET comparison_url=? WHERE side=0 AND url=?", (after_url, before_url)
            )
    for before_url, after_url in sorted(declaration["pairs"].items()):
        if not con.execute("SELECT 1 FROM pages WHERE side=0 AND url=?", (before_url,)).fetchone():
            con.execute(
                "INSERT INTO pairs VALUES (?,?,?,?)",
                (
                    before_url,
                    after_url,
                    "before_not_crawled",
                    _json(_pair(before_url, after_url, "explicit_pair", "before_not_crawled")),
                ),
            )
    # Include identity-mapped pages in collision detection: A -> B must not
    # silently merge with an existing, otherwise unmapped baseline B.
    collision = con.execute(
        "SELECT comparison_url FROM pages WHERE side=0 GROUP BY comparison_url HAVING count(*)>1 LIMIT 1"
    ).fetchone()
    if collision:
        raise CompareError(f"url correspondence has a page collision at {collision[0]!r}")
    con.execute("""UPDATE issues SET comparison_url=COALESCE(
        (SELECT after_url FROM pairs WHERE before_url=issues.url AND state='matched'), url)
        WHERE side=0""")


def _export(
    root: Path, name: str, rows: Iterable[Any], *, compression: str = "none"
) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    size = 0
    compressed = compression == "gzip"
    path = root / f"{name}.ndjson{'.gz' if compressed else ''}"
    with path.open("xb") as raw:
        # Exclude clock and destination path from gzip headers: the same rows
        # produce the same bytes in separate CLI and MCP output directories.
        with (
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6)
            if compressed
            else nullcontext(raw)
        ) as stream:
            for row in rows:
                data = (_json(row) + "\n").encode("utf-8")
                stream.write(data)
                if not compressed:
                    digest.update(data)
                count += 1
                size += len(data)
        raw.flush()
        os.fsync(raw.fileno())
    stored_size = size
    if compressed:
        stored_size = 0
        with path.open("rb") as stream:
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(data)
                stored_size += len(data)
    return {
        "path": path.name,
        "format": "ndjson.gz" if compressed else "ndjson",
        "rows": count,
        "bytes": stored_size,
        "sha256": digest.hexdigest(),
        **({"compression": "gzip", "uncompressed_bytes": size} if compressed else {}),
    }


def iter_compare_rows(manifest_path: str | Path, name: str):
    """Validate and stream one complete comparison file, plain or gzip.

    The stored-byte hash is verified before yielding. Exhaust the iterator to
    validate JSONL framing, decompressor integrity, row and decoded byte counts.
    """
    manifest_path = Path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "compare.v2":
            raise CompareError("unsupported comparison manifest schema")
        entry = manifest["files"][name]
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or Path(relative).name != relative
            or relative in {".", ".."}
        ):
            raise CompareError("comparison file path must be a local filename")
        path = manifest_path.parent / relative
        if path.is_symlink():
            raise CompareError("comparison file must not be a symbolic link")
        format_ = entry["format"]
        if format_ not in {"ndjson", "ndjson.gz"}:
            raise CompareError("unsupported comparison file format")
        compressed = format_ == "ndjson.gz"
        if compressed and entry.get("compression") != "gzip":
            raise CompareError("comparison compression metadata mismatch")
        expected_size = entry["uncompressed_bytes"] if compressed else entry["bytes"]
        for value in (entry["bytes"], expected_size, entry["rows"]):
            if type(value) is not int or value < 0:
                raise CompareError("invalid comparison file count metadata")
        count = size = stored_size = 0
        digest = hashlib.sha256()
        # Keep the same descriptor across the digest and decode passes.
        with path.open("rb") as raw:
            for data in iter(lambda: raw.read(1024 * 1024), b""):
                stored_size += len(data)
                digest.update(data)
            if stored_size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                raise CompareError("comparison file checksum or byte count mismatch")
            raw.seek(0)
            with (
                gzip.GzipFile(mode="rb", fileobj=raw) if compressed else nullcontext(raw)
            ) as stream:
                for line in stream:
                    size += len(line)
                    count += 1
                    if size > expected_size or count > entry["rows"] or not line.endswith(b"\n"):
                        raise CompareError("comparison file row framing or count mismatch")
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise CompareError("comparison file contains a non-object row")
                    yield row
            if count != entry["rows"] or size != expected_size:
                raise CompareError("comparison file row or decoded byte count mismatch")
    except (OSError, EOFError, ValueError, KeyError, TypeError, zlib.error) as exc:
        if isinstance(exc, CompareError):
            raise
        raise CompareError(f"invalid comparison file {name!r}: {exc}") from exc


def _facts(con: sqlite3.Connection):
    for raw_pair, raw_before, raw_after in con.execute("""
        SELECT pairs.document, b.document, a.document FROM pairs
        JOIN pages b ON b.side=0 AND b.url=pairs.before_url
        JOIN pages a ON a.side=1 AND a.url=pairs.after_url
        WHERE pairs.state='matched' ORDER BY pairs.before_url
    """):
        pair = json.loads(raw_pair)
        before, after = _page_facts(json.loads(raw_before)), _page_facts(json.loads(raw_after))
        yield {
            **{
                key: pair[key]
                for key in ("before_url", "after_url", "correspondence_key", "kind", "host_changed")
            },
            "before": before,
            "after": after,
            "changed": sorted(name for name in before if before[name] != after[name]),
        }


def compare_to_files(
    before: Any,
    after: Any,
    *,
    out_dir: str | Path,
    force: bool,
    correspondence: Any,
    compression: str = "none",
) -> dict[str, Any]:
    """Write a compare.v2 directory; reject duplicate identities, never truncate."""
    from seohead.verification import source_identity

    destination = Path(out_dir).absolute()
    if destination.exists():
        raise FileExistsError(f"comparison output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    declaration = _load_correspondence(correspondence) if correspondence is not None else None
    identities = {"before": source_identity(before), "after": source_identity(after)}
    with tempfile.TemporaryDirectory(prefix=".compare-", dir=destination.parent) as temporary:
        root = Path(temporary)
        with closing(sqlite3.connect(root / "index.sqlite")) as con:
            con.executescript("""
                PRAGMA temp_store=FILE;
                PRAGMA cache_size=-8192;
                CREATE TABLE pages(side INTEGER, url TEXT, comparison_url TEXT, document TEXT, PRIMARY KEY(side,url));
                CREATE TABLE issues(side INTEGER, check_key TEXT, url TEXT, comparison_url TEXT, document TEXT, PRIMARY KEY(side,check_key,url));
                CREATE TABLE pairs(before_url TEXT PRIMARY KEY, after_url TEXT, state TEXT, document TEXT);
                CREATE TABLE delta(bucket TEXT, check_key TEXT, url TEXT, document TEXT);
            """)
            _index(con, before, 0)
            _index(con, after, 1)
            if declaration is not None:
                _bind(con, declaration)
            try:
                con.execute(
                    "CREATE UNIQUE INDEX issue_identity ON issues(side,check_key,comparison_url)"
                )
            except sqlite3.IntegrityError as exc:
                raise CompareError("url correspondence creates duplicate finding keys") from exc
            con.execute("CREATE INDEX page_identity ON pages(side,comparison_url)")
            for side, other in ((0, after), (1, before)):
                partial = bool(_run(other).get("crawl_partial"))
                present_bucket, absent_bucket = (
                    ("left", "disappeared") if side == 0 else ("entered", "appeared")
                )
                con.execute(
                    """INSERT INTO delta
                    SELECT CASE WHEN i.comparison_url='' OR ? OR EXISTS (
                        SELECT 1 FROM pages p WHERE p.side=? AND p.comparison_url=i.comparison_url
                    ) THEN ? ELSE ? END, i.check_key, i.url, i.document
                    FROM issues i WHERE i.side=? AND NOT EXISTS (
                        SELECT 1 FROM issues j WHERE j.side=? AND j.check_key=i.check_key
                        AND j.comparison_url=i.comparison_url)
                    """,
                    (partial, 1 - side, present_bucket, absent_bucket, side, 1 - side),
                )
            con.execute("CREATE INDEX delta_order ON delta(bucket,check_key,url)")
            con.commit()
            from functools import partial

            export = partial(_export, compression=compression)
            files = {}
            for bucket in _BUCKETS[:-1]:
                files[bucket] = export(
                    root,
                    bucket,
                    (
                        json.loads(row[0])
                        for row in con.execute(
                            "SELECT document FROM delta WHERE bucket=? ORDER BY check_key,url",
                            (bucket,),
                        )
                    ),
                )
            files["unchanged"] = export(
                root,
                "unchanged",
                (
                    {"before": json.loads(row[0]), "after": json.loads(row[1])}
                    for row in con.execute("""SELECT b.document,a.document FROM issues b JOIN issues a
                    ON a.side=1 AND a.check_key=b.check_key AND a.comparison_url=b.comparison_url
                    WHERE b.side=0 ORDER BY b.check_key,b.url""")
                ),
            )
            files["by_check"] = export(
                root,
                "by_check",
                (
                    {"check": row[0], **dict(zip(_BUCKETS[:-1], row[1:], strict=True))}
                    for row in con.execute("""SELECT check_key,
                    sum(bucket='entered'),sum(bucket='left'),sum(bucket='appeared'),sum(bucket='disappeared')
                    FROM delta GROUP BY check_key ORDER BY check_key""")
                ),
            )
            if declaration is not None:
                files["correspondence"] = export(
                    root,
                    "correspondence",
                    (
                        json.loads(row[0])
                        for row in con.execute("SELECT document FROM pairs ORDER BY before_url")
                    ),
                )
                files["facts"] = export(root, "facts", _facts(con))
            counts = {bucket: files[bucket]["rows"] for bucket in _BUCKETS}
            totals = {}
            for side, label in ((0, "before"), (1, "after")):
                totals[label] = con.execute(
                    "SELECT count(*) FROM issues WHERE side=?", (side,)
                ).fetchone()[0]
                identities[label]["urls_crawled"] = con.execute(
                    "SELECT count(*) FROM pages WHERE side=?", (side,)
                ).fetchone()[0]
            conservation = {
                "before_issues": totals["before"],
                "after_issues": totals["after"],
                "before_accounted": counts["left"] + counts["disappeared"] + counts["unchanged"],
                "after_accounted": counts["entered"] + counts["appeared"] + counts["unchanged"],
            }
            if (
                conservation["before_issues"] != conservation["before_accounted"]
                or conservation["after_issues"] != conservation["after_accounted"]
            ):
                raise CompareError("comparison conservation failed")
        warnings = list(
            dict.fromkeys(
                preflight(before, after) + comparison_warnings(_header(before), _header(after))
            )
        )
        result = {
            "schema_version": "compare.v2",
            **identities,
            "force": force,
            "warnings": warnings,
            "compatibility": comparison_compatibility(_header(before), _header(after)),
            "summary": counts,
            "conservation": {**conservation, "state": "complete"},
            "policies": {
                "identity": "exact check and target URL; declared correspondence only",
                "duplicates": "reject duplicate page URLs, finding keys, and correspondence collisions",
                "suppression": "all retained findings, including suppressed rows; no view filtering",
                "unchanged": "same finding key; both evidence rows retained, not an evidence-equality assertion",
                "partial": "partial or incompatible observations remain warned deltas, not verified fixes",
            },
            "files": files,
        }
        gaps = _measurement_gaps(before, after)
        if gaps:
            result["measurement_gaps"] = gaps
        if declaration is not None:
            result["correspondence"] = {
                "schema_version": "url-correspondence.v1",
                "origin_map": declaration["origin_map"],
            }
        manifest = root / "compare.json"
        with manifest.open("x", encoding="utf-8") as stream:
            stream.write(_json(result) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Keep the temporary SQL index out of the retained artifact contract.
        (root / "index.sqlite").unlink()
        destination.mkdir()  # exclusive publication; an existing output is never replaced
        for path in sorted(root.iterdir(), key=lambda path: path.name == "compare.json"):
            os.replace(path, destination / path.name)
    return {**result, "manifest": str(destination / "compare.json"), "out_dir": str(destination)}
