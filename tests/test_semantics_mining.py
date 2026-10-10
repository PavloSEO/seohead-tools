import pytest

from seohead.semantics.stages import mine

pytestmark = pytest.mark.usefixtures("semantics_offline")


def test_mining_uses_toolkit_title_description_and_headings(monkeypatch):
    calls = []

    def parse(url, options):
        calls.append((url, options))
        return {
            "ok": True,
            "title": "Synthetic title",
            "meta_description": "Synthetic description",
            "headings": {"h1": ["Synthetic heading"], "h2": ["Second heading"]},
        }

    monkeypatch.setattr(mine, "parse_url", parse)
    text = mine._fetch("https://example.invalid/page")
    assert all(
        word in text for word in ("Synthetic title", "Synthetic description", "Synthetic heading")
    )
    assert calls[0][1]["links"] is False


def test_failed_toolkit_measurement_is_not_page_text(monkeypatch, capsys):
    monkeypatch.setattr(
        mine, "parse_url", lambda *a, **kw: {"ok": False, "error": "blocked target"}
    )
    assert mine._fetch("https://example.invalid/page") == ""
    assert "blocked target" in capsys.readouterr().out
