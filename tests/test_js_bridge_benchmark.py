"""Benchmark fixture correctness and evidence gates, without a browser launch."""

import json
from pathlib import Path

import pytest

from scripts.benchmark_js_bridge import (
    PROFILE,
    Origin,
    cases,
    cpu_seconds,
    growth_gate,
    process_sample,
)
from scripts.generate_headless_capability_matrix import OUT, document, render
from seohead.crawl.settings import describe_settings
from seohead.tools.parser import parse_html


def test_profile_freezes_72_cases_and_crosses_the_rendered_form_boundary():
    profile = json.loads(PROFILE.read_text())
    pairs = cases(profile)
    assert len(pairs) == 36
    assert len(pairs) * len(profile["temperature"]) == 72
    assert max(x["pages"] * x["forms"] for x in pairs) == 20480
    assert profile["budgets"]["render_wall_seconds"] == 600
    assert profile["budgets"]["total_outputs_mib"] == 16384


@pytest.mark.parametrize("pages", [32, 80, 160])
@pytest.mark.parametrize("links", [8, 64])
def test_fixture_counts_come_from_real_dom_not_raw_script_text(pages, links):
    raw = Origin(pages, links, 128, "javascript").page(0).decode()
    parsed = parse_html(raw, "http://127.0.0.1:8000/p/0")
    assert not parsed["forms"] and not parsed["links"]
    html = Origin(pages, links, 128, "html").page(0).decode()
    facts = parse_html(html, "http://127.0.0.1:8000/p/0")
    assert len(facts["forms"]) == 128
    assert len(facts["links"]) == links
    assert len(html.encode()) == 16384


def test_process_sample_includes_children_and_excludes_unrelated_processes(monkeypatch):
    monkeypatch.setattr(
        "scripts.benchmark_js_bridge.subprocess.check_output",
        lambda *a, **kw: (
            "10 1 100 00:01.50\n11 10 200 00:02.00\n12 11 300 00:00.50\n40 1 900 00:50.00\n"
        ),
    )
    known = set()
    result = process_sample(10, known)
    assert result["rss_bytes"] == 600 * 1024
    assert known == {10, 11, 12}
    assert sum(result["cpu_seconds"].values()) == 4
    assert cpu_seconds("1-02:03:04") == 93784


def test_missing_growth_measurements_cannot_be_called_passed():
    result = growth_gate([], json.loads(PROFILE.read_text()))
    assert result["status"] == "blocked"
    assert result["missing_phase_axes"]


def test_versioned_matrix_covers_all_settings_and_resolves_evidence_paths():
    matrix = document()
    assert OUT.read_text(encoding="utf-8") == render()
    assert {x["path"] for x in matrix["settings"]} == {x["path"] for x in describe_settings()}
    root = Path(__file__).resolve().parents[1]
    for row in matrix["settings"] + matrix["workflows"]:
        assert (root / row["implementation"]).is_file()
        assert (root / row["behavioral_test_reference"]).is_file()
        assert row["linked_acceptance_gaps"]


def test_explicit_browser_preflight_does_not_start_playwright(monkeypatch, tmp_path):
    import sys

    from scripts.benchmark_js_bridge import installed_browser

    executable = tmp_path / "owned-browser"
    executable.write_text("fixture")
    monkeypatch.setenv("SEOHEAD_CHROME", str(executable))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert installed_browser() == executable.resolve()


def test_owned_origins_reuse_the_frozen_authority_between_pairs():
    with Origin(2, 1, 2, "html") as first:
        authority = first.url
        port = first.server.server_port
    with Origin(2, 1, 2, "javascript", port=port) as second:
        assert second.url == authority


def test_benchmark_freezes_every_default_before_environment_overrides(monkeypatch):
    from scripts.benchmark_js_bridge import benchmark_overrides
    from seohead.crawl.settings import load

    monkeypatch.setenv("SEOHEAD_CRAWL_MAX_URLS", "9000")
    case = {"pages": 2, "mode": "javascript", "render_limit": 2}
    overrides = benchmark_overrides(case, json.loads(PROFILE.read_text()))
    assert {row["path"] for row in describe_settings()} <= set(overrides)
    resolved = load(overrides=overrides)
    assert resolved["limits"]["max_urls"] == 2
    assert resolved["rendering"]["browser"]["transport"] == "local"
    assert resolved["http"]["credential_headers"] == []
