"""Resumable, daily Search Console archives in a local SQLite database.

Different grains are independent datasets. Never add their metrics together.
Only the existing GSC client performs HTTP and handles credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from seohead.core.sqlite import open_readonly, open_writer

SCHEMA_VERSION = 2
SEARCH_TYPES = ("web", "image", "video", "news", "discover", "googleNews")
MAX_TRANSIENT_ATTEMPTS = 3
QUOTA_REASONS = (
    "quotaExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "dailyLimitExceeded",
    "servingLimitExceeded",
)
OBSERVATIONS_VIEW = """
CREATE VIEW observations AS
SELECT j.provider,j.engine,j.date_timezone,j.site_url,j.dataset,j.search_type,j.aggregation_type,j.actual_aggregation_type,j.start_date,j.end_date,
 f.data_date,q.value AS query,p.value AS page,f.country,f.device,
 coalesce(nullif(f.appearance,''), CASE
  WHEN j.dataset='appearance_detail'
   AND json_extract(j.filters,'$[0].filters[0].dimension')='searchAppearance'
   AND json_extract(j.filters,'$[0].filters[0].operator')='equals'
  THEN json_extract(j.filters,'$[0].filters[0].expression') END, '') AS appearance,
 f.clicks,f.impressions,f.ctr,f.position,j.state,j.cap_possible,
 j.id AS job_id,j.request_key,j.dimensions,j.filters,j.metadata,j.updated_at,
 (SELECT max(r.checked_at) FROM requests r WHERE r.job_id=j.id) AS last_requested_at
FROM facts f JOIN jobs j ON j.id=f.job_id
LEFT JOIN strings q ON q.id=f.query_id LEFT JOIN strings p ON p.id=f.page_id
WHERE j.dataset!='availability' OR NOT EXISTS (
 SELECT 1 FROM facts earlier JOIN jobs other ON other.id=earlier.job_id
 WHERE other.id<j.id AND other.dataset='availability'
 AND other.provider=j.provider AND other.engine=j.engine
 AND other.date_timezone=j.date_timezone
 AND other.site_url=j.site_url AND other.search_type=j.search_type
 AND other.dimensions=j.dimensions AND other.aggregation_type=j.aggregation_type
 AND other.filters=j.filters AND earlier.data_date=f.data_date
 AND earlier.query_id=f.query_id AND earlier.page_id=f.page_id
 AND earlier.country=f.country AND earlier.device=f.device AND earlier.appearance=f.appearance
);
"""
SCHEMA = (
    """
