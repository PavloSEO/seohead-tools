"""Reanalysis retains captured render provenance from audit.v2 headers."""

import hashlib

from scripts.accept_million_crawl import run_stage
from seohead.mcp import handlers
from seohead.storage.audit_v2 import AuditV2Reader
from seohead.storage.native_scan import NativeScan


def test_v2_render_header_reaches_offline_assembler_without_materialization(tmp_path, monkeypatch):
    path = tmp_path / "source/native.sqlite"
    render_summary = {
        "mode": "raw",
        "render_requests": 0,
        "note": "Synthetic explicit capture summary",
    }
    save = NativeScan.save_audit_v2

    def annotate(writer, header, collections):
        header = {**header, "run": {**header["run"], "render_escalation": render_summary}}
        return save(writer, header, collections)

    with monkeypatch.context() as setup:
        setup.setattr(NativeScan, "save_audit_v2", annotate)
        outcome = run_stage(
            tmp_path / "source", pages=8, shard_size=4, interrupt_after=2, consumers=False
        )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    captured = []
    assemble = handlers._audit_crawl_result

    def inspect(*args, **kwargs):
        captured.append(kwargs["captured_render_summary"])
        return assemble(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("reanalysis materialized audit collections")

    monkeypatch.setattr(handlers, "_audit_crawl_result", inspect)
    monkeypatch.setattr(AuditV2Reader, "materialize_legacy", forbidden)
    result = handlers.scan_reanalyze(
        input_path=str(path),
        out=str(tmp_path / "derived.sqlite"),
        producer_build=outcome["source_revision"],
    )
    assert result["audit_available"] is True
    assert captured == [render_summary]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
