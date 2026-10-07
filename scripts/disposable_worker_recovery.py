#!/usr/bin/env python3
"""Exercise an owned Linux SSH worker against a genuinely full bounded tmpfs.

This is a disposable CI fixture, not a service launcher. It uses the existing
ASGI app, queue, collector, TLS helper and local system sshd. SSH/API/proxy bind
only loopback; the synthetic HTTP target binds one owned RFC1918 interface.
No host SSH config, user account, public endpoint, or production secret is used.
"""

from __future__ import annotations

import argparse
import errno
import http.client
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.disposable_service_profile import TLSReverseProxyHandler, _certificate  # noqa: E402
from scripts.linux_lifecycle_smoke import run, validate_revision  # noqa: E402

HOST = "worker-fixture.example.test"
PROJECT = "fixture"
TMPFS_BYTES = 8 * 1024 * 1024
MARKER = "disposable-worker-fixture.json"


def require_linux() -> None:
    if sys.platform != "linux":
        raise RuntimeError("the worker recovery fixture requires disposable Linux")


def private_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if address.version != 4 or not any(
        address in ipaddress.ip_network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise ValueError("the fixture requires an owned RFC1918 address")
    return str(address)


@contextmanager
def fixture_dns(address: str) -> Iterator[None]:
    """Only DNS is synthetic; policy, sockets and the native collector are real."""
    address = private_address(address)
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        return original(address if host == HOST else host, port, *args, **kwargs)

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original


def backend(state: Path, build: str, *, observe_errors: bool = False):
    import seohead.remote_api.backend as implementation
    from seohead.remote_api.backend import RemoteProjectLimits, SQLiteJobBackend

    if not Path(implementation.__file__).resolve().is_relative_to(ROOT):
        raise RuntimeError("worker imported a different checkout")
    marker = json.loads((state / MARKER).read_text())
    if marker != {"fixture": "disposable-worker-recovery.v1", "build": build}:
        raise ValueError("worker root is not bound to this disposable build")

    def native(*args, **kwargs):
        from seohead.servers.scan_handlers import crawl_site_scan

        try:
            return crawl_site_scan(*args, **kwargs)
        except Exception as exc:
            # Observe actual errors without replacing the collector or changing
            # its exception handling. Record codes only, never paths or messages.
            chain, seen = [], set()
            current: BaseException | None = exc
            while current is not None and id(current) not in seen and len(chain) < 10:
                seen.add(id(current))
                chain.append(
                    {
                        "type": type(current).__name__,
                        "errno": getattr(current, "errno", None),
                        "sqlite_errorcode": getattr(current, "sqlite_errorcode", None),
                    }
                )
                current = current.__cause__ or current.__context__
            (state / "observed-write-error.json").write_text(json.dumps(chain))
            raise

    return SQLiteJobBackend(
        state,
        {PROJECT: RemoteProjectLimits(allowed_private_hosts=frozenset({HOST}))},
        producer_build=build,
        lease_seconds=3,
        runner=native if observe_errors else None,
    )


@contextmanager
def mounted_tmpfs(path: Path) -> Iterator[None]:
    require_linux()
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    options = (
        f"size={TMPFS_BYTES},mode=0700,uid={os.getuid()},gid={os.getgid()},nosuid,nodev,noexec"
    )
    run(["sudo", "-n", "mount", "-t", "tmpfs", "-o", options, "seohead-fixture", str(path)])
    try:
        if run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)]) != "tmpfs":
            raise RuntimeError("fixture mount is not tmpfs")
        if not path.is_mount():
            raise RuntimeError("fixture directory is not a distinct mount")
        yield
    finally:
        run(["sudo", "-n", "umount", str(path)])


def fill_tmpfs(path: Path) -> dict[str, int]:
    """Require a bounded real mount before making a single filesystem write."""
    require_linux()
    parent = path.parent
    if (
        not parent.is_mount()
        or run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(parent)]) != "tmpfs"
    ):
        raise ValueError("refusing to fill an ordinary filesystem")
    capacity = shutil.disk_usage(parent).total
    if not 0 < capacity <= TMPFS_BYTES:
        raise ValueError("fixture filesystem exceeds the bounded tmpfs size")
    written = 0
    with path.open("wb", buffering=0) as handle:
        try:
            while written <= TMPFS_BYTES:
                count = handle.write(b"\0" * 65536)
                if not count:
                    raise RuntimeError("filesystem write made no progress")
                written += count
        except OSError as exc:
            if exc.errno != errno.ENOSPC:
                raise
            return {"errno": exc.errno, "written_bytes": written, "capacity_bytes": capacity}
    raise RuntimeError("bounded filesystem did not report ENOSPC")


