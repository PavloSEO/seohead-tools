"""Benchmark fixture correctness and evidence gates, without a browser launch."""

import json
from pathlib import Path

import pytest

from scripts.benchmark_js_bridge import (
    IDENTITY_FIELDS,
    PROFILE,
    Origin,
    cases,
    continuation_plan,
    cpu_seconds,
    digest,
    growth_gate,
    process_sample,
    verify_export,
)
from scripts.generate_headless_capability_matrix import OUT, document, render
from seohead.checks.parser import parse_html
from seohead.crawl.settings import describe_settings


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


def _retained_matrix(tmp_path):
    """One completed pair; receipts use tiny files, without running a collector."""
    profile = json.loads(PROFILE.read_text())
    previous = tmp_path / "previous"
    pair = previous / "pair-00"
    pair.mkdir(parents=True)
    browser = tmp_path / "browser"
    browser.write_text("fixture")
    browser.chmod(0o700)
    identity = {key: f"frozen-{key}" for key in IDENTITY_FIELDS}
    identity.update(
        revision="a" * 40,
        dirty=False,
        fixture_origin="http://127.0.0.1:57171",
        browser_sha256=digest(browser),
    )
    config = {
        **cases(profile)[0],
        "source_revision": identity["revision"],
        "origin_port": 57171,
        "browser_path": str(browser),
    }
    rows = []
    for name in profile["temperature"]:
        target = pair / name
        target.mkdir()
        scan = target / "scan.seohead"
        scan.write_bytes(b"retained scan")
        audit = target / "scan.seohead.audit-v2.sqlite"
        audit.write_bytes(b"retained audit")
        (target / "pages.pages.csv").write_text(
            "url;title;representation\n"
            + "".join(
                f"http://127.0.0.1:57171/p/{n};Owned page {n};static\n"
                for n in range(config["pages"])
            )
        )
        row = {
            "case": name,
            "status": "passed",
            "scan_sha256": digest(scan),
            "audit_sha256": digest(audit),
            "inspect_ok": True,
            "reanalysis_ok": True,
            "export": {"ok": True, "counts": {"pages": config["pages"]}},
        }
        (target / "result.json").write_text(json.dumps(row))
        rows.append(row)
    worker = {"status": "passed", "configuration": config, "identity": identity, "cases": rows}
    record = {"status": "passed", "worker_exit": 0, "configuration": config, "phase_samples": []}
    for path, value in (
        (
            previous / "frozen-manifest.json",
            {"approved_command": "run", "identity": identity, "profile": profile},
        ),
        (previous / "manifest.json", {"identity": identity, "results": [record]}),
        (pair / "configuration.json", config),
        (pair / "measurement.json", record),
        (pair / "result.json", worker),
    ):
        path.write_text(json.dumps(value))
    return previous, identity, profile, browser


