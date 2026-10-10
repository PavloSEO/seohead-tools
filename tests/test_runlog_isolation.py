"""The test suite must never write the run journal into the real user configuration."""

from seohead.core import runlog


def test_the_journal_is_off_by_default_under_pytest():
    assert runlog.log_path() is None


def test_a_journaled_call_does_not_touch_the_home_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    wrapped = runlog.journaled("parse", lambda **kwargs: {"ok": True})
    assert wrapped(url="https://example.com/") == {"ok": True}
    assert not (tmp_path / ".config" / "seohead" / "runs.jsonl").exists()
