"""Versioned remediation ledger: stable finding/occurrence identity over saved scans.

The ledger is a separate local SQLite artifact (``ledger.v1``) bound to a
``seohead.project.v1`` project/site identity.  It ingests validated
``scan.v1``/``scan.v2`` artifacts read-only -- never appending to, upgrading, or
mutating the source scan -- and records which findings were observed, where each
occurrence lives, and every later observation of the same case, so a recheck or
remediation workflow can resume without rerunning the analyzer.

Identity rules (docs/LEDGER.md):

* a *finding* is the logical problem at project/site scope: one check at one
  subject (a canonical URL, or the explicit ``scope:site`` subject for an
  audit-wide finding);
* an *occurrence* is where that problem manifests: one check at one affected
  URL or non-URL subject, under one representation, with a typed discriminator
  separating firings on the same URL;
* *affected URLs* are finding membership, counted separately so findings,
  occurrences and distinct affected URLs never collapse into one number;
* an *observation* is one immutable sighting of an occurrence bound to an exact
  source scan revision (scan UUID + evidence revision + audit SHA-256 +
  configuration + producer identity).  Re-ingesting the same source revision is
  idempotent; a new revision appends and never overwrites the baseline.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import ScanError, _dump, _loads, open_scan
from .native_scan import _utc

APPLICATION_ID = 1397051212  # ASCII SEOL; scan artifacts use SEOH (1397051208).
USER_VERSION = 1
FORMAT_VERSION = "ledger.v1"
READ_TIMEOUT_SECONDS = 30
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_REPRESENTATIONS = frozenset(
    {"static", "rendered", "legacy_fragment", "legacy_unknown", "unknown", "scope"}
)
_SCOPE_SUBJECT = "site"
_KEY_FINDING = "seohead.ledger-finding.v1"
_KEY_OCCURRENCE = "seohead.ledger-occurrence.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_UTC = timezone.utc

# A lifecycle is deliberately a decision layer over retained observations.  It
# never changes an observation or turns an absent/failed fetch into a fix.
_LIFECYCLE_STATES = frozenset(
    {
        "detected",
        "verified",
        "fix_reported",
        "recheck_pending",
        "resolved",
        "persisting",
        "false_positive_reviewed",
        "unverifiable",
        "regressed",
    }
)
_TRANSITIONS = {
    "detected": frozenset({"verified", "fix_reported", "false_positive_reviewed", "unverifiable"}),
    "verified": frozenset(
        {"fix_reported", "recheck_pending", "false_positive_reviewed", "unverifiable"}
    ),
    "fix_reported": frozenset({"verified", "recheck_pending", "unverifiable"}),
    "recheck_pending": frozenset({"resolved", "persisting", "regressed", "unverifiable"}),
    "resolved": frozenset({"recheck_pending", "regressed"}),
    "persisting": frozenset({"fix_reported", "recheck_pending", "regressed", "unverifiable"}),
    "false_positive_reviewed": frozenset({"verified", "recheck_pending"}),
    "unverifiable": frozenset({"verified", "fix_reported", "recheck_pending"}),
    "regressed": frozenset({"fix_reported", "recheck_pending", "unverifiable"}),
}
_MEASURED_OUTCOMES = frozenset({"resolved", "persisting", "regressed"})


class LedgerError(ScanError):
    """The input cannot be used as a supported, consistent ledger artifact."""


def _runtime() -> None:
    if sqlite3 is None:
        raise LedgerError(
            "ledger.v1 requires a Python installation with the sqlite3 standard-library module"
        )
    if sqlite3.sqlite_version_info < (3, 31, 0):
        raise LedgerError(
            f"ledger.v1 requires SQLite >= 3.31; this Python uses {sqlite3.sqlite_version}"
        )


def _schema() -> str:
    return files(__package__).joinpath("ledger_v1.sql").read_text(encoding="utf-8")


def _ddl_statements() -> list[str]:
    """Schema statements minus PRAGMAs, for transactional migration.

    ``executescript`` commits any pending transaction first, so a migration that
    must roll back on failure applies the DDL one statement at a time instead.
    """
    statements = []
    for piece in _schema().split(";"):
        # Keep the piece verbatim: sqlite_master.sql stores embedded comments,
        # so the migrated schema must textually match a direct executescript.
        # Comments in ledger_v1.sql never contain ';' for exactly this reason.
        meaningful = [
            line
            for line in piece.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        if meaningful and not meaningful[0].strip().upper().startswith("PRAGMA"):
            statements.append(piece)
    return statements


def _objects(con) -> list[tuple]:
    return [
        tuple(row)
        for row in con.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name"
        )
    ]


@lru_cache(maxsize=1)
def _expected() -> list[tuple]:
    _runtime()
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(_schema())
        return _objects(con)
    finally:
        con.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(*parts: str) -> str:
    encoded = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_url(value: str) -> str:
    """Canonicalize an affected URL with the audit's own equality policy.

    This delegates to :func:`seohead.sf.core.normalize.norm_url` -- the same
    strip/lowercase-scheme-and-host/drop-trailing-slash rule the analyzer uses
    for URL identity -- so a recheck reproduces the same key without a second
    normalization contract to drift.  Query and fragment are preserved.
    """
    from seohead.sf.core.normalize import norm_url

    if not isinstance(value, str) or not value.strip():
        raise LedgerError("affected URL must be a nonempty string")
    normalized = norm_url(value)
    if not normalized:
        raise LedgerError("affected URL must be a nonempty string")
    if len(normalized) > 2048:
        raise LedgerError("affected URL exceeds the 2048-character bound")
    return normalized


def finding_key(
    *,
    project_uuid: str,
    site_host: str,
    check: str,
    subject_type: str,
    subject_value: str,
) -> str:
    """Stable logical identity of a check/problem at project/site scope."""
    return _key(_KEY_FINDING, project_uuid, site_host, check, subject_type, subject_value)


def occurrence_key(
    *,
    project_uuid: str,
    site_host: str,
    check: str,
    subject_type: str,
    subject_value: str,
    representation: str,
    discriminator_type: str,
    discriminator_value: str,
) -> str:
    """Stable, versioned identity of one affected case.

    Deliberately excludes severity, message, HTTP status, evidence value or
    hash, configuration, producer build, timestamps, the run-local audit
    ordinal and the scan UUID: those describe an *observation* of the case,
    never the case itself.
    """
    return _key(
        _KEY_OCCURRENCE,
        project_uuid,
        site_host,
        check,
        subject_type,
        subject_value,
        representation,
        discriminator_type,
        discriminator_value,
    )


def _validated_time(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != _UTC.utcoffset(parsed):
        return None
    return value


def _site_row(con, netloc: str):
    return con.execute(
        "SELECT * FROM site WHERE netloc=? ORDER BY (role='primary') DESC, site_id LIMIT 1",
        (netloc,),
    ).fetchone()


def _validate(con) -> None:
    if _objects(con) != _expected():
        raise LedgerError(
            "ledger schema differs: missing/changed tables, indexes or unexpected schema objects"
        )
    if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise LedgerError("ledger database failed quick_check")
    if con.execute("PRAGMA foreign_key_check").fetchone():
        raise LedgerError("ledger database has inconsistent foreign-key references")
    rows = con.execute("SELECT * FROM ledger").fetchall()
    if len(rows) != 1:
        raise LedgerError("ledger.v1 requires exactly one header")
    header = dict(rows[0])
    if header["format_version"] != FORMAT_VERSION:
        raise LedgerError("unsupported ledger format_version")
    try:
        uuid.UUID(header["ledger_uuid"])
    except (TypeError, ValueError) as exc:
        raise LedgerError("invalid ledger UUID") from exc
    if not _REVISION.fullmatch(header["writer_revision"]):
        raise LedgerError("ledger writer build must be a full lowercase Git commit SHA")
    if type(header["ledger_revision"]) is not int or header["ledger_revision"] < 0:
        raise LedgerError("invalid ledger revision")
    if (
        con.execute(
            "SELECT 1 FROM finding WHERE current_state NOT IN ("
            + ",".join("?" for _ in _LIFECYCLE_STATES)
            + ") LIMIT 1",
            tuple(sorted(_LIFECYCLE_STATES)),
        ).fetchone()
        or con.execute(
            "SELECT 1 FROM occurrence WHERE current_state NOT IN ("
            + ",".join("?" for _ in _LIFECYCLE_STATES)
            + ") LIMIT 1",
            tuple(sorted(_LIFECYCLE_STATES)),
        ).fetchone()
    ):
        raise LedgerError("ledger contains an unsupported lifecycle state")
    if con.execute(
        "SELECT 1 FROM decision WHERE state NOT IN ("
        + ",".join("?" for _ in _LIFECYCLE_STATES)
        + ") OR actor='' OR reason='' OR decided_at_state!='known' OR decided_at IS NULL "
        "LIMIT 1",
        tuple(sorted(_LIFECYCLE_STATES)),
    ).fetchone():
        raise LedgerError("ledger contains an invalid lifecycle decision")
    for site in con.execute("SELECT * FROM site"):
        try:
            uuid.UUID(site["project_uuid"])
        except (TypeError, ValueError) as exc:
            raise LedgerError("site row has an invalid project UUID") from exc
        if site["netloc"] != urlsplit(site["target"]).netloc.lower():
            raise LedgerError("site row identity is inconsistent")
    for row in con.execute(
        "SELECT f.finding_key,f.subject_type,f.subject_value,c.check_key,s.project_uuid,s.host "
        "FROM finding f JOIN check_def c ON c.check_id=f.check_id JOIN site s ON s.site_id=f.site_id"
    ):
        expected = finding_key(
            project_uuid=row["project_uuid"],
            site_host=row["host"],
            check=row["check_key"],
            subject_type=row["subject_type"],
            subject_value=row["subject_value"],
        )
        if row["finding_key"] != expected:
            raise LedgerError("finding identity does not match its stored key")
    for row in con.execute(
        "SELECT o.occurrence_key,o.subject_type,o.subject_value,o.representation,"
        "o.discriminator_type,o.discriminator_value,c.check_key,s.project_uuid,s.host "
        "FROM occurrence o JOIN check_def c ON c.check_id=o.check_id "
        "JOIN finding f ON f.finding_id=o.finding_id JOIN site s ON s.site_id=f.site_id"
    ):
        expected = occurrence_key(
            project_uuid=row["project_uuid"],
            site_host=row["host"],
            check=row["check_key"],
            subject_type=row["subject_type"],
            subject_value=row["subject_value"],
            representation=row["representation"],
            discriminator_type=row["discriminator_type"],
            discriminator_value=row["discriminator_value"],
        )
        if row["occurrence_key"] != expected:
            raise LedgerError("occurrence identity does not match its stored key")
    if con.execute(
        "SELECT 1 FROM source_scan WHERE NOT (audit_sha256 GLOB '[0-9a-f][0-9a-f]*' "
        "AND length(audit_sha256)=64) OR NOT (scan_sha256 GLOB '[0-9a-f][0-9a-f]*' "
        "AND length(scan_sha256)=64) LIMIT 1"
    ).fetchone():
        raise LedgerError("source scan binding has an invalid digest")
    if con.execute(
        "SELECT 1 FROM observation WHERE (observed_at IS NULL) != (observed_at_state='unknown') LIMIT 1"
    ).fetchone():
        raise LedgerError("observation time state disagrees with its stored value")


def _reader(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA trusted_schema=OFF")
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _writer(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.absolute().as_uri() + "?mode=rw", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA trusted_schema=OFF")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=FULL")
    return con


def _migrate_0_to_1(con: sqlite3.Connection) -> None:
    """Complete a staged ``ledger.v0`` stub into the full v1 schema.

    ``ledger.v0`` is a bootstrap stub format an interrupted or hand-staged
    creation may leave: the ``SEOL`` application id, a ``user_version`` of 0
    and a one-row ``ledger_meta`` marker naming the intended ledger UUID, its
    project/site binding and the writer that staged it.  A stub never held
    findings, and ``create_ledger`` itself always writes a complete validated
    ``ledger.v1`` -- only an explicit write open finishes a stub.
    """
    try:
        row = con.execute("SELECT * FROM ledger_meta WHERE singleton=1").fetchone()
    except sqlite3.Error as exc:
        raise LedgerError("ledger.v0 stub is missing its ledger_meta marker") from exc
    if row is None or row["format_version"] != "ledger.v0":
        raise LedgerError("ledger.v0 marker does not name ledger.v0")
    meta = dict(row)
    con.execute("DROP TABLE ledger_meta")
    for statement in _ddl_statements():
        con.execute(statement)
    con.execute(f"PRAGMA user_version={USER_VERSION}")
    con.execute(
        "INSERT INTO ledger VALUES(1,?,?,?,?,?,?)",
        (
            meta["ledger_uuid"],
            FORMAT_VERSION,
            meta["created_at"],
            meta["writer_version"],
            meta["writer_revision"],
            0,
        ),
    )
    register_site(
        con,
        project_uuid=meta["project_uuid"],
        target=meta["site_target"],
        role="primary",
        created_at=meta["created_at"],
    )


_MIGRATIONS = {0: _migrate_0_to_1}


def _migrate(con: sqlite3.Connection) -> None:
    """Apply known migrations inside the caller's write transaction.

    Only a write-mode open reaches here: reading never mutates the file, an
    unknown/newer version was refused before this point, and a raised migration
    error leaves the caller's transaction to roll the file back unchanged.
    """
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < 0 or version > USER_VERSION:
        raise LedgerError(f"unsupported ledger user_version {version}")
    while version < USER_VERSION:
        migration = _MIGRATIONS.get(version)
        if migration is None:
            raise LedgerError(f"no ledger migration path from version {version}")
        migration(con)
        new_version = con.execute("PRAGMA user_version").fetchone()[0]
        if new_version != version + 1:
            raise LedgerError(f"ledger migration {version} did not advance the version")
        version = new_version


def open_ledger(path: str | Path, *, write: bool = False) -> sqlite3.Connection:
    """Return a validated ledger connection; the caller must close it.

    A read open never mutates the artifact.  A write open is the explicit
    operation allowed to migrate a known older version, inside one transaction;
    a newer/unknown version refuses without touching the file.
    """
    _runtime()
    path = Path(path)
    con = None
    try:
        con = _writer(path) if write else _reader(path)
        app_id = con.execute("PRAGMA application_id").fetchone()[0]
        if app_id != APPLICATION_ID:
            raise LedgerError(f"foreign application_id {app_id}; expected {APPLICATION_ID} (SEOL)")
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if write:
            if version > USER_VERSION or version < 0:
                raise LedgerError(
                    f"unsupported ledger user_version {version}; this build knows {USER_VERSION}"
                )
            if version != USER_VERSION:
                con.execute("BEGIN IMMEDIATE")
                try:
                    _migrate(con)
                    # Validation stays inside the transaction: a migration that
                    # produced an inconsistent ledger rolls back untouched.
                    _validate(con)
                    con.commit()
                except BaseException:
                    con.rollback()
                    raise
                return con
        elif version != USER_VERSION:
            raise LedgerError(
                f"unsupported ledger user_version {version}; this build knows {USER_VERSION}"
            )
        _validate(con)
        return con
    except (OSError, sqlite3.Error, ValueError) as exc:
        if con is not None:
            con.close()
        if isinstance(exc, LedgerError):
            raise
        raise LedgerError(f"cannot read ledger: {exc}") from exc


def create_ledger(path: str | Path, *, project_dir: str | Path, producer_build: str) -> Path:
    """Create one new empty ledger bound to a validated project; never overwrites."""
    _runtime()
    if not isinstance(producer_build, str) or not _REVISION.fullmatch(producer_build):
        raise LedgerError(
            "producer_build must identify the ledger-writing build with a full lowercase Git SHA"
        )
    from seohead import __version__
    from seohead.projects.workspace import open_project

    project = open_project(project_dir)["project"]
    out = Path(path).absolute()
    if os.path.lexists(out):
        raise LedgerError(f"ledger already exists: {out}; ledgers never overwrite")
    if not out.parent.is_dir():
        raise LedgerError(f"ledger parent directory does not exist: {out.parent}")
    temporary = None
    con = None
    try:
        fd, name = tempfile.mkstemp(prefix=".ledger-", suffix=".sqlite", dir=out.parent)
        os.close(fd)
        temporary = Path(name)
        con = sqlite3.connect(temporary)
        con.row_factory = sqlite3.Row
        con.executescript(_schema())
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("BEGIN")
        now = _utc()
        con.execute(
            "INSERT INTO ledger VALUES(1,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                FORMAT_VERSION,
                now,
                __version__,
                producer_build,
                0,
            ),
        )
        register_site(
            con,
            project_uuid=project["project_uuid"],
            target=project["site"]["target"],
            role="primary",
            created_at=now,
        )
        con.commit()
        con.close()
        con = None
        check = open_ledger(temporary)
        check.close()
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, out)
        from seohead.filesystem import fsync_directory

        fsync_directory(out.parent)
        return out
    except FileExistsError as exc:
        raise LedgerError(f"ledger already exists: {out}; ledgers never overwrite") from exc
    except (OSError, sqlite3.Error, ValueError) as exc:
        if isinstance(exc, LedgerError):
            raise
        raise LedgerError(f"cannot create ledger: {exc}") from exc
    finally:
        if con is not None:
            con.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def register_site(
    con: sqlite3.Connection,
    *,
    project_uuid: str,
    target: str,
    role: str = "secondary",
    created_at: str | None = None,
) -> int:
    """Register one site identity on an open write connection.

    ``target`` is normalized with the same ``seohead.project.v1`` site-target
    rules the project workspace applies; scans bind by exact ``netloc`` match,
    the convention saved-evidence validation already uses.  A repeat call with
    an identical (project_uuid, netloc) pair returns the existing row.
    """
    from seohead.projects.workspace import _target as _normalize_target
    from seohead.recon.net import normalize_domain

    try:
        normalized_uuid = str(uuid.UUID(project_uuid))
    except (TypeError, ValueError, AttributeError) as exc:
        raise LedgerError("site registration requires a project UUID") from exc
    if role not in {"primary", "secondary"}:
        raise LedgerError("site role must be 'primary' or 'secondary'")
    normalized = _normalize_target(target)
    parts = urlsplit(normalized)
    host = normalize_domain(parts.hostname or "")
    netloc = parts.netloc.lower()
    if not host or not netloc:
        raise LedgerError("site registration requires a public hostname")
    row = con.execute(
        "SELECT site_id,target,host,role FROM site WHERE project_uuid=? AND netloc=?",
        (normalized_uuid, netloc),
    ).fetchone()
    if row is not None:
        if row["target"] != normalized or row["host"] != host:
            raise LedgerError("site identity conflicts with a registered row")
        return int(row["site_id"])
    con.execute(
        "INSERT INTO site(project_uuid,target,host,netloc,role,created_at) VALUES(?,?,?,?,?,?)",
        (normalized_uuid, normalized, host, netloc, role, created_at or _utc()),
    )
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])


def _representation_map(scan_con, document: dict[str, Any]) -> dict[str, str]:
    """URL -> recorded representation, the audit document's claim first."""
    result: dict[str, str] = {}
    try:
        for row in scan_con.execute(
            "SELECT u.url,p.representation FROM pages p JOIN urls u USING(url_id)"
        ):
            if isinstance(row[1], str) and row[1]:
                result[canonical_url(row[0])] = row[1]
    except (sqlite3.Error, LedgerError):
        pass
    for page in document.get("pages") or []:
        if not isinstance(page, dict):
            continue
        metrics = page.get("metrics") if isinstance(page.get("metrics"), dict) else {}
        value = metrics.get("representation")
        url = page.get("url")
        if isinstance(url, str) and isinstance(value, str) and value:
            try:
                result[canonical_url(url)] = value
            except LedgerError:
                continue
    return result


