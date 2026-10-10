"""Provider MCP handlers: thin delegation to the shared provider boundary."""

from __future__ import annotations

from seohead.mcp import provider_handlers


def test_provider_doctor_delegates_to_sources_doctor(monkeypatch):
    sentinel = {"ok": True, "sources": []}
    monkeypatch.setattr(provider_handlers, "_doctor", lambda: sentinel)
    assert provider_handlers.provider_doctor() is sentinel


def test_provider_join_forwards_arguments_and_keyword_flags(monkeypatch):
    seen = {}

    def fake_join(crawl_pages, evidence_rows, *, review_external_only, adjustments):
        seen.update(
            crawl_pages=crawl_pages,
            evidence_rows=evidence_rows,
            review_external_only=review_external_only,
            adjustments=adjustments,
        )
        return {"ok": True, "rows": 0}

    monkeypatch.setattr(provider_handlers, "_join", fake_join)
    result = provider_handlers.provider_join(
        [{"url": "https://example.test/a"}],
        [{"url": "https://example.test/a", "metric": 1}],
        review_external_only=True,
        adjustments=[{"url": "https://example.test/a", "action": "keep"}],
    )
    assert result == {"ok": True, "rows": 0}
    assert seen == {
        "crawl_pages": [{"url": "https://example.test/a"}],
        "evidence_rows": [{"url": "https://example.test/a", "metric": 1}],
        "review_external_only": True,
        "adjustments": [{"url": "https://example.test/a", "action": "keep"}],
    }


def test_provider_join_defaults_adjustments_to_none(monkeypatch):
    seen = {}

    def fake_join(crawl_pages, evidence_rows, *, review_external_only, adjustments):
        seen["adjustments"] = adjustments
        seen["review_external_only"] = review_external_only
        return {}

    monkeypatch.setattr(provider_handlers, "_join", fake_join)
    provider_handlers.provider_join([], [])
    assert seen == {"adjustments": None, "review_external_only": False}


def test_provider_registry_delegates(monkeypatch):
    monkeypatch.setattr(provider_handlers, "_registry", lambda: {"providers": ["gsc"]})
    assert provider_handlers.provider_registry() == {"providers": ["gsc"]}
