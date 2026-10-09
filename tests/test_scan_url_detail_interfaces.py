"""One bounded retained URL-detail contract across handler, CLI and local MCP."""

from __future__ import annotations

import hashlib
import json

import pytest

from seohead import cli
from seohead.crawl.settings import fingerprint, load
from seohead.mcp import handlers
from seohead.mcp.history_handlers import _detail_headers
from seohead.mcp.mcp_server import build_server
from seohead.storage.native_scan import NativeScan
from tests.test_native_capture import _event
from tests.test_scan_native import _metadata, _record, _runtime

URL = "https://example.test/?token=top-secret"


def _mcp_tool():
    return build_server()._tool_manager.get_tool("seo_scan_url_detail")


def _scan(tmp_path):
    settings = load(overrides={"speed.min_delay_seconds": 0})
    metadata = _metadata()
    metadata["config"] = settings
    metadata["config_fingerprint"] = fingerprint(settings)
    path = tmp_path / "detail.sqlite"
    with NativeScan.create(path, **metadata) as scan:
        scan.enqueue([(URL, 0)])
        record = _record(URL)
        record.update(
            crawl_depth=0,
            canonical="https://example.test/canonical?key=not-for-output",
            final_url=URL,
            redirect_chain=[
                {
                    "request_url": URL,
                    "status_code": 301,
                    "location_raw": "https://example.test/?token=redirect-secret",
                    "next_url": URL,
                    "blocked": False,
                }
            ],
        )
        scan.commit_page(
            scan.claim(1)[0],
            record,
            forms=[
                {"page": URL, "method": "get", "action": "/find?q=secret-a", "has_password": False},
                {
                    "page": URL,
                    "method": "post",
                    "action": "/send?token=secret-b",
                    "has_password": True,
                },
            ],
            captures=[
                _event(URL, redirect_history=()),
                _event(URL, redirect_history=()),
            ],
            runtime=_runtime(),
        )
        scan.finish_capture()
    return path


def test_handler_cli_and_mcp_return_the_same_bounded_redacted_detail(tmp_path, capsys):
    path = _scan(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    query = {"input_path": str(path), "url": URL, "response_limit": 1, "form_limit": 1}

    direct = handlers.scan_url_detail(**query)
    assert direct["ok"] is True and direct["state"] == "available"
    assert direct["source"]["source_kind"] == "native"
    assert direct["page"]["url"].endswith("token=%5Bredacted%5D")
    assert "top-secret" not in json.dumps(direct)
    assert direct["responses"]["has_more"] is True
    assert direct["responses"]["next_offset"] == 1
    assert direct["forms"]["has_more"] is True
    assert direct["forms"]["next_offset"] == 1
    assert direct["forms"]["items"][0]["action"] == "/find?q=%5Bredacted%5D"
    assert direct["page"]["redirect_chain"][0]["location_raw"].endswith("token=%5Bredacted%5D")
    assert (
        cli.main(
            [
                "scan",
                "url-detail",
                "--scan",
                str(path),
                "--url",
                URL,
                "--response-limit",
                "1",
                "--form-limit",
                "1",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == direct
    assert _mcp_tool().fn(**query) == direct
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_exact_url_unavailable_source_and_limits_are_named(tmp_path):
    path = _scan(tmp_path)
    missing = handlers.scan_url_detail(input_path=str(path), url="https://example.test/missing")
    assert missing["ok"] is True and missing["state"] == "not_found"
    limited = handlers.scan_url_detail(input_path=str(path), url=URL, max_bytes=4096)
    assert limited["state"] == "limit_reached"
    sf = tmp_path / "audit.json"
    sf.write_text("{}")
    unavailable = handlers.scan_url_detail(input_path=str(sf), url=URL)
    assert unavailable == {
        "ok": True,
        "state": "unavailable",
        "reason": "Screaming Frog audit JSON does not retain native per-URL transport evidence",
        "source": {"source_kind": "screaming_frog", "scan_uuid": None, "evidence_revision": None},
    }


def test_header_reader_redacts_a_sensitive_legacy_pair_without_returning_its_value():
    result = _detail_headers(json.dumps([["authorization", "never-return-this"]]), "headers")
    assert result == [["X-SEOHEAD-Redacted-Headers", "authorization"]]
    assert "never-return-this" not in json.dumps(result)


def test_mcp_is_read_only_and_error_is_structured(tmp_path):
    tool = _mcp_tool()
    assert tool.annotations.readOnlyHint is True
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as exc:
        tool.fn(input_path=str(tmp_path / "missing.sqlite"), url=URL)
    assert json.loads(str(exc.value))["ok"] is False
