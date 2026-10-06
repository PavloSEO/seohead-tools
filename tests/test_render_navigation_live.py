"""Owned-loopback Chromium proof for rendered navigation provenance (#826)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from seohead.crawl import settings
from seohead.tools.render import render_document


def test_real_browser_records_spa_and_script_navigation(monkeypatch):
    chrome = next(
        Path.home().glob(
            "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        ),
        None,
    )
    if chrome is None:
        pytest.skip("Chromium headless shell is not installed")
    monkeypatch.setenv("SEOHEAD_CHROME", str(chrome))
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/next":
                body = b"<html><body>next</body></html>"
            else:
                body = b"<script>history.pushState({},'', '/spa'); history.replaceState({},'', '?view=rendered'); setTimeout(()=>location.assign('/next'), 20)</script>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = settings.load(
            overrides={"rendering.mode": "js", "rendering.browser.script_timeout_seconds": 0.1}
        )["rendering"]
        result = render_document(f"http://127.0.0.1:{server.server_port}/", config, nav_timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result["ok"], result
    events = result["renderer"]["navigation"]["events"]
    assert [event["kind"] for event in events] == [
        "initial_http_navigation",
        "spa_history_change",
        "spa_history_change",
        "script_navigation",
    ]
    assert result["renderer"]["navigation"]["interaction_policy"] == "no_clicks"
    assert all(event["elapsed_ms"] >= 0 and event["user_click"] is None for event in events)


@pytest.fixture
def navigation_origin(monkeypatch):
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
                pytest.skip("Chromium is unavailable; no installation attempted")
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            redirects = {"/redirect": (301, "/second"), "/second": (302, "/routes")}
            if self.path in redirects:
                code, target = redirects[self.path]
                self.send_response(code)
                self.send_header("Location", target)
                self.end_headers()
                return
            body = "<html><title>Owned fixture</title><body><h1>Navigation evidence</h1>Fixture content</body></html>"
            if self.path == "/routes":
                body += "<script>history.pushState({},'', '?q=1');location.hash='part';setTimeout(()=>location.assign('/done'),30)</script>"
            if self.path == "/anchor":
                body += "<a href='/done' id='next'>Next</a><script>setTimeout(()=>document.querySelector('#next').click(),30)</script>"
            if self.path == "/loop":
                body += (
                    "<script>for(let i=0;i<50;i++)history.pushState({},'', '?route='+i)</script>"
                )
            if self.path == "/blocked":
                body += "<script>setTimeout(()=>location.assign('http://192.0.2.1/blocked'),30)</script>"
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_browser_distinguishes_http_history_fragment_script(navigation_origin):
    from seohead.servers.handlers import HANDLERS
    from seohead.tools.navigation import validate_navigation

    result = HANDLERS["render_check"](url=navigation_origin + "/redirect")
    assert result["ok"], result
    nav = result["navigation"]
    validate_navigation(nav)
    assert nav["state"] == "complete", nav
    kinds = [event["kind"] for event in nav["events"]]
    assert kinds == [
        "http_redirect",
        "http_redirect",
        "initial_http_navigation",
        "spa_history_change",
        "fragment_navigation",
        "script_navigation",
    ], nav
    assert [event["status_code"] for event in nav["events"][:2]] == [301, 302]
    assert nav["events"][3]["destination"].endswith("/routes?q=1")
    assert nav["events"][4]["destination"].endswith("/routes?q=1#part")


def test_real_browser_scripted_anchor_does_not_claim_human_click(navigation_origin):
    from seohead.tools.render import render_check

    result = render_check(navigation_origin + "/anchor", settle_ms=200)
    assert result["ok"], result
    assert result["navigation"]["events"][-1]["kind"] == "anchor_navigation"
    assert result["navigation"]["events"][-1]["user_click"] is None


def test_real_browser_loop_preserves_explicit_omissions(navigation_origin):
    from seohead.tools.render import render_check

    result = render_check(navigation_origin + "/loop", settle_ms=200)
    assert result["ok"], result
    nav = result["navigation"]
    assert len(nav["events"]) == 32
    assert nav["events_omitted"] == 19
    assert nav["state"] == "partial"
    assert nav["reason"] == "event_budget_exhausted"


def test_real_browser_blocked_navigation_keeps_partial_evidence(navigation_origin):
    from seohead.tools.render import render_check

    result = render_check(navigation_origin + "/blocked", settle_ms=200)
    assert not result["ok"], result
    assert result["navigation"]["state"] == "partial"
    assert result["navigation"]["events"][0]["kind"] == "initial_http_navigation"


def test_real_navigation_survives_offline_report_export_and_reanalysis(
    navigation_origin, tmp_path, monkeypatch
):
    import hashlib
    import json

    from seohead.servers.navigation_handlers import scan_navigation
    from seohead.servers.reanalysis_handlers import reanalyze_scan
    from seohead.storage.native_scan import NativeScan
    from tests.test_native_capture import _claim, _event
    from tests.test_native_render_atomic import _rendered_record
    from tests.test_scan_native import _metadata, _record, _runtime

    target = navigation_origin + "/routes"
    config = settings.load(overrides={"rendering.browser.script_timeout_seconds": 0.2})["rendering"]
    rendered = render_document(target, config)
    assert rendered["ok"], rendered
    path = tmp_path / "navigation.seohead"
    metadata = _metadata(**{"storage.body_mode": "captured_entity_bytes"})
    metadata["start_url"] = target
    with NativeScan.create(path, **metadata) as scan:
        lease = _claim(scan, target)
        scan.commit_page(lease, _record(target), captures=[_event(target)], runtime=_runtime())
        record = _rendered_record(target)
        record["final_url"] = rendered["final_url"]
        document_id = scan.commit_render(
            target,
            record,
            html=rendered["html"],
            renderer=rendered["renderer"],
            captured_at="2026-10-06T10:00:00Z",
        )
        scan.finish_capture(reason="finished")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = scan_navigation(str(path), document_id=document_id)
    assert report["state"] == "complete", (rendered["renderer"], report)
    assert (
        report["items"][0]["navigation"]["events"] == rendered["renderer"]["navigation"]["events"]
    )
    exported = tmp_path / "navigation.json"
    exported.write_text(json.dumps(report))
    assert json.loads(exported.read_text()) == report
    assert scan_navigation(str(path), limit=1)["items"] == report["items"]

    def no_network(*args, **kwargs):
        raise AssertionError("offline reanalysis must not navigate or fetch")

    monkeypatch.setattr("seohead.tools.render.render_document", no_network)
    monkeypatch.setattr("seohead.recon.net.http_client", no_network)
    derived = tmp_path / "reanalysis.seohead"
    result = reanalyze_scan(str(path), str(derived), producer_build="b" * 40)
    assert result["ok"], result
    assert scan_navigation(str(derived), document_id=document_id)["items"] == report["items"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_real_remote_navigation_keeps_pinned_policy_without_local_fallback(
    navigation_origin, monkeypatch, tmp_path
):
    import importlib.metadata
    import json
    import os
    import selectors
    import subprocess

    import playwright

    from seohead.tools.render import RenderCancelled

    chrome = os.environ.get("SEOHEAD_CHROME")
    if chrome is None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            chrome = pw.chromium.executable_path
    driver = Path(playwright.__file__).parent / "driver"
    config_file = tmp_path / "owned-browser.json"
    config_file.write_text(
        json.dumps(
            {
                "headless": True,
                "executablePath": chrome,
                "chromiumSandbox": True,
                "host": "127.0.0.1",
                "port": 0,
            }
        )
    )
    process = subprocess.Popen(
        [
            str(driver / "node"),
            str(driver / "package" / "cli.js"),
            "launch-server",
            "--browser",
            "chromium",
            "--config",
            str(config_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(20):
                pytest.fail("owned remote browser did not start")
            endpoint = process.stdout.readline().strip()
        assert endpoint.startswith("ws://127.0.0.1:")
        monkeypatch.setenv("SEOHEAD_OWNED_BROWSER", endpoint)
        monkeypatch.setenv("SEOHEAD_CHROME", str(tmp_path / "missing-local-browser"))
        config = settings.load(
            overrides={
                "rendering.browser.transport": "remote",
                "rendering.browser.remote_endpoint_env": "SEOHEAD_OWNED_BROWSER",
                "rendering.browser.remote_playwright_version": importlib.metadata.version(
                    "playwright"
                ),
                "rendering.browser.script_timeout_seconds": 0.2,
            }
        )["rendering"]
        result = render_document(navigation_origin + "/routes", config, nav_timeout=5)
        assert result["ok"], result
        assert result["renderer"]["transport"]["mode"] == "remote"
        assert result["renderer"]["navigation"]["events"][-1]["kind"] == "script_navigation"
        blocked = render_document(navigation_origin + "/blocked", config, nav_timeout=5)
        assert not blocked["ok"], blocked
        assert blocked["renderer"]["navigation"]["state"] == "partial"

        def cancel():
            raise RenderCancelled()

        cancelled = render_document(
            navigation_origin + "/routes", config, nav_timeout=5, request_gate=cancel
        )
        assert not cancelled["ok"], cancelled
        process.terminate()
        process.communicate(timeout=10)
        disconnected = render_document(navigation_origin + "/routes", config, nav_timeout=1)
        assert disconnected["reason"] == "remote_connection_failed", disconnected
        assert endpoint not in str(disconnected)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
