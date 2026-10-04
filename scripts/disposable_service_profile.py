#!/usr/bin/env python3
"""Prove the self-hosted service boundary on an owned loopback fixture.

This is deliberately not a deployment tool. It creates a temporary HTTP
upstream, TLS reverse proxy, artifact volume, backups, and release pointers,
then removes the entire fixture. The result is a small JSON evidence record for
CI, not a claim about a public DNS name, firewall, or production certificate.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_TOKEN = "disposable-profile-token"
_ARTIFACT = b"synthetic retained artifact\n"


class UpstreamHandler(BaseHTTPRequestHandler):
    """A private service fixture with an authenticated artifact route."""

    def do_GET(self) -> None:
        if self.path == "/healthz":
            body, status = b'{"ok":true}\n', 200
        elif self.path == "/artifact" and self.headers.get("Authorization") == f"Bearer {_TOKEN}":
            body, status = _ARTIFACT, 200
        else:
            body, status = b"unauthorized\n", 401
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class TLSReverseProxyHandler(BaseHTTPRequestHandler):
    """Minimal owned proxy used only to prove the TLS boundary in CI."""

    def do_GET(self) -> None:
        upstream = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=5)
        try:
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() not in {"connection", "host"}
            }
            upstream.request("GET", self.path, headers=headers)
            response = upstream.getresponse()
            body = response.read()
            self.send_response(response.status)
            self.send_header(
                "Content-Type", response.getheader("Content-Type", "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            upstream.close()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _request(port: int, cert: Path, *, token: str | None = None) -> tuple[int, bytes]:
    context = ssl.create_default_context(cafile=str(cert))
    connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        connection.request("GET", "/artifact", headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _certificate(directory: Path) -> tuple[Path, Path]:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl is required for the disposable TLS fixture")
    cert, key = directory / "proxy.crt", directory / "proxy.key"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cert, key


def _switch(current: Path, release: Path) -> None:
    replacement = current.with_name(f".current-{uuid.uuid4().hex}")
    replacement.symlink_to(release, target_is_directory=True)
    os.replace(replacement, current)


def run_profile() -> dict[str, Any]:
    """Run and verify the owned fixture, returning only non-secret metrics."""
    with tempfile.TemporaryDirectory(prefix="seohead-disposable-profile-") as temporary:
        root = Path(temporary)
        cert, key = _certificate(root)
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), TLSReverseProxyHandler)
        proxy.upstream_port = upstream.server_port
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
        _serve(upstream)
        _serve(proxy)
        try:
            denied, _ = _request(proxy.server_port, cert)
            granted, artifact = _request(proxy.server_port, cert, token=_TOKEN)
            if denied != 401 or granted != 200 or artifact != _ARTIFACT:
                raise AssertionError("TLS proxy did not preserve authenticated artifact access")
            if upstream.server_address[0] != "127.0.0.1" or proxy.server_address[0] != "127.0.0.1":
                raise AssertionError("disposable service fixture escaped loopback")

            volume = root / "volume"
            artifact_path = volume / "projects" / "alpha" / "job-1" / "audit.json"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_bytes(_ARTIFACT)
            backup = root / "backup"
            shutil.copytree(volume, backup)
            artifact_path.write_bytes(b"mutated\n")
            shutil.rmtree(volume)
            shutil.copytree(backup, volume)
            if artifact_path.read_bytes() != _ARTIFACT:
                raise AssertionError("artifact backup restore did not recover the original bytes")
            artifact_path.unlink()
            if artifact_path.exists():
                raise AssertionError("owned artifact expiry did not remove the fixture artifact")

            releases = root / "releases"
            old, new = releases / "old", releases / "new"
            old.mkdir(parents=True)
            new.mkdir()
            (old / "revision").write_text("old\n")
            (new / "revision").write_text("new\n")
            current = root / "current"
            _switch(current, old)
            _switch(current, new)
            upgraded = (current / "revision").read_text().strip()
            _switch(current, old)
            rolled_back = (current / "revision").read_text().strip()
            if (upgraded, rolled_back) != ("new", "old"):
                raise AssertionError("release upgrade or rollback pointer check failed")
            return {
                "fixture": "owned-loopback-tls-reverse-proxy",
                "upstream_bind": upstream.server_address[0],
                "proxy_bind": proxy.server_address[0],
                "tls_verified": True,
                "unauthorized_status": denied,
                "authorized_status": granted,
                "artifact_backup_restore": True,
                "artifact_expiry": True,
                "upgrade_revision": upgraded,
                "rollback_revision": rolled_back,
            }
        finally:
            proxy.shutdown()
            upstream.shutdown()
            proxy.server_close()
            upstream.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-out", required=True, type=Path)
    args = parser.parse_args(argv)
    metrics = run_profile()
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metrics, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
