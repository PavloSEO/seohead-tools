from __future__ import annotations

import sqlite3

import pytest

from seohead.core.sqlite import open_readonly, open_writer


def test_open_writer_creates_database_and_applies_row_factory_and_pragmas(tmp_path):
    path = tmp_path / "data.sqlite"
    con = open_writer(path, row_factory=sqlite3.Row, pragmas=("PRAGMA query_only=OFF",))
    try:
        con.execute("CREATE TABLE item (value TEXT)")
        con.execute("INSERT INTO item VALUES ('a')")
        con.commit()
        row = con.execute("SELECT value FROM item").fetchone()
        assert row["value"] == "a"
    finally:
        con.close()
    assert path.is_file()


def test_open_readonly_rejects_writes(tmp_path):
    path = tmp_path / "data.sqlite"
    seed = sqlite3.connect(path)
    seed.execute("CREATE TABLE item (value TEXT)")
    seed.commit()
    seed.close()

    con = open_readonly(path.resolve(), row_factory=sqlite3.Row, pragmas=("PRAGMA query_only=ON",))
    try:
        assert con.execute("SELECT count(*) FROM item").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO item VALUES ('x')")
    finally:
        con.close()


def test_open_readonly_does_not_create_missing_file(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        open_readonly(path.resolve())
    assert not path.exists()
