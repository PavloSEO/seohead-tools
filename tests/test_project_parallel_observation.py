"""Concurrent local project runs retain separate observation and pacing facts."""

from __future__ import annotations

import threading

from seohead.projects import run_observation
from seohead.projects.origin_pacing import ProjectOriginPacer
from seohead.projects.workspace import create_project

TARGET = "https://example.test/"


def _project(tmp_path):
    return create_project(tmp_path / "project", TARGET)["path"]


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
