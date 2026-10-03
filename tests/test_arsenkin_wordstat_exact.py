"""Exact `!W` must be requested from the provider tool that actually exists.

`keywords_exact` used to create a task named `keywords_frequency` with the phrase array under
`keywords`. Arsenkin has no such tool, so every call answered `404 WRONG_TOOL` and the command
was dead. The live contract is the `wordstat` tool, `type=1`, phrases under `queries`, regions
as a list; punctuation such as `/` or quotes makes the provider reject the whole batch with
`422 JSON_VALIDATION_ERROR`.

The `ws` selectors are four different operators and only one of them is `!W`
(https://help.arsenkin.ru/api/api-wordstat/frequency_value): `base` is broad (WS), `quoted` is
the quoted phrase ("WS"), `overal` is the exact wordform (!WS) that `!W` asks for, and `exact`
is the strict-order form ([!WS]). These tests pin the payload, the parser and the sanitizer.
No network and no billing: the client is a double, because a real call spends account limits.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from seohead import cli
from seohead.data_sources import arsenkin
from seohead.servers import handlers


class _StubClient(arsenkin.ArsenkinClient):
    """Records the created task and replays a canned provider result."""

    def __init__(self, result):
        self.created: list[tuple[str, dict]] = []
        self._result = result

    def set_task(self, tools_name, data):
        self.created.append((tools_name, data))
        return {"task_id": 31420542, "cost": 100, "raw": {}}

    def wait(self, task_id, timeout=900, interval=8):
        return {"code": "TASK_RESULT", "task_id": task_id, "result": self._result}


def _provider_result(region="149"):
    """A synthetic `/get` payload with all four `ws` fields deliberately distinct."""
    return {
        "type": 1,
        "task_id": "31420542",
        "data": {
            "device": None,
            "task_id": "31420542",
            "queries": ["работа для подростков", "график 4 3 это как"],
            "regions": {region: "Беларусь"},
            "result": {
                "работа для подростков": {
                    region: {"base": 550, "quoted": 15, "overal": 8, "exact": 3}
                },
                "график 4 3 это как": {region: {"base": 6, "quoted": 2, "overal": 1, "exact": 0}},
            },
        },
    }


# --- payload ---------------------------------------------------------------


def test_payload_uses_the_wordstat_tool_and_queries_field():
    payload = arsenkin.wordstat_payload(["средняя зарплата"], 149)
    assert arsenkin.WORDSTAT_TOOL == "wordstat"
    assert payload == {
        "type": 1,
        "regions": [149],
        "ws": ["base", "overal"],
        "queries": ["средняя зарплата"],
    }
    assert "keywords" not in payload


def test_payload_requests_overal_not_quoted_or_exact():
    """`!W` is the `overal` selector: `quoted` is "WS" and `exact` is [!WS]."""
    ws = arsenkin.wordstat_payload(["оклад"], 149)["ws"]
    assert "overal" in ws
    assert "quoted" not in ws
    assert "exact" not in ws


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("график 4/3 это как", "график 4 3 это как"),
        ('зарплата "на руки"', "зарплата на руки"),
        ("ставка 0,5 — сколько часов", "ставка 0 5 сколько часов"),
        ("  лишние   пробелы  ", "лишние пробелы"),
        ("job-hopping", "job-hopping"),
    ],
)
def test_disallowed_punctuation_is_stripped(raw, expected):
    assert arsenkin.sanitize_wordstat_query(raw) == expected


def test_empty_phrases_are_dropped_and_an_all_empty_batch_fails():
    payload = arsenkin.wordstat_payload(["///", "оклад"], 149)
    assert payload["queries"] == ["оклад"]
    with pytest.raises(ValueError):
        arsenkin.wordstat_payload(["///", "***"], 149)


def test_region_is_always_a_list_of_one():
    # A multi-region request SUMS frequency, so the caller asks one region at a time.
    assert arsenkin.wordstat_payload(["оклад"], "149")["regions"] == [149]


# --- parsing ---------------------------------------------------------------


def test_parse_reports_overal_as_the_exact_figure():
    parsed = arsenkin.parse_wordstat(_provider_result(), 149)
    assert parsed["warnings"] == []
    # The quoted ("WS") 15 and strict-order ([!WS]) 3 must not surface as `!W`.
    assert parsed["frequencies"] == {
        "работа для подростков": {"base": 550, "overal": 8},
        "график 4 3 это как": {"base": 6, "overal": 1},
    }


def test_parse_accepts_the_get_envelope():
    envelope = {"code": "TASK_RESULT", "result": _provider_result()}
    parsed = arsenkin.parse_wordstat(envelope, 149)
    assert parsed["frequencies"]["работа для подростков"]["overal"] == 8


def test_parse_matches_the_requested_string_region_key():
    # Region keys arrive as JSON strings; a normal match parses without warnings.
    parsed = arsenkin.parse_wordstat(_provider_result(region="225"), 225)
    assert parsed["warnings"] == []
    assert parsed["frequencies"]["работа для подростков"]["overal"] == 8


def test_parse_never_borrows_another_region_as_the_requested_one():
    # Region 149 is requested but the provider only returned 225: the 225 numbers must not
    # be relabeled as 149's. The phrase is omitted and the mismatch is warned about, while
    # the raw payload still travels upstream for recovery.
    parsed = arsenkin.parse_wordstat(_provider_result(region="225"), 149)
    assert parsed["frequencies"] == {}
    assert len(parsed["warnings"]) == 2
    assert all("149" in warning and "225" in warning for warning in parsed["warnings"])


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": {"result": []}}, None, "x"])
def test_parse_reports_unavailable_on_unexpected_shape(payload):
    # The task is already paid for; the raw payload still travels upstream for recovery.
    parsed = arsenkin.parse_wordstat(payload, 149)
    assert parsed["frequencies"] == {}
    assert parsed["warnings"]


def test_parse_warns_on_non_dict_rows_and_region_values():
    result = {
        "data": {
            "result": {
                "non-dict row": 42,
                "bad region value": {"149": "oops"},
                "missing fields": {"149": {}},
            }
        }
    }
    parsed = arsenkin.parse_wordstat(result, 149)
    # Absent frequency fields stay None -- they must never be reported as zero.
    assert parsed["frequencies"] == {"missing fields": {"base": None, "overal": None}}
    assert len(parsed["warnings"]) == 2


# --- handler ---------------------------------------------------------------


def test_handler_creates_a_wordstat_task_and_returns_frequencies(monkeypatch):
    stub = _StubClient(_provider_result())
    monkeypatch.setattr(arsenkin, "ArsenkinClient", lambda *a, **kw: stub)

    out = handlers.keywords_exact(
        keywords=["работа для подростков", "график 4/3 это как"], region=149
    )

    assert stub.created[0][0] == "wordstat"
    assert stub.created[0][1]["queries"] == ["работа для подростков", "график 4 3 это как"]
    assert stub.created[0][1]["ws"] == ["base", "overal"]
    assert out["ok"] is True
    assert out["task_id"] == 31420542
    assert out["cost"] == 100
    assert out["region"] == 149
    assert out["frequencies"]["работа для подростков"] == {"base": 550, "overal": 8}
    assert out["cleaned"] == {"график 4/3 это как": "график 4 3 это как"}
    assert out["result"]["data"]["regions"] == {"149": "Беларусь"}
    assert "warnings" not in out


def test_handler_surfaces_region_warnings_and_keeps_the_raw_result(monkeypatch):
    stub = _StubClient(_provider_result(region="225"))
    monkeypatch.setattr(arsenkin, "ArsenkinClient", lambda *a, **kw: stub)

    out = handlers.keywords_exact(keywords=["работа для подростков"], region=149)

    assert out["ok"] is True
    assert out["frequencies"] == {}
    assert out["warnings"]
    # The raw provider payload is preserved so the paid result stays recoverable.
    assert out["result"]["data"]["regions"] == {"225": "Беларусь"}


def test_handler_without_wait_reports_the_billed_task(monkeypatch):
    stub = _StubClient(_provider_result())
    monkeypatch.setattr(arsenkin, "ArsenkinClient", lambda *a, **kw: stub)

    out = handlers.keywords_exact(keywords=["оклад/ставка"], region=149, wait=False)

    assert out["task_id"] == 31420542
    assert out["cost"] == 100
    assert out["region"] == 149
    assert out["cleaned"] == {"оклад/ставка": "оклад ставка"}
    assert "frequencies" not in out


def test_handler_fails_structurally_when_every_query_sanitizes_empty(monkeypatch):
    stub = _StubClient(_provider_result())
    monkeypatch.setattr(arsenkin, "ArsenkinClient", lambda *a, **kw: stub)

    out = handlers.keywords_exact(keywords=["///", "***"], region=149)

    assert out["ok"] is False
    assert out["code"] == "EMPTY_QUERIES"
    assert stub.created == []


def test_handler_still_requires_keywords():
    with pytest.raises(ValueError):
        handlers.keywords_exact(keywords=[])


# --- shared interface paths ------------------------------------------------


def test_cli_maps_flags_to_the_shared_handler(monkeypatch, capsys):
    seen = {}
    monkeypatch.setitem(
        handlers.HANDLERS, "keywords_exact", lambda **kw: seen.update(kw) or {"ok": True}
    )
    rc = cli.main(["keywords-exact", "--keywords", "оклад,ставка", "--region", "149", "--no-wait"])
    assert rc == 0
    assert seen == {"keywords": ["оклад", "ставка"], "region": 149, "wait": False}
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_mcp_tool_calls_the_shared_handler(monkeypatch):
    pytest.importorskip("mcp")
    from seohead.servers.mcp_server import build_server

    seen = {}
    monkeypatch.setattr(
        "seohead.servers.handlers.keywords_exact",
        lambda **kw: seen.update(kw) or {"ok": True},
    )
    tool = next(
        tool
        for tool in build_server()._tool_manager.list_tools()
        if tool.name == "seo_keywords_exact"
    )
    assert asyncio.run(tool.run({"keywords": ["оклад"], "region": 149, "wait": False})) == {
        "ok": True
    }
    assert seen == {"keywords": ["оклад"], "region": 149, "wait": False}
