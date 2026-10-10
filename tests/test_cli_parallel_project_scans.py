"""Three explicit local project crawls share one origin pace and survive one cancellation."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from pathlib import Path

import pytest

from seohead.projects import run_observation
from seohead.projects.workspace import create_project
from seohead.storage.native_scan import NativeScan

BUILD = "b" * 40


@contextlib.contextmanager
def _owned_site():
    hits: list[tuple[float, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            hits.append((time.monotonic(), self.path))
            if self.path == "/robots.txt":
                body, media_type = b"User-agent: *\nAllow: /\n", "text/plain"
            elif self.path == "/":
                body, media_type = (
                    b'<html><body><a href="/slow">next</a></body></html>',
                    "text/html",
                )
            elif self.path == "/slow":
                time.sleep(3)
                body, media_type = b"<html><body>slow retained page</body></html>", "text/html"
            else:
                body, media_type = b"not found", "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://crawl.localhost:{server.server_port}/", hits
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _pages(path: Path) -> int:
    try:
        with contextlib.closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as con:
            return con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    except sqlite3.OperationalError as exc:
        if "no such table: pages" in str(exc):
            return 0
        raise


def _command(root: Path, project: Path, observer_run_id: str) -> subprocess.Popen[str]:
    env = os.environ | {
        "PYTHONPATH": str(root),
        "SEOHEAD_ALLOW_PRIVATE_HOSTS": "crawl.localhost,127.0.0.1",
    }
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "seohead.cli",
            "crawl-site",
            "--project",
            str(project),
            "--observer-run-id",
            observer_run_id,
            "--max-urls",
            "2",
            "--producer-build",
            BUILD,
            "--set",
            "rendering.mode=raw",
            "--set",
            "limits.max_requests=10",
            "--set",
            "limits.max_crawl_seconds=30",
        ],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.mark.skipif(os.name == "nt", reason="uses POSIX signals and process groups")
def test_three_project_scans_share_pacing_and_one_sigint_does_not_stop_the_others(tmp_path):
    root = Path(__file__).parents[1]
    project = tmp_path / "project"
    with _owned_site() as (target, hits):
        create_project(project, target)
        observer_ids = [str(uuid.uuid4()) for _ in range(3)]
        processes = [_command(root, project, observer_id) for observer_id in observer_ids]
        try:
            deadline = time.monotonic() + 30
            scans: list[Path] = []
            while time.monotonic() < deadline:
                scans = [
                    path
                    for path in (project / "scans").glob("*.sqlite")
                    if not path.name.startswith(".native-scan-")
                ]
                if len(scans) == 3 and all(_pages(path) >= 1 for path in scans):
                    break
                time.sleep(0.05)
            assert len(scans) == 3 and all(_pages(path) >= 1 for path in scans)

            processes[0].send_signal(signal.SIGINT)
            cancelled_stdout, cancelled_stderr = processes[0].communicate(timeout=30)
            assert processes[0].returncode in {0, 2}, cancelled_stderr
            cancelled = json.loads(cancelled_stdout)
            assert cancelled["partial"] is True and cancelled["finish_reason"] == "interrupted"

            completed = []
            for process in processes[1:]:
                stdout, stderr = process.communicate(timeout=45)
                assert process.returncode == 0, stderr
                completed.append(json.loads(stdout))
            assert all(
                result["partial"] is False and result["finish_reason"] == "finished"
                for result in completed
            )
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)

    runs = run_observation.status(project, limit=3)["items"]
    assert len(runs) == 3 and len({run["id"] for run in runs}) == 3
    assert {run["id"] for run in runs} == set(observer_ids)
    assert sum(run["state"] == "partial" for run in runs) == 1
    assert sum(run["state"] == "finished" for run in runs) == 2
    assert all(run["collector"]["origin"] == "crawl.localhost" for run in runs)
    assert all(run["collector"]["aggregate_max_requests_per_second"] == 2.0 for run in runs)
    assert all(NativeScan.inspect(path)["scan"]["scan_uuid"] for path in scans)

    timestamps = [stamp for stamp, _path in hits]
    assert len(timestamps) >= 6
    assert all(after - before >= 0.35 for before, after in pairwise(timestamps))