def _observation_index(scan_con) -> tuple[dict[str, set[str]], dict[int, str]]:
    """Map each saved page URL to the typed observation ids it owns.

    ``observation_id`` values in the evidence contract are ``url_id:N``,
    ``document_id:N``, ``response_id:N`` or ``language_evidence:...:document:N``.
    Resolving them to URLs lets one occurrence name the retained evidence for
    its own affected page instead of borrowing another page's.
    """
    by_url: dict[str, set[str]] = {}
    doc_url: dict[int, str] = {}
    try:
        rows = scan_con.execute(
            "SELECT u.url,p.url_id,p.document_id,d.source_response_id "
            "FROM pages p JOIN urls u USING(url_id) "
            "LEFT JOIN documents d ON d.document_id=p.document_id"
        )
    except sqlite3.Error:
        return by_url, doc_url
    for url, url_id, document_id, source_response_id in rows:
        try:
            canon = canonical_url(url)
        except LedgerError:
            continue
        owned = by_url.setdefault(canon, set())
        owned.add(f"url_id:{url_id}")
        if type(document_id) is int and document_id > 0:
            owned.add(f"document_id:{document_id}")
            doc_url[document_id] = canon
        if type(source_response_id) is int and source_response_id > 0:
            owned.add(f"response_id:{source_response_id}")
    return by_url, doc_url


