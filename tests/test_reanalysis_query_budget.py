"""Reader deadlines bound SQLite work without counting Python consumer pauses."""

import sqlite3
import threading

import pytest

from seohead.storage.read_budget import ReadConnection


def test_paused_python_consumer_gets_a_fresh_sql_step_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("seohead.storage.read_budget.time.monotonic", lambda: clock[0])
    con = sqlite3.connect(":memory:", factory=ReadConnection)
    try:
        con.set_query_budget(0.1)
        rows = con.execute("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3")
        assert next(rows) == (1,)
        clock[0] += 900  # Long parsing/writing work outside SQLite is not query time.
        assert rows.fetchone() == (2,)
        clock[0] += 900
        assert rows.fetchmany(1) == [(3,)]
        clock[0] += 900
        assert con.execute("SELECT 4").fetchall() == [(4,)]
    finally:
        con.close()


def test_expensive_sql_is_still_interrupted_with_per_operation_budget(monkeypatch):
    clock = [0.0]

    def advancing_time():
        clock[0] += 0.01
        return clock[0]

    monkeypatch.setattr("seohead.storage.read_budget.time.monotonic", advancing_time)
    con = sqlite3.connect(":memory:", factory=ReadConnection)
    try:
        con.set_query_budget(0.05)
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            con.execute(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT SUM(x) FROM n"
            ).fetchone()
    finally:
        con.close()


def test_explicit_connection_cancellation_remains_effective():
    con = sqlite3.connect(":memory:", factory=ReadConnection)
    con.set_query_budget(60)
    cancel = threading.Timer(0.01, con.interrupt)
    try:
        cancel.start()
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            con.execute(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT SUM(x) FROM n"
            ).fetchone()
    finally:
        cancel.join()
        con.close()


def test_public_reader_keeps_validation_before_scoped_query_budgets(tmp_path, monkeypatch):
    from seohead.storage import open_scan
    from seohead.storage.native_scan import NativeScan
    from tests.test_scan_native import _metadata

    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()):
        pass
    original = NativeScan._validate_native
    validated = []

    def validate(con):
        assert isinstance(con, ReadConnection)
        assert con.query_timeout_seconds is None
        validated.append(True)
        return original(con)

    monkeypatch.setattr(NativeScan, "_validate_native", staticmethod(validate))
    con = open_scan(path, require_audit=False, query_timeout_seconds=0.1)
    try:
        assert validated == [True]
        assert con.query_timeout_seconds == 0.1
        assert con.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        con.close()
