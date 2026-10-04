"""Durable project run observation stays read-only for second-screen consumers."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from multiprocessing import get_context
from pathlib import Path

from seohead.projects import run_observation
from seohead.projects.workspace import create_project
from seohead.sf import cli as sf_cli


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    return root


def _start_in_child(directory: str, ready, release) -> None:
    run_observation.start(
        directory,
        kind="native",
        mode="spider",
        max_urls=100,
        config_fingerprint="synthetic",
        artifact=Path(directory) / "scans" / "live.sqlite",
    )
    ready.set()
    release.wait(10)


def _finish_in_child(directory: str, barrier, outcomes) -> None:
    barrier.wait(10)
    run = run_observation.start(
        directory,
        kind="native",
        mode="spider",
        max_urls=100,
        config_fingerprint="synthetic",
        artifact=Path(directory) / "scans" / f"{os.getpid()}.sqlite",
    )
    run_observation.finish(directory, run["id"], state="finished", reason="finished")
    outcomes.put(run["id"])


def test_project_run_records_real_counters_and_no_false_site_percentage(tmp_path):
    root = _project(tmp_path)
    run = run_observation.start(
        root,
        kind="native",
        mode="spider",
        max_urls=100,
        config_fingerprint="synthetic",
        artifact=root / "scans" / "scan.sqlite",
    )
    run_observation.progress(root, run["id"], fetched=9, queued=91, inflight=1, excluded=3)
    run_observation.phase(root, run["id"], "analysis")

    live = run_observation.status(root)
    item = live["items"][0]
    assert item["pid_state"] == "live"
    assert item["collector"] == {
        "mode": "spider",
        "max_urls": 100,
        "max_requests": 0,
        "max_crawl_seconds": 0,
        "max_requests_per_second": None,
        "config_fingerprint": "synthetic",
        "resumed": False,
    }
    assert item["artifact"] == "scans/scan.sqlite"
    assert item["counters"] == {
        "fetched": 9,
        "queued": 91,
        "inflight": 1,
        "excluded": 3,
        "rate_per_second": None,
    }
    assert "percent" not in item and "site_total" not in item
    assert [event["phase"] for event in item["events"]] == ["admission", "collection", "analysis"]

    run_observation.finish(
        root,
        run["id"],
        state="partial",
        reason="url_limit",
        counters={
            "fetched": 100,
            "queued": 17,
            "inflight": 0,
            "excluded": 3,
            "rate_per_second": 2.0,
        },
    )
    retained = run_observation.status(root)["items"][0]
    assert retained["state"] == "partial"
    assert retained["pid_state"] == "retained"
    assert retained["finish_reason"] == "url_limit"
    assert retained["counters"]["rate_per_second"] == 2.0


def test_observer_read_marks_dead_launcher_abandoned_without_mutating_document(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)
    run = run_observation.start(
        root,
        kind="native",
        mode="spider",
        max_urls=10,
        config_fingerprint="synthetic",
        artifact=root / "scans" / "scan.sqlite",
    )
    document = root / run_observation.NAME
    before = document.read_bytes()

    def missing(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", missing)
    result = run_observation.status(root)

    assert result["items"][0]["id"] == run["id"]
    assert result["items"][0]["state"] == "running"
    assert result["items"][0]["pid_state"] == "abandoned"
    assert document.read_bytes() == before


def test_second_process_can_read_a_live_attempt_then_reconnect_to_retained_history(tmp_path):
    root = _project(tmp_path)
    context = get_context("spawn")
    ready, release = context.Event(), context.Event()
    child = context.Process(target=_start_in_child, args=(str(root), ready, release))
    child.start()
    try:
        assert ready.wait(10)
        running = run_observation.status(root)
        assert running["items"][0]["pid_state"] == "live"
        assert running["items"][0]["state"] == "running"
    finally:
        release.set()
        child.join(timeout=15)
    assert child.exitcode == 0

    # The child ended without a terminal update.  A replacement observer names
    # that situation rather than changing the retained collector record.
    deadline = time.monotonic() + 2
    while (
        time.monotonic() < deadline
        and run_observation.status(root)["items"][0]["pid_state"] == "live"
    ):
        time.sleep(0.02)
    assert run_observation.status(root)["items"][0]["pid_state"] == "abandoned"


def test_project_bound_sf_exports_records_analysis_without_invented_counters(tmp_path, monkeypatch):
    root = _project(tmp_path)
    out = root / "reports" / "sf"

    class Result:
        def __init__(self) -> None:
            self.run = {"crawl_valid": True}

    monkeypatch.setattr(sf_cli, "run_audit", lambda **_kwargs: Result())
    monkeypatch.setattr(sf_cli, "write_json", lambda _result, path: path)
    monkeypatch.setattr(sf_cli, "write_markdown", lambda _result, path: path)

    assert (
        sf_cli.main(
            [
                "run",
                "--exports-dir",
                str(tmp_path / "exports"),
                "--project",
                str(root),
                "--out",
                str(out),
                "--quiet",
            ]
        )
        == 0
    )
    run = run_observation.status(root)["items"][0]
    assert run["kind"] == "screaming_frog"
    assert run["collector"]["mode"] == "sf_exports"
    assert run["artifact"] == "reports/sf"
    assert run["state"] == "finished"
    assert run["counters"] == {
        "fetched": None,
        "queued": None,
        "inflight": None,
        "excluded": None,
        "rate_per_second": None,
    }
    assert [event["phase"] for event in run["events"]] == ["admission", "analysis", "finalizing"]


def test_actual_collector_child_is_distinct_from_its_live_controller(tmp_path):
    root = _project(tmp_path)
    run = run_observation.start(
        root,
        kind="screaming_frog",
        mode="sf_live",
        max_urls=0,
        config_fingerprint="synthetic",
        artifact=root / "reports" / "sf",
        counters={"fetched": None, "queued": None, "inflight": None, "excluded": None},
    )
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        run_observation.collector_started(root, run["id"], child.pid)
        active = run_observation.status(root)["items"][0]
        assert active["controller"]["pid"] == os.getpid()
        assert active["collector_runtime"]["pid"] == child.pid
        assert active["controller"]["pid"] != active["collector_runtime"]["pid"]
        assert active["collector_runtime"]["state"] == "live"
        assert active["collector_runtime"]["observed_at"]
    finally:
        child.terminate()
        child.wait(timeout=10)

    finished_child = run_observation.status(root)["items"][0]
    assert finished_child["state"] == "running"
    assert finished_child["collector_runtime"]["state"] == "abandoned"


def test_two_processes_keep_both_terminal_run_updates_after_revision_contention(tmp_path):
    root = _project(tmp_path)
    context = get_context("spawn")
    barrier, outcomes = context.Barrier(2), context.Queue()
    children = [
        context.Process(target=_finish_in_child, args=(str(root), barrier, outcomes))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=20)
    assert all(child.exitcode == 0 for child in children)
    ids = {outcomes.get(timeout=2), outcomes.get(timeout=2)}
    rows = run_observation.status(root)["items"]
    assert {row["id"] for row in rows} == ids
    assert {row["state"] for row in rows} == {"finished"}


def test_out_of_range_collector_pid_is_unknown_not_an_observer_crash(tmp_path):
    root = _project(tmp_path)
    run = run_observation.start(
        root,
        kind="screaming_frog",
        mode="sf_live",
        max_urls=0,
        config_fingerprint="synthetic",
        artifact=root / "reports" / "sf",
    )
    run_observation.collector_started(root, run["id"], 10**100)
    assert run_observation.status(root)["items"][0]["collector_runtime"]["state"] == "unknown"
