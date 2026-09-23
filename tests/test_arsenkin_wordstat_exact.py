"""Exact `!W` must be requested from the provider tool that actually exists.

`keywords_exact` used to create a task named `keywords_frequency` with the phrase array under
`keywords`. Arsenkin has no such tool, so every call answered `404 WRONG_TOOL` and the command
was dead. The live contract is the `wordstat` tool, `type=1`, phrases under `queries`, regions
as a list; punctuation such as `/` or quotes makes the provider reject the whole batch with
`422 JSON_VALIDATION_ERROR`.

These tests pin the payload and the result parsing. No network and no billing: the client is a
double, because a real call spends account limits.
"""

from __future__ import annotations

import pytest

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
    return {
        "type": 1,
        "task_id": "31420542",
        "data": {
            "device": None,
            "task_id": "31420542",
            "queries": ["работа для подростков", "график 4 3 это как"],
            "regions": {region: "Беларусь"},
            "result": {
                "работа для подростков": {region: {"base": 550, "quoted": 15}},
                "график 4 3 это как": {region: {"base": 6, "quoted": 1}},
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
        "ws": ["base", "quoted"],
        "queries": ["средняя зарплата"],
    }
    assert "keywords" not in payload


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


def test_parse_flattens_phrase_and_region_nesting():
    parsed = arsenkin.parse_wordstat(_provider_result(), 149)
    assert parsed == {
        "работа для подростков": {"base": 550, "quoted": 15},
        "график 4 3 это как": {"base": 6, "quoted": 1},
    }


def test_parse_accepts_the_get_envelope():
    envelope = {"code": "TASK_RESULT", "result": _provider_result()}
    assert arsenkin.parse_wordstat(envelope, 149)["работа для подростков"]["quoted"] == 15


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": {"result": []}}, None, "x"])
def test_parse_returns_empty_mapping_on_unexpected_shape(payload):
    # The task is already paid for; the raw payload still travels upstream for recovery.
    assert arsenkin.parse_wordstat(payload, 149) == {}


# --- handler ---------------------------------------------------------------


def test_handler_creates_a_wordstat_task_and_returns_frequencies(monkeypatch):
    stub = _StubClient(_provider_result())
    monkeypatch.setattr(arsenkin, "ArsenkinClient", lambda *a, **kw: stub)

    out = handlers.keywords_exact(
        keywords=["работа для подростков", "график 4/3 это как"], region=149
    )

    assert stub.created[0][0] == "wordstat"
    assert stub.created[0][1]["queries"] == ["работа для подростков", "график 4 3 это как"]
    assert out["ok"] is True
    assert out["region"] == 149
    assert out["frequencies"]["работа для подростков"]["quoted"] == 15
    assert out["cleaned"] == {"график 4/3 это как": "график 4 3 это как"}
    assert out["result"]["data"]["regions"] == {"149": "Беларусь"}


def test_handler_without_wait_reports_the_billed_task(monkeypatch):
    stub = _StubClient(_provider_result())
    monkeypatch.setattr(arsenkin, "ArsenkinClient", lambda *a, **kw: stub)

    out = handlers.keywords_exact(keywords=["оклад"], region=149, wait=False)

    assert out["task_id"] == 31420542
    assert out["cost"] == 100
    assert "frequencies" not in out


def test_handler_still_requires_keywords():
    with pytest.raises(ValueError):
        handlers.keywords_exact(keywords=[])
