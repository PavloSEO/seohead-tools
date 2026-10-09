"""Paid-call confirmation gate: refusal happens before any client or request exists."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from seohead.data_sources import dataforseo, spend_gate


def _eligible(**overrides):
    kwargs = {"enabled": True, "account_eligible": True, "cache_key": "k-949"}
    kwargs.update(overrides)
    return kwargs


def test_gate_refuses_without_confirmation():
    result = spend_gate.confirmation_required(False, "op", {"cost_usd": 0.02})
    assert result["ok"] is False
    assert result["state"] == "confirmation_required"
    assert result["operation"] == "op"
    assert result["estimate"] == {"cost_usd": 0.02}


def test_gate_passes_only_on_explicit_true():
    assert spend_gate.confirmation_required(True, "op") is None
    assert spend_gate.confirmation_required(1, "op")["state"] == "confirmation_required"


def test_backlinks_refused_without_confirm_and_no_request_sent(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("paid request must not be sent without confirm_paid")

    monkeypatch.setattr(dataforseo, "_run", forbidden)
    client = SimpleNamespace(env="sandbox", post=forbidden)
    result = dataforseo.backlinks_summary("example.com", client=client, **_eligible())
    assert result["state"] == "confirmation_required"
    assert result["ok"] is False


def test_backlinks_runs_when_confirmed(monkeypatch):
    calls = []

    def fake_run(source, operation, payload, n):
        calls.append((operation, n))
        return ([{"target": "example.com"}], [], 0.02, False)

    monkeypatch.setattr(dataforseo, "_run", fake_run)
    client = SimpleNamespace(env="sandbox")
    result = dataforseo.backlinks_summary(
        "example.com", client=client, confirm_paid=True, **_eligible()
    )
    assert calls == [("backlinks_summary", 1)]
    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["cost_usd"] == 0.02


def test_disabled_adapter_still_skips_before_gate(monkeypatch):
    monkeypatch.setattr(dataforseo, "_run", lambda *a, **k: pytest.fail("no request"))
    result = dataforseo.backlinks_summary("example.com", enabled=False)
    assert result["state"] == "skipped"
