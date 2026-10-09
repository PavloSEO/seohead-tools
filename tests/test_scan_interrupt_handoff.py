"""Deterministic interruption across the owned native post-collection boundary."""

from __future__ import annotations

import hashlib
from contextlib import closing

import pytest

from seohead.crawl.sqlite_adapter import ScanRun, crawl_to_scan
from seohead.mcp import scan_handlers
from seohead.storage import open_scan
from seohead.storage.audit_v2 import audit_v2_path
from seohead.storage.native_audit import AuditSizeError
from seohead.storage.native_scan import NativeScan
from tests.test_scan_history import _event
from tests.test_scan_native import _metadata, _record, _runtime


@pytest.fixture
def capture(tmp_path, monkeypatch):
    path = tmp_path / "captured.sqlite"
    metadata = _metadata(
        **{
            "limits.max_urls": 8,
            "storage.format_version": "scan.v2",
            "robots.policy": "ignore",
        }
    )
    with NativeScan.create(
        path,
        initial_sitemaps=(("https://example.test/sitemap.xml", "sitemap-only"),),
        format_version="scan.v2",
        **metadata,
    ) as scan:
        scan.enqueue([("https://example.test/", 0)])
        lease = scan.claim(1)[0]
        scan.commit_page(
            lease,
            _record(),
            captures=[_event(lease.url, b"<html>retained</html>")],
            runtime=_runtime(),
        )
        root = scan.sitemap_roots()[0]
        scan.write_sitemap_members(root["sitemap_url_id"], [(0, lease.url)])
        scan.finish_sitemap(root["sitemap_url_id"], True, "")
    run = ScanRun(
        path=str(path),
        pages=1,
        links=0,
        forms=0,
        lifecycle="running",
        finish_reason="finished",
        partial=False,
        resumed=True,
        start_page_gate={"html": "<html>retained</html>", "outlinks": 0, "external_outlinks": 0},
    )
    monkeypatch.setattr("seohead.crawl.sqlite_adapter.crawl_to_scan", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(
        "seohead.mcp.handlers._audit_crawl_result",
        lambda *_args, **_kwargs: (
            {"synthetic_summary": "saved"},
            ({"schema_version": "2.0", "issues": [], "pages": []}, {"/issues": [], "/pages": []}),
        ),
    )
    return path, metadata["config"]


def _evidence(path):
    with closing(open_scan(path, require_audit=False)) as con:
        return {
            **{
                table: list(con.execute(f'SELECT * FROM "{table}"'))
                for table in ("pages", "responses", "documents", "frontier")
            },
            "sitemap": list(
                con.execute(
                    "SELECT * FROM context_items WHERE kind IN "
                    "('sitemap_declaration','sitemap_declared_url','sitemap_fetch_summary')"
                )
            ),
        }


def _call(path, settings, **kwargs):
    return scan_handlers.crawl_site_scan(
        "https://example.test/",
        scan_out=str(path),
        settings=settings,
        producer_build="a" * 40,
        **kwargs,
    )


@pytest.mark.parametrize("stage", ["open", "snapshot", "rebuild", "analysis", "save", "finalize"])
def test_interrupt_at_each_post_capture_stage_keeps_checkpoint_and_releases_writer(
    capture, monkeypatch, stage
):
    path, settings = capture
    before = _evidence(path)
    armed = True

    def interrupt_once(function):
        def wrapped(*args, **kwargs):
            nonlocal armed
            if armed:
                armed = False
                raise KeyboardInterrupt
            return function(*args, **kwargs)

        return wrapped

    if stage == "open":
        # The writer lock has been acquired at this exact regression point.
        monkeypatch.setattr(
            NativeScan, "_connect_writer", staticmethod(interrupt_once(NativeScan._connect_writer))
        )
    elif stage == "snapshot":
        monkeypatch.setattr(
            NativeScan, "resume_snapshot", interrupt_once(NativeScan.resume_snapshot)
        )
    elif stage == "rebuild":
        monkeypatch.setattr(
            scan_handlers,
            "_rebuild_page_result",
            interrupt_once(scan_handlers._rebuild_page_result),
        )
    elif stage == "analysis":
        from seohead.mcp import handlers

        monkeypatch.setattr(
            handlers, "_audit_crawl_result", interrupt_once(handlers._audit_crawl_result)
        )
    elif stage == "save":
        monkeypatch.setattr(NativeScan, "save_audit_v2", interrupt_once(NativeScan.save_audit_v2))
    else:
        monkeypatch.setattr(NativeScan, "finish_capture", interrupt_once(NativeScan.finish_capture))

    result = _call(path, settings)
    assert armed is False
    assert result["finish_reason"] == "interrupted"
    assert result["partial"] is True and result["audit_available"] is False
    assert result["finalized"] is False and result["urls_collected"] == 1
    assert "retained capture can be resumed" in result["audit_reason"]
    assert _evidence(path) == before
    # Reopening immediately in this process proves both connection and writer
    # lock ownership were released, including interruption inside open().
    with NativeScan.open(path) as scan:
        snapshot = scan.resume_snapshot()
        assert snapshot["scan"]["lifecycle"] == "interrupted"
        assert snapshot["scan"]["finish_reason"] == "interrupted"
        assert snapshot["scan"]["crawl_partial"] == 1
    # Resume through the real collector; a complete retained sitemap and drained
    # frontier require no network, even after interruption during finalization.
    monkeypatch.setattr("seohead.crawl.sqlite_adapter.crawl_to_scan", crawl_to_scan)
    monkeypatch.setattr(
        "httpx.Client.send", lambda *_args, **_kwargs: pytest.fail("retained resume refetched data")
    )
    resumed = scan_handlers.resume_scan(str(path), producer_build="a" * 40)
    assert resumed["audit_available"] is True
    assert resumed["partial"] is False and resumed["finish_reason"] == "finished"
    assert NativeScan.inspect(path)["scan"]["lifecycle"] == "finished"
    assert _evidence(path) == before


@pytest.mark.parametrize("phase", ["external", "render", "analysis", "finalizing"])
def test_interrupt_at_phase_transition_is_inside_the_same_owned_boundary(capture, phase):
    path, settings = capture
    before = _evidence(path)
    if phase == "external":
        settings["discovery"]["external"]["crawl"] = True
    if phase == "render":
        settings["rendering"]["mode"] = "javascript"
        settings["rendering"]["rendered_links"]["crawl"] = True

    def observe(name):
        if name == phase:
            raise KeyboardInterrupt

    result = _call(path, settings, observation=observe)
    assert result["finish_reason"] == "interrupted" and result["partial"] is True
    assert result["audit_available"] is False
    assert _evidence(path) == before
    with NativeScan.open(path):
        pass


@pytest.mark.parametrize("saved_audit", [True, False])
def test_interrupt_after_final_commit_keeps_completed_state_and_audit_truth(
    capture, monkeypatch, saved_audit
):
    path, settings = capture
    finish = NativeScan.finish_capture
    committed = {}

    def finish_then_interrupt(scan, *args, **kwargs):
        assert finish(scan, *args, **kwargs)
        committed["scan"] = hashlib.sha256(path.read_bytes()).hexdigest()
        companion = audit_v2_path(path)
        if companion.exists():
            committed["audit"] = hashlib.sha256(companion.read_bytes()).hexdigest()
        raise KeyboardInterrupt

    monkeypatch.setattr(NativeScan, "finish_capture", finish_then_interrupt)
    if not saved_audit:

        def unavailable(*_args, **_kwargs):
            raise AuditSizeError("synthetic unavailable audit")

        monkeypatch.setattr("seohead.mcp.handlers._audit_crawl_result", unavailable)
    result = _call(path, settings)
    assert result["finish_reason"] == "finished"
    assert result["partial"] is False and result["finalized"] is True
    assert result["audit_available"] is saved_audit
    assert hashlib.sha256(path.read_bytes()).hexdigest() == committed["scan"]
    if saved_audit:
        assert result["synthetic_summary"] == "saved"
        assert hashlib.sha256(audit_v2_path(path).read_bytes()).hexdigest() == committed["audit"]
    assert NativeScan.inspect(path)["scan"]["lifecycle"] == "finished"


def test_interrupt_before_collector_returns_does_not_claim_or_mutate_a_capture(
    capture, monkeypatch
):
    path, settings = capture
    before = path.read_bytes()

    def not_admitted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("seohead.crawl.sqlite_adapter.crawl_to_scan", not_admitted)
    with pytest.raises(KeyboardInterrupt):
        _call(path, settings)
    assert path.read_bytes() == before


def test_resuming_post_capture_stop_keeps_nonrecoverable_omission_flags(capture, monkeypatch):
    path, settings = capture

    def interrupt_analysis(name):
        if name == "analysis":
            raise KeyboardInterrupt

    assert _call(path, settings, observation=interrupt_analysis)["partial"] is True
    limitation = "link_observations_omitted: synthetic retained omission"
    with NativeScan.open(path) as scan:
        scan._note(limitation)
        scan.con.commit()
    monkeypatch.setattr("seohead.crawl.sqlite_adapter.crawl_to_scan", crawl_to_scan)
    monkeypatch.setattr(
        "httpx.Client.send", lambda *_args, **_kwargs: pytest.fail("retained resume refetched data")
    )
    result = scan_handlers.resume_scan(str(path), producer_build="a" * 40)
    assert result["partial"] is True
    assert result["finish_reason"] == "finished" and result["audit_available"] is True
    assert limitation in result["limitations"]
    assert NativeScan.inspect(path)["scan"]["crawl_partial"] == 1
