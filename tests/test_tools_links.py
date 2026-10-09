"""seohead.checks.links.check_links — no network; the page and every HTTP response
are faked."""

from __future__ import annotations

from seohead.checks import links as links_mod
from seohead.checks.links import check_links


class _FakeResponse:
    def __init__(self, status_code: int = 200, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class _FakeClient:
    """Records every URL it was asked to HEAD, in order."""

    def __init__(self):
        self.head_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def head(self, url: str):
        self.head_calls.append(url)
        return _FakeResponse(200)

    def get(self, url: str):  # pragma: no cover - only used on a HEAD 4xx/405
        return _FakeResponse(200)


def _wire_fake_page(monkeypatch, links: list[dict]):
    monkeypatch.setattr(
        "seohead.checks.parser.parse_url",
        lambda url, options=None: {"ok": True, "links": links},
    )
    client = _FakeClient()
    monkeypatch.setattr(links_mod, "http_client", lambda *a, **kw: (client, False))
    return client


def test_fragment_variants_of_one_link_are_checked_once_not_twice(monkeypatch):
    """#194: /guide#first and /guide#second are the same server resource; deduping
    the literal href string checked it twice and could still report the run as
    truncated even though nothing distinct was actually skipped."""
    client = _wire_fake_page(
        monkeypatch,
        [
            {"href": "https://example.com/guide#first", "external": False},
            {"href": "https://example.com/guide#second", "external": False},
        ],
    )
    result = check_links("https://example.com/", limit=200)
    assert result["checked"] == 1
    assert result["truncated"] is False
    assert client.head_calls == ["https://example.com/guide"]


def test_a_fragment_variant_cannot_crowd_out_a_later_unique_target(monkeypatch):
    """#194: with a limit of 2, two fragment variants of one page plus one distinct
    link must count as two probes, not three -- so the distinct link is still
    reached instead of being pushed past the bound by a phantom duplicate."""
    client = _wire_fake_page(
        monkeypatch,
        [
            {"href": "https://example.com/guide#first", "external": False},
            {"href": "https://example.com/guide#second", "external": False},
            {"href": "https://example.com/other", "external": False},
        ],
    )
    result = check_links("https://example.com/", limit=2)
    assert result["checked"] == 2
    assert result["truncated"] is False
    assert client.head_calls == ["https://example.com/guide", "https://example.com/other"]
