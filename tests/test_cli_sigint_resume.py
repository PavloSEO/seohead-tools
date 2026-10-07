"""A real CLI SIGINT retains native evidence and makes the same scan resumable."""

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
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from seohead.projects.workspace import create_project
from seohead.storage.native_scan import NativeScan

BUILD = "a" * 40


@contextlib.contextmanager
def _slow_site():
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            hits.append(self.path)
            if self.path == "/robots.txt":
                body, media_type = b"User-agent: *\nAllow: /\n", "text/plain"
            elif self.path == "/":
                links = "".join(f'<a href="/page-{index}">page</a>' for index in range(7))
                body, media_type = f"<html><body>{links}</body></html>".encode(), "text/html"
            elif self.path.startswith("/page-"):
                time.sleep(0.35)
                body, media_type = b"<html><body>retained page</body></html>", "text/html"
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
    with contextlib.closing(sqlite3.connect(path)) as con:
        return con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]


def _run(command: list[str], *, root: Path) -> subprocess.Popen[str]:
    env = os.environ | {
        "PYTHONPATH": str(root),
        "SEOHEAD_ALLOW_PRIVATE_HOSTS": "crawl.localhost,127.0.0.1",
    }
    return subprocess.Popen(
        command,
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_cli_sigint_keeps_a_project_scan_and_resume_finishes_without_refetching(tmp_path):
    root = Path(__file__).parents[1]
    project = tmp_path / "project"
    with _slow_site() as (target, hits):
        create_project(project, target)
        command = [
            sys.executable,
            "-m",
            "seohead.cli",
            "crawl-site",
            "--project",
            str(project),
            "--max-urls",
            "40",
            "--producer-build",
            BUILD,
            "--set",
            "rendering.mode=raw",
            "--set",
            "limits.max_requests=100",
            "--set",
            "limits.max_crawl_seconds=60",
        ]
        process = _run(command, root=root)
        deadline = time.monotonic() + 20
        scans = []
        while time.monotonic() < deadline:
            scans = list((project / "scans").glob("*.sqlite"))
            if scans and _pages(scans[0]) >= 1:
                break
            time.sleep(0.05)
        assert scans and _pages(scans[0]) >= 1, "the crawl never retained its start page"
        scan = scans[0]
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode in {0, 2}, stderr
        interrupted = json.loads(stdout)
        assert interrupted["scan"] == str(scan)
        assert interrupted["partial"] is True
        assert interrupted["finish_reason"] == "interrupted"
        assert scan.is_file()
        initial_pages = _pages(scan)
        with NativeScan.open(scan) as stored:
            snapshot = stored.resume_snapshot(include_edges=True)
            assert snapshot["scan"]["lifecycle"] == "interrupted"
            assert snapshot["scan"]["crawl_partial"] == 1
            assert snapshot["counts"]["pages"] == initial_pages >= 1
            assert snapshot["counts"]["links"] >= 7
            assert stored.con.execute("SELECT COUNT(*) FROM documents").fetchone()[0] >= 1

        resumed = _run(
            [
                sys.executable,
                "-m",
                "seohead.cli",
                "crawl-site",
                "--project",
                str(project),
                "--resume",
                str(scan),
                "--producer-build",
                BUILD,
            ],
            root=root,
        )
        stdout, stderr = resumed.communicate(timeout=45)
        assert resumed.returncode == 0, stderr
        completed = json.loads(stdout)
        assert completed["resumed"] is True
        assert completed["partial"] is False
        assert completed["finish_reason"] == "finished"
        assert scan.is_file()
        snapshot = NativeScan.inspect(scan)
        assert snapshot["scan"]["lifecycle"] == "finished"
        assert snapshot["counts"]["pages"] == 8
        assert snapshot["counts"]["links"] >= 7
        with contextlib.closing(sqlite3.connect(scan)) as stored:
            assert stored.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 8
        served = Counter(hits)
        assert served["/"] == 1
        assert len([path for path in served if path.startswith("/page-")]) == 7
