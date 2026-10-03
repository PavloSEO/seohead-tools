"""The provider capability/workflow matrix stays bound to the code it describes."""

from __future__ import annotations

import inspect
import json

from seohead.cli import COMMANDS
from seohead.data_sources.providers import provider_registry
from seohead.provider_matrix import (
    PROVIDER_REF_KINDS,
    SUPPORT_STATES,
    UNSUPPORTED_WORK,
    WORKFLOWS,
    ProviderRef,
    render,
)


def _row(workflow: str):
    matches = [row for row in WORKFLOWS if row.workflow == workflow]
    assert len(matches) == 1, f"expected exactly one {workflow} row"
    return matches[0]


def test_every_workflow_status_is_a_declared_state():
    assert {row.status for row in WORKFLOWS} <= set(SUPPORT_STATES)


def test_every_provider_ref_kind_is_declared():
    for row in WORKFLOWS:
        for ref in row.providers:
            assert isinstance(ref, ProviderRef)
            assert ref.kind in PROVIDER_REF_KINDS, f"{row.workflow}: unknown kind {ref.kind}"


def test_every_surface_command_is_a_real_cli_command():
    referenced = {command for row in WORKFLOWS for command in row.surface}
    missing = referenced - set(COMMANDS)
    assert not missing, f"matrix references commands that do not exist: {sorted(missing)}"


def test_every_registry_provider_ref_is_registered():
    registered = set(provider_registry()["providers"])
    for row in WORKFLOWS:
        missing = {ref.name for ref in row.providers if ref.kind == "registry"} - registered
        assert not missing, f"{row.workflow} references unregistered providers: {missing}"


def test_every_registered_provider_appears_in_some_workflow():
    covered = {ref.name for row in WORKFLOWS for ref in row.providers if ref.kind == "registry"}
    missing = set(provider_registry()["providers"]) - covered
    assert not missing, f"registered providers absent from the matrix: {sorted(missing)}"


def test_dedicated_refs_resolve_to_real_integrations():
    # A "dedicated" ref must name an integration module the handlers actually call —
    # membership in the provider registry is neither required nor sufficient.
    from seohead.data_sources import dataforseo

    dedicated = {ref.name for row in WORKFLOWS for ref in row.providers if ref.kind == "dedicated"}
    assert dedicated == {"dataforseo"}
    assert dataforseo.SOURCE == "dataforseo"


def test_local_refs_resolve_to_in_process_modules():
    import importlib

    for row in WORKFLOWS:
        for ref in row.providers:
            if ref.kind == "local":
                module = importlib.import_module(ref.name)
                assert module.__name__ == ref.name


def test_dataforseo_registry_entry_covers_only_backlinks():
    # google-keywords/google-serp must not be attributed to the registry id that
    # declares only backlinks_summary and is off by default.
    entry = provider_registry()["providers"]["dataforseo_backlinks"]
    assert entry["operations"] == ["backlinks_summary"]
    assert entry["default_enabled"] is False
    for workflow in ("google-demand", "serp-collection"):
        names = {ref.name for ref in _row(workflow).providers}
        assert "dataforseo_backlinks" not in names
        assert any(
            ref.kind == "dedicated" and ref.name == "dataforseo" for ref in _row(workflow).providers
        )


def test_keywords_cluster_is_local_text_clustering_not_serp(monkeypatch):
    """keywords-cluster calls the in-process clusterer with a supplied keyword list.

    There is no provider transport on this path: the handler signature takes only
    params, and the clusterer takes a single params dict — no fetcher or URL.
    """
    from seohead.servers import handlers
    from seohead.tools import clusterer

    params = inspect.signature(clusterer.run_clusterer).parameters
    assert list(params) == ["params"]

    captured = {}

    def fake_run_clusterer(p):
        captured.update(p)
        return {"ok": True, "clusters": [{"label": "x", "keywords": p["keywords"]}]}

    monkeypatch.setattr(clusterer, "run_clusterer", fake_run_clusterer)
    result = handlers.keywords_cluster(keywords=["synthetic alpha", "synthetic beta"])
    assert captured["keywords"] == ["synthetic alpha", "synthetic beta"]
    assert result["ok"] is True and "clusters" in result

    row = _row("keyword-text-clustering")
    assert row.surface == ("keywords-cluster",)
    assert {ref.kind for ref in row.providers} == {"local"}
    assert "arsenkin" not in {ref.name for ref in row.providers}
    # SERP clustering by results overlap stays a distinct, unsupported declared
    # operation; no shipped command reaches it.
    serp_row = _row("serp-clustering")
    assert serp_row.status == "unsupported" and serp_row.surface == ()
    assert [(ref.kind, ref.name) for ref in serp_row.providers] == [("registry", "arsenkin")]


