"""Loopback acceptance coverage for retained sitemap-only native scans."""

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
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from seohead import cli
from seohead.servers import handlers
from seohead.storage import ScanError, open_scan

BUILD = "a" * 40


def _config(path: Path, *, max_urls: int = 8) -> Path:
    path.write_text(
        json.dumps(
            {
                "limits": {"max_urls": max_urls, "max_requests": 20},
                "speed": {"min_delay_seconds": 0},
                "robots": {"policy": "ignore"},
            }
        ),
        encoding="utf-8",
    )
    return path


@contextlib.contextmanager
def _site(
    routes: dict[str, tuple[int, str, bytes, dict[str, str]]],
) -> Iterator[tuple[str, list[str]]]:
    hits: list[str] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with lock:
                hits.append(self.path)
            status, content_type, body, headers = routes.get(
                self.path, (404, "text/plain", b"missing", {})
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://sitemap.localhost:{server.server_port}", hits
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _sitemap(*urls: str) -> bytes:
    members = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{members}</urlset>'
    ).encode()


def _crawl(base: str, scan: Path, config: Path) -> dict:
    return handlers.crawl_site(
        sitemap=f"{base}/sitemap.xml",
        sitemap_only=True,
        scan_out=str(scan),
        config=str(config),
        producer_build=BUILD,
    )


def _stored_members(scan: Path) -> set[str]:
    with open_scan(scan, require_audit=False) as con:
        rows = con.execute(
            "SELECT payload_json FROM context_items WHERE kind='sitemap_declared_url'"
        )
        return {
            con.execute(
                "SELECT url FROM urls WHERE url_id=?", (json.loads(row[0])["url_id"],)
            ).fetchone()[0]
            for row in rows
        }


@pytest.mark.parametrize("url_budget", [0, 8])
def test_sitemap_only_keeps_xml_members_without_spidering_root_or_destinations(
    monkeypatch, tmp_path, url_budget
):
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "sitemap.localhost")
    routes: dict[str, tuple[int, str, bytes, dict[str, str]]] = {}
    with _site(routes) as (base, hits):
        outside = "http://outside.localhost/not-in-scope"
        routes.update(
            {
                "/sitemap.xml": (
                    200,
                    "application/xml",
                    _sitemap(f"{base}/member", f"{base}/jump", outside),
                    {},
                ),
                "/member": (
                    200,
                    "text/html",
                    b'<a href="/root-only">root</a><link rel="canonical" href="/canonical-only">',
                    {},
                ),
                "/jump": (302, "text/html", b"", {"Location": "/redirect-destination"}),
            }
        )
        scan = tmp_path / "only.sqlite"
        config = _config(tmp_path / "crawl.json")
        document = json.loads(config.read_text())
        document["limits"]["max_urls"] = url_budget
        config.write_text(json.dumps(document))
        result = _crawl(base, scan, config)

    assert result["partial"] is False
    assert result["urls_collected"] == 2
    assert _stored_members(scan) == {f"{base}/member", f"{base}/jump", outside}
    assert "/member" in hits and "/jump" in hits
    assert not {"/", "/root-only", "/canonical-only", "/redirect-destination"} & set(hits)
    with sqlite3.connect(scan) as con:
        excluded = con.execute(
            "SELECT COUNT(*) FROM decisions WHERE reason='outside_host'"
        ).fetchone()[0]
    assert excluded == 1


