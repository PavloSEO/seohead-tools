"""Reader deadlines bound SQLite work without counting Python consumer pauses."""

import sqlite3
import threading

import pytest

from seohead.storage.read_budget import ReadConnection


def test_paused_python_consumer_gets_a_fresh_sql_step_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("seohead.storage.read_budget.time.monotonic", lambda: clock[0])
    sql = (
        "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<2000) "
        "SELECT (SELECT SUM(x + outer_row.v) FROM n) "
        "FROM (SELECT 1 AS v UNION ALL SELECT 2 UNION ALL SELECT 3) outer_row"
    )
    # Negative control: the former sticky connection deadline really fires on
    # the expensive next row. A three-literal SELECT never exercises the VM guard.
    sticky = sqlite3.connect(":memory:")
    try:
        sticky.set_progress_handler(lambda: int(clock[0] > 0.1), 1000)
        rows = sticky.execute(sql)
        clock[0] = 900
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            rows.fetchone()
    finally:
        sticky.close()
    clock[0] = 0
    con = sqlite3.connect(":memory:", factory=ReadConnection)
    try:
        con.set_query_budget(0.1)
        rows = con.execute(sql)
        clock[0] += 900
        assert next(rows) == (2003000,)
        clock[0] += 900
        assert rows.fetchone() == (2005000,)
        clock[0] += 900
        assert rows.fetchmany(1) == [(2007000,)]
        clock[0] += 900
        assert con.execute("SELECT 4").fetchall() == [(4,)]
    finally:
        con.close()


@pytest.mark.parametrize("seconds", [True, False, float("nan"), float("inf"), -1, 0])
def test_invalid_query_budget_is_refused(seconds):
    con = sqlite3.connect(":memory:", factory=ReadConnection)
    try:
        with pytest.raises(ValueError, match="finite positive"):
            con.set_query_budget(seconds)
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