CREATE TABLE archive_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE strings(id INTEGER PRIMARY KEY, value TEXT UNIQUE);
INSERT INTO strings(id,value) VALUES(0,NULL);
CREATE TABLE jobs(
 id INTEGER PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
 provider TEXT NOT NULL, engine TEXT NOT NULL, date_timezone TEXT NOT NULL,
 site_url TEXT NOT NULL, dataset TEXT NOT NULL, search_type TEXT NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 dimensions TEXT NOT NULL, aggregation_type TEXT NOT NULL, filters TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', next_row INTEGER NOT NULL DEFAULT 0,
 saved_rows INTEGER NOT NULL DEFAULT 0, received_rows INTEGER NOT NULL DEFAULT 0,
 duplicate_rows INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
 cap_possible INTEGER NOT NULL DEFAULT 0, error TEXT, retry_at REAL NOT NULL DEFAULT 0,
 actual_aggregation_type TEXT, metadata TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX jobs_pending ON jobs(state,retry_at,start_date,id);
CREATE TABLE facts(
 job_id INTEGER NOT NULL REFERENCES jobs(id), data_date TEXT NOT NULL,
 query_id INTEGER NOT NULL REFERENCES strings(id), page_id INTEGER NOT NULL REFERENCES strings(id),
 country TEXT NOT NULL, device TEXT NOT NULL, appearance TEXT NOT NULL,
 clicks REAL, impressions REAL, ctr REAL, position REAL,
 PRIMARY KEY(job_id,data_date,query_id,page_id,country,device,appearance)
) WITHOUT ROWID;
CREATE TABLE requests(
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 start_row INTEGER NOT NULL, received_rows INTEGER NOT NULL, status INTEGER,
 reason TEXT, elapsed_ms INTEGER NOT NULL, checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
    + OBSERVATIONS_VIEW
)


class Archive:
    """One writer per archive; transaction boundaries are one received API page."""

    def __init__(self, path: str | Path, *, create: bool = True, read_only: bool = False):
        from seohead.data_sources import sources_db

        self.path = Path(path).expanduser().resolve()
        existed = self.path.exists()
        self.read_only = read_only
        if not existed and (read_only or not create):
            raise FileNotFoundError("GSC archive does not exist; prepare it first")
        self._lock = None
        # The lock lifetime intentionally matches this archive object, not a single method.
        if read_only:
            self.db = open_readonly(self.path, timeout=30)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._lock = open(str(self.path) + ".lock", "a+b")  # noqa: SIM115
            self._lock.seek(0)
            if os.fstat(self._lock.fileno()).st_size == 0:
                self._lock.write(b"0")
                self._lock.flush()
            self._lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._lock.close()
                raise RuntimeError("Another writer already owns this GSC archive") from None
            if not existed:
                # New archives live in the same versioned project SQLite store as the broader
                # provider history. GSC-specific queue/fact tables retain their independent
                # grains without inventing a second file or a competing credential boundary.
                bootstrap = sources_db.connect(self.path, create=True)
                bootstrap.close()
                existed = True
            try:
                self.db = open_writer(self.path, timeout=30)
            except sqlite3.Error:
                self._lock.close()
                raise
        self.db.row_factory = sqlite3.Row
        if existed:
            marker = self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='archive_meta'"
            ).fetchone()
            if not marker:
                source_identity = (
                    dict(self.db.execute("SELECT key, value FROM sources_meta"))
                    if self.db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='sources_meta'"
                    ).fetchone()
                    else {}
                )
                if (
                    source_identity.get("kind") != sources_db.SCHEMA_KIND
                    or source_identity.get("schema_version") != sources_db.SCHEMA_VERSION
                    or read_only
                    or not create
                ):
                    self.db.close()
                    if self._lock:
                        self._lock.close()
                    raise ValueError(
                        "Existing database is not a GSC archive; refusing to modify it"
                    )
                self.db.executescript(SCHEMA)
                self.db.execute(
                    "INSERT INTO archive_meta VALUES('schema_version',?)", (str(SCHEMA_VERSION),)
                )
                self.db.execute(
                    "INSERT INTO archive_meta VALUES('kind','seohead.search_analytics')"
                )
                self.db.commit()
                marker = True
            version = self.db.execute(
                "SELECT value FROM archive_meta WHERE key='schema_version'"
            ).fetchone()
            kind = self.db.execute("SELECT value FROM archive_meta WHERE key='kind'").fetchone()
            if (
                not version
                or int(version[0]) not in (1, SCHEMA_VERSION)
                or not kind
                or kind[0] != "seohead.search_analytics"
            ):
                self.db.close()
                if self._lock:
                    self._lock.close()
                raise ValueError("Unsupported GSC archive schema")
            if int(version[0]) == 1 and not read_only:
                with self.db:
                    self.db.execute("DROP VIEW observations")
                    self.db.execute(OBSERVATIONS_VIEW)
                    self.db.execute(
                        "UPDATE archive_meta SET value=? WHERE key='schema_version'",
                        (str(SCHEMA_VERSION),),
                    )
        else:
            self.db.executescript(SCHEMA)
            self.db.execute(
                "INSERT INTO archive_meta VALUES('schema_version',?)", (str(SCHEMA_VERSION),)
            )
            self.db.execute("INSERT INTO archive_meta VALUES('kind','seohead.search_analytics')")
            self.db.commit()
        self.db.execute("PRAGMA foreign_keys=ON")
        if read_only:
            self.db.execute("PRAGMA query_only=ON")
        else:
            self.db.execute("PRAGMA journal_mode=WAL")
        self._strings: dict[str, int] = {}
        self._token: str | None = None
        self._token_time = 0.0

    def close(self) -> None:
        self.db.close()
        if self._lock:
            self._lock.close()

    def backup(self, destination: str | Path) -> dict[str, Any]:
        """Make and verify a consistent snapshot without overwriting another file."""
        destination = Path(destination).expanduser().resolve()
        if destination.exists() or destination == self.path:
            raise ValueError("Backup destination must be a new file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup failed SQLite integrity verification")
            source_rows = self.db.execute("SELECT count(*) FROM facts").fetchone()[0]
            backup_rows = target.execute("SELECT count(*) FROM facts").fetchone()[0]
            if source_rows != backup_rows:
                raise RuntimeError("Backup row count mismatch")
        finally:
            target.close()
        digest = hashlib.sha256()
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"path": str(destination), "rows": backup_rows, "sha256": digest.hexdigest()}

    def _queue(
        self,
        site: str,
        dataset: str,
        kind: str,
        start: str,
        end: str,
        dimensions: list[str],
        aggregation: str = "auto",
        filters: list | None = None,
    ) -> None:
        values = (
            "gsc",
            "google",
            "America/Los_Angeles",
            site,
            dataset,
            kind,
            start,
            end,
            json.dumps(dimensions),
            aggregation,
            json.dumps(filters or [], sort_keys=True),
        )
        key = hashlib.sha256(json.dumps(values).encode()).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO jobs(request_key,provider,engine,date_timezone,site_url,dataset,search_type,start_date,"
            "end_date,dimensions,aggregation_type,filters) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, *values),
        )

    def prepare(self, site_url: str, start_date: str, end_date: str) -> dict[str, Any]:
        from seohead.data_sources.gsc import _validate_date_range

        if self.read_only:
            raise ValueError("Cannot prepare a read-only archive")
        error = _validate_date_range(start_date, end_date)
        if error or not site_url:
            raise ValueError(error or "site_url is required")
        with self.db:
            for kind in SEARCH_TYPES:
                # Existing requests cover their entire interval, including dates with zero
                # observations. Queue only gaps, not another overlapping availability grain.
                covered = self.db.execute(
                    "SELECT start_date,end_date FROM jobs WHERE provider='gsc' AND engine='google' "
                    "AND site_url=? AND dataset='availability' AND search_type=? "
                    "AND dimensions=? AND aggregation_type='auto' AND filters='[]' "
                    "AND end_date>=? AND start_date<=? ORDER BY start_date,end_date",
                    (site_url, kind, json.dumps(["date"]), start_date, end_date),
                )
                cursor, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
                for interval in covered:
                    begin, finish = map(date.fromisoformat, interval)
                    if begin > cursor:
                        self._queue(
                            site_url,
                            "availability",
                            kind,
                            cursor.isoformat(),
                            (begin - timedelta(days=1)).isoformat(),
                            ["date"],
                        )
                    if finish >= end:
                        cursor = None
                        break
                    cursor = max(cursor, finish + timedelta(days=1))
                if cursor is not None and cursor <= end:
                    self._queue(
                        site_url,
                        "availability",
                        kind,
                        cursor.isoformat(),
                        end.isoformat(),
                        ["date"],
                    )
        return self.status()

    def _provider_backoff(self) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT value FROM archive_meta WHERE key='backoff:gsc:google'"
        ).fetchone()
        if row:
            return json.loads(row[0])
        # Version-one archives only stored quota waits on individual jobs.
        legacy = self.db.execute(
            "SELECT retry_at,error FROM jobs WHERE provider='gsc' AND engine='google' "
            "AND state='quota_wait' ORDER BY retry_at DESC LIMIT 1"
        ).fetchone()
        return (
            {"retry_at": legacy[0], "reason": legacy[1]}
            if legacy
            else {"retry_at": 0, "reason": None}
        )

    def _intern(self, value: str | None) -> int:
        if value is None:
            return 0
        if value not in self._strings:
            self.db.execute("INSERT OR IGNORE INTO strings(value) VALUES(?)", (value,))
            self._strings[value] = self.db.execute(
                "SELECT id FROM strings WHERE value=?", (value,)
            ).fetchone()[0]
        return self._strings[value]

    def _expand(self, job: sqlite3.Row) -> None:
        if job["dataset"] == "availability":
            dates = self.db.execute(
                "SELECT DISTINCT data_date FROM facts WHERE job_id=? ORDER BY data_date",
                (job["id"],),
            )
            for row in dates:
                day = row[0]
                datasets = [
                    ("totals_page", ["country", "device"], "byPage"),
                    ("pages", ["page"], "byPage"),
                ]
                if job["search_type"] not in ("discover", "googleNews"):
                    datasets += [
                        ("totals_property", ["country", "device"], "byProperty"),
                        ("queries", ["query"], "byProperty"),
                        ("detail", ["page", "query", "country", "device"], "byPage"),
                    ]
                else:
                    datasets += [("detail", ["page", "country", "device"], "byPage")]
                if job["search_type"] == "web":
                    datasets += [("appearances", ["searchAppearance"], "auto")]
                for label, dimensions, aggregation in datasets:
                    self._queue(
                        job["site_url"],
                        label,
                        job["search_type"],
                        day,
                        day,
                        dimensions,
                        aggregation,
                    )
        elif job["dataset"] == "appearances":
            for row in self.db.execute("SELECT appearance FROM facts WHERE job_id=?", (job["id"],)):
                filters = [
                    {
                        "groupType": "and",
                        "filters": [
                            {
                                "dimension": "searchAppearance",
                                "operator": "equals",
                                "expression": row[0],
                            }
                        ],
                    }
                ]
                self._queue(
                    job["site_url"],
                    "appearance_detail",
                    job["search_type"],
                    job["start_date"],
                    job["end_date"],
                    ["page", "query", "country", "device"],
                    "auto",
                    filters,
                )

    def run_batch(self, max_requests: int = 1, pause: float = 1.0, fetcher=None) -> dict[str, Any]:
        from seohead.data_sources import gsc

        if self.read_only:
            raise ValueError("Cannot run a read-only archive")
        if type(max_requests) is not int or not 1 <= max_requests <= 1000:
            raise ValueError("max_requests must be 1..1000")
        if not 0 <= pause <= 60:
            raise ValueError("pause must be 0..60 seconds")
        performed = 0
        for _ in range(max_requests):
            if self._provider_backoff()["retry_at"] > time.time():
                break
            job = self.db.execute(
                "SELECT * FROM jobs WHERE provider='gsc' AND engine='google' AND state IN ('pending','quota_wait','retry_wait') AND retry_at<=? "
                "ORDER BY CASE WHEN dataset='availability' THEN 0 ELSE 1 END,start_date,id LIMIT 1",
                (time.time(),),
            ).fetchone()
            if not job:
                break
            if fetcher is None and (not self._token or time.monotonic() - self._token_time > 3000):
                self._token, _error = gsc._acquire_token(None)
                if not self._token:
                    raise RuntimeError("GSC authentication is unavailable")
                self._token_time = time.monotonic()
            time.sleep(pause)
            started = time.monotonic()
            response = (fetcher or gsc.search_analytics_page)(
                job["site_url"],
                start_date=job["start_date"],
                end_date=job["end_date"],
                dimensions=json.loads(job["dimensions"]),
                search_type=job["search_type"],
                aggregation_type=job["aggregation_type"],
                data_state="final",
                row_limit=25000,
                start_row=job["next_row"],
                dimension_filter_groups=json.loads(job["filters"]),
                token=self._token,
            )
            performed += 1
            rows = response.get("rows", [])
            with self.db:
                self.db.execute(
                    "INSERT INTO requests(job_id,start_row,received_rows,status,reason,elapsed_ms) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        job["id"],
                        job["next_row"],
                        len(rows),
                        response.get("status"),
                        response.get("reason"),
                        int((time.monotonic() - started) * 1000),
                    ),
                )
                if not response.get("ok"):
                    status, reason = response.get("status"), response.get("reason")
                    quota = status == 429 or reason in QUOTA_REASONS
                    transient = reason == "transport_error" or (
                        isinstance(status, int) and 500 <= status <= 599
                    )
                    state, retry_at = "failed", 0.0
                    if quota:
                        # A quota is shared by this provider/engine, not merely the failed
                        # property or job. Persist the gate so reopening cannot bypass it.
                        retry_at = time.time() + (86400 if reason == "dailyLimitExceeded" else 900)
                        state = "quota_wait"
                        self.db.execute(
                            "INSERT OR REPLACE INTO archive_meta(key,value) VALUES('backoff:gsc:google',?)",
                            (json.dumps({"retry_at": retry_at, "reason": reason or "http_429"}),),
                        )
                    elif transient:
                        attempts = self.db.execute(
                            "SELECT count(*) FROM requests WHERE job_id=? AND "
                            "(reason='transport_error' OR status BETWEEN 500 AND 599) AND id>"
                            "coalesce((SELECT max(id) FROM requests WHERE job_id=? "
                            "AND status BETWEEN 200 AND 299 AND reason IS NULL),0)",
                            (job["id"], job["id"]),
                        ).fetchone()[0]
                        if attempts < MAX_TRANSIENT_ATTEMPTS:
                            state, retry_at = "retry_wait", time.time() + 5 * (2 ** (attempts - 1))
                    self.db.execute(
                        "UPDATE jobs SET state=?,error=?,retry_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (
                            state,
                            reason or "request_failed",
                            retry_at,
                            job["id"],
                        ),
                    )
                    if quota:
                        break
                    continue
                dimensions = json.loads(job["dimensions"])
                appearance_filters = {
                    item["expression"]
                    for group in json.loads(job["filters"])
                    for item in group.get("filters", [])
                    if item.get("dimension") == "searchAppearance"
                    and item.get("operator") == "equals"
                }
                filtered_appearance = (
                    next(iter(appearance_filters)) if len(appearance_filters) == 1 else ""
                )
                values = []
                for row in rows:
                    dims = dict(zip(dimensions, row["keys"], strict=True))
                    values.append(
                        (
                            job["id"],
                            dims.get("date", job["start_date"]),
                            self._intern(dims.get("query")),
                            self._intern(dims.get("page")),
                            dims.get("country", ""),
                            dims.get("device", ""),
                            dims.get("searchAppearance", filtered_appearance),
                            row["clicks"],
                            row["impressions"],
                            row.get("ctr"),
                            row.get("position"),
                        )
                    )
                before = self.db.total_changes
                self.db.executemany(
                    "INSERT OR IGNORE INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)", values
                )
                saved = self.db.total_changes - before
                offset = job["next_row"] + len(rows)
                terminal = len(rows) < 25000 or offset >= 50000
                capped = offset >= 50000 and len(rows) == 25000
                state = "capped" if capped else "complete" if terminal else "pending"
                self.db.execute(
                    "UPDATE jobs SET next_row=?,saved_rows=saved_rows+?,received_rows=received_rows+?,"
                    "duplicate_rows=duplicate_rows+?,pages=pages+1,state=?,cap_possible=?,error=NULL,retry_at=0,"
                    "actual_aggregation_type=coalesce(?,actual_aggregation_type),metadata=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (
                        offset,
                        saved,
                        len(rows),
                        len(rows) - saved,
                        state,
                        int(capped),
                        response.get("response_aggregation_type"),
                        json.dumps(response.get("metadata", {})),
                        job["id"],
                    ),
                )
                if terminal:
                    self._expand(job)
        return {**self.status(), "requests_this_batch": performed}

    def status(self) -> dict[str, Any]:
        states = {
            r[0]: r[1] for r in self.db.execute("SELECT state,count(*) FROM jobs GROUP BY state")
        }
        totals = self.db.execute(
            "SELECT coalesce(sum(saved_rows),0),coalesce(sum(duplicate_rows),0),"
            "coalesce(sum(cap_possible),0) FROM jobs"
        ).fetchone()
        return {
            "ok": True,
            "database": str(self.path),
            "states": states,
            "rows": totals[0],
            "duplicate_rows_observed": totals[1],
            "capped_jobs": totals[2],
            "provider_states": [
                dict(r)
                for r in self.db.execute(
                    "SELECT provider,engine,state,count(*) AS jobs FROM jobs GROUP BY provider,engine,state"
                )
            ],
            "provider_backoff": {"provider": "gsc", "engine": "google", **self._provider_backoff()},
            "complete": bool(states)
            and not any(states.get(s) for s in ("pending", "quota_wait", "retry_wait", "failed")),
        }