@pytest.mark.parametrize(
    ("status", "body", "raises"),
    [
        (200, b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>', True),
        (200, b"<urlset>", True),
        (503, b"unavailable", False),
    ],
    ids=("empty", "malformed", "http-failure"),
)
def test_sitemap_only_refuses_unusable_xml_without_falling_back_to_root(
    monkeypatch, tmp_path, status, body, raises
):
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "sitemap.localhost")
    with _site({"/sitemap.xml": (status, "application/xml", body, {})}) as (base, hits):
        scan = tmp_path / f"{status}.sqlite"
        if raises:
            with pytest.raises(ScanError):
                _crawl(base, scan, _config(tmp_path / f"{status}.json"))
        else:
            result = _crawl(base, scan, _config(tmp_path / f"{status}.json"))
            assert result["partial"] or result["finish_reason"] != "finished"

    assert "/" not in hits
    assert "/sitemap.xml" in hits
    if scan.exists():
        assert (
            open_scan(scan, require_audit=False).execute("SELECT lifecycle FROM scan").fetchone()[0]
            != "finished"
        )


def test_sitemap_only_refuses_population_over_budget_before_crawling_members(monkeypatch, tmp_path):
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "sitemap.localhost")
    routes: dict[str, tuple[int, str, bytes, dict[str, str]]] = {}
    with _site(routes) as (base, hits):
        routes.update(
            {
                "/sitemap.xml": (
                    200,
                    "application/xml",
                    _sitemap(f"{base}/one", f"{base}/two", f"{base}/three"),
                    {},
                )
            }
        )
        with pytest.raises(ScanError, match="population exceeds"):
            _crawl(base, tmp_path / "budget.sqlite", _config(tmp_path / "budget.json", max_urls=2))

    assert not {"/", "/one", "/two", "/three"} & set(hits)


def test_resume_reuses_retained_sitemap_population_after_real_sigint(monkeypatch, tmp_path):
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_HOSTS", "sitemap.localhost")
    routes: dict[str, tuple[int, str, bytes, dict[str, str]]] = {}
    with _site(routes) as (base, hits):
        routes.update(
            {
                "/sitemap.xml": (
                    200,
                    "application/xml",
                    _sitemap(f"{base}/one", f"{base}/two"),
                    {},
                ),
                "/one": (200, "text/html", b"one", {}),
                "/two": (200, "text/html", b"two", {}),
            }
        )
        scan = tmp_path / "resume.sqlite"
        config = _config(tmp_path / "resume.json")
        root = Path(__file__).parents[1]
        env = os.environ | {
            "PYTHONPATH": str(root),
            "SEOHEAD_ALLOW_PRIVATE_HOSTS": "sitemap.localhost",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "seohead.cli",
                "crawl-site",
                "--sitemap",
                f"{base}/sitemap.xml",
                "--sitemap-only",
                "--scan-out",
                str(scan),
                "--config",
                str(config),
                "--producer-build",
                BUILD,
            ],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 15
        while "/one" not in hits and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "/one" in hits
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode in {0, 2}, stderr
        assert json.loads(stdout)["finish_reason"] == "interrupted"
        sitemap_hits = hits.count("/sitemap.xml")

        resumed = handlers.crawl_site(resume=str(scan), producer_build=BUILD)

    assert resumed["resumed"] is True
    assert resumed["partial"] is False
    assert hits.count("/sitemap.xml") == sitemap_hits
    assert "/" not in hits
    assert {"/one", "/two"} <= set(hits)
    with pytest.raises(ValueError, match="sitemap_only"):
        handlers.crawl_site(resume=str(scan), sitemap_only=False, producer_build=BUILD)


def test_cli_and_mcp_normalize_default_sitemap_only_to_resume_safe_none(monkeypatch):
    pytest.importorskip("mcp")
    captured: list[dict] = []

    def fake(**kwargs):
        captured.append(kwargs)
        return {"ok": True}

    monkeypatch.setitem(handlers.HANDLERS, "crawl_site", fake)
    monkeypatch.setattr(handlers, "crawl_site", fake)

    assert cli.main(["crawl-site", "--resume", "scan.sqlite", "--quiet"]) == 0

    from seohead.servers.mcp_server import build_server

    tool = build_server()._tool_manager.get_tool("seo_crawl_site")
    tool.fn(resume="scan.sqlite", sitemap_only=False)

    assert captured[0].get("sitemap_only") is None
    assert captured[1].get("sitemap_only") is None