def test_google_keywords_and_serp_use_the_dedicated_integration(monkeypatch):
    """google-keywords/google-serp dispatch to seohead.data_sources.dataforseo
    directly; they never pass through the provider registry."""
    from seohead.data_sources import dataforseo
    from seohead.servers import handlers

    calls = []

    monkeypatch.setattr(
        dataforseo,
        "search_volume",
        lambda keywords, **kw: calls.append(("search_volume", keywords)) or {"ok": True},
    )
    monkeypatch.setattr(
        dataforseo,
        "serp",
        lambda query, **kw: calls.append(("serp", query)) or {"ok": True},
    )

    assert handlers.google_keywords(keywords=["buy shoes"])["ok"] is True
    assert handlers.google_serp(query="buy shoes")["ok"] is True
    assert calls == [("search_volume", ["buy shoes"]), ("serp", "buy shoes")]


def test_registry_backlinks_route_is_separate_from_demand_paths(monkeypatch):
    """provider-collect dataforseo_backlinks dispatches only backlinks_summary."""
    from seohead.data_sources import dataforseo, providers

    calls = []

    def fake_backlinks_summary(**request):
        calls.append(request)
        return {"ok": True, "state": "complete", "summary": {"target": request["target"]}}

    monkeypatch.setattr(dataforseo, "backlinks_summary", fake_backlinks_summary)
    envelope = providers.provider_collect(
        "dataforseo_backlinks", "backlinks_summary", {"target": "example.com"}
    )
    assert calls == [{"target": "example.com"}]
    evidence = envelope["evidence"]
    assert evidence["provider"] == "dataforseo_backlinks"
    assert evidence["operation"] == "backlinks_summary"

    row = _row("link-evidence")
    assert [(ref.kind, ref.name) for ref in row.providers] == [("registry", "dataforseo_backlinks")]


def test_crux_is_field_data_and_pagespeed_stays_lab_only():
    """CrUX returns real-user field metrics; the PSI parser returns Lighthouse lab
    samples marked lab_only — the matrix must keep the two measurement types apart."""
    from seohead.data_sources import crux, pagespeed

    crux_body = {
        "record": {
            "key": {"origin": "https://example.com"},
            "metrics": {
                "largest_contentful_paint": {"percentiles": {"p75": 2500}},
            },
        }
    }
    field = crux.query(
        origin="https://example.com",
        api_key="synthetic-key",
        fetcher=lambda _payload, _key: json.dumps(crux_body),
    )
    assert field["ok"] and field["metrics"]["largest_contentful_paint"]["p75"] == 2500

    psi_body = {
        "lighthouseResult": {
            "finalUrl": "https://example.com/",
            "fetchTime": "2026-01-01T00:00:00Z",
            "lighthouseVersion": "12.0.0",
            "categories": {"performance": {"score": 0.9, "title": "Performance"}},
            "audits": {},
        }
    }
    lab = pagespeed.sample(
        ["https://example.com/"],
        api_key="synthetic-key",
        fetcher=lambda _url, _key: json.dumps(psi_body),
    )
    sample = lab["samples"][0]
    assert sample["lab_only"] is True
    assert "not CrUX field data" in sample["note"]

    assert [ref.name for ref in _row("field-vitals").providers] == ["crux"]
    lab_row = _row("lab-vitals")
    assert [ref.name for ref in lab_row.providers] == ["pagespeed"]
    assert "lab_only" in lab_row.limitations
    assert "real-user" in lab_row.limitations


def test_unsupported_work_is_named_not_silent():
    assert UNSUPPORTED_WORK, "unsupported work must stay explicit"
    rendered = render()
    for name, _ in UNSUPPORTED_WORK:
        assert name in rendered


def test_no_provider_is_labelled_live_verified():
    # "verified" is deliberately not a SUPPORT_STATE: a declared registry entry is not
    # evidence of live access, so no status cell may claim it.
    assert "verified" not in SUPPORT_STATES and "live-verified" not in SUPPORT_STATES
    assert "nothing in this matrix is labelled live-verified" in render()


def test_render_is_deterministic_and_covers_the_registry():
    first, second = render(), render()
    assert first == second
    for name in provider_registry()["providers"]:
        assert f"`{name}`" in first