_DOC_MARKER = re.compile(r":document:(\d+):representation:")


def _occurrence_evidence(
    contract: dict[str, Any],
    *,
    role: str,
    url: str | None,
    by_url: dict[str, set[str]],
    doc_url: dict[int, str],
) -> dict[str, Any]:
    """Slice the issue-level contract refs down to this occurrence's own page."""
    observations = contract.get("observations") if isinstance(contract, dict) else None
    if not isinstance(observations, list) or not observations:
        reason = (
            contract.get("reason")
            if isinstance(contract, dict) and isinstance(contract.get("reason"), str)
            else "no saved evidence contract is present"
        )
        return {
            "state": "unavailable",
            "reason": reason or "no retained observation for this affected URL",
            "references": [],
        }
    refs: list[dict[str, Any]] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        observation_id = item.get("observation_id")
        if not isinstance(observation_id, str):
            continue
        if role == "target":
            if item.get("role") == "target":
                refs.append(item)
                continue
            match = _DOC_MARKER.search(observation_id)
            if match and doc_url.get(int(match.group(1))) == url:
                refs.append(item)
        elif url is not None and observation_id in by_url.get(url, set()):
            refs.append(item)
    if not refs:
        return {
            "state": "unavailable",
            "reason": "no retained observation for this affected URL",
            "references": [],
        }
    state = refs[0].get("state")
    return {
        "state": state if state in {"measured", "imported_projection"} else "unavailable",
        "reason": ""
        if state in {"measured", "imported_projection"}
        else "retained observation state is unknown",
        "references": [
            {
                key: item[key]
                for key in ("id", "source_table", "observation_id", "role", "representation")
                if key in item
            }
            for item in refs
        ],
    }


def _coverage_state(issue: dict[str, Any], locations: list) -> tuple[str, int]:
    """Classify how much of a reported total the audit enumerated.

    ``enumerated`` means the saved document names the whole reported total;
    ``capped`` means the audit kept only a bounded location list;
    ``aggregate_only`` means repeated locations lack enough stable locator
    data to distinguish their events. ``unknown`` means the document does not
    state a usable total. The enumerated count is stable location events, not
    distinct affected URLs; those memberships are stored separately.
    """
    reported = issue.get("occurrences_count")
    entries = _location_entries(locations)
    by_url: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_url.setdefault(entry["url"], []).append(entry)
    locator_groups = _locator_groups(issue, entries)
    enumerated = sum(len(locator_groups[url]) if url in locator_groups else 1 for url in by_url)
    if not locations and isinstance(issue.get("target_url"), str):
        enumerated = 1
    if type(reported) is not int or reported < 0:
        return "unknown", enumerated
    if not locations:
        return ("enumerated" if reported <= enumerated else "aggregate_only"), enumerated
    if reported > len(locations):
        return "capped", min(reported, enumerated)
    if reported > enumerated:
        return "aggregate_only", enumerated
    return "enumerated", min(reported, enumerated)


def _check_id(con, check_key: str, refuse_new: bool) -> int:
    if not isinstance(check_key, str) or not check_key or len(check_key) > 128:
        raise LedgerError("an audit issue requires a bounded check identifier")
    row = con.execute("SELECT check_id FROM check_def WHERE check_key=?", (check_key,)).fetchone()
    if row is not None:
        return int(row[0])
    if refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    con.execute("INSERT INTO check_def(check_key) VALUES(?)", (check_key,))
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])


