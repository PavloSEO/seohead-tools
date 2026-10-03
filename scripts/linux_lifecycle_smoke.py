#!/usr/bin/env python3
"""Exercise a clean Linux source install, upgrade, rollback, and local JS scan."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURE_HTML = b"""<!doctype html>
<html><head><title>Static lifecycle fixture</title></head>
<body><div id="app"></div>
<script>document.querySelector('#app').innerHTML =
  '<main><h1>Rendered lifecycle marker</h1></main>';
document.title = 'Rendered lifecycle title';</script></body></html>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE_HTML)))
        self.end_headers()
        self.wfile.write(FIXTURE_HTML)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 900,
) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def process_group_rss_kib(process_group: int) -> int:
    """Sample resident memory for the command and its browser descendants on Linux."""
    total = 0
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            stat = stat_path.read_text()
            fields = stat[stat.rfind(")") + 2 :].split()
            if int(fields[2]) != process_group:
                continue
            status_path = stat_path.parent / "status"
            for line in status_path.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
        except (OSError, ValueError, IndexError):
            continue
    return total


def run_measured(
    args: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 900
) -> tuple[str, dict[str, float | int]]:
    started = time.monotonic()
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    peak_rss_kib = 0
    deadline = started + timeout
    while process.poll() is None:
        peak_rss_kib = max(peak_rss_kib, process_group_rss_kib(process.pid))
        if time.monotonic() >= deadline:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise TimeoutError(f"command exceeded {timeout}s: {args[0]}")
        time.sleep(0.1)
    stdout, stderr = process.communicate()
    peak_rss_kib = max(peak_rss_kib, process_group_rss_kib(process.pid))
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
    return stdout.strip(), {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "sampled_process_tree_peak_rss_kib": peak_rss_kib,
    }


def directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink():
            try:
                total += child.stat().st_size
            except FileNotFoundError:
                continue
    return total


def identity(python: Path, env: dict[str, str], cwd: Path) -> dict[str, str]:
    value = json.loads(
        run(
            [
                str(python),
                "-c",
                "import json; from seohead.build_provenance import packaged_provenance; "
                "p=packaged_provenance(); print(json.dumps({'version': p.version, "
                "'revision': p.revision}))",
            ],
            cwd=cwd,
            env=env,
        )
    )
    return value


