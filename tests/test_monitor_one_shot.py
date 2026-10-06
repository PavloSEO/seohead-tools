"""Offline acceptance tests for the explicit one-shot monitor collector."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from seohead.projects.monitoring import collect_once, configure, local_deliver, schedule
from seohead.projects.runtime import read_document, write_document
from seohead.projects.workspace import create_project
from seohead.servers import monitor_handlers


class _Page(BaseHTTPRequestHandler):
    conditional = 0
    requests = 0
    cache_control = "max-age=0"
    link_href = "/one"
    cookies: ClassVar[list[str | None]] = []

    def do_GET(self):
        type(self).requests += 1
        type(self).cookies.append(self.headers.get("Cookie"))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        etag = f'"{type(self).link_href}"'
        if self.headers.get("If-None-Match") == etag:
            type(self).conditional += 1
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        body = (
            "<html><head><title>Monitor</title><link rel='canonical' href='/page'></head>"
            f"<body>PRIVATE-BODY<a href='{type(self).link_href}'>link</a></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", type(self).cache_control)
        self.send_header("ETag", etag)
        if type(self).cache_control == "no-store":
            self.send_header("Set-Cookie", "session=private")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def loopback(monkeypatch):
    _Page.conditional = _Page.requests = 0
    _Page.cache_control = "max-age=0"
    _Page.link_href = "/one"
    _Page.cookies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SEOHEAD_ALLOW_PRIVATE_NETWORKS", "1")
    try:
        # Match the IPv4-only listener regardless of localhost DNS address order.
        yield f"http://127.0.0.1:{server.server_port}/page"
    finally:
        server.shutdown()
        thread.join()


def _project(tmp_path, url):
    tmp_path.mkdir(parents=True, exist_ok=True)
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": True,
            "urls": [url],
            "max_urls": 1,
            "max_requests": 1,
            "max_render_requests": 0,
            "full_refresh_every": 2,
        },
    )
    return project, configured


def test_handler_one_shot_uses_loopback_cache_and_keeps_304_effective_evidence(tmp_path, loopback):
    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    preview = monitor_handlers.monitor_collect(str(project), claimed["revision"])
    assert preview["applied"] is False and preview["preview"]["remaining_urls"] == [loopback]
    assert _Page.requests == 0

    first = monitor_handlers.monitor_collect(str(project), claimed["revision"], apply=True)
    source = first["run"]["observations"][0]["source"]
    assert first["run"]["observations"][0]["cache_state"] == "fresh"
    assert first["run"]["observations"][0]["request_count"] == 1
    assert all((project / ref).is_file() for ref in (source["body_ref"], source["validation_ref"]))
    assert (
        hashlib.sha256((project / source["body_ref"]).read_bytes()).hexdigest()
        == source["body_sha256"]
    )

    again_claimed = schedule(project, action="start", expected_revision=first["revision"])
    second = monitor_handlers.monitor_collect(str(project), again_claimed["revision"], apply=True)
    observation = second["run"]["observations"][0]
    validation = json.loads((project / observation["source"]["validation_ref"]).read_text())
    assert _Page.conditional == 1
    assert observation["cache_state"] == "revalidated"
    assert observation["request_count"] == 1
    assert observation["measurement"]["status"] == validation["effective_status_code"] == 200
    assert validation["validators"]["etag"] == '"/one"'


def test_expired_or_dispatched_claim_never_replays_automatically(tmp_path, monkeypatch):
    project, configured = _project(tmp_path, "https://example.test/a")
    monkeypatch.setattr("seohead.projects.monitoring._now", lambda: "2026-10-06T00:00:00Z")
    claimed = schedule(
        project, action="start", expected_revision=configured["revision"], lease_seconds=2
    )
    with pytest.raises(ValueError, match="lease expired"):
        collect_once(
            project,
            expected_revision=claimed["revision"],
            apply=True,
            now=lambda: "2026-10-06T00:00:03Z",
        )
    interrupted = read_document(project, "monitor.json")
    assert interrupted["runner"]["state"] == "interrupted"

    restarted = schedule(project, action="start", expected_revision=interrupted["revision"])
    document = read_document(project, "monitor.json")
    document["runner"]["dispatched_urls"] = ["https://example.test/a"]
    document["revision"] += 1
    write_document(project, "monitor.json", document, expected_revision=restarted["revision"])
    with pytest.raises(ValueError, match="interrupted after dispatch"):
        collect_once(project, expected_revision=document["revision"], apply=True)


def test_loopback_redirect_is_one_bounded_request_and_fresh_cache_hit_stays_partial(
    tmp_path, loopback
):
    redirect_project, configured = _project(
        tmp_path / "redirect", loopback.replace("/page", "/redirect")
    )
    claimed = schedule(redirect_project, action="start", expected_revision=configured["revision"])
    redirected = collect_once(redirect_project, expected_revision=claimed["revision"], apply=True)
    assert _Page.requests == 1
    assert redirected["run"]["observations"][0]["measurement"]["status"] == 302
    assert redirected["run"]["state"] == "partial"

    _Page.cache_control = "max-age=600"
    cached_project, configured = _project(tmp_path / "cached", loopback)
    claimed = schedule(cached_project, action="start", expected_revision=configured["revision"])
    first = collect_once(cached_project, expected_revision=claimed["revision"], apply=True)
    claimed = schedule(cached_project, action="start", expected_revision=first["revision"])
    cached = collect_once(cached_project, expected_revision=claimed["revision"], apply=True)
    assert cached["run"]["observations"][0]["cache_state"] == "cached"
    assert cached["run"]["observations"][0]["request_count"] == 0
    assert cached["run"]["state"] == "partial"
    assert cached["run"]["recoveries"] == []


def test_mid_run_lease_expiry_and_no_store_cookie_keep_body_unavailable(
    tmp_path, loopback, monkeypatch
):
    project = tmp_path / "lease" / "project"
    (tmp_path / "lease").mkdir()
    create_project(project, "https://example.test/")
    policy = {
        "enabled": True,
        "urls": [loopback, loopback + "?second"],
        "max_urls": 2,
        "max_requests": 2,
        "max_render_requests": 0,
        "full_refresh_every": 2,
    }
    configured = configure(project, policy)
    monkeypatch.setattr("seohead.projects.monitoring._now", lambda: "2026-10-06T00:00:00Z")
    claimed = schedule(
        project, action="start", expected_revision=configured["revision"], lease_seconds=2
    )
    ticks = iter(
        [
            "2026-10-06T00:00:00Z",
            "2026-10-06T00:00:00Z",
            "2026-10-06T00:00:03Z",
            "2026-10-06T00:00:03Z",
        ]
    )
    with pytest.raises(ValueError, match="lease expired"):
        collect_once(
            project, expected_revision=claimed["revision"], apply=True, now=lambda: next(ticks)
        )
    assert read_document(project, "monitor.json")["runner"]["state"] == "interrupted"

    _Page.cache_control = "no-store"
    no_store, configured = _project(tmp_path / "no-store", loopback)
    claimed = schedule(no_store, action="start", expected_revision=configured["revision"])
    retained = collect_once(no_store, expected_revision=claimed["revision"], apply=True)
    observation = retained["run"]["observations"][0]
    validation = (no_store / observation["source"]["validation_ref"]).read_text()
    assert observation["source"]["body_ref"] is None
    assert retained["run"]["state"] == "partial"
    assert "PRIVATE-BODY" not in validation
    assert _Page.cookies and _Page.cookies[-1] is None


def test_same_count_different_link_destination_derives_link_change(tmp_path, loopback):
    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    first = collect_once(project, expected_revision=claimed["revision"], apply=True)
    _Page.link_href = "/two"
    claimed = schedule(project, action="start", expected_revision=first["revision"])
    second = collect_once(project, expected_revision=claimed["revision"], apply=True)
    changes = second["run"]["observations"][0]["changes"]
    assert any(change["kind"] == "links_changed" for change in changes)
    validation = json.loads(
        (project / second["run"]["observations"][0]["source"]["validation_ref"]).read_text()
    )
    assert validation["links_sha256"] == second["run"]["observations"][0]["source"]["links_sha256"]


@pytest.mark.parametrize("cache_control", ["private, no-store", "NO-STORE"])
def test_cacheable_body_then_current_no_store_never_retains_new_body(
    tmp_path, loopback, cache_control
):
    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    first = collect_once(project, expected_revision=claimed["revision"], apply=True)
    _Page.cache_control = cache_control
    _Page.link_href = "/new-no-store"
    claimed = schedule(project, action="start", expected_revision=first["revision"])
    second = collect_once(project, expected_revision=claimed["revision"], apply=True)
    source = second["run"]["observations"][0]["source"]
    validation = json.loads((project / source["validation_ref"]).read_text())
    assert source["body_ref"] is None
    assert validation["transport"]["cache_control"] == cache_control
    assert validation["transport"]["status_code"] == 200
    assert "PRIVATE-BODY" not in (project / source["validation_ref"]).read_text()


def test_tampered_claim_plan_is_rejected_before_http(tmp_path, loopback):
    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    document = read_document(project, "monitor.json")
    document["runner"]["claim_id"] = "monitor:../escape"
    document["runner"]["plan"]["planned_urls"] = ["https://outside.test/"]
    document["revision"] += 1
    write_document(project, "monitor.json", document, expected_revision=claimed["revision"])
    with pytest.raises(ValueError, match="claim id is invalid"):
        collect_once(project, expected_revision=document["revision"], apply=True)
    assert _Page.requests == 0


def test_non_boolean_apply_is_rejected_before_http(tmp_path, loopback):
    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    with pytest.raises(ValueError, match="apply must be a boolean"):
        collect_once(project, expected_revision=claimed["revision"], apply="false")
    assert _Page.requests == 0


def test_same_revision_concurrent_apply_dispatches_at_most_one_url(tmp_path, loopback, monkeypatch):
    import seohead.projects.monitoring as monitoring

    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    original = monitoring._load
    gate = threading.Barrier(2)
    calls = threading.local()

    def synchronized(directory):
        root, document = original(directory)
        calls.count = getattr(calls, "count", 0) + 1
        if calls.count == 2:
            gate.wait(timeout=5)
        return root, document

    monkeypatch.setattr(monitoring, "_load", synchronized)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: _attempt_collect(project, claimed["revision"]),
                range(2),
            )
        )
    assert sum(result == "ok" for result in results) == 1
    assert sum(result == "conflict" for result in results) == 1
    assert _Page.requests == 1


def _attempt_collect(project, revision):
    try:
        collect_once(project, expected_revision=revision, apply=True)
    except ValueError as exc:
        assert "writer owns" in str(exc) or "revision conflict" in str(exc)
        return "conflict"
    return "ok"


def test_provenance_tampering_is_rejected_and_local_receipt_is_deduplicated(tmp_path, loopback):
    project, configured = _project(tmp_path, loopback)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    retained = collect_once(project, expected_revision=claimed["revision"], apply=True)
    from seohead.projects.monitoring import run

    actionable = run(
        project,
        "scan:local-receipt",
        [{"url": loopback, "changes": [{"kind": "status_changed", "severity": "warning"}]}],
        retained["revision"],
    )
    source = retained["run"]["observations"][0]["source"]
    (project / source["validation_ref"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match retained bytes"):
        run(
            project,
            "scan:tampered",
            [{"url": loopback, "changes": [], "source": source}],
            actionable["revision"],
        )

    # The original immutable run remains locally receiptable exactly once.
    first = local_deliver(
        project, scan_id="scan:local-receipt", expected_revision=actionable["revision"]
    )
    second = local_deliver(
        project, scan_id="scan:local-receipt", expected_revision=first["revision"]
    )
    assert first["receipts"][0]["state"] == "sent"
    assert second["receipts"][0]["state"] == "delivered"


def test_provenance_refuses_parent_symlinks_and_oversize_artifacts(tmp_path, loopback):
    from seohead.projects.monitoring import run

    linked, configured = _project(tmp_path / "linked", loopback)
    outside = tmp_path / "outside"
    outside.mkdir()
    (linked / "reports" / "monitor").symlink_to(outside, target_is_directory=True)
    source = {
        "body_ref": None,
        "body_sha256": None,
        "validation_ref": "reports/monitor/validation.json",
        "validation_sha256": "0" * 64,
        "links_sha256": "0" * 64,
    }
    with pytest.raises(ValueError, match="must not traverse symlinks"):
        run(
            linked,
            "scan:link",
            [{"url": loopback, "changes": [], "source": source}],
            configured["revision"],
        )

    oversized, configured = _project(tmp_path / "oversized", loopback)
    artifact = oversized / "reports" / "monitor" / "large.validation.json"
    artifact.parent.mkdir()
    with artifact.open("wb") as stream:
        stream.truncate(1024 * 1024 * 1024 + 1)
    source["validation_ref"] = "reports/monitor/large.validation.json"
    with pytest.raises(ValueError, match="hashing byte budget"):
        run(
            oversized,
            "scan:oversized",
            [{"url": loopback, "changes": [], "source": source}],
            configured["revision"],
        )
    assert os.stat(artifact).st_size > 1024 * 1024 * 1024


def test_cache_and_local_receipt_targets_refuse_symlinks_before_side_effects(tmp_path, loopback):
    from seohead.projects.monitoring import run

    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("outside bytes", encoding="utf-8")

    project, configured = _project(tmp_path / "cache", loopback)
    (project / "reports" / "monitor-cache").symlink_to(outside, target_is_directory=True)
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    with pytest.raises(ValueError, match="report target is unsafe"):
        collect_once(project, expected_revision=claimed["revision"], apply=True)
    assert _Page.requests == 0 and marker.read_text(encoding="utf-8") == "outside bytes"

    project, configured = _project(tmp_path / "receipt", loopback)
    actionable = run(
        project,
        "scan:receipt",
        [{"url": loopback, "changes": [{"kind": "status_changed", "severity": "warning"}]}],
        configured["revision"],
    )
    (project / "reports" / "monitor-delivery-receipts.sqlite").symlink_to(marker)
    with pytest.raises(ValueError, match="report target is unsafe"):
        local_deliver(project, scan_id="scan:receipt", expected_revision=actionable["revision"])
    assert marker.read_text(encoding="utf-8") == "outside bytes"