def _insert_finding(
    con,
    *,
    site_id: int,
    check_id: int,
    check_key: str,
    project_uuid: str,
    site_host: str,
    subject_type: str,
    subject_value: str,
    now: str,
    refuse_new: bool,
) -> tuple[int, bool, str]:
    key = finding_key(
        project_uuid=project_uuid,
        site_host=site_host,
        check=check_key,
        subject_type=subject_type,
        subject_value=subject_value,
    )
    row = con.execute("SELECT finding_id FROM finding WHERE finding_key=?", (key,)).fetchone()
    if row is not None:
        return int(row[0]), False, key
    if refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    con.execute(
        "INSERT INTO finding(site_id,check_id,subject_type,subject_value,finding_key,first_seen_at)"
        " VALUES(?,?,?,?,?,?)",
        (site_id, check_id, subject_type, subject_value, key, now),
    )
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0]), True, key


def _insert_occurrence(
    con,
    *,
    finding_id: int,
    check_id: int,
    check_key: str,
    project_uuid: str,
    site_host: str,
    subject_type: str,
    subject_value: str,
    representation: str,
    discriminator_type: str,
    discriminator_value: str,
    now: str,
    refuse_new: bool,
) -> tuple[int, bool, str]:
    if representation not in _REPRESENTATIONS:
        raise LedgerError(f"unsupported occurrence representation {representation!r}")
    key = occurrence_key(
        project_uuid=project_uuid,
        site_host=site_host,
        check=check_key,
        subject_type=subject_type,
        subject_value=subject_value,
        representation=representation,
        discriminator_type=discriminator_type,
        discriminator_value=discriminator_value,
    )
    row = con.execute(
        "SELECT occurrence_id FROM occurrence WHERE occurrence_key=?", (key,)
    ).fetchone()
    if row is not None:
        return int(row[0]), False, key
    if refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    con.execute(
        "INSERT INTO occurrence(finding_id,check_id,subject_type,subject_value,representation,"
        "discriminator_type,discriminator_value,occurrence_key,first_seen_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            finding_id,
            check_id,
            subject_type,
            subject_value,
            representation,
            discriminator_type,
            discriminator_value,
            key,
            now,
        ),
    )
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0]), True, key


def _insert_member(
    con, *, finding_id: int, url: str, role: str, now: str, refuse_new: bool
) -> bool:
    cursor = con.execute(
        "INSERT OR IGNORE INTO affected_url(finding_id,url,role,first_seen_at) VALUES(?,?,?,?)",
        (finding_id, url, role, now),
    )
    created = cursor.rowcount > 0
    if created and refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    return created


def _insert_projection(
    con, *, finding_id: int, source_scan_id: int, row: dict[str, Any], refuse_new: bool
) -> bool:
    existing = con.execute(
        "SELECT payload_sha256 FROM finding_observation "
        "WHERE finding_id=? AND source_scan_id=? AND issue_ordinal=?",
        (finding_id, source_scan_id, row["issue_ordinal"]),
    ).fetchone()
    if existing is not None:
        if existing[0] != row["payload_sha256"]:
            raise LedgerError("conflicting finding projection under an identical source revision")
        return False
    if refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    con.execute(
        "INSERT INTO finding_observation(finding_id,source_scan_id,issue_ordinal,"
        "audit_fingerprint,severity,status_code,message,occurrences_count,enumerated_count,"
        "coverage_state,payload_json,payload_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            finding_id,
            source_scan_id,
            row["issue_ordinal"],
            row["audit_fingerprint"],
            row["severity"],
            row["status_code"],
            row["message"],
            row["occurrences_count"],
            row["enumerated_count"],
            row["coverage_state"],
            row["payload_json"],
            row["payload_sha256"],
        ),
    )
    return True


def _insert_group(
    con, *, finding_id: int, source_scan_id: int, row: dict[str, Any], refuse_new: bool
) -> bool:
    existing = con.execute(
        "SELECT group_check,group_value,group_count,group_urls_json FROM finding_group "
        "WHERE finding_id=? AND source_scan_id=? AND group_ref=?",
        (finding_id, source_scan_id, row["group_ref"]),
    ).fetchone()
    if existing is not None:
        stored = {
            "group_check": existing["group_check"],
            "group_value": existing["group_value"],
            "group_count": existing["group_count"],
            "group_urls_json": existing["group_urls_json"],
        }
        presented = {name: row[name] for name in stored}
        if stored != presented:
            raise LedgerError("conflicting group membership under an identical source revision")
        return False
    if refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    con.execute(
        "INSERT INTO finding_group(finding_id,source_scan_id,group_ref,group_check,group_value,"
        "group_count,group_urls_json) VALUES(?,?,?,?,?,?,?)",
        (
            finding_id,
            source_scan_id,
            row["group_ref"],
            row["group_check"],
            row["group_value"],
            row["group_count"],
            row["group_urls_json"],
        ),
    )
    return True


def _insert_observation(
    con, *, occurrence_id: int, source_scan_id: int, row: dict[str, Any], refuse_new: bool
) -> bool:
    existing = con.execute(
        "SELECT payload_sha256 FROM observation "
        "WHERE occurrence_id=? AND source_scan_id=? AND issue_ordinal=?",
        (occurrence_id, source_scan_id, row["issue_ordinal"]),
    ).fetchone()
    if existing is not None:
        if existing[0] != row["payload_sha256"]:
            raise LedgerError("conflicting observation bytes under an identical source revision")
        return False
    if refuse_new:
        raise LedgerError("conflicting evidence under a previously bound source revision")
    revision = con.execute(
        "SELECT COALESCE(MAX(observation_revision),0)+1 FROM observation WHERE occurrence_id=?",
        (occurrence_id,),
    ).fetchone()[0]
    con.execute(
        "INSERT INTO observation(occurrence_id,source_scan_id,observation_revision,role,"
        "issue_ordinal,evidence_state,evidence_reason,evidence_json,payload_sha256,"
        "config_fingerprint,producer_version,producer_revision,observed_at,observed_at_state,"
        "ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            occurrence_id,
            source_scan_id,
            revision,
            row["role"],
            row["issue_ordinal"],
            row["evidence_state"],
            row["evidence_reason"],
            row["evidence_json"],
            row["payload_sha256"],
            row["config_fingerprint"],
            row["producer_version"],
            row["producer_revision"],
            row["observed_at"],
            row["observed_at_state"],
            row["ingested_at"],
        ),
    )
    return True


def _affected_urls(issue: dict[str, Any]) -> list[tuple[str, str]]:
    """Ordered (canonical URL, role) members; duplicates collapse, roles may both persist."""
    members: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(url: Any, role: str) -> None:
        if not isinstance(url, str) or not url.strip():
            return
        canon = canonical_url(url)
        if (canon, role) not in seen:
            seen.add((canon, role))
            members.append((canon, role))

    add(issue.get("target_url"), "subject")
    locations = issue.get("locations")
    if isinstance(locations, list):
        for location in locations:
            if not isinstance(location, dict):
                continue
            add(location.get("source_url"), "location")
            add(location.get("url"), "location")
    return members


def _location_entries(locations: list[Any]) -> list[dict[str, Any]]:
    """Return the stable source/page locator data carried by audit locations."""
    entries = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        raw_url = location.get("source_url") or location.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        try:
            url = canonical_url(raw_url)
        except LedgerError:
            continue
        path = location.get("link_path")
        entries.append(
            {
                "url": url,
                "link_path": path.strip()
                if isinstance(path, str) and path.strip() and len(path) <= 2048
                else None,
                "representation": location.get("representation"),
            }
        )
    return entries