def switch_current(current: Path, release: Path, suffix: str) -> None:
    temporary = current.with_name(f".current-{suffix}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, current)


def scan_once(
    *,
    command: Path,
    python: Path,
    release: Path,
    project: Path,
    config: Path,
    url: str,
    expected_revision: str,
    label: str,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(project.parent / "operator-home"),
            "PLAYWRIGHT_BROWSERS_PATH": str(release / "browsers"),
            "SEOHEAD_ALLOW_PRIVATE_HOSTS": "127.0.0.1",
            "SEOHEAD_RUN_LOG": str(project / "runs.jsonl"),
            "TMPDIR": str(project.parent.parent / "tmp"),
        }
    )
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    before = identity(python, env, project)
    if before["revision"] != expected_revision:
        raise AssertionError(f"installed provenance differs: {before!r}")
    version_text = run([str(command), "--version"], cwd=project, env=env)
    if version_text != f"seohead {before['version']}":
        raise AssertionError(f"CLI version disagrees with packaged provenance: {version_text}")

    scan = project / "scans" / f"{label}.sqlite"
    scan.parent.mkdir(parents=True, exist_ok=True)
    stdout, measurements = run_measured(
        [
            str(command),
            "crawl-site",
            "--url",
            url,
            "--config",
            str(config),
            "--scan-out",
            str(scan),
            "-q",
        ],
        cwd=project,
        env=env,
    )
    result = json.loads(stdout)
    if result.get("finish_reason") != "finished" or result.get("urls_collected") != 1:
        raise AssertionError(f"synthetic crawl did not finish cleanly: {result!r}")
    rendered = result.get("render_escalation") or {}
    if rendered.get("render_requests") != 1:
        raise AssertionError(f"JavaScript rendering did not run: {rendered!r}")
    if not scan.is_file() or scan.stat().st_size == 0:
        raise AssertionError("crawl did not create the requested scan artifact")

    with sqlite3.connect(f"file:{scan}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT title, representation FROM pages LIMIT 1").fetchone()
    if row != ("Rendered lifecycle title", "rendered"):
        raise AssertionError(f"saved scan is missing rendered page evidence: {row!r}")

    status = json.loads(
        run(
            [str(command), "scan", "status", "--scan", str(scan)],
            cwd=project,
            env=env,
        )
    )
    if status.get("source", {}).get("writer_revision") != expected_revision:
        raise AssertionError(f"scan lost its producer revision: {status!r}")
    listing = json.loads(
        run(
            [str(command), "scan", "list", "--directory", str(scan.parent)],
            cwd=project,
            env=env,
        )
    )
    if listing.get("total", 0) < 1:
        raise AssertionError(f"scan artifact was not discoverable: {listing!r}")
    measurements["artifact_bytes"] = scan.stat().st_size
    measurements["producer_version"] = before["version"]
    measurements["producer_revision"] = before["revision"]
    measurements["scan_path"] = scan.relative_to(project.parent).as_posix()
    return measurements


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref", required=True, help="immutable base commit for the upgrade test"
    )
    parser.add_argument("--metrics-out", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    if sys.platform != "linux" or not Path("/proc/self/stat").exists():
        parser.error("this disposable lifecycle smoke requires Linux procfs")

    repository = args.project_root.resolve()
    if run(["git", "status", "--porcelain"], cwd=repository):
        parser.error("run from a clean checkout so packaged producer revisions are verifiable")
    candidate_revision = run(["git", "rev-parse", "HEAD"], cwd=repository)
    base_revision = run(["git", "rev-parse", f"{args.base_ref}^{{commit}}"], cwd=repository)
    if base_revision == candidate_revision:
        parser.error("base and candidate revisions must differ")

    temp_root = Path(tempfile.mkdtemp(prefix="seohead-linux-lifecycle-"))
    base_source = temp_root / "source-base"
    releases = temp_root / "releases"
    install_root = temp_root / "install"
    project = temp_root / "workspace" / "synthetic-project"
    config = temp_root / "workspace" / "crawl.json"
    browser_cache = temp_root / "browser-cache"
    uv_cache = temp_root / "uv-cache"
    install_root.mkdir()
    releases.mkdir()
    project.mkdir(parents=True)
    browser_cache.mkdir()
    uv_cache.mkdir()
    config.write_text(
        json.dumps(
            {
                "limits": {"max_urls": 1, "max_depth": 0},
                "speed": {"min_delay_seconds": 0.05},
                "robots": {"policy": "ignore"},
                "rendering": {
                    "mode": "js",
                    "escalation": {
                        "policy": "full",
                        "sample_per_pattern": 1,
                        "max_render_urls": 1,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    worktree_added = False
    metrics: dict[str, Any] = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "uv": run(["uv", "--version"]),
        "dependency_profile": ["render", "reports"],
        "base_revision": base_revision,
        "candidate_revision": candidate_revision,
        "synthetic_fixture": {"page_count": 1, "html_bytes": len(FIXTURE_HTML)},
        "network_scope": "localhost fixture only; no paid/provider/customer calls",
    }

    try:
        run(["git", "worktree", "add", "--detach", str(base_source), base_revision], cwd=repository)
        worktree_added = True
        sources = {base_revision: base_source, candidate_revision: repository}
        environments: dict[str, Path] = {}
        for revision, source in sources.items():
            release = releases / revision
            release.mkdir()
            environment = release / "venv"
            build_env = os.environ.copy()
            build_env.update(
                {
                    "UV_PROJECT_ENVIRONMENT": str(environment),
                    "UV_CACHE_DIR": str(uv_cache),
                    "PLAYWRIGHT_BROWSERS_PATH": str(release / "browsers"),
                }
            )
            started = time.monotonic()
            run(
                [
                    "uv",
                    "sync",
                    "--locked",
                    "--no-dev",
                    "--extra",
                    "render",
                    "--extra",
                    "reports",
                    "--no-editable",
                ],
                cwd=source,
                env=build_env,
                timeout=1800,
            )
            release_metrics = {
                "install_seconds": round(time.monotonic() - started, 3),
                "venv_bytes": directory_size(environment),
            }
            metrics.setdefault("installations", {})[revision] = release_metrics
            environments[revision] = environment

        candidate_env = environments[candidate_revision]
        candidate_browser_env = os.environ.copy()
        candidate_browser_env["PLAYWRIGHT_BROWSERS_PATH"] = str(
            releases / candidate_revision / "browsers"
        )
        started = time.monotonic()
        run(
            [
                str(candidate_env / "bin/python"),
                "-m",
                "playwright",
                "install",
                "--with-deps",
                "chromium",
            ],
            env=candidate_browser_env,
            timeout=1800,
        )
        metrics["browser_install_seconds"] = round(time.monotonic() - started, 3)
        metrics["browser_bytes"] = directory_size(releases / candidate_revision / "browsers")
        for revision in sources:
            if revision == candidate_revision:
                continue
            browser_env = os.environ.copy()
            browser_env["PLAYWRIGHT_BROWSERS_PATH"] = str(releases / revision / "browsers")
            run(
                [
                    str(environments[revision] / "bin/python"),
                    "-m",
                    "playwright",
                    "install",
                    "chromium",
                ],
                env=browser_env,
                timeout=1800,
            )
            metrics["browser_bytes"] += directory_size(releases / revision / "browsers")
        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"

        try:
            current = install_root / "current"
            command = install_root / "bin" / "seohead"
            command.parent.mkdir()
            command.symlink_to(current / "venv" / "bin" / "seohead")

            switch_current(current, releases / base_revision, "base")
            metrics["base_scan"] = scan_once(
                command=command,
                python=environments[base_revision] / "bin/python",
                release=releases / base_revision,
                project=project,
                config=config,
                url=url,
                expected_revision=base_revision,
                label="base",
            )

            switch_current(current, releases / candidate_revision, "candidate")
            metrics["candidate_scan"] = scan_once(
                command=command,
                python=environments[candidate_revision] / "bin/python",
                release=releases / candidate_revision,
                project=project,
                config=config,
                url=url,
                expected_revision=candidate_revision,
                label="candidate",
            )

            switch_current(current, releases / base_revision, "rollback")
            rollback_env = os.environ.copy()
            rollback_env["PLAYWRIGHT_BROWSERS_PATH"] = str(releases / base_revision / "browsers")
            rollback_identity = identity(
                environments[base_revision] / "bin/python", rollback_env, project
            )
            if rollback_identity["revision"] != base_revision:
                raise AssertionError("rollback did not restore the recorded producer build")
            if run([str(command), "--version"], cwd=project, env=rollback_env) != (
                f"seohead {rollback_identity['version']}"
            ):
                raise AssertionError("rollback CLI version does not match the prior build")
            for name in ("base", "candidate"):
                status = json.loads(
                    run(
                        [
                            str(command),
                            "scan",
                            "status",
                            "--scan",
                            str(project / "scans" / f"{name}.sqlite"),
                        ],
                        cwd=project,
                        env=rollback_env,
                    )
                )
                if status.get("ok") is not True:
                    raise AssertionError(f"rollback could not read the {name} artifact: {status!r}")
            metrics["rollback_revision"] = rollback_identity["revision"]
            metrics["workspace_bytes"] = directory_size(project)
            metrics["temporary_bytes"] = directory_size(project.parent.parent / "tmp")
            metrics["release_and_browser_bytes"] = directory_size(releases)
            metrics["uv_cache_bytes"] = directory_size(uv_cache)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    finally:
        if worktree_added:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(base_source)],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
            )
        shutil.rmtree(temp_root, ignore_errors=True)

    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
