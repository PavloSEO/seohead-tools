"""Projectless crawls share one per-host schedule through the user state directory."""

import os
import stat

from seohead.projects.origin_pacing import ProjectOriginPacer

TARGET = "https://example.test/start"


def _state(tmp_path, monkeypatch):
    state = tmp_path / "state" / "seohead"
    monkeypatch.setenv("SEOHEAD_CONFIG_DIR", str(state))
    return state


def test_projectless_pacer_stores_schedule_in_user_state_dir(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    pacer = ProjectOriginPacer(None, TARGET, minimum_delay_seconds=0, max_requests_per_second=4)

    assert pacer.path == state / ".origin-pacing.sqlite"
    assert pacer.reserve() == 0.0
    assert stat.S_IMODE(os.stat(pacer.path).st_mode) == 0o600


def test_projectless_pacers_share_one_host_schedule(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    first = ProjectOriginPacer(None, TARGET, minimum_delay_seconds=0, max_requests_per_second=2)
    second = ProjectOriginPacer(None, TARGET, minimum_delay_seconds=0, max_requests_per_second=2)

    assert first.reserve() == 0.0
    # A second process reserves the slot after the first one: about one interval later.
    assert 0.4 < second.reserve() <= 0.5


def test_projectless_unlimited_pacer_reserves_nothing(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    pacer = ProjectOriginPacer(None, TARGET, minimum_delay_seconds=0, max_requests_per_second=0)

    assert [pacer.reserve() for _ in range(5)] == [0.0] * 5
    assert not pacer.path.exists()