def _locator_groups(
    issue: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Return repeated URL locations whose link paths make each occurrence stable."""
    reported = issue.get("occurrences_count")
    by_url: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_url.setdefault(entry["url"], []).append(entry)
    if type(reported) is not int or reported <= len(by_url):
        return {}
    return {
        url: rows
        for url, rows in by_url.items()
        if len(rows) > 1
        and all(row["link_path"] for row in rows)
        and len({row["link_path"] for row in rows}) == len(rows)
    }


def _locator_discriminator(subject_value: str, url: str, link_path: str) -> str:
    return "link_path:" + _key("seohead.ledger-link-path.v1", subject_value, url, link_path)


def _occurrence_specs(
    issue: dict[str, Any],
    *,
    subject_type: str,
    subject_value: str,
    members: list[tuple[str, str]],
    representations: dict[str, str],
) -> list[dict[str, Any]]:
    """Derive the occurrence set for one saved issue.

    The primary occurrence sits at the finding's own subject; every other
    distinct affected URL gets an occurrence discriminated by that subject, so
    two findings of one check landing on the same page stay two cases.  A
    location naming the subject itself merges into the primary occurrence.
    """

    def repr_for(url: str) -> str:
        value = issue.get("representation") or representations.get(url)
        if not isinstance(value, str) or not value:
            return "unknown"
        return value

    locations = issue.get("locations") if isinstance(issue.get("locations"), list) else []
    entries = _location_entries(locations)
    locator_groups = _locator_groups(issue, entries)
    specs = []
    if subject_type == "scope":
        specs.append(
            {
                "subject_type": "scope",
                "subject_value": _SCOPE_SUBJECT,
                "representation": "scope",
                "discriminator_type": "primary",
                "discriminator_value": "",
                "role": "target",
            }
        )
    elif subject_value in locator_groups:
        for entry in locator_groups[subject_value]:
            representation = entry["representation"]
            if representation not in _REPRESENTATIONS:
                representation = repr_for(subject_value)
            specs.append(
                {
                    "subject_type": "url",
                    "subject_value": subject_value,
                    "representation": representation,
                    "discriminator_type": "locator",
                    "discriminator_value": _locator_discriminator(
                        subject_value, subject_value, entry["link_path"]
                    ),
                    "role": "target",
                }
            )
    else:
        specs.append(
            {
                "subject_type": "url",
                "subject_value": subject_value,
                "representation": repr_for(subject_value),
                "discriminator_type": "primary",
                "discriminator_value": "",
                "role": "target",
            }
        )
    for url, _member_role in members:
        if subject_type == "url" and url == subject_value:
            continue
        if url in locator_groups:
            for entry in locator_groups[url]:
                representation = entry["representation"]
                if representation not in _REPRESENTATIONS:
                    representation = repr_for(url)
                specs.append(
                    {
                        "subject_type": "url",
                        "subject_value": url,
                        "representation": representation,
                        "discriminator_type": "locator",
                        "discriminator_value": _locator_discriminator(
                            subject_value, url, entry["link_path"]
                        ),
                        "role": "source",
                    }
                )
        else:
            specs.append(
                {
                    "subject_type": "url",
                    "subject_value": url,
                    "representation": repr_for(url),
                    "discriminator_type": "subject",
                    "discriminator_value": subject_value,
                    "role": "source",
                }
            )
    return specs


def _bind_source(
    con,
    *,
    site_id: int,
    scan: dict[str, Any],
    audit_row: dict[str, Any],
    file_sha256: str,
    now: str,
) -> tuple[int, bool, bool, bool]:
    """Bind one validated source revision; refuse an inconsistent reused identity.

    Returns ``(source_scan_id, created, bytes_differ, restored)``.
    ``bytes_differ`` marks a same-revision binding whose container bytes differ
    from the first seen digest -- legitimate after pin/copy -- where the ingest
    must reproduce the recorded content exactly or fail rather than mint
    alternate truth.  ``restored`` records a bound artifact marked ``missing``
    that a successful re-ingest proved present again.
    """
    row = con.execute(
        "SELECT * FROM source_scan WHERE scan_uuid=? AND evidence_revision=?",
        (scan["scan_uuid"], scan["evidence_revision"]),
    ).fetchone()
    fields = {
        "site_id": site_id,
        "scan_uuid": scan["scan_uuid"],
        "format_version": scan["format_version"],
        "source_kind": scan["source_kind"],
        "lifecycle": scan["lifecycle"],
        "evidence_revision": scan["evidence_revision"],
        "audit_sha256": audit_row["sha256"],
        "audit_schema_version": audit_row["schema_version"],
        "audit_created_at": audit_row["created_at"],
        "config_fingerprint": scan["config_fingerprint"],
        "writer_version": scan["writer_version"],
        "writer_revision": scan["writer_revision"],
        "analyzer_version": audit_row["analyzer_version"],
        "analyzer_revision": audit_row["analyzer_revision"],
        "scan_sha256": file_sha256,
        "crawl_partial": int(bool(scan["crawl_partial"])),
        "corpus_partial": int(bool(scan["corpus_partial"])),
        "artifact_state": "present",
        "missing_reason": "",
        "ingested_at": now,
    }
    if row is None:
        con.execute(
            "INSERT INTO source_scan(site_id,scan_uuid,format_version,source_kind,lifecycle,"
            "evidence_revision,audit_sha256,audit_schema_version,audit_created_at,"
            "config_fingerprint,writer_version,writer_revision,analyzer_version,"
            "analyzer_revision,scan_sha256,crawl_partial,corpus_partial,artifact_state,"
            "missing_reason,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fields["site_id"],
                fields["scan_uuid"],
                fields["format_version"],
                fields["source_kind"],
                fields["lifecycle"],
                fields["evidence_revision"],
                fields["audit_sha256"],
                fields["audit_schema_version"],
                fields["audit_created_at"],
                fields["config_fingerprint"],
                fields["writer_version"],
                fields["writer_revision"],
                fields["analyzer_version"],
                fields["analyzer_revision"],
                fields["scan_sha256"],
                fields["crawl_partial"],
                fields["corpus_partial"],
                fields["artifact_state"],
                fields["missing_reason"],
                fields["ingested_at"],
            ),
        )
        return int(con.execute("SELECT last_insert_rowid()").fetchone()[0]), True, False, False
    stored = dict(row)
    for name in (
        "site_id",
        "format_version",
        "source_kind",
        "lifecycle",
        "audit_sha256",
        "audit_schema_version",
        "config_fingerprint",
        "writer_version",
        "writer_revision",
        "analyzer_version",
        "analyzer_revision",
        "crawl_partial",
        "corpus_partial",
    ):
        if stored[name] != fields[name]:
            raise LedgerError(
                f"source scan {scan['scan_uuid']} revision {scan['evidence_revision']} "
                f"was already bound with different {name}; refusing alternate truth"
            )
    restored = stored["artifact_state"] == "missing"
    if restored:
        con.execute(
            "UPDATE source_scan SET artifact_state='present',missing_reason='' WHERE source_scan_id=?",
            (stored["source_scan_id"],),
        )
    return (
        int(stored["source_scan_id"]),
        False,
        stored["scan_sha256"] != file_sha256,
        restored,
    )


def ingest_scan(ledger: str | Path | sqlite3.Connection, scan_path: str | Path) -> dict[str, Any]:
    """Ingest one validated saved audit as baseline/history without rerunning it.

    The source artifact is opened strictly read-only through :func:`open_scan`:
    ingest never writes to the scan, never upgrades ``scan.v1`` to ``scan.v2``,
    and leaves its ``user_version`` and bytes untouched.  Re-ingesting the same
    source revision is idempotent; a new revision appends observation history.
    ``ledger`` may be a path (opened write-mode for this call) or an already
    open write connection, which receives the whole ingest in one transaction.
    """
    path = Path(scan_path)
    digest = _sha256_file(path)
    scan_con = open_scan(path)
    try:
        scan_row = scan_con.execute("SELECT * FROM scan WHERE singleton=1").fetchone()
        audit = scan_con.execute("SELECT * FROM audit WHERE singleton=1").fetchone()
        if audit is None:
            raise LedgerError("source scan has no saved audit to ingest")
        scan, audit_row = dict(scan_row), dict(audit)
        document = _loads(audit_row["document_json"], "audit")
        from seohead.sf.core.evidence_contract import attach_contract

        projected = attach_contract(document, scan_uuid=scan["scan_uuid"], con=scan_con)
        representations = _representation_map(scan_con, document)
        by_url, doc_url = _observation_index(scan_con)
    finally:
        scan_con.close()
    run = document.get("run") if isinstance(document.get("run"), dict) else {}
    document_uuid = run.get("scan_uuid")
    # A native/reanalysis audit's own scan_uuid must agree with its container;
    # a legacy import carries the *original* run's uuid as provenance while the
    # binding uuid was minted at import time, so it is compared as data instead.
    if (
        scan["source_kind"] != "legacy_import"
        and isinstance(document_uuid, str)
        and document_uuid
        and document_uuid != scan["scan_uuid"]
    ):
        raise LedgerError("saved audit scan UUID disagrees with its scan header")
    start = scan.get("start_url") or run.get("start_url") or run.get("source")
    netloc = urlsplit(start).netloc.lower() if isinstance(start, str) and start else ""
    if not netloc:
        raise LedgerError("source scan does not record the site it belongs to")
    issues = projected.get("issues") if isinstance(projected.get("issues"), list) else []
    raw_issues = document.get("issues") if isinstance(document.get("issues"), list) else []
    groups = {
        group.get("group_id"): group
        for group in (document.get("groups") or [])
        if isinstance(group, dict)
    }
    observed_at = _validated_time(run.get("generated_at"))

    own = not isinstance(ledger, sqlite3.Connection)
    con = open_ledger(ledger, write=True) if own else ledger
    try:
        site = _site_row(con, netloc)
        if site is None:
            raise LedgerError(
                f"scan site {netloc!r} does not match any registered ledger site identity"
            )
        site_uuid, site_host = site["project_uuid"], site["host"]
        now = _utc()
        con.execute("BEGIN IMMEDIATE")
        counts = {
            "findings": 0,
            "occurrences": 0,
            "affected_urls": 0,
            "finding_observations": 0,
            "group_memberships": 0,
            "observations": 0,
        }
        source_scan_id, new_source, bytes_differ, restored = _bind_source(
            con,
            site_id=int(site["site_id"]),
            scan=scan,
            audit_row=audit_row,
            file_sha256=digest,
            now=now,
        )
        # Once a source revision is bound, a replay must reproduce its recorded
        # rows exactly: a new row under a bound revision means the same claimed
        # identity now presents different content, which is refused, not merged.
        refuse_new = not new_source
        for ordinal, projected_issue in enumerate(issues):
            if not isinstance(projected_issue, dict):
                raise LedgerError("saved audit issue is not an object")
            issue = raw_issues[ordinal] if ordinal < len(raw_issues) else projected_issue
            check_key = projected_issue.get("check")
            check_id = _check_id(con, check_key, refuse_new)
            target = projected_issue.get("target_url")
            if isinstance(target, str) and target.strip():
                subject_type, subject_value = "url", canonical_url(target)
            else:
                subject_type, subject_value = "scope", _SCOPE_SUBJECT
            finding_id, created, _ = _insert_finding(
                con,
                site_id=int(site["site_id"]),
                check_id=check_id,
                check_key=check_key,
                project_uuid=site_uuid,
                site_host=site_host,
                subject_type=subject_type,
                subject_value=subject_value,
                now=now,
                refuse_new=refuse_new,
            )
            counts["findings"] += int(created)
            members = _affected_urls(projected_issue)
            for url, role in members:
                counts["affected_urls"] += _insert_member(
                    con,
                    finding_id=finding_id,
                    url=url,
                    role=role,
                    now=now,
                    refuse_new=refuse_new,
                )
            contract = (
                projected_issue.get("evidence", {}).get("contract", {})
                if isinstance(projected_issue.get("evidence"), dict)
                else {}
            )
            specs = _occurrence_specs(
                projected_issue,
                subject_type=subject_type,
                subject_value=subject_value,
                members=members,
                representations=representations,
            )
            locations = (
                projected_issue.get("locations")
                if isinstance(projected_issue.get("locations"), list)
                else []
            )
            coverage_state, enumerated = _coverage_state(projected_issue, locations)
            payload = _dump(issue)
            if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise LedgerError("saved issue projection exceeds the ledger payload bound")
            counts["finding_observations"] += _insert_projection(
                con,
                finding_id=finding_id,
                source_scan_id=source_scan_id,
                refuse_new=refuse_new,
                row={
                    "issue_ordinal": str(projected_issue.get("id") or ""),
                    "audit_fingerprint": projected_issue.get("fingerprint")
                    if isinstance(projected_issue.get("fingerprint"), str)
                    else None,
                    "severity": str(projected_issue.get("severity") or ""),
                    "status_code": projected_issue.get("status_code")
                    if type(projected_issue.get("status_code")) is int
                    else None,
                    "message": str(projected_issue.get("message") or ""),
                    "occurrences_count": projected_issue.get("occurrences_count")
                    if type(projected_issue.get("occurrences_count")) is int
                    else 0,
                    "enumerated_count": enumerated,
                    "coverage_state": coverage_state,
                    "payload_json": payload,
                    "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                },
            )
            group_ref = projected_issue.get("group_id")
            if isinstance(group_ref, str) and group_ref:
                group = groups.get(group_ref) or {}
                counts["group_memberships"] += _insert_group(
                    con,
                    finding_id=finding_id,
                    source_scan_id=source_scan_id,
                    refuse_new=refuse_new,
                    row={
                        "group_ref": group_ref,
                        "group_check": str(group.get("check") or check_key),
                        "group_value": group.get("value")
                        if isinstance(group.get("value"), str)
                        else None,
                        "group_count": group.get("count")
                        if type(group.get("count")) is int
                        else None,
                        "group_urls_json": _dump(group.get("urls") or []),
                    },
                )
            for spec in specs:
                occurrence_id, created, _ = _insert_occurrence(
                    con,
                    finding_id=finding_id,
                    check_id=check_id,
                    check_key=check_key,
                    project_uuid=site_uuid,
                    site_host=site_host,
                    now=now,
                    refuse_new=refuse_new,
                    **{name: value for name, value in spec.items() if name != "role"},
                )
                counts["occurrences"] += int(created)
                evidence = _occurrence_evidence(
                    contract,
                    role=spec["role"],
                    url=spec["subject_value"] if spec["subject_type"] == "url" else None,
                    by_url=by_url,
                    doc_url=doc_url,
                )
                evidence_json = _dump(evidence)
                occurrence_payload = _dump(
                    {
                        "occurrence": spec,
                        "issue_ordinal": str(projected_issue.get("id") or ""),
                        "severity": projected_issue.get("severity"),
                        "status_code": projected_issue.get("status_code"),
                        "message": projected_issue.get("message"),
                        "occurrences_count": projected_issue.get("occurrences_count"),
                        "coverage_state": coverage_state,
                        "group_ref": group_ref if isinstance(group_ref, str) else None,
                        "evidence": evidence,
                    }
                )
                counts["observations"] += _insert_observation(
                    con,
                    occurrence_id=occurrence_id,
                    source_scan_id=source_scan_id,
                    refuse_new=refuse_new,
                    row={
                        "role": spec["role"],
                        "issue_ordinal": str(projected_issue.get("id") or ""),
                        "evidence_state": evidence["state"],
                        "evidence_reason": evidence["reason"],
                        "evidence_json": evidence_json,
                        "payload_sha256": hashlib.sha256(
                            occurrence_payload.encode("utf-8")
                        ).hexdigest(),
                        "config_fingerprint": scan["config_fingerprint"],
                        "producer_version": audit_row["analyzer_version"],
                        "producer_revision": audit_row["analyzer_revision"],
                        "observed_at": observed_at,
                        "observed_at_state": "known" if observed_at else "unknown",
                        "ingested_at": now,
                    },
                )
        changed = new_source or restored or any(counts.values())
        if changed:
            con.execute("UPDATE ledger SET ledger_revision=ledger_revision+1 WHERE singleton=1")
        revision = con.execute("SELECT ledger_revision FROM ledger WHERE singleton=1").fetchone()[0]
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        if own:
            con.close()
    return {
        "ok": True,
        "ledger_revision": int(revision),
        "site_id": int(site["site_id"]),
        "site_netloc": netloc,
        "source_scan_id": source_scan_id,
        "scan_uuid": scan["scan_uuid"],
        "format_version": scan["format_version"],
        "evidence_revision": scan["evidence_revision"],
        "audit_sha256": audit_row["sha256"],
        "new_source": new_source,
        "already_recorded": not changed,
        "scan_bytes_differ": bytes_differ,
        "recorded": counts,
    }


def note_source_missing(
    ledger: str | Path | sqlite3.Connection, source_scan_id: int, *, reason: str
) -> dict[str, Any]:
    """Record that a bound source artifact is gone (pruned or moved away).

    Only the availability flag changes: the binding, digests and observation
    history stay so every recorded case still cites its original evidence.
    """
    if not isinstance(reason, str) or not reason or len(reason) > 512:
        raise LedgerError("a missing-source note requires a bounded reason")
    own = not isinstance(ledger, sqlite3.Connection)
    con = open_ledger(ledger, write=True) if own else ledger
    try:
        row = con.execute(
            "SELECT source_scan_id,artifact_state FROM source_scan WHERE source_scan_id=?",
            (source_scan_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("unknown source scan binding")
        con.execute("BEGIN IMMEDIATE")
        try:
            if row["artifact_state"] != "missing":
                con.execute(
                    "UPDATE source_scan SET artifact_state='missing',missing_reason=? "
                    "WHERE source_scan_id=?",
                    (reason, source_scan_id),
                )
                con.execute("UPDATE ledger SET ledger_revision=ledger_revision+1 WHERE singleton=1")
            revision = con.execute(
                "SELECT ledger_revision FROM ledger WHERE singleton=1"
            ).fetchone()[0]
            con.commit()
        except BaseException:
            con.rollback()
            raise
    finally:
        if own:
            con.close()
    return {"ok": True, "ledger_revision": int(revision)}


def _lifecycle_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise LedgerError(f"{name} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _finding_state(con: sqlite3.Connection, finding_id: int) -> str:
    """Project exact occurrence states to one conservative finding state.

    A finding is resolved only when every retained occurrence is resolved.
    Mixed evidence is never flattened to resolved; the most actionable retained
    state remains visible to a page that lists findings.
    """
    states = {
        row[0]
        for row in con.execute(
            "SELECT current_state FROM occurrence WHERE finding_id=?", (finding_id,)
        )
    }
    if not states:
        return "detected"
    if states == {"resolved"}:
        return "resolved"
    if "regressed" in states:
        return "regressed"
    if "persisting" in states:
        return "persisting"
    if "recheck_pending" in states:
        return "recheck_pending"
    if states == {"false_positive_reviewed"}:
        return "false_positive_reviewed"
    if "unverifiable" in states:
        return "unverifiable"
    if "fix_reported" in states:
        return "fix_reported"
    if "verified" in states:
        return "verified"
    return "detected"


def _outcome_observation(
    con: sqlite3.Connection, occurrence_id: int, observation_id: int | None
) -> int | None:
    """Validate an evidence reference used for a measured lifecycle outcome."""
    if observation_id is None:
        raise LedgerError("a measured lifecycle outcome requires an observation_id")
    if type(observation_id) is not int or observation_id < 1:
        raise LedgerError("observation_id must be a positive integer")
    row = con.execute(
        "SELECT o.observation_id,o.observation_revision,o.evidence_state,s.artifact_state "
        "FROM observation o JOIN source_scan s ON s.source_scan_id=o.source_scan_id "
        "WHERE o.observation_id=? AND o.occurrence_id=?",
        (observation_id, occurrence_id),
    ).fetchone()
    if row is None:
        raise LedgerError("observation_id does not belong to this occurrence")
    if row["evidence_state"] != "measured" or row["artifact_state"] != "present":
        raise LedgerError("measured lifecycle outcomes require retained measured evidence")
    if row["observation_revision"] <= 1:
        raise LedgerError(
            "measured lifecycle outcomes require a later observation, not baseline evidence"
        )
    return int(row["observation_id"])


def transition_occurrence(
    ledger: str | Path | sqlite3.Connection,
    *,
    occurrence_key: str,
    state: str,
    actor: str,
    reason: str,
    expected_revision: int,
    observation_id: int | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    """Append one validated, revision-safe lifecycle decision for a case.

    The caller supplies the ledger revision read from ``ledger_summary`` or
    ``read_cases``.  A stale writer is refused before mutation.  In particular,
    a user assertion cannot resolve a finding: ``resolved``, ``persisting`` and
    ``regressed`` require a retained measured observation after the baseline.
    """
    if not isinstance(occurrence_key, str) or not _SHA256.fullmatch(occurrence_key):
        raise LedgerError("occurrence_key must be a lowercase SHA-256 key")
    if state not in _LIFECYCLE_STATES:
        raise LedgerError("unsupported lifecycle state")
    actor = _lifecycle_text(actor, "actor", 128)
    reason = _lifecycle_text(reason, "reason", 2048)
    if type(expected_revision) is not int or expected_revision < 0:
        raise LedgerError("expected_revision must be a nonnegative integer")
    if decided_at is not None and _validated_time(decided_at) is None:
        raise LedgerError("decided_at must be a UTC ISO-8601 timestamp")

    own = not isinstance(ledger, sqlite3.Connection)
    con = open_ledger(ledger, write=True) if own else ledger
    try:
        con.execute("BEGIN IMMEDIATE")
        header = con.execute("SELECT ledger_revision FROM ledger WHERE singleton=1").fetchone()
        if header is None or header["ledger_revision"] != expected_revision:
            raise LedgerError("ledger revision changed; reread cases before recording a decision")
        occurrence = con.execute(
            "SELECT occurrence_id,finding_id,current_state FROM occurrence WHERE occurrence_key=?",
            (occurrence_key,),
        ).fetchone()
        if occurrence is None:
            raise LedgerError("unknown occurrence_key")
        previous = occurrence["current_state"]
        if state == previous:
            raise LedgerError("lifecycle transition must change the current state")
        if state not in _TRANSITIONS.get(previous, frozenset()):
            raise LedgerError(f"invalid lifecycle transition from {previous!r} to {state!r}")
        linked_observation = (
            _outcome_observation(con, int(occurrence["occurrence_id"]), observation_id)
            if state in _MEASURED_OUTCOMES
            else None
        )
        if observation_id is not None and state not in _MEASURED_OUTCOMES:
            linked_observation = _outcome_observation(
                con, int(occurrence["occurrence_id"]), observation_id
            )
        next_revision = expected_revision + 1
        recorded_at = decided_at or _utc()
        con.execute(
            "INSERT INTO decision(occurrence_id,state,actor,reason,decided_at,decided_at_state,"
            "observation_id,ledger_revision) VALUES(?,?,?,?,?,?,?,?)",
            (
                occurrence["occurrence_id"],
                state,
                actor,
                reason,
                recorded_at,
                "known",
                linked_observation,
                next_revision,
            ),
        )
        con.execute(
            "UPDATE occurrence SET current_state=? WHERE occurrence_id=?",
            (state, occurrence["occurrence_id"]),
        )
        finding_state = _finding_state(con, int(occurrence["finding_id"]))
        con.execute(
            "UPDATE finding SET current_state=? WHERE finding_id=?",
            (finding_state, occurrence["finding_id"]),
        )
        con.execute("UPDATE ledger SET ledger_revision=? WHERE singleton=1", (next_revision,))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        if own:
            con.close()
    return {
        "ok": True,
        "occurrence_key": occurrence_key,
        "previous_state": previous,
        "state": state,
        "finding_state": finding_state,
        "observation_id": linked_observation,
        "ledger_revision": next_revision,
    }


def remediation_summary(ledger: str | Path | sqlite3.Connection) -> dict[str, Any]:
    """Return case counts and explicitly named remediation/recheck denominators.

    ``resolved_percent`` includes unresolved evidence states in its denominator;
    ``rechecked_percent`` deliberately excludes ``unverifiable`` from its
    numerator.  This makes a partial or failed recheck visible instead of
    shrinking the original case population.
    """
    own = not isinstance(ledger, sqlite3.Connection)
    con = open_ledger(ledger) if own else ledger
    try:
        counts = {state: 0 for state in _LIFECYCLE_STATES}
        for row in con.execute(
            "SELECT current_state,COUNT(*) AS count FROM occurrence GROUP BY current_state"
        ):
            counts[row["current_state"]] = int(row["count"])
        original = sum(counts.values())
        false_positive = counts["false_positive_reviewed"]
        remediation_denominator = original - false_positive
        resolved = counts["resolved"]
        rechecked = resolved + counts["persisting"] + counts["regressed"]
        return {
            "ok": True,
            "ledger_revision": int(
                con.execute("SELECT ledger_revision FROM ledger WHERE singleton=1").fetchone()[0]
            ),
            "counts": counts,
            "denominators": {
                "verified_original_occurrences": original,
                "remediation_cases": remediation_denominator,
            },
            "resolved_percent": (
                round(100 * resolved / remediation_denominator, 1)
                if remediation_denominator
                else None
            ),
            "rechecked_percent": round(100 * rechecked / original, 1) if original else None,
            "reason": None if original else "the ledger has no occurrence population",
        }
    finally:
        if own:
            con.close()


def remediation_report(ledger: str | Path | sqlite3.Connection) -> dict[str, Any]:
    """Build deterministic JSON-ready before/after rows without performing I/O or network work."""
    summary = remediation_summary(ledger)
    cases = read_cases(ledger)
    rows = []
    for finding in cases["findings"]:
        for occurrence in finding["occurrences"]:
            decisions = occurrence["decisions"]
            latest = decisions[-1] if decisions else None
            rows.append(
                {
                    "finding_key": finding["finding_key"],
                    "check": finding["check"],
                    "subject": occurrence["subject_value"],
                    "occurrence_key": occurrence["occurrence_key"],
                    "baseline": occurrence["observations"][0]
                    if occurrence["observations"]
                    else None,
                    "state": occurrence["current_state"],
                    "decision": latest,
                    "observations": occurrence["observations"],
                }
            )
    return {"schema_version": "remediation-report.v1", "summary": summary, "cases": rows}


def ledger_summary(ledger: str | Path | sqlite3.Connection) -> dict[str, Any]:
    """Return ledger identity plus the independent finding/occurrence/URL counts."""
    own = not isinstance(ledger, sqlite3.Connection)
    con = open_ledger(ledger) if own else ledger
    try:
        counts = {}
        for table in (
            "site",
            "source_scan",
            "check_def",
            "finding",
            "occurrence",
            "affected_url",
            "finding_observation",
            "finding_group",
            "observation",
            "decision",
        ):
            counts[table] = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        counts["distinct_affected_urls"] = con.execute(
            "SELECT COUNT(DISTINCT url) FROM affected_url"
        ).fetchone()[0]
        header = dict(con.execute("SELECT * FROM ledger WHERE singleton=1").fetchone())
        sites = [dict(row) for row in con.execute("SELECT * FROM site ORDER BY site_id")]
        return {
            "ok": True,
            "format_version": header["format_version"],
            "ledger_uuid": header["ledger_uuid"],
            "ledger_revision": header["ledger_revision"],
            "sites": sites,
            "counts": counts,
        }
    finally:
        if own:
            con.close()


def _observation_view(row: sqlite3.Row, previous: dict[str, Any] | None) -> dict[str, Any]:
    basis = {
        "config_fingerprint": row["config_fingerprint"],
        "producer_version": row["producer_version"],
        "producer_revision": row["producer_revision"],
    }
    changed = (
        None
        if previous is None
        else {name: basis[name] != previous["basis"][name] for name in basis}
    )
    return {
        "observation_id": row["observation_id"],
        "observation_revision": row["observation_revision"],
        "source_scan": {
            "source_scan_id": row["source_scan_id"],
            "scan_uuid": row["scan_uuid"],
            "format_version": row["format_version"],
            "evidence_revision": row["evidence_revision"],
            "audit_sha256": row["audit_sha256"],
            "artifact_state": row["artifact_state"],
            "crawl_partial": bool(row["crawl_partial"]),
            "corpus_partial": bool(row["corpus_partial"]),
        },
        "role": row["role"],
        "issue_ordinal": row["issue_ordinal"],
        "evidence_state": row["evidence_state"],
        "evidence_reason": row["evidence_reason"],
        "evidence": json.loads(row["evidence_json"]),
        "payload_sha256": row["payload_sha256"],
        "basis": basis,
        # Comparability is explicit data, not an inference: a changed basis
        # marks the observation as measured under different rules, never as
        # resolved or overwritten.
        "basis_changed": changed,
        "observed_at": row["observed_at"],
        "observed_at_state": row["observed_at_state"],
        "ingested_at": row["ingested_at"],
    }


def read_cases(
    ledger: str | Path | sqlite3.Connection,
    *,
    check: str | None = None,
    url: str | None = None,
    finding_key: str | None = None,
) -> dict[str, Any]:
    """Read back exact cases and their per-occurrence history.

    Filters are exact: ``check`` is a registry check id, ``url`` is canonicalized
    with the audit's own URL policy before matching subjects or affected-URL
    membership, and ``finding_key`` selects one case directly.  The result is
    the ledger population itself -- findings, their occurrences, affected-URL
    membership, group memberships and ordered observations -- so a second agent
    can resume without conversation history.
    """
    own = not isinstance(ledger, sqlite3.Connection)
    con = open_ledger(ledger) if own else ledger
    try:
        clauses, values = [], []
        if check is not None:
            clauses.append("c.check_key=?")
            values.append(check)
        if finding_key is not None:
            clauses.append("f.finding_key=?")
            values.append(finding_key)
        if url is not None:
            canon = canonical_url(url)
            clauses.append(
                "(f.subject_value=? OR EXISTS (SELECT 1 FROM affected_url a "
                "WHERE a.finding_id=f.finding_id AND a.url=?))"
            )
            values.extend([canon, canon])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        findings = []
        finding_rows = con.execute(
            "SELECT f.*,c.check_key FROM finding f JOIN check_def c ON c.check_id=f.check_id "
            + where
            + " ORDER BY c.check_key,f.subject_value",
            values,
        ).fetchall()
        for finding in finding_rows:
            members = [
                {"url": row["url"], "role": row["role"], "first_seen_at": row["first_seen_at"]}
                for row in con.execute(
                    "SELECT * FROM affected_url WHERE finding_id=? ORDER BY url,role",
                    (finding["finding_id"],),
                )
            ]
            projections = [
                {
                    "source_scan_id": row["source_scan_id"],
                    "issue_ordinal": row["issue_ordinal"],
                    "audit_fingerprint": row["audit_fingerprint"],
                    "severity": row["severity"],
                    "status_code": row["status_code"],
                    "message": row["message"],
                    "occurrences_count": row["occurrences_count"],
                    "enumerated_count": row["enumerated_count"],
                    "coverage_state": row["coverage_state"],
                    "payload_sha256": row["payload_sha256"],
                }
                for row in con.execute(
                    "SELECT * FROM finding_observation WHERE finding_id=? ORDER BY source_scan_id",
                    (finding["finding_id"],),
                )
            ]
            memberships = [
                {
                    "source_scan_id": row["source_scan_id"],
                    "group_ref": row["group_ref"],
                    "group_check": row["group_check"],
                    "group_value": row["group_value"],
                    "group_count": row["group_count"],
                    "group_urls": json.loads(row["group_urls_json"]),
                }
                for row in con.execute(
                    "SELECT * FROM finding_group WHERE finding_id=? ORDER BY source_scan_id",
                    (finding["finding_id"],),
                )
            ]
            occurrences = []
            for occurrence in con.execute(
                "SELECT * FROM occurrence WHERE finding_id=? "
                "ORDER BY subject_value,representation,discriminator_value",
                (finding["finding_id"],),
            ):
                observations = []
                previous = None
                for row in con.execute(
                    "SELECT o.*,s.scan_uuid,s.format_version,s.evidence_revision,"
                    "s.audit_sha256,s.artifact_state,s.crawl_partial,s.corpus_partial "
                    "FROM observation o JOIN source_scan s ON s.source_scan_id=o.source_scan_id "
                    "WHERE o.occurrence_id=? ORDER BY o.observation_revision",
                    (occurrence["occurrence_id"],),
                ):
                    view = _observation_view(row, previous)
                    previous = view
                    observations.append(view)
                decisions = [
                    {
                        "decision_id": row["decision_id"],
                        "state": row["state"],
                        "actor": row["actor"],
                        "reason": row["reason"],
                        "decided_at": row["decided_at"],
                        "decided_at_state": row["decided_at_state"],
                        "observation_id": row["observation_id"],
                        "ledger_revision": row["ledger_revision"],
                    }
                    for row in con.execute(
                        "SELECT * FROM decision WHERE occurrence_id=? ORDER BY decision_id",
                        (occurrence["occurrence_id"],),
                    )
                ]
                occurrences.append(
                    {
                        "occurrence_id": occurrence["occurrence_id"],
                        "occurrence_key": occurrence["occurrence_key"],
                        "subject_type": occurrence["subject_type"],
                        "subject_value": occurrence["subject_value"],
                        "representation": occurrence["representation"],
                        "discriminator_type": occurrence["discriminator_type"],
                        "discriminator_value": occurrence["discriminator_value"],
                        "current_state": occurrence["current_state"],
                        "first_seen_at": occurrence["first_seen_at"],
                        "observations": observations,
                        "decisions": decisions,
                    }
                )
            findings.append(
                {
                    "finding_id": finding["finding_id"],
                    "finding_key": finding["finding_key"],
                    "check": finding["check_key"],
                    "subject_type": finding["subject_type"],
                    "subject_value": finding["subject_value"],
                    "current_state": finding["current_state"],
                    "first_seen_at": finding["first_seen_at"],
                    "affected_urls": members,
                    "projections": projections,
                    "group_memberships": memberships,
                    "occurrences": occurrences,
                }
            )
        return {"ok": True, "findings": findings}
    finally:
        if own:
            con.close()