def observed_enospc(errors: list[dict[str, Any]]) -> bool:
    return any(
        row.get("errno") == errno.ENOSPC
        or (
            isinstance(row.get("sqlite_errorcode"), int)
            and row["sqlite_errorcode"] & 255 == 13  # SQLite SQLITE_FULL (also on Python 3.10).
        )
        for row in errors
    )


class Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            body, mime = b"User-agent: *\nAllow: /\n", "text/plain"
        else:
            body = (
                b"<!doctype html><html><head><title>Owned worker fixture</title></head>"
                b'<body><h1>Owned fixture</h1><p id="js">raw</p><a href="/second">Next</a>'
                b'<script>document.querySelector("#js").textContent="Rendered worker proof";'
                b'document.title="Rendered worker proof";</script></body></html>'
            )
            mime = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


class Proxy(TLSReverseProxyHandler):
    def do_POST(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=30)
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "connection", "transfer-encoding"}
            }
            connection.request("POST", self.path, body, headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()


@contextmanager
def http_server(server: ThreadingHTTPServer) -> Iterator[int]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def api_server(app) -> Iterator[int]:
    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                if not thread.is_alive():
                    raise RuntimeError("owned ASGI server stopped during startup")
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("owned ASGI server did not start")
            yield listener.getsockname()[1]
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("owned ASGI server did not stop")


@contextmanager
def ssh_worker(root: Path, state: Path, build: str, address: str) -> Iterator[list[str]]:
    import pwd

    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if not Path(sshd).is_file():
        raise RuntimeError("existing system sshd is required on the disposable runner")
    client, host = root / "client-key", root / "host-key"
    for key in (client, host):
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    authorized = root / "authorized_keys"
    authorized.write_text("restrict " + client.with_suffix(".pub").read_text())
    authorized.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    user = pwd.getpwuid(os.getuid()).pw_name
    command = shlex.join(
        [
            "env",
            f"SEOHEAD_CHROME={os.environ.get('SEOHEAD_CHROME', '')}",
            "timeout",
            "--signal=TERM",
            "--kill-after=5",
            "100",
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker-root",
            str(state),
            "--build",
            build,
            "--fixture-address",
            address,
        ]
    )
    config = root / "sshd_config"
    config.write_text(
        f"Port {port}\nListenAddress 127.0.0.1\nHostKey {host}\n"
        f"PidFile {root / 'sshd.pid'}\nAuthorizedKeysFile {authorized}\nAllowUsers {user}\n"
        "PasswordAuthentication no\nKbdInteractiveAuthentication no\nAuthenticationMethods publickey\n"
        "UsePAM yes\nStrictModes yes\nPermitRootLogin no\nPermitTTY no\nPermitUserRC no\nDisableForwarding yes\n"
        f"ForceCommand {command}\nLogLevel ERROR\n"
    )
    known_hosts = root / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} " + host.with_suffix(".pub").read_text())
    args = [
        "ssh",
        "-F",
        "/dev/null",
        "-T",
        "-p",
        str(port),
        "-i",
        str(client),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "LogLevel=ERROR",
        f"{user}@127.0.0.1",
    ]
    with (root / "sshd.log").open("w") as log:
        process = subprocess.Popen(
            ["sudo", "-n", sshd, "-D", "-e", "-f", str(config)],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("owned sshd stopped during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            yield args
        finally:
            if process.poll() is None:
                run(["sudo", "-n", "kill", "-TERM", "--", f"-{process.pid}"])
                process.wait(timeout=10)


def execute(evidence: Path, metrics: dict[str, Any]) -> None:
    require_linux()
    if os.geteuid() == 0:
        raise RuntimeError("run the fixture as the non-root CI user with passwordless sudo")
    from seohead.job_contracts import Principal
    from seohead.remote_api.app import TokenAuthenticator, create_app

    build = validate_revision(run(["git", "rev-parse", "HEAD"], cwd=ROOT))
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT):
        raise RuntimeError("the disposable source checkout must be clean")
    address = next(
        (
            value
            for value in run(["hostname", "-I"]).split()
            if ipaddress.ip_address(value).version == 4
        ),
        "",
    )
    address = private_address(address)
    if not os.environ.get("SEOHEAD_CHROME") or not Path(os.environ["SEOHEAD_CHROME"]).is_file():
        raise RuntimeError("an existing sandboxed Chrome executable is required")
    runner_temp = os.environ.get("RUNNER_TEMP")
    if not runner_temp or not Path(runner_temp).is_dir():
        raise RuntimeError("RUNNER_TEMP must name the disposable runner workspace")
    if not Path("/run/sshd").is_dir():
        raise RuntimeError("the disposable runner requires the existing /run/sshd directory")
    evidence.mkdir(parents=True, exist_ok=False)
    metrics.update(
        build=build,
        fixture="disposable-ssh-asgi-tmpfs.v1",
        api_bind="127.0.0.1",
        ssh_bind="127.0.0.1",
        proxy_bind="127.0.0.1",
        target_bind="owned_rfc1918",
    )
    with (
        tempfile.TemporaryDirectory(
            prefix="seohead-worker-recovery-", dir=runner_temp
        ) as directory,
        fixture_dns(address),
    ):
        root = Path(directory)
        state = root / "state"
        state.mkdir(mode=0o700)
        (state / MARKER).write_text(
            json.dumps({"fixture": "disposable-worker-recovery.v1", "build": build})
        )
        queue = backend(state, build)
        token = secrets.token_urlsafe(32)
        grants = frozenset({"scan:submit", "scan:list", "scan:read", "scan:cancel", "scan:result"})
        app = create_app(
            queue,
            TokenAuthenticator(
                {TokenAuthenticator.digest(token): Principal("owned-fixture", {PROJECT: grants})}
            ),
            target_policy=queue,
        )
        cert, key = _certificate(root)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
        try:
            metrics["stage"] = "ssh_api_startup"
            with (
                http_server(ThreadingHTTPServer((address, 0), Site)) as site_port,
                api_server(app) as api_port,
            ):
                proxy.upstream_port = api_port
                with http_server(proxy) as port, ssh_worker(root, state, build, address) as ssh:
                    route = f"/api/v1/projects/{PROJECT}/scans"

                    def request(method: str, path: str, body=None, *, authenticated=True, key=None):
                        connection = http.client.HTTPSConnection(
                            "localhost",
                            port,
                            context=ssl.create_default_context(cafile=str(cert)),
                            timeout=30,
                        )
                        data = json.dumps(body).encode() if body is not None else None
                        headers = {"Authorization": f"Bearer {token}"} if authenticated else {}
                        if data is not None:
                            headers.update(
                                {
                                    "Content-Type": "application/json",
                                    "Content-Length": str(len(data)),
                                }
                            )
                        if key:
                            headers["Idempotency-Key"] = key
                        try:
                            connection.request(method, path, data, headers)
                            response = connection.getresponse()
                            return response.status, response.read()
                        finally:
                            connection.close()

                    def submit(key: str, mode="raw") -> str:
                        status, data = request(
                            "POST",
                            route,
                            {
                                "target_url": f"http://{HOST}:{site_port}/",
                                "options": {
                                    "max_urls": 10,
                                    "max_requests": 30,
                                    "max_crawl_seconds": 60,
                                    "rendering_mode": mode,
                                },
                            },
                            key=key,
                        )
                        if status != 202:
                            raise AssertionError("owned scan was not admitted")
                        return json.loads(data)["job_id"]

                    def status(job: str) -> dict[str, Any]:
                        code, data = request("GET", f"{route}/{job}")
                        if code != 200:
                            raise AssertionError("authenticated job status was unavailable")
                        return json.loads(data)

                    def worker():
                        return json.loads(run(ssh, cwd=ROOT, timeout=120))

                    def download(job: str) -> dict[str, str]:
                        import hashlib

                        status, raw = request("GET", f"{route}/{job}/result")
                        result = json.loads(raw)
                        if status != 200 or result["coverage"] != "complete":
                            raise AssertionError("baseline or recovery scan is not complete")
                        digests = {}
                        for artifact in result["artifacts"]:
                            path = f"{route}/{job}/artifacts/{artifact['artifact_id']}"
                            if request("GET", path, authenticated=False)[0] != 401:
                                raise AssertionError("artifact allowed an anonymous download")
                            status, data = request("GET", path)
                            if status != 200 or len(data) != artifact["size_bytes"]:
                                raise AssertionError("retained artifact download changed")
                            digests[artifact["kind"]] = hashlib.sha256(data).hexdigest()
                        return digests

                    if worker()["job"] is not None:
                        raise AssertionError("new fixture queue was not empty")
                    metrics["ssh_forced_command_verified"] = True
                    metrics["stage"] = "baseline"
                    baseline = submit("baseline-fixture")
                    if worker()["job"]["state"] != "finished":
                        raise AssertionError("baseline worker did not finish")
                    if status(baseline)["state"] != "finished":
                        raise AssertionError("baseline status did not publish the terminal result")
                    before = download(baseline)
                    cancelled = submit("cancel-fixture")
                    code, data = request("POST", f"{route}/{cancelled}/cancel")
                    if code != 200 or json.loads(data)["state"] != "cancelled":
                        raise AssertionError(
                            "authenticated cancellation did not produce a terminal job"
                        )
                    if status(cancelled)["state"] != "cancelled" or worker()["job"] is not None:
                        raise AssertionError("cancelled job was still eligible for worker dispatch")
                    metrics["queued_cancellation_verified"] = True
                    failed = submit("enospc-fixture")
                    job_dir = state / "projects" / PROJECT / failed
                    metrics["stage"] = "actual_enospc"
                    with mounted_tmpfs(job_dir):
                        metrics["tmpfs"] = fill_tmpfs(job_dir / "owned-filler")
                        outcome = worker()["job"]
                        errors = json.loads((state / "observed-write-error.json").read_text())
                        if not observed_enospc(errors):
                            raise AssertionError(
                                "native worker did not expose a real ENOSPC/SQLITE_FULL write"
                            )
                        if outcome["state"] not in {"failed", "partial"}:
                            raise AssertionError("full filesystem was reported as a successful job")
                        if status(failed)["state"] != outcome["state"]:
                            raise AssertionError(
                                "failed storage state was not visible through the API"
                            )
                        metrics["enospc_worker"] = {
                            "state": outcome["state"],
                            "reason": outcome["finish_reason"],
                            "errors": errors,
                        }
                        shutil.copytree(
                            job_dir,
                            evidence / "failed-job",
                            ignore=shutil.ignore_patterns("owned-filler"),
                        )
                    shutil.copytree(evidence / "failed-job", job_dir, dirs_exist_ok=True)
                    if download(baseline) != before:
                        raise AssertionError("ENOSPC changed earlier retained evidence")
                    if worker()["job"] is not None or queue.recover_expired() != 0:
                        raise AssertionError(
                            "terminal failure was replayed or left an expired lease"
                        )
                    if queue.get_result(PROJECT, failed).coverage == "complete":
                        raise AssertionError("failed job became complete after storage recovery")
                    metrics["baseline_sha256"] = before
                    metrics["baseline_unchanged"] = True
                    metrics["terminal_failure_not_replayed"] = True
                    metrics["stage"] = "recovery_js"
                    recovered = submit("recovery-fixture", "js")
                    if worker()["job"]["state"] != "finished":
                        raise AssertionError("fresh recovery worker did not finish")
                    if status(recovered)["state"] != "finished":
                        raise AssertionError("recovery status did not publish the terminal result")
                    metrics["recovery_sha256"] = download(recovered)
                    saved = state / "projects" / PROJECT / recovered / "scan.sqlite"
                    with sqlite3.connect(saved.as_uri() + "?mode=ro", uri=True) as scan:
                        pages = scan.execute("SELECT title,representation FROM pages").fetchall()
                    if pages != [("Rendered worker proof", "rendered")] * 2:
                        raise AssertionError(
                            "recovery did not retain actual rendered page evidence"
                        )
                    metrics["rendered_pages"] = len(pages)
                    metrics["job_states"] = [
                        queue.get_job(PROJECT, job).state for job in (baseline, failed, recovered)
                    ]
            metrics["ok"] = True
            metrics["stage"] = "complete"
        finally:
            preserve_state(state, evidence)
            if (root / "sshd.log").exists():
                (evidence / "sshd-error.log").write_text(
                    (root / "sshd.log").read_text()[-4096:].replace(str(root), "<fixture>")
                )


def preserve_state(state: Path, evidence: Path) -> None:
    """Copy only synthetic job state, not SSH keys, token values or TLS keys."""
    if (state / "projects").exists():
        shutil.copytree(
            state / "projects",
            evidence / "projects",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("owned-filler"),
        )
    if (state / "jobs.sqlite").exists():
        with (
            sqlite3.connect(state / "jobs.sqlite") as source,
            sqlite3.connect(evidence / "jobs.sqlite") as destination,
        ):
            source.backup(destination)
    if (state / "observed-write-error.json").exists():
        shutil.copy2(state / "observed-write-error.json", evidence / "observed-write-error.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-out", type=Path)
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--worker-root", type=Path)
    parser.add_argument("--build")
    parser.add_argument("--fixture-address")
    args = parser.parse_args(argv)
    if args.worker_root is not None:
        require_linux()
        build = validate_revision(args.build)
        if run(["git", "rev-parse", "HEAD"], cwd=ROOT) != build:
            raise ValueError("worker checkout changed after fixture startup")
        with fixture_dns(args.fixture_address):
            queue = backend(args.worker_root, build, observe_errors=True)
            job = queue.run_one("owned-ssh-worker")
            print(json.dumps({"job": job.model_dump(mode="json") if job else None}))
        return 0
    if args.metrics_out is None or args.evidence_out is None:
        parser.error("--metrics-out and --evidence-out are required")
    metrics: dict[str, Any] = {"ok": False, "stage": "preflight"}
    try:
        execute(args.evidence_out, metrics)
    except Exception as exc:
        # No exception text: it may contain command arguments or private paths.
        metrics["failure"] = {"type": type(exc).__name__, "stage": metrics["stage"]}
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metrics, sort_keys=True))
    return 0 if metrics["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
