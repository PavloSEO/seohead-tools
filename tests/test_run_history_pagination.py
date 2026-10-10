"""Bounded, read-only run history through core, actual CLI and initialized MCP."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from seohead import cli
from seohead.mcp import handlers
from seohead.projects import run_observation
from seohead.projects.observer import observe
from seohead.projects.workspace import create_project
from tests.test_project_observer_sites import _prepare_with_competitors

ROOT = Path(__file__).resolve().parents[1]


def _start(root: Path) -> str:
    identifier = str(uuid.uuid4())
    run_observation.start(
        root,
        kind="native",
        mode="spider",
        max_urls=10,
        config_fingerprint="synthetic-history",
        artifact=None,
        run_id=identifier,
    )
    return identifier


def _history(root: Path, count: int = 50) -> tuple[list[str], list[str]]:
    active = [_start(root)]
    terminal = []
    for index in range(count):
        identifier = _start(root)
        run_observation.finish(
            root,
            identifier,
            state=("finished", "failed", "partial", "cancelled")[index % 4],
            reason="synthetic history",
        )
        terminal.append(identifier)
    active.append(_start(root))
    return terminal, active


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def _terminal_ids(page: dict) -> list[str]:
    return [row["id"] for row in page["items"] if row["state"] != "running"]


def _assert_page(page, terminal, active, offset, limit):
    expected = list(reversed(terminal))[offset : offset + limit]
    assert _terminal_ids(page) == expected
    assert {row["id"] for row in page["items"] if row["state"] == "running"} == set(active)
    assert len(page["items"]) == len(expected) + len(active) <= 100
    assert page["total"] == len(terminal) + len(active)
    assert page["active_total"] == len(active)
    assert page["terminal_total"] == len(terminal)
    more = offset + len(expected) < len(terminal)
    assert page["has_more"] is more
    assert page["pagination"] == {
        "offset": offset,
        "limit": limit,
        "total": len(terminal),
        "next_offset": offset + len(expected) if more else None,
        "has_more": more,
    }
    assert page["retention"] == {
        "max_runs": 100,
        "eviction": "oldest_terminal_on_start",
        "evicted_total": None,
    }


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://history.example.test/")
    return root


def test_fifty_terminal_ids_page_once_with_active_runs_and_unchanged_default(project):
    terminal, active = _history(project)
    before = _files(project)
    first = run_observation.status(project)
    _assert_page(first, terminal, active, 0, 20)
    seen = []
    for offset in (0, 20, 40, 50, 1000):
        page = run_observation.status(project, offset=offset)
        _assert_page(page, terminal, active, offset, 20)
        assert page["revision"] == first["revision"]
        seen.extend(_terminal_ids(page))
    assert seen == list(reversed(terminal))
    assert len(set(seen)) == 50
    assert _files(project) == before


def test_order_is_admission_not_finish_time_and_revision_changes_only_on_write(project):
    identifiers = [_start(project) for _ in range(3)]
    for identifier in reversed(identifiers):
        run_observation.finish(project, identifier, state="finished", reason="synthetic")
    initial = run_observation.status(project, limit=1)
    assert _terminal_ids(initial) == identifiers[-1:]
    assert _terminal_ids(run_observation.status(project, offset=1, limit=1)) == identifiers[-2:-1]
    assert run_observation.status(project)["revision"] == initial["revision"]
    latest = _start(project)
    updated = run_observation.status(project, offset=1, limit=1)
    assert updated["revision"] > initial["revision"]
    assert updated["active_total"] == 1 and updated["items"][0]["id"] == latest


@pytest.mark.parametrize("count", [3, 100])
def test_empty_and_all_active_pages_remain_bounded_and_do_not_invent_terminals(project, count):
    _assert_page(run_observation.status(project, offset=3, limit=2), [], [], 3, 2)
    active = [_start(project) for _ in range(count)]
    document = project / run_observation.NAME
    saved = json.loads(document.read_text())
    saved["runs"][0].update(pid=2**31 - 1, controller_pid=2**31 - 1)
    document.write_text(json.dumps(saved))
    before = _files(project)
    page = run_observation.status(project, offset=50, limit=1)
    _assert_page(page, [], active, 50, 1)
    abandoned = next(row for row in page["items"] if row["id"] == active[0])
    assert abandoned["pid_state"] in {"abandoned", "unknown", "stale"}
    assert abandoned["state"] == "running"
    if count == 100:
        with pytest.raises(ValueError, match="active runs cannot be evicted"):
            _start(project)
    assert _files(project) == before


@pytest.mark.parametrize("value", [-1, True, False, 1.0, "1", None])
@pytest.mark.parametrize("name", ["offset", "limit"])
def test_core_and_shared_handler_reject_invalid_page_arguments(project, name, value):
    with pytest.raises(ValueError):
        run_observation.status(project, **{name: value})
    with pytest.raises(ValueError):
        handlers.project_observe(str(project), **{f"run_{name}": value})


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_outside_retention_bound_is_rejected(project, limit):
    with pytest.raises(ValueError):
        run_observation.status(project, limit=limit)
    with pytest.raises(ValueError):
        observe(str(project), run_limit=limit)


@pytest.mark.parametrize("damage", ["duplicate", "foreign_project", "bad_shape"])
def test_malformed_or_foreign_run_document_is_rejected_without_repair(project, damage):
    _history(project, 1)
    path = project / run_observation.NAME
    saved = json.loads(path.read_text())
    if damage == "duplicate":
        saved["runs"][1]["id"] = saved["runs"][0]["id"]
    elif damage == "foreign_project":
        saved["project_uuid"] = str(uuid.uuid4())
    else:
        saved["runs"] = {}
    path.write_text(json.dumps(saved))
    before = _files(project)
    with pytest.raises(ValueError):
        run_observation.status(project, offset=1)
    assert _files(project) == before


def test_existing_retention_evicts_only_oldest_terminal_and_reports_retained_totals(project):
    active = _start(project)
    terminal = []
    for _ in range(101):
        identifier = _start(project)
        run_observation.finish(project, identifier, state="finished", reason="synthetic")
        terminal.append(identifier)
    before = _files(project)
    page = run_observation.status(project, limit=100)
    _assert_page(page, terminal[-99:], [active], 0, 100)
    assert not set(terminal[:2]) & {row["id"] for row in page["items"]}
    assert _files(project) == before


def test_page_scope_applies_per_site_and_root_runs_remain_primary(tmp_path):
    root = tmp_path / "owner"
    prepared = _prepare_with_competitors(root)["preparation"]
    children = [root / item["directory"] for item in prepared["competitors"]]
    histories = [_history(path, 4) for path in [root, *children]]
    before = _files(root)
    snapshot = observe(str(root), run_offset=2, run_limit=2)
    assert snapshot["runs"] == snapshot["sites"]["items"][0]["runs"]
    identities = set()
    for site, (terminal, active) in zip(snapshot["sites"]["items"], histories, strict=True):
        page = site["runs"]
        _assert_page(page, terminal, active, 2, 2)
        selected = {row["id"] for row in page["items"]}
        assert not selected & identities
        identities.update(selected)
    assert _files(root) == before


def _command(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "seohead.cli", "project-observe", *arguments],
        cwd=ROOT,
        env=os.environ | {"PYTHONPATH": str(ROOT), "SEOHEAD_RUN_LOG": "off"},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_flags_json_and_explicit_overrides_preserve_directory_and_page(project):
    terminal, active = _history(project, 7)
    before = _files(project)
    document = {"directory": str(project), "run_offset": 3, "run_limit": 2, "scan_limit": 3}
    for arguments, offset in (
        (["--directory", str(project), "--run-offset", "3", "--run-limit", "2"], 3),
        (["--input", json.dumps(document)], 3),
        (["--input", json.dumps(document), "--run-offset", "5"], 5),
    ):
        result = _command(*arguments)
        assert result.returncode == 0, result.stderr
        _assert_page(json.loads(result.stdout)["runs"], terminal, active, offset, 2)
    parser = cli.build_parser()
    _, kwargs = cli._build_kwargs(
        "project-observe",
        parser.parse_args(
            [
                "project-observe",
                "--input",
                json.dumps(document),
            ]
        ),
    )
    assert kwargs == document
    for value in (-1, True, 1.0, "1"):
        result = _command("--input", json.dumps({**document, "run_offset": value}))
        assert result.returncode != 0
    assert _files(project) == before


def test_initialized_stdio_mcp_pages_all_fifty_ids_and_rejects_coercion(project):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    terminal, active = _history(project)
    before = _files(project)

    async def run():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "seohead.mcp.mcp_server"],
            cwd=str(ROOT),
            env={"PYTHONPATH": str(ROOT), "SEOHEAD_RUN_LOG": "off"},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            schema = tools["seo_project_observe"].inputSchema["properties"]
            assert schema["run_offset"]["default"] == 0
            assert schema["run_limit"]["default"] == 20
            seen, revisions = [], set()
            for offset in (0, 20, 40, 50):
                response = await session.call_tool(
                    "seo_project_observe",
                    {
                        "directory": str(project),
                        "run_offset": offset,
                        "run_limit": 20,
                    },
                )
                assert not response.isError, response
                payload = response.structuredContent
                payload = payload.get("result", payload)
                page = payload["runs"]
                _assert_page(page, terminal, active, offset, 20)
                seen.extend(_terminal_ids(page))
                revisions.add(page["revision"])
            assert seen == list(reversed(terminal)) and len(revisions) == 1
            for name, value in (
                ("run_offset", -1),
                ("run_offset", True),
                ("run_offset", 1.0),
                ("run_offset", "1"),
                ("run_limit", False),
                ("run_limit", 0),
                ("run_limit", 101),
                ("run_limit", 2.0),
                ("run_limit", "2"),
            ):
                response = await session.call_tool(
                    "seo_project_observe",
                    {
                        "directory": str(project),
                        name: value,
                    },
                )
                assert response.isError, (name, value, response)

    asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert _files(project) == before


@pytest.fixture(autouse=True)
def _several_active_runs_in_one_process(monkeypatch):
    """These fixtures keep several running records in one process, which start() now refuses."""
    monkeypatch.setattr(run_observation, "_owner_busy", lambda run: False)
