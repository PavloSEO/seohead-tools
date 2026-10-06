"""Owned synthetic TLS interception proves explicit trust and browser POST replay.

No platform trust store is changed; SSL_CERT_FILE selects a temporary fixture CA.
The proxy terminates CONNECT itself and never connects to an external endpoint.
"""

from __future__ import annotations

import socket
import socketserver
import ssl
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from seohead.recon import net
from seohead.tools import render
from tests.test_proxy_tls_connect import _certificate, _Server


@pytest.fixture
def intercepting_proxy(monkeypatch, tmp_path):
    chrome = next(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        ),
        None,
    )
    if chrome is not None:
        monkeypatch.setenv("SEOHEAD_CHROME", str(chrome))
    else:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            if not Path(pw.chromium.executable_path).exists():
                pytest.skip("Chromium unavailable; no installation attempted")
    tls, cert = _certificate(tmp_path)
    seen = []

    class Proxy(socketserver.StreamRequestHandler):
        def handle(self):
            connect = self.rfile.readline().decode().strip()
            if not connect.startswith("CONNECT "):
                return
            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                pass
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            try:
                with tls.wrap_socket(self.connection, server_side=True) as tunnel:
                    reader = tunnel.makefile("rb")
                    first = reader.readline().decode().strip()
                    if not first:
                        return
                    method, path, _ = first.split(" ", 2)
                    headers = {}
                    while line := reader.readline():
                        if line in (b"\r\n", b"\n"):
                            break
                        key, value = line.decode().split(":", 1)
                        headers[key.lower()] = value.strip()
                    body = reader.read(int(headers.get("content-length", "0")))
                    seen.append((method, path, body))
                    status, extra = "200 OK", ""
                    if path == "/post":
                        content = b"POST reached the owned intercepting proxy"
                    elif path == "/large":
                        content = b"x" * 4096
                    elif path == "/reject":
                        status, extra = "302 Found", "Location: http://192.0.2.1/private\r\n"
                        content = b""
                    else:
                        suffix = (
                            "/large"
                            if path == "/oversize"
                            else "/reject"
                            if path == "/blocked"
                            else "/post"
                        )
                        content = (
                            "<html><title>Proxy fixture</title><body><h1>Owned proxy</h1><p>Raw text</p>"
                            f"<script>fetch('{suffix}',{{method:'POST',body:'synthetic'}}).then(r=>r.text()).then(x=>document.querySelector('h1').textContent=x)</script>"
                            "</body></html>"
                        ).encode()
                    response = (
                        f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\nContent-Length: {len(content)}\r\nConnection: close\r\n{extra}\r\n".encode()
                        + content
                    )
                    tunnel.sendall(response)
            except (ssl.SSLError, OSError):
                pass

    original_lookup = socket.getaddrinfo

    def lookup(host, port, *args, **kwargs):
        if host == "example.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return original_lookup(host, port, *args, **kwargs)

    monkeypatch.setattr(net.socket, "getaddrinfo", lookup)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("SEOHEAD_ALLOW_PRIVATE_NETWORKS", raising=False)
    monkeypatch.delenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", raising=False)
    with _Server(("127.0.0.1", 0), Proxy) as proxy:
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        route = net.resolve_proxy_route(
            f"http://127.0.0.1:{proxy.server_address[1]}", allow_private=True
        )
        try:
            yield route, cert, seen
        finally:
            proxy.shutdown()
            thread.join(timeout=5)


def test_owned_proxy_requires_explicit_ca_and_replays_post(intercepting_proxy, monkeypatch):
    route, cert, seen = intercepting_proxy
    rejected = render.render_check("https://example.test/", proxy_route=route)
    assert not rejected["ok"], rejected
    assert "TLS certificate verification failed" in rejected["error"]
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    accepted = render.render_check("https://example.test/", proxy_route=route)
    assert accepted["ok"], accepted
    assert accepted["rendered"]["h1"] == "POST reached the owned intercepting proxy"
    assert ("POST", "/post", b"synthetic") in seen
    assert str(cert) not in str(accepted)


def test_public_handler_over_owned_interceptor_names_cap_and_private_rejection(
    intercepting_proxy, monkeypatch
):
    from seohead.servers.handlers import HANDLERS

    route, cert, _seen = intercepting_proxy
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    factory = render.http_client
    options = net.crawl_transport_options(route)
    # Model a transparent interceptor at the transport boundary; the public
    # handler has no proxy argument. All real bytes pass the owned TLS proxy.
    monkeypatch.setattr(
        render, "http_client", lambda timeout, **kwargs: factory(timeout, **{**kwargs, **options})
    )
    accepted = HANDLERS["render_check"](url="https://example.test/")
    assert accepted["ok"], accepted
    assert accepted["rendered"]["h1"] == "POST reached the owned intercepting proxy"
    rejected = HANDLERS["render_check"](url="https://example.test/blocked")
    assert not rejected["ok"], rejected
    assert "private" in rejected["error"].lower() or "non-public" in rejected["error"].lower(), (
        rejected
    )
    pinned = render._pinned_browser_route
    monkeypatch.setattr(
        render,
        "_pinned_browser_route",
        lambda client, **kwargs: pinned(client, max_response_bytes=512, **kwargs),
    )
    capped = HANDLERS["render_check"](url="https://example.test/oversize")
    assert not capped["ok"], capped
    assert "byte limit" in capped["error"]


def test_public_cli_uses_explicit_ca_without_transport_injection(monkeypatch, tmp_path):
    import datetime
    import ipaddress
    import json
    import os
    import subprocess
    import sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    chrome = next(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        ),
        None,
    )
    if chrome is None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            chrome = Path(pw.chromium.executable_path)
        if not chrome.exists():
            pytest.skip("Chromium unavailable; no installation attempted")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "owned-loopback-fixture")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><title>Owned TLS</title><body><h1>Raw</h1><script>fetch('/post',{method:'POST',body:'fixture'}).then(r=>r.text()).then(t=>document.querySelector('h1').textContent=t)</script></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            body = b"Public CLI POST observed"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(repo),
        "SEOHEAD_CHROME": str(chrome),
        "SEOHEAD_ALLOW_PRIVATE_HOSTS": "127.0.0.1",
    }
    env.pop("SSL_CERT_FILE", None)
    env.pop("SSL_CERT_DIR", None)
    env.pop("SEOHEAD_ALLOW_PRIVATE_NETWORKS", None)
    args = [
        sys.executable,
        "-m",
        "seohead",
        "render-check",
        "--url",
        f"https://127.0.0.1:{server.server_port}/",
    ]
    try:
        rejected = subprocess.run(
            args, cwd=repo, env=env, capture_output=True, text=True, timeout=30
        )
        rejection = json.loads(rejected.stdout)
        assert not rejection["ok"], rejection
        assert "TLS certificate verification failed" in rejection["error"]
        env["SSL_CERT_FILE"] = str(cert_path)
        accepted = subprocess.run(
            args, cwd=repo, env=env, capture_output=True, text=True, timeout=30
        )
        result = json.loads(accepted.stdout)
        assert result["ok"], result
        assert result["rendered"]["h1"] == "Public CLI POST observed"
        assert posts == [b"fixture"]
        assert str(cert_path) not in accepted.stdout + accepted.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
