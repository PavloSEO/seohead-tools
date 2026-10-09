"""Issue #154, end to end: the robots.txt block and the link graph a native crawl already
collects (``SpiderResult.robots_blocked`` and ``.links``) must reach the audit, not just the
run's own ``discovery`` summary.

Three pages, reached three different ways:

- ``/`` links to both ``/blocked/`` and ``/old``.
- ``/blocked/`` answers normally (200) but robots.txt disallows it, and the crawl runs under
  ``robots="report_only"`` — fetched anyway, with one inbound link, so BLOCKED_BY_ROBOTS and
  IMPORTANT_URL_BLOCKED_BY_ROBOTS must both fire.
- ``/old`` 301-redirects to ``/new/``. A redirect enqueues its target without recording a
  ``LinkEdge`` for it (``spider.py:handle_redirect``), so ``/new/`` is the one page in this
  fixture with a real, non-homepage crawl depth and provably zero inlinks — the case
  ORPHAN_PAGE exists to catch, on evidence only a followed-link crawl can produce.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from seohead.mcp import handlers

ROBOTS = "User-agent: *\nAllow: /\nDisallow: /blocked/\n"

HOME = (
    "<html><head><title>Home</title>"
    "<meta name='description' content='The home page of the robots and orphan fixture site.'>"
    "</head><body><h1>Home</h1>"
    "<a href='/blocked/'>blocked</a> <a href='/old'>old</a></body></html>"
)
BLOCKED = (
    "<html><head><title>Blocked</title>"
    "<meta name='description' content='A page robots.txt disallows under report_only policy.'>"
    "</head><body><h1>Blocked</h1><p>Reachable, but disallowed.</p></body></html>"
)
NEW = (
    "<html><head><title>New</title>"
    "<meta name='description' content='Reached only via a redirect, so nothing links here.'>"
    "</head><body><h1>New</h1><p>No hyperlink ever names this URL.</p></body></html>"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str, status: int = 200, extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            return self._send(ROBOTS.encode(), "text/plain; charset=utf-8")
        if path == "/":
            return self._send(HOME.encode(), "text/html; charset=utf-8")
        if path == "/blocked/":
            return self._send(BLOCKED.encode(), "text/html; charset=utf-8")
        if path == "/old":
            base = f"http://{self.headers.get('Host', '127.0.0.1')}"
            return self._send(b"", "text/html", status=301, extra={"Location": f"{base}/new/"})
        if path == "/new/":
            return self._send(NEW.encode(), "text/html; charset=utf-8")
        self._send(b"not found", "text/plain", status=404)

    def log_message(self, format: str, *args) -> None:
        pass


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_NETWORKS", "1")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_robots_block_and_orphan_reach_the_audit(site, tmp_path):
    result = handlers.crawl_site(
        url=f"{site}/", out_dir=str(tmp_path), max_urls=10, min_delay=0, robots="report_only"
    )

    assert result["discovery"]["robots_blocked"] == 1
    assert result["discovery"]["links_seen"] == 2

    audit = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    pages = {p["url"]: p for p in audit["pages"]}
    blocked_page = pages[f"{site}/blocked/"]
    assert blocked_page["indexability"] == "Non-Indexable"
    assert blocked_page["indexability_status"] == "Blocked by Robots.txt"

    fired = {issue["check"] for issue in audit["issues"]}
    skipped = {s["id"] for s in audit["run"]["checks_skipped"]}
    silent_would_mean_untested = {"BLOCKED_BY_ROBOTS", "IMPORTANT_URL_BLOCKED_BY_ROBOTS"} - (
        fired | skipped
    )
    assert not silent_would_mean_untested

    assert "BLOCKED_BY_ROBOTS" in fired
    assert "IMPORTANT_URL_BLOCKED_BY_ROBOTS" in fired

    orphan_issues = [i for i in audit["issues"] if i["check"] == "ORPHAN_PAGE"]
    assert "ORPHAN_PAGE" in fired or "ORPHAN_PAGE" in skipped
    assert any(i["target_url"] == f"{site}/new/" for i in orphan_issues), orphan_issues