def test_continuation_plan_reuses_only_verified_pairs_and_preserves_the_origin(tmp_path):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    before = {str(p): p.read_bytes() for p in previous.rglob("*") if p.is_file()}
    plan = continuation_plan(previous, identity, profile, verify_files=True)
    assert plan["status"] == "ready"
    assert len(plan["retained_pairs"]) == 1
    assert plan["pending_pairs"] == list(range(1, 36))
    assert plan["origin_port"] == 57171
    assert {str(p): p.read_bytes() for p in previous.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("key", IDENTITY_FIELDS)
def test_continuation_refuses_changed_source_runtime_or_profile(tmp_path, key):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    identity = {**identity, key: "changed"}
    plan = continuation_plan(previous, identity, profile)
    assert plan["status"] == "blocked"
    assert f"source/runtime identity changed: {key}" in plan["blockers"]


def test_continuation_does_not_restart_partial_recovery_or_reset_elapsed_budget(tmp_path):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    recovery = previous / "pair-01" / "recovery"
    recovery.mkdir(parents=True)
    checkpoint = recovery / "scan.seohead"
    checkpoint.write_bytes(b"active interrupted render with no final interval")
    plan = continuation_plan(previous, identity, profile, verify_files=True)
    assert plan["status"] == "blocked"
    assert any(
        "unfinished pair" in reason and "elapsed budgets" in reason for reason in plan["blockers"]
    )
    assert checkpoint.read_bytes() == b"active interrupted render with no final interval"


@pytest.mark.parametrize("damage", ["scan", "supervisor", "worker", "case"])
def test_continuation_refuses_missing_or_changed_passed_evidence(tmp_path, damage):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    pair = previous / "pair-00"
    if damage == "scan":
        (pair / "application_cold" / "scan.seohead").write_bytes(b"changed")
    elif damage == "supervisor":
        (pair / "measurement.json").unlink()
    elif damage == "worker":
        result = json.loads((pair / "result.json").read_text())
        result["cases"].pop()
        (pair / "result.json").write_text(json.dumps(result))
    else:
        (pair / "application_cold" / "result.json").write_text("{}")
    plan = continuation_plan(previous, identity, profile, verify_files=True)
    assert plan["status"] == "blocked"
    assert any("pair-00:" in reason for reason in plan["blockers"])


def test_metadata_only_plan_does_not_claim_to_verify_artifact_checksums(tmp_path):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    (previous / "pair-00/application_cold/scan.seohead").write_bytes(b"changed")
    plan = continuation_plan(previous, identity, profile)
    assert plan["status"] == "ready_for_artifact_validation"
    assert plan["artifact_hashes_verified"] is False


@pytest.mark.parametrize("damage", ["failure", "counts", "duplicate", "missing", "representation"])
def test_export_gate_refuses_failed_or_incomplete_consumers(tmp_path, damage):
    previous, _identity, profile, _ = _retained_matrix(tmp_path)
    target = previous / "pair-00/application_cold"
    config = {**cases(profile)[0], "origin_port": 57171}
    exported = {"ok": True, "counts": {"pages": config["pages"]}}
    path = target / "pages.pages.csv"
    if damage == "failure":
        exported = {"ok": False, "error": "bounded export failed"}
    elif damage == "counts":
        exported["counts"]["pages"] -= 1
    elif damage == "duplicate":
        with path.open("a") as stream:
            stream.write("http://127.0.0.1:57171/p/0;Owned page 0;static\n")
    elif damage == "missing":
        path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    else:
        path.write_text(path.read_text().replace(";static", ";rendered"))
    with pytest.raises(ValueError, match=r"export|CSV"):
        verify_export(exported, target, config)


def test_continue_skips_passed_pairs_without_resetting_retained_disk_budget(tmp_path, monkeypatch):
    from scripts import benchmark_js_bridge as benchmark

    previous, identity, _profile, browser = _retained_matrix(tmp_path)
    output = tmp_path / "continued"
    calls = []
    monkeypatch.setattr(benchmark, "source_identity", lambda: dict(identity))
    monkeypatch.setattr(benchmark, "installed_browser", lambda: browser)
    monkeypatch.setattr(benchmark, "growth_gate", lambda *args: {"status": "passed"})

    def supervise(config, target, suite_root, profile, *, retained_bytes):
        assert retained_bytes > 0
        assert config["origin_port"] == 57171
        calls.append(target.name)
        return {"status": "passed", "reason": "", "configuration": config}

    monkeypatch.setattr(benchmark, "supervise", supervise)
    assert (
        benchmark.main(
            [
                "continue",
                "--out",
                str(output),
                "--resume-from",
                str(previous),
                "--source-revision",
                identity["revision"],
                "--execute",
            ]
        )
        == 0
    )
    assert calls == [f"pair-{n:02d}" for n in range(1, 36)]
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["results"]) == 36
    assert manifest["results"][0]["retained_from"] == str(previous / "pair-00")
    assert not (output / "pair-00").exists()


def test_chained_continuation_keeps_original_receipts_and_disk_roots(tmp_path):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    first = continuation_plan(previous, identity, profile, verify_files=True)
    second = tmp_path / "second"
    second.mkdir()
    frozen = {
        "approved_command": "continue",
        "profile": profile,
        "identity": identity,
        "retained_output_roots": first["retained_output_roots"],
    }
    (second / "frozen-manifest.json").write_text(json.dumps(frozen))
    (second / "manifest.json").write_text(
        json.dumps({"identity": identity, "results": first["retained_pairs"]})
    )
    plan = continuation_plan(second, identity, profile, verify_files=True)
    assert plan["status"] == "ready"
    assert plan["retained_output_roots"] == [str(previous), str(second)]
    assert plan["retained_pairs"] == first["retained_pairs"]
    frozen["retained_output_roots"] = []
    (second / "frozen-manifest.json").write_text(json.dumps(frozen))
    assert (
        "retained pair is outside the recorded output budget roots"
        in continuation_plan(second, identity, profile)["blockers"]
    )


def test_completed_matrix_is_not_restarted_into_an_empty_directory(tmp_path):
    previous, identity, profile, _ = _retained_matrix(tmp_path)
    manifest = json.loads((previous / "manifest.json").read_text())
    manifest["results"] *= len(cases(profile))
    (previous / "manifest.json").write_text(json.dumps(manifest))
    plan = continuation_plan(previous, identity, profile)
    assert "matrix already has every pair; no continuation is pending" in plan["blockers"]
