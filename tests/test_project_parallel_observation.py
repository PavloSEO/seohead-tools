"""Concurrent local project runs retain separate observation and pacing facts."""

from __future__ import annotations

import multiprocessing
import threading
import time
from contextlib import closing

import pytest

from seohead.projects import run_observation
from seohead.projects.origin_pacing import ProjectOriginPacer
from seohead.projects.workspace import create_project

TARGET = "https://example.test/"


def _project(tmp_path):
    return create_project(tmp_path / "project", TARGET)["path"]


def _reserve_in_process(project: str, start, results) -> None:
    pacer = ProjectOriginPacer(project, TARGET, minimum_delay_seconds=0, max_requests_per_second=20)
    if not start.wait(timeout=10):
        raise RuntimeError("concurrent pacing start did not arrive")
    results.put(pacer.reserve())


def test_three_simultaneous_runs_keep_independent_uuid_artifact_and_progress(tmp_path):
    project = _project(tmp_path)
    barrier = threading.Barrier(3)
    failures: list[BaseException] = []
    runs = []

    def record(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            run = run_observation.start(
                project,
                kind="native",
                mode="spider",
                max_urls=10,
                max_requests=20,
                max_crawl_seconds=30,
                max_requests_per_second=2.0,
                config_fingerprint=f"config-{index}",
                artifact=f"{project}/scans/run-{index}.sqlite",
                origin="example.test",
                aggregate_max_requests_per_second=2.0,
            )
            run_observation.progress(
                project,
                run["id"],
                fetched=index + 1,
                queued=10 - index,
                inflight=1,
                excluded=0,
                rate_per_second=1.0,
            )
            runs.append(run)
        except BaseException as exc:  # pragma: no cover - assertion happens in parent thread
            failures.append(exc)

    threads = [threading.Thread(target=record, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not failures
    assert len({run["id"] for run in runs}) == 3

    status = run_observation.status(project, limit=3)
    assert status["active_total"] == 3
    assert {row["source_kind"] for row in status["items"]} == {"native"}
    assert {row["artifact"] for row in status["items"]} == {
        f"scans/run-{index}.sqlite" for index in range(3)
    }
    assert {row["collector"]["config_fingerprint"] for row in status["items"]} == {
        f"config-{index}" for index in range(3)
    }
    assert all(
        row["collector"]["aggregate_max_requests_per_second"] == 2.0 for row in status["items"]
    )
    assert all(row["telemetry"]["sampled_at"] for row in status["items"])


def test_project_origin_pacer_reserves_one_shared_host_schedule(tmp_path):
    project = _project(tmp_path)
    barrier = threading.Barrier(3)
    waits: list[float] = []
    failures: list[BaseException] = []

    def reserve() -> None:
        try:
            pacer = ProjectOriginPacer(
                project, TARGET, minimum_delay_seconds=0, max_requests_per_second=20
            )
            barrier.wait(timeout=5)
            waits.append(pacer.reserve())
        except BaseException as exc:  # pragma: no cover - assertion happens in parent thread
            failures.append(exc)

    threads = [threading.Thread(target=reserve) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not failures
    assert len(waits) == 3
    ordered = sorted(waits)
    assert ordered[0] < 0.01
    assert ordered[1] >= 0.03
    assert ordered[2] >= 0.08


def test_first_open_is_private_and_safe_across_three_processes(tmp_path):
    project = _project(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_reserve_in_process, args=(project, start, results))
        for _ in range(3)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    waits = sorted(results.get(timeout=5) for _ in processes)
    assert waits[0] < 0.01 and waits[1] >= 0.03 and waits[2] >= 0.08
    pacer = ProjectOriginPacer(project, TARGET, minimum_delay_seconds=0)
    pacer.reserve()
    assert pacer.path.stat().st_mode & 0o777 == 0o600
    pacer.path.unlink()
    assert not pacer.path.exists()


def test_pacer_refuses_unbounded_intervals_and_future_store_waits(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError, match="60 second safety bound"):
        ProjectOriginPacer(project, TARGET, minimum_delay_seconds=61)
    pacer = ProjectOriginPacer(project, TARGET, minimum_delay_seconds=0)
    pacer.reserve()
    import sqlite3

    with closing(sqlite3.connect(pacer.path)) as con:
        con.execute("UPDATE origin_turns SET next_at=?", (time.time() + 61,))
        con.commit()
    with pytest.raises(ValueError, match="excessive wait"):
        pacer.reserve()
