"""Locale, explicit scan selection and unknown-progress observer contracts."""

from __future__ import annotations

import copy
import io

import pytest
from rich.console import Console

from seohead.cli import build_parser
from seohead.tui import localization, scan_now
from seohead.tui.app import run


@pytest.fixture(autouse=True)
def language_reset():
    original = localization.LANGUAGE
    yield
    localization.LANGUAGE = original


def test_flags_and_locale(monkeypatch):
    args = build_parser().parse_args(
        ["watch", "--project", "work", "--lang", "ru", "--scan", "one", "--compact"]
    )
    assert args.lang == "ru" and args.scan == "one" and args.compact
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    assert localization.resolve_language(None) == "ru"
    assert localization.resolve_language("en") == "en"


def test_scan_selector_preserves_source_and_fails_explicitly():
    snapshot = {"scans": {"items": [{"uuid": "one"}, {"uuid": "two"}]}, "runs": {"items": []}}
    before = copy.deepcopy(snapshot)
    assert scan_now.select(snapshot, "one")["scans"]["items"] == [{"uuid": "one"}]
    assert snapshot == before
    with pytest.raises(ValueError, match="absent"):
        scan_now.select(snapshot, "missing")


def test_compact_uses_real_counts_and_does_not_invent_unknowns(monkeypatch):
    snapshot = {
        "project": {"site": {"label": "User title"}},
        "runs": {
            "items": [
                {
                    "id": "one",
                    "state": "running",
                    "counters": {"fetched": 4, "queued": 5, "inflight": 1},
                    "telemetry": {"queue_semantics": "separate", "state": "fresh"},
                }
            ]
        },
        "scans": {"items": []},
    }
    monkeypatch.setattr("seohead.tui.app._watch_snapshot", lambda _p: (snapshot, []))
    output = io.StringIO()
    assert (
        run(
            project="workspace",
            lang="ru",
            scan="one",
            compact=True,
            stdin=io.StringIO(),
            console=Console(file=output, width=300, no_color=True),
        )
        == 0
    )
    rendered = output.getvalue()
    assert "Скан сейчас" in rendered and "4 / 10" in rendered and "40%" in rendered
    assert "Ошибки нет данных" in rendered and "User title" in rendered
    snapshot["runs"]["items"][0]["telemetry"]["queue_semantics"] = "unknown"
    assert "40%" not in scan_now.summary(snapshot)
