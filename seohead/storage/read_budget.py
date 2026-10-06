"""Bound SQLite execution without charging Python work between fetched rows."""

from __future__ import annotations

import math
import sqlite3
import time


class ReadCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=(), /):
        self.connection.renew_query_budget()
        return super().execute(sql, parameters)

    def executemany(self, sql, parameters, /):
        self.connection.renew_query_budget()
        return super().executemany(sql, parameters)

    def __next__(self):
        self.connection.renew_query_budget()
        return super().__next__()

    def fetchone(self):
        self.connection.renew_query_budget()
        return super().fetchone()

    def fetchmany(self, size=None):
        self.connection.renew_query_budget()
        return super().fetchmany() if size is None else super().fetchmany(size)

    def fetchall(self):
        self.connection.renew_query_budget()
        return super().fetchall()


class ReadConnection(sqlite3.Connection):
    """Start in validation mode; activate per-operation budgets after validation."""

    query_timeout_seconds = None

    def set_query_budget(self, seconds):
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("SQLite query timeout must be a finite positive number")
        self.query_timeout_seconds = float(seconds)
        self.renew_query_budget()
        self.set_progress_handler(lambda: int(time.monotonic() > self._query_deadline), 1000)

    def renew_query_budget(self):
        if self.query_timeout_seconds is not None:
            self._query_deadline = time.monotonic() + self.query_timeout_seconds

    def cursor(self, factory=ReadCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=(), /):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters, /):
        return self.cursor().executemany(sql, parameters)
