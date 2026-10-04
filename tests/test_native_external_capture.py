from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from seohead.crawl.settings import load
from seohead.servers.reanalysis_handlers import reanalyze_scan
from seohead.servers.scan_handlers import crawl_site_scan, resume_scan
from seohead.storage import open_scan, read_audit


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
        elif self.path == "/":
            body = (
                f"<!doctype html><html><head><title>site</title></head><body>"
                f'<a href="http://outside.localhost:{self.server.server_port}/out">out</a>'
                '<a href="/next">next</a>'
                "</body></html>"
            ).encode()
        else:
            body = b"<title>external</title>"
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def test_native_v2_external_capture_retains_outcome_and_summary_offline(monkeypatch, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # The pinned transport reconnects to the owned loopback address after DNS
    # validation.  This fixture explicitly authorizes that isolated test
    # network; production remains denied by default.
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_NETWORKS", "1")
    try:
        settings = load(
            overrides={
                "speed.min_delay_seconds": 0,
                "discovery.external.crawl": True,
                "external_checks.max_targets": 4,
                "storage.body_mode": "captured_entity_bytes",
            }
        )
        path = tmp_path / "scan.sqlite"
        result = crawl_site_scan(
            f"http://site.localhost:{server.server_port}/",
            scan_out=str(path),
            settings=settings,
            producer_build="a" * 40,
        )
        assert result["audit_available"] is True
        assert read_audit(path)["run"]["external_checks"]["fetched"] == 1
        with open_scan(path, require_audit=False) as scan:
            checks = [
                json.loads(row[0])
                for row in scan.execute("SELECT payload_json FROM external_checks ORDER BY ordinal")
            ]
            assert checks[0]["url"].startswith("http://outside.localhost:")
            assert checks[0]["outcome"] == "fetched"
            summary = json.loads(
                scan.execute("SELECT payload_json FROM external_check_summary").fetchone()[0]
            )
            assert summary["fetched"] == 1

        # Reanalysis is offline: after the owned listener has gone away, it
        # must retain the captured coverage statement rather than trying the
        # destination again or erasing it from the derived audit header.
        server.shutdown()
        server.server_close()
        derived = tmp_path / "derived.sqlite"
        reanalyze_scan(str(path), str(derived), producer_build="b" * 40)
        assert read_audit(derived)["run"]["external_checks"]["fetched"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_native_external_capture_resume_does_not_repeat_completed_destination(
    monkeypatch, tmp_path
):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_NETWORKS", "1")
    try:
        path = tmp_path / "resumable.sqlite"
        settings = load(
            overrides={
                "speed.min_delay_seconds": 0,
                "limits.max_urls": 1,
                "discovery.external.crawl": True,
                "external_checks.max_targets": 4,
                "storage.body_mode": "captured_entity_bytes",
            }
        )
        crawl_site_scan(
            f"http://site.localhost:{server.server_port}/",
            scan_out=str(path),
            settings=settings,
            producer_build="a" * 40,
        )
        with open_scan(path, require_audit=False) as scan:
            assert scan.execute("SELECT lifecycle FROM scan").fetchone()[0] == "interrupted"
            assert scan.execute("SELECT COUNT(*) FROM external_checks").fetchone()[0] == 1

        resume_scan(str(path), producer_build="a" * 40)

        with open_scan(path, require_audit=False) as scan:
            summary = json.loads(
                scan.execute("SELECT payload_json FROM external_check_summary").fetchone()[0]
            )
            assert scan.execute("SELECT COUNT(*) FROM external_checks").fetchone()[0] == 1
            assert summary["records_carried"] == summary["resumed"] == 1
            assert read_audit(path)["run"]["external_checks"]["resumed"] == 1
    finally:
        server.shutdown()
        server.server_close()
