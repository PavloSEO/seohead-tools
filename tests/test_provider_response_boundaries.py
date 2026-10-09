"""Offline regressions for malformed successful provider responses."""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from seohead import cli
from seohead.data_sources import crux, gsc, spend, yandex_cloud


class _MalformedResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"{not valid json"


def test_wordstat_received_malformed_response_is_journaled_once(monkeypatch, tmp_path):
    monkeypatch.setenv("SEOHEAD_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    calls = []

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(request)
        return _MalformedResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ValueError):
        yandex_cloud.Wordstat(api_key="synthetic", folder_id="synthetic").top("synthetic seed")

    assert len(calls) == 1
    rows = spend.read_all()
    assert len(rows) == 1
    assert rows[0]["operation"] == "wordstat.topRequests"
    assert rows[0]["extra"] == {
        "response_received": True,
        "attempt_failed": "malformed_response",
        "charge_uncertain": True,
        "status": 200,
    }


ProviderCall = Callable[[str], dict[str, Any]]


def _search_analytics(body: str) -> dict[str, Any]:
    return gsc.search_analytics(
        "sc-domain:example.com",
        start_date="2026-01-01",
        end_date="2026-01-31",
        token="synthetic",
        fetcher=lambda _payload, _token: body,
    )


def _inspect_url(body: str) -> dict[str, Any]:
    return gsc.inspect_url(
        "sc-domain:example.com",
        "https://example.com/",
        token="synthetic",
        fetcher=lambda _payload, _token: body,
    )


def _crux_report(body: str) -> dict[str, Any]:
    return crux.query(
        url="https://example.com/",
        api_key="synthetic",
        fetcher=lambda _payload, _key: body,
    )


@pytest.mark.parametrize("provider", [_search_analytics, _inspect_url, _crux_report])
@pytest.mark.parametrize("body", ["{", "[]", "", "  ", "null"])
def test_provider_rejects_malformed_successful_response(provider: ProviderCall, body: str):
    result = provider(body)

    assert result["ok"] is False
    assert "malformed response" in result["error"]


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        (_search_analytics, '{"rows": {}}'),
        (_search_analytics, '{"rows": ["not a row"]}'),
        (_inspect_url, '{"inspectionResult": []}'),
        (_inspect_url, '{"inspectionResult": {"indexStatusResult": []}}'),
        (_crux_report, '{"record": []}'),
        (_crux_report, '{"record": {"key": [], "metrics": {}}}'),
        (_crux_report, '{"record": {"metrics": {"lcp": []}}}'),
        (_crux_report, '{"record": {"metrics": {"lcp": {"percentiles": []}}}}'),
        (_crux_report, '{"record": {"metrics": null}}'),
        (_crux_report, '{"record": {"key": null}}'),
        (_crux_report, '{"record": {"metrics": {"lcp": {"percentiles": null}}}}'),
    ],
)
def test_provider_rejects_invalid_nested_containers(provider: ProviderCall, body: str):
    result = provider(body)

    assert result["ok"] is False
    assert "malformed response" in result["error"]


@pytest.mark.parametrize("body", ["{", "[]"])
@pytest.mark.parametrize("mode", ["search_analytics", "inspect_url", "crux"])
def test_cli_serializes_malformed_provider_responses(monkeypatch, capsys, mode: str, body: str):
    if mode == "search_analytics":
        monkeypatch.setenv("GSC_ACCESS_TOKEN", "synthetic")
        monkeypatch.setattr(gsc, "_default_fetcher", lambda _url: lambda _payload, _token: body)
        argv = [
            "gsc-query",
            "--site-url",
            "sc-domain:example.com",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-01-31",
        ]
    elif mode == "inspect_url":
        monkeypatch.setenv("GSC_ACCESS_TOKEN", "synthetic")
        monkeypatch.setattr(gsc, "_default_fetcher", lambda _url: lambda _payload, _token: body)
        argv = [
            "gsc-query",
            "--site-url",
            "sc-domain:example.com",
            "--mode",
            "inspect_url",
            "--inspection-url",
            "https://example.com/",
        ]
    else:
        monkeypatch.setenv("CRUX_API_KEY", "synthetic")
        monkeypatch.setattr(crux, "_default_fetcher", lambda _payload, _key: body)
        argv = ["crux-report", "--url", "https://example.com/"]

    assert cli.main(argv) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "malformed response" in result["error"]


def test_mcp_serializes_a_malformed_provider_result(monkeypatch):
    pytest.importorskip("mcp")
    from mcp import types

    from seohead.mcp.mcp_server import build_server

    monkeypatch.setenv("GSC_ACCESS_TOKEN", "synthetic")
    monkeypatch.setattr(gsc, "_default_fetcher", lambda _url: lambda _payload, _token: "{")
    server = build_server()._mcp_server
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="seo_gsc_query",
            arguments={
                "site_url": "sc-domain:example.com",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            },
        ),
    )
    result = asyncio.run(server.request_handlers[types.CallToolRequest](request)).root
    assert result.isError is True
    message = result.content[0].text
    payload = json.loads(message[message.index("{") :])
    assert payload["ok"] is False
    assert "malformed response" in payload["error"]
