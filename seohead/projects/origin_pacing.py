"""Project-local cross-process origin pacing for explicit native captures."""

from __future__ import annotations

import math
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from .workspace import _load

PUBLIC_PROJECT_MAX_REQUESTS_PER_SECOND = 2.0
_MAX_INTERVAL_SECONDS = 60.0
_NAME = ".origin-pacing.sqlite"


def _origin(target: str) -> str:
    parsed = urlsplit(target)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("project origin needs a hostname")
    return host


class ProjectOriginPacer:
    """Reserve one request turn across local processes for one project host."""

    def __init__(
        self,
        directory: str | Path,
        target: str,
        *,
        minimum_delay_seconds: float,
        max_requests_per_second: float = PUBLIC_PROJECT_MAX_REQUESTS_PER_SECOND,
    ) -> None:
        root, _project = _load(directory)
        if (
            not isinstance(minimum_delay_seconds, (int, float))
            or not math.isfinite(minimum_delay_seconds)
            or minimum_delay_seconds < 0
        ):
            raise ValueError("minimum delay must be finite and nonnegative")
        if (
            not isinstance(max_requests_per_second, (int, float))
            or not math.isfinite(max_requests_per_second)
            or max_requests_per_second <= 0
        ):
            raise ValueError("maximum request rate must be finite and positive")
        self.root = root
        self.origin = _origin(target)
        self.max_requests_per_second = float(max_requests_per_second)
        self.interval_seconds = min(
            _MAX_INTERVAL_SECONDS,
            max(float(minimum_delay_seconds), 1.0 / self.max_requests_per_second),
        )
        self.path = root / _NAME

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise ValueError("project origin pacing store must not be a symlink")
        created = not self.path.exists()
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute(
                "CREATE TABLE IF NOT EXISTS origin_turns (origin TEXT PRIMARY KEY, next_at REAL NOT NULL)"
            )
            if created:
                os.chmod(self.path, 0o600)
            if (
                self.path.is_symlink()
                or not self.path.is_file()
                or self.path.stat().st_mode & 0o077
            ):
                raise ValueError("project origin pacing store must be private")
            return con
        except BaseException:
            con.close()
            raise

    def reserve(self) -> float:
        """Atomically reserve a wall-clock request slot and return its wait time."""
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                row = con.execute(
                    "SELECT next_at FROM origin_turns WHERE origin=?", (self.origin,)
                ).fetchone()
                prior = float(row[0]) if row is not None else now
                if not math.isfinite(prior):
                    raise ValueError("project origin pacing store contains an invalid timestamp")
                slot = max(now, prior)
                con.execute(
                    "INSERT INTO origin_turns(origin,next_at) VALUES(?,?) "
                    "ON CONFLICT(origin) DO UPDATE SET next_at=excluded.next_at",
                    (self.origin, slot + self.interval_seconds),
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return max(0.0, slot - now)

    def wait_turn(self) -> None:
        delay = self.reserve()
        if delay:
            time.sleep(delay)
