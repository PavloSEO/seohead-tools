"""Offline acceptance for the typed BI projection (#834)."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from seohead import cli
from seohead.crawl import sqlite_adapter
from seohead.data_sources.evidence_import import normalize_inline
from seohead.data_sources.evidence_join import evidence_compatibility, join_evidence
from seohead.reports import bi as bi_report
from seohead.reports.bi import BIExportError, export_bi
from seohead.servers import handlers
from seohead.storage import open_scan


def _offline_fetch(url: str):
    if url.endswith("/robots.txt"):
        body = "User-agent: *\nAllow: /\n"
        return SimpleNamespace(
            status_code=200,
            text=body,
            content=body.encode(),
            headers={"content-type": "text/plain"},
        )
    if url.endswith("/child"):
        body = (
            "<html><head><title>Child</title>"
            '<meta name="description" content="A child page description">'
            "</head><body><h1>Child page</h1></body></html>"
        )
    else:
        body = (
            "<html><head><title>=Home formula</title>"
            '<meta name="description" content="A home page description long enough for the fixture">'
            "</head><body><h1>Home page</h1>"
            '<a href="/child">=first link</a><a href="/child">second link</a>'
            "</body></html>"
        )
    return SimpleNamespace(
        status_code=200,
        text=body,
        content=body.encode(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def _crawl_with_audit(tmp_path: Path, monkeypatch) -> Path:
    original = sqlite_adapter.crawl_to_scan

    def offline_crawl(*args, **kwargs):
        kwargs["fetcher"] = _offline_fetch
        kwargs["sleeper"] = lambda _seconds: None
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_adapter, "crawl_to_scan", offline_crawl)
    scan_path = tmp_path / "synthetic-scan.sqlite"
    result = handlers.crawl_site(
        url="https://example.test/",
        scan_out=str(scan_path),
        producer_build="a" * 40,
        overrides={
            "speed.min_delay_seconds": 0,
            "limits.max_urls": 2,
            "limits.max_depth": 1,
        },
    )
    assert result["audit_available"] is True
    return scan_path


def _provider_manifest() -> dict:
    return {
        "format": "seohead.evidence-mapping.v1",
        "source": {
            "provider": "gsc",
            "operation": "search_analytics",
            "reporting_identity": "synthetic-property",
            "privacy": "supplied",
            "timezone": "America/Los_Angeles",
            "attribution": "last-click",
            "search_engine": "google",
            "search_type": "web",
        },
        "url": {"field": "url", "kind": "absolute"},
        "row_shape": "flat",
        "dimensions": ["query"],
        "metrics": [
            {"name": "clicks", "type": "number", "unit": "count"},
            {"name": "impressions", "type": "number", "unit": "count"},
        ],
        "period": {"start_date": "2026-01-01", "end_date": "2026-01-07"},
        "collection": {"state": "partial", "reason": "synthetic sampled fixture", "sampled": True},
        "duplicate_policy": "mark",
    }


def _provider_document() -> dict:
    return normalize_inline(
        [
            {
                "url": "https://example.test/",
                "query": "=formula query",
                "clicks": 0,
                "impressions": 100,
            },
            {
                "url": "https://example.test/",
                "query": "=formula query",
                "clicks": 3,
                "impressions": 120,
            },
            {
                "url": "https://outside.example.test/page",
                "query": "external only",
                "clicks": 4,
                "impressions": None,
            },
            {"url": "/relative", "query": "unkeyable", "clicks": 2, "impressions": 3},
        ],
        manifest=_provider_manifest(),
    )


def _csv_rows(package: Path, manifest: dict, dataset: str) -> list[dict[str, str]]:
    result = []
    for partition in manifest["datasets"][dataset]["partitions"]:
        with (package / partition["path"]).open(encoding="utf-8", newline="") as stream:
            result.extend(csv.DictReader(stream))
    return result


def _files(package: Path) -> dict[str, bytes]:
    return {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in sorted(package.rglob("*"))
        if path.is_file()
    }


def test_million_page_projection_factories_are_lazy_and_reiterable():
    produced = {"count": 0}

    def pages():
        for ordinal in range(1_000_000):
            produced["count"] += 1
            yield {
                "url": f"https://example.test/{ordinal}",
                "page_ordinal": ordinal,
                "status_code": 200,
                "content_type": "text/html",
                "size_bytes": 1,
                "response_time": 0.01,
                "title": "Page",
                "meta_description": "",
                "h1": "Page",
                "canonical": "",
                "word_count": 1,
                "crawl_depth": 1,
                "inlinks": 0,
                "unique_inlinks": 0,
                "outlinks": 0,
                "external_outlinks": 0,
                "representation": "static",
                "document_id": 1,
            }

    run = bi_report._RunInput(
        run_id="large-synthetic",
        source_kind="scan",
        source_schema="scan.v1",
        source_name="large.sqlite",
        source_sha256="a" * 64,
        source_bytes=1,
        run_metadata={
            "crawl_state": "complete",
            "crawl_reason": None,
            "capabilities": {"links": {"state": "complete"}},
        },
        pages_factory=pages,
        page_count=1_000_000,
        findings_factory=lambda: iter(()),
        finding_count=0,
        groups=[],
        links_factory=lambda: iter(()),
        links_source_state="complete",
        links_source_reason=None,
        coverage_rows=[],
        link_count=0,
    )
    first = next(run.pages_factory())
    assert first["url"] == "https://example.test/0"
    assert produced["count"] == 1, "page factory must not materialize the million-page population"
    first_cohorts = list(__import__("itertools").islice(bi_report._cohort_rows(run, [], None), 5))
    assert len(first_cohorts) == 5
    assert produced["count"] == 2, "cohort projection must consume only its current page"
    assert sum(1 for _ in run.pages_factory()) == run.page_count
    assert produced["count"] == 1_000_002, "the full source remains re-iterable without a list"


def test_scan_projection_conserves_pages_findings_links_and_provider_grain(tmp_path, monkeypatch):
    scan_path = _crawl_with_audit(tmp_path, monkeypatch)
    provider_path = tmp_path / "synthetic-evidence.json"
    provider_path.write_text(json.dumps(_provider_document()), encoding="utf-8")
    package = tmp_path / "bi-package"

    result = export_bi(
        scan=scan_path,
        provider_joins=[provider_path],
        out_dir=package,
        max_rows_per_file=3,
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    with open_scan(scan_path) as con:
        source_audit = json.loads(con.execute("SELECT document_json FROM audit").fetchone()[0])
        source_pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        source_links = con.execute("SELECT COUNT(*) FROM links").fetchone()[0]

    pages = _csv_rows(package, manifest, "pages")
    findings = _csv_rows(package, manifest, "findings")
    metrics = _csv_rows(package, manifest, "metrics")
    links = _csv_rows(package, manifest, "link_occurrences")
    coverage = _csv_rows(package, manifest, "coverage")

    assert len(pages) == source_pages == manifest["datasets"]["pages"]["row_count"] == 2
    assert len(findings) == len(source_audit["issues"])
    assert len(links) == source_links == 2
    assert len({row["link_occurrence_id"] for row in links}) == 2
    assert {row["link_ordinal"] for row in links} == {"0", "1"}
    assert len(metrics) == 4 * 2  # every provider source row remains at its own grain
    assert [part["rows"] for part in manifest["datasets"]["metrics"]["partitions"]] == [3, 3, 2]
    assert all(
        part["sha256"] == hashlib.sha256((package / part["path"]).read_bytes()).hexdigest()
        for part in manifest["datasets"]["metrics"]["partitions"]
    )
    assert all(
        part["bytes"] == (package / part["path"]).stat().st_size
        for part in manifest["datasets"]["metrics"]["partitions"]
    )

    clicks = [row for row in metrics if row["metric_name"] == "clicks"]
    zero = next(row for row in clicks if row["source_row_index"] == "0")
    assert zero["value_number"] == "0"
    assert zero["value_state"] == "measured"
    assert zero["period_start"] == "2026-01-01" and zero["period_end"] == "2026-01-07"
    assert zero["timezone"] == "America/Los_Angeles"
    assert zero["attribution"] == "last-click"
    assert zero["dimension_query"] == "'=formula query"

    suppressed = next(
        row
        for row in metrics
        if row["metric_name"] == "impressions" and row["source_row_index"] == "2"
    )
    assert suppressed["value_number"] == ""
    assert suppressed["value_state"] == "unavailable"
    assert suppressed["value_reason"] == "null"
    assert all(
        row["ambiguous_source_row"] == "true"
        for row in metrics
        if row["source_row_index"] in {"0", "1"}
    )
    assert manifest["datasets"]["pages"]["schema_version"] == "seohead.bi.pages.v1"
    assert manifest["datasets"]["pages"]["primary_key"] == ["run_id", "url_observation_id"]
    assert manifest["datasets"]["metrics"]["row_count"] == len(metrics)
    assert manifest["datasets"]["link_occurrences"]["row_count"] == source_links
    assert manifest["datasets"]["metrics"]["formula_safe_cells_prefixed"] > 0
    assert any(row["population"] == "crawl_only" and row["source_rows"] == "1" for row in coverage)
    assert any(
        row["population"] == "external_only" and row["source_rows"] == "1" for row in coverage
    )
    assert any(
        row["population"] == "unkeyable_rows" and row["source_rows"] == "1" for row in coverage
    )
    assert result["crawl_state"] in {"complete", "partial"}

    repeated = export_bi(
        scan=scan_path,
        provider_joins=[provider_path],
        out_dir=tmp_path / "bi-package-repeat",
        max_rows_per_file=3,
    )
    assert _files(package) == _files(Path(repeated["output_directory"]))


def test_site_audit_nulls_and_formula_cells_are_explicitly_safe(tmp_path):
    audit = {
        "schema": "seohead.site-audit/1",
        "url": "https://example.test/",
        "domain": "example.test",
        "generated_at": "2026-01-02T03:04:05Z",
        "site": {},
        "pages": [
            {
                "url": "https://example.test/",
                "status_code": 200,
                "title": "=SUM(A1:A2)",
                "description": "Synthetic description",
                "h1": "Heading",
                "word_count": 0,
            }
        ],
        "findings": [
            {
                "source": "synthetic",
                "severity": "notice",
                "url": "https://example.test/",
                "text": "=formula finding",
            }
        ],
        "summary": {
            "pages_checked": 1,
            "findings_total": 1,
            "findings_by_severity": {"critical": 0, "warning": 0, "notice": 1},
            "tools_run": [],
            "tools_failed": [],
        },
    }
    package = tmp_path / "site-audit-bi"
    export_bi(audit=audit, out_dir=package)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    page = _csv_rows(package, manifest, "pages")[0]
    finding = _csv_rows(package, manifest, "findings")[0]

    assert page["title"] == "'=SUM(A1:A2)"
    assert page["title_state"] == "measured"
    assert page["word_count"] == "0" and page["word_count_state"] == "measured"
    assert finding["message"] == "'=formula finding"
    assert manifest["datasets"]["pages"]["formula_safe_cells_prefixed"] >= 1
    assert manifest["datasets"]["findings"]["formula_safe_cells_prefixed"] >= 1
    assert manifest["datasets"]["metrics"]["state"] == "not_configured"
    assert manifest["datasets"]["link_occurrences"]["state"] == "unavailable"


def test_legacy_untyped_provider_join_is_counted_as_unsupported(tmp_path):
    audit_path = Path("tests/doc_fixtures/run/audit.json")
    legacy = tmp_path / "legacy-provider-join.json"
    legacy.write_text(
        json.dumps(
            {
                "format": "seohead.provider-join.v1",
                "join": {"summary": {"rows": 7}},
            }
        ),
        encoding="utf-8",
    )
    package = tmp_path / "legacy-bi"

    export_bi(audit=audit_path, provider_joins=[legacy], out_dir=package)

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    metric_sources = [
        row
        for row in _csv_rows(package, manifest, "coverage")
        if row["dataset"] == "metrics" and row["population"] == "provider_source"
    ]
    assert len(metric_sources) == 1
    assert metric_sources[0]["state"] == "unsupported"
    assert metric_sources[0]["source_rows"] == "7"
    assert "does not declare typed metric names" in metric_sources[0]["reason"]
    assert manifest["datasets"]["metrics"]["state"] == "unavailable"
    assert manifest["datasets"]["metrics"]["row_count"] == 0


def test_saved_issue781_join_artifact_projects_without_rejoining_or_summing(tmp_path):
    doc = _provider_document()
    audit = {
        "schema": "seohead.site-audit/1",
        "url": "https://example.test/",
        "domain": "example.test",
        "generated_at": "2026-01-02T03:04:05Z",
        "site": {},
        "pages": [{"url": "https://example.test/", "title": "Synthetic"}],
        "findings": [],
        "summary": {
            "pages_checked": 1,
            "findings_total": 0,
            "findings_by_severity": {"critical": 0, "warning": 0, "notice": 0},
            "tools_run": [],
            "tools_failed": [],
        },
    }
    join = join_evidence(audit["pages"], doc, crawl={"source": "audit"})
    compatibility = evidence_compatibility(doc, doc)
    artifact = tmp_path / "saved-evidence-join.json"
    artifact.write_text(
        json.dumps({"join": join, "compatibility": compatibility}), encoding="utf-8"
    )
    package = tmp_path / "saved-join-bi"

    export_bi(audit=audit, provider_joins=[artifact], out_dir=package)

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    metrics = _csv_rows(package, manifest, "metrics")
    populations = [row["population_state"] for row in metrics]
    assert len(metrics) == 8
    assert populations.count("matched") == 4
    assert populations.count("external_only") == 2
    assert populations.count("unkeyable") == 2
    assert manifest["provider_sources"][0]["compatibility"]["verdict"] == "compatible"
    assert len({row["metric_observation_id"] for row in metrics}) == len(metrics)


def test_hard_output_limit_fails_without_publishing_a_partial_package(tmp_path):
    audit_path = Path("tests/doc_fixtures/run/audit.json")
    destination = tmp_path / "too-small"
    with pytest.raises(BIExportError, match=r"bound|partition byte limit|row exceeds"):
        export_bi(
            audit=audit_path,
            out_dir=destination,
            max_rows_per_file=10,
            max_bytes_per_file=1024,
            max_output_bytes=1024,
        )
    assert not destination.exists()


def test_cli_and_mcp_share_the_same_offline_package_writer(tmp_path, capsys):
    audit = {
        "schema": "seohead.site-audit/1",
        "url": "https://example.test/",
        "domain": "example.test",
        "generated_at": "2026-01-02T03:04:05Z",
        "site": {},
        "pages": [{"url": "https://example.test/", "title": "Synthetic"}],
        "findings": [],
        "summary": {
            "pages_checked": 1,
            "findings_total": 0,
            "findings_by_severity": {"critical": 0, "warning": 0, "notice": 0},
            "tools_run": [],
            "tools_failed": [],
        },
    }
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    cli_dir = tmp_path / "cli-bi"
    mcp_dir = tmp_path / "mcp-bi"
    assert cli.main(["bi-export", "--audit", str(audit_path), "--out-dir", str(cli_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    tool = next(
        item
        for item in __import__("seohead.servers.mcp_server", fromlist=["build_server"])
        .build_server()
        ._tool_manager.list_tools()
        if item.name == "seo_bi_export"
    )
    response = asyncio.run(tool.run({"audit": str(audit_path), "out_dir": str(mcp_dir)}))
    assert response["format"] == "seohead.bi-export-result.v1"
    assert _files(cli_dir) == _files(mcp_dir)


def test_companion_audit_findings_are_projected_without_reading_empty_inline_slot(
    tmp_path, monkeypatch
):
    from seohead.storage.audit_v2 import write_audit_v2

    scan_path = _crawl_with_audit(tmp_path, monkeypatch)
    with open_scan(scan_path) as con:
        document = json.loads(con.execute("SELECT document_json FROM audit").fetchone()[0])
        metadata = dict(con.execute("SELECT * FROM scan").fetchone())
    issues, pages, groups = (document.pop(key) for key in ("issues", "pages", "groups"))
    document.update(issues=[], pages=[], groups=[])
    write_audit_v2(
        scan_path,
        document,
        {"/issues": issues, "/pages": pages, "/groups": groups},
        {
            "scan_uuid": metadata["scan_uuid"],
            "evidence_revision": metadata["evidence_revision"],
            "analyzer_version": metadata["writer_version"],
            "analyzer_revision": metadata["writer_revision"],
        },
    )
    monkeypatch.setattr(
        "seohead.storage.audit_v2.AuditV2Reader.materialize_legacy",
        lambda *_args, **_kwargs: pytest.fail(
            "BI must stream audit.v2 findings, not materialize legacy JSON"
        ),
    )
    package = tmp_path / "companion-bi"
    export_bi(scan=scan_path, out_dir=package)
    manifest = json.loads((package / "manifest.json").read_text())
    assert len(_csv_rows(package, manifest, "findings")) == len(issues)
    assert manifest["run"]["audit_available"] is True


def test_cohorts_keep_zero_quadrants_separate_from_unconfigured_provider_evidence(tmp_path):
    audit = {
        "schema": "seohead.site-audit/1",
        "url": "https://example.test/",
        "domain": "example.test",
        "generated_at": "2026-01-02T03:04:05Z",
        "site": {},
        "pages": [{"url": "https://example.test/", "status_code": 200, "crawl_depth": 3}],
        "findings": [],
        "summary": {
            "pages_checked": 1,
            "findings_total": 0,
            "findings_by_severity": {"critical": 0, "warning": 0, "notice": 0},
            "tools_run": [],
            "tools_failed": [],
        },
    }

    def evidence(provider, metric, value, *, timezone="UTC"):
        return normalize_inline(
            [{"url": "https://example.test/", metric: value}],
            manifest={
                "format": "seohead.evidence-mapping.v1",
                "source": {
                    "provider": provider,
                    "operation": "synthetic",
                    "privacy": "supplied",
                    "timezone": timezone,
                },
                "url": {"field": "url", "kind": "absolute"},
                "row_shape": "flat",
                "dimensions": [],
                "metrics": [{"name": metric, "type": "number", "unit": "count"}],
                "period": {"start_date": "2026-01-01", "end_date": "2026-01-07"},
                "collection": {"state": "complete"},
            },
        )

    gsc = tmp_path / "gsc.json"
    ga4 = tmp_path / "ga4.json"
    gsc.write_text(json.dumps(evidence("gsc", "clicks", 0)), encoding="utf-8")
    ga4.write_text(json.dumps(evidence("ga4", "sessions", 2)), encoding="utf-8")
    package = tmp_path / "cohort-bi"

    export_bi(
        audit=audit,
        provider_joins=[gsc, ga4],
        out_dir=package,
        search_metric="clicks",
    )

    manifest = json.loads((package / "manifest.json").read_text())
    cohorts = _csv_rows(package, manifest, "cohorts")
    quadrant = next(row for row in cohorts if row["cohort_id"] == "search_visibility_vs_sessions")
    assert quadrant["membership"] == "member"
    assert quadrant["value_label"] == "zero_search_positive_sessions"
    assert quadrant["search_value"] == "0"
    assert quadrant["sessions_value"] == "2"
    assert quadrant["period_start"] == "2026-01-01"
    assert quadrant["timezone"] == "UTC"
    assert manifest["datasets"]["cohorts"]["row_count"] == 5

    no_provider = tmp_path / "cohort-no-provider"
    export_bi(audit=audit, out_dir=no_provider)
    missing = next(
        row
        for row in _csv_rows(
            no_provider, json.loads((no_provider / "manifest.json").read_text()), "cohorts"
        )
        if row["cohort_id"] == "search_visibility_vs_sessions"
    )
    assert missing["membership"] == "unclassified"
    assert missing["state"] == "not_configured"
    inlinks = next(
        row
        for row in _csv_rows(
            no_provider, json.loads((no_provider / "manifest.json").read_text()), "cohorts"
        )
        if row["cohort_id"] == "observed_unique_inlink_share"
    )
    assert inlinks["state"] == "unavailable"

    mismatched = tmp_path / "ga4-mismatched.json"
    mismatched.write_text(
        json.dumps(evidence("ga4", "sessions", 2, timezone="America/New_York")),
        encoding="utf-8",
    )
    incompatible_package = tmp_path / "cohort-incompatible"
    export_bi(
        audit=audit,
        provider_joins=[gsc, mismatched],
        out_dir=incompatible_package,
        search_metric="clicks",
    )
    incompatible = next(
        row
        for row in _csv_rows(
            incompatible_package,
            json.loads((incompatible_package / "manifest.json").read_text()),
            "cohorts",
        )
        if row["cohort_id"] == "search_visibility_vs_sessions"
    )
    assert incompatible["membership"] == "unclassified"
    assert incompatible["state"] == "incomplete"


def test_bi_export_cli_reaches_the_split_xlsx_consumer(tmp_path, capsys):
    package = tmp_path / "bi"
    workbook = tmp_path / "pages.xlsx"
    assert (
        cli.main(
            [
                "bi-export",
                "--audit",
                "examples/audit.json",
                "--out-dir",
                str(package),
                "--xlsx-out",
                str(workbook),
                "--xlsx-dataset",
                "pages",
                "--xlsx-max-rows-per-sheet",
                "2",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["xlsx"]["dataset"] == "pages"
    assert workbook.is_file()


def test_scan_hash_budget_refuses_before_writing_a_package(tmp_path, monkeypatch):
    scan_path = _crawl_with_audit(tmp_path, monkeypatch)
    with pytest.raises(BIExportError, match="scan exceeds"):
        export_bi(scan=scan_path, out_dir=tmp_path / "bounded", max_scan_bytes=1)
    assert not (tmp_path / "bounded").exists()
