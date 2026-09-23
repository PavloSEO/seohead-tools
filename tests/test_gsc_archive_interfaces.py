"""Archive actions share validated, bounded CLI/handler/MCP behavior without live APIs."""

import asyncio
import hashlib
import io
import json
import sqlite3

import pytest

from seohead import cli
from seohead.data_sources import gsc
from seohead.data_sources.gsc_archive import Archive
from seohead.servers import handlers


@pytest.fixture(autouse=True)
def no_google(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No live GSC authentication or network allowed")

    monkeypatch.setattr(gsc, "_acquire_token", forbidden)
    monkeypatch.setattr(gsc, "search_analytics_page", forbidden)


@pytest.mark.parametrize("action", ["status", "run", "backup"])
def test_missing_archive_is_not_created(tmp_path, action):
    database = tmp_path / "missing parent" / "search-console.sqlite"
    kwargs = {"backup_path": str(tmp_path / "backup.sqlite")} if action == "backup" else {}
    result = handlers.gsc_archive(database=str(database), action=action, **kwargs)
    assert result["ok"] is False and result["state"] == "not_found"
    assert not database.parent.exists()


def prepare(path):
    return handlers.gsc_archive(
        database=str(path),
        action="prepare",
        site_url="sc-domain:example.test",
        start_date="2026-01-01",
        end_date="2026-01-02",
    )


def test_prepare_status_and_backup_share_core_without_network(tmp_path):
    database = tmp_path / "with spaces" / "search-console.sqlite"
    result = prepare(database)
    assert result["states"] == {"pending": 6}
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    status = handlers.gsc_archive(database=str(database))
    assert status["states"] == {"pending": 6}
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    destination = tmp_path / "snapshot.sqlite"
    result = handlers.gsc_archive(
        database=str(database), action="backup", backup_path=str(destination)
    )
    assert result["ok"] and len(result["backup"]["sha256"]) == 64
    with sqlite3.connect(destination) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 6
    with pytest.raises(ValueError, match="new file"):
        handlers.gsc_archive(database=str(database), action="backup", backup_path=str(destination))


def test_status_is_available_while_writer_owns_archive(tmp_path):
    path = tmp_path / "archive.sqlite"
    prepare(path)
    writer = Archive(path, create=False)
    try:
        assert handlers.gsc_archive(database=str(path))["ok"] is True
    finally:
        writer.close()


def test_read_only_status_does_not_migrate_v1(tmp_path):
    path = tmp_path / "archive.sqlite"
    prepare(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE archive_meta SET value='1' WHERE key='schema_version'")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert handlers.gsc_archive(database=str(path))["ok"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT value FROM archive_meta WHERE key='schema_version'").fetchone()[0]
            == "1"
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"database": None},
        {"database": []},
        {"database": ""},
        {"database": ":memory:"},
        {"database": "https://example.test/archive.sqlite"},
        {"database": "file:archive.sqlite?mode=rwc"},
        {"action": "delete"},
        {"max_requests": True},
        {"max_requests": 0},
        {"max_requests": 1001},
        {"max_requests": "3"},
        {"pause": True},
        {"pause": -1},
        {"pause": 61},
        {"pause": float("nan")},
        {"pause": float("inf")},
        {"action": "prepare"},
        {
            "action": "prepare",
            "site_url": "sc-domain:example.test",
            "start_date": "2026-02-30",
            "end_date": "2026-03-01",
        },
        {"site_url": "sc-domain:example.test"},
        {"backup_path": "unrequested.sqlite"},
    ],
)
def test_invalid_json_types_and_actions_fail_before_creation(tmp_path, kwargs):
    database = tmp_path / "uncreated" / "archive.sqlite"
    params = {"database": str(database), **kwargs}
    with pytest.raises(ValueError):
        handlers.gsc_archive(**params)
    assert not database.parent.exists()


def test_directory_and_foreign_database_are_refused_without_mutation(tmp_path):
    with pytest.raises(ValueError, match="file"):
        handlers.gsc_archive(database=str(tmp_path))
    path = tmp_path / "foreign.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE valuable(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="not a GSC archive"):
        handlers.gsc_archive(database=str(path))
    assert path.read_bytes() == before


def test_run_forwards_explicit_bound_and_pause_to_one_core(tmp_path, monkeypatch):
    path = tmp_path / "archive.sqlite"
    prepare(path)
    calls = []
    monkeypatch.setattr(
        Archive, "run_batch", lambda self, **kw: calls.append((self.read_only, kw)) or {"ok": True}
    )
    result = handlers.gsc_archive(database=str(path), action="run", max_requests=2, pause=0)
    assert result["ok"]
    assert calls == [(False, {"max_requests": 2, "pause": 0})]


def test_cli_json_only_and_flags_override_including_zero(tmp_path, monkeypatch, capsys):
    captured = []
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("not JSON; must not be read"))
    monkeypatch.setitem(
        handlers.HANDLERS, "gsc_archive", lambda **kw: captured.append(kw) or {"ok": True}
    )
    payload = {
        "database": str(tmp_path / "archive.sqlite"),
        "action": "run",
        "max_requests": 7,
        "pause": 1,
    }
    assert (
        cli.main(
            ["gsc-archive", "--input", json.dumps(payload), "--max-requests", "2", "--pause", "0"]
        )
        == 0
    )
    assert captured == [{**payload, "max_requests": 2, "pause": 0.0}]
    assert json.loads(capsys.readouterr().out)["ok"]


def test_cli_prepare_and_missing_status_are_real_handlers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("invalid stdin"))
    path = tmp_path / "archive.sqlite"
    assert cli.main(["gsc-archive", "--database", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "not_found"
    assert not path.exists()
    args = [
        "gsc-archive",
        "--database",
        str(path),
        "--action",
        "prepare",
        "--site-url",
        "sc-domain:example.test",
        "--start-date",
        "2026-01-01",
        "--end-date",
        "2026-01-02",
    ]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["states"] == {"pending": 6}


def test_cli_rejects_bad_json_shape_and_bool_limits(tmp_path, capsys):
    assert cli.main(["gsc-archive", "--input", "[]"]) == 1
    assert (
        cli.main(
            ["gsc-archive", "--input", json.dumps([["database", str(tmp_path / "archive.sqlite")]])]
        )
        == 1
    )
    assert (
        cli.main(
            [
                "gsc-archive",
                "--input",
                json.dumps(
                    {
                        "database": str(tmp_path / "archive.sqlite"),
                        "action": "run",
                        "max_requests": True,
                    }
                ),
            ]
        )
        == 1
    )
    assert not (tmp_path / "archive.sqlite").exists()


def test_mcp_schema_and_side_effect_annotations():
    from seohead.servers.mcp_server import build_server

    tools = asyncio.run(build_server().list_tools())
    tool = next(t for t in tools if t.name == "seo_gsc_archive")
    assert tool.inputSchema["required"] == ["database"]
    assert tool.inputSchema["properties"]["action"]["default"] == "status"
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.openWorldHint is True
    assert tool.annotations.destructiveHint is False
