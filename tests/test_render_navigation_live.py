"""Owned-loopback Chromium proof for rendered navigation provenance (#826)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from seohead.crawl import settings
from seohead.tools.render import render_document


def test_real_browser_records_spa_and_script_navigation(monkeypatch):
    chrome = next(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"), None)
    if chrome is None:
        pytest.skip("Chromium headless shell is not installed")
    monkeypatch.setenv("SEOHEAD_CHROME", str(chrome))
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/next":
                body = b"<html><body>next</body></html>"
            else:
                body = b"<script>history.pushState({},'', '#spa'); setTimeout(()=>location.assign('/next'), 20)</script>"
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
        config = settings.load(overrides={"rendering.mode": "js", "rendering.browser.script_timeout_seconds": 0.1})["rendering"]
        result = render_document(f"http://127.0.0.1:{server.server_port}/", config, nav_timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result["ok"], result
    events = result["renderer"]["navigation"]["events"]
    assert [event["kind"] for event in events] == ["initial_http_navigation", "spa_history_change", "script_navigation"]
    assert result["renderer"]["navigation"]["interaction_policy"] == "no_clicks"
    assert all(event["elapsed_ms"] >= 0 and event["user_click"] is False for event in events)
