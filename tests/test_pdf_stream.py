"""Explicit overview policy, complete companions and atomic failure behavior."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from seohead.reports import build_report, pdf_stream
from seohead.storage.audit_v2 import AuditV2Reader, write_audit_v2
from tests.test_scan_audit_v2 import _scan


def _fixture(tmp_path, count=40, *, summary_extra=None):
    scan = tmp_path / "scan.sqlite"
    binding = _scan(scan)
    issues = [
        {
            "id": f"issue-{index}",
            "check": "TITLE_MISSING",
            "severity": "warning",
            "target_url": f"https://example.test/{index}",
            "message": f"Saved observation {index}.",
            "occurrences_count": 1,
        }
        for index in range(count)
    ]
    pages = [
        {
            "url": f"https://example.test/{index}",
            "status_code": 200,
            "metrics": {"title": f"Page {index}"},
        }
        for index in range(count)
    ]
    header = {
        "schema_version": "2.0",
        "run": {
            "source": "https://example.test/",
            "collector": "seohead.crawl",
            "crawl_valid": True,
            "crawl_partial": True,
            "crawl_finish_reason": "Page budget reached",
            "checks_skipped": [],
        },
        "summary": {
            "totals": {"urls_crawled": count, "issues_total": count},
            "by_severity": {"critical": 0, "warning": count, "notice": 0},
            "sitemap": {"missing": []},
            "evidence_contract": {"capability_rows": []},
        },
        "issues": [],
        "pages": [],
        "groups": [],
        "suppressed_issues": [],
    }
    header["summary"].update(summary_extra or {})
    collections = {
        "/issues": issues,
        "/pages": pages,
        "/groups": [{"id": "group-1", "members": ["https://example.test/0"]}],
        "/suppressed_issues": [
            {
                **issues[0],
                "id": "suppressed-1",
                "suppression": {"rule_id": "approved", "reason": "Intentional"},
            }
        ],
        "/run/checks_skipped": [{"id": "H1_MISSING", "reason": "Input unavailable"}],
        "/summary/sitemap/missing": [{"url": "https://example.test/sitemap-only"}],
        "/summary/evidence_contract/capability_rows": [
            {"check": "TITLE_MISSING", "state": "measured", "reason": "Saved title evidence"},
            {"check": "H1_MISSING", "state": "unavailable", "reason": "Input unavailable"},
        ],
    }
    write_audit_v2(scan, header, collections, binding)
    return scan, collections


@pytest.fixture
def renderer(monkeypatch):
    pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter

    from seohead.reports.pdf_validation import _expected_text
    from tests.test_pdf_validation import _write_text_pdf

    models = []

    def render(model, path, *, lang):
        models.append(model)
        tokens = _expected_text(model)
        _write_text_pdf(
            path, [" ".join(token for token, count in tokens.items() for _ in range(count))]
        )
        reader = PdfReader(path)
        writer = PdfWriter()
        writer.append_pages_from_reader(reader)
        for index, name in enumerate(model["artifacts"]):
            writer.add_uri(
                0, pdf_stream.ARTIFACT_LINK_PREFIX + name, [0, index * 10, 50, index * 10 + 10]
            )
        with path.open("wb") as output:
            writer.write(output)
        return {"ok": True, "format": "pdf", "path": str(path), "lang": lang}

    monkeypatch.setattr("seohead.reports.pdf_output.write_pdf_report", render)
    return models


def test_overview_conserves_complete_companions_and_portable_links(tmp_path, monkeypatch, renderer):
    from pypdf import PdfReader

    scan, collections = _fixture(tmp_path)
    monkeypatch.setattr(
        AuditV2Reader, "materialize_legacy", lambda *_args, **_kwargs: pytest.fail("materialized")
    )
    target = tmp_path / "report.pdf"
    result = build_report(scan, "pdf", str(target), pdf_policy="overview-v1", lang="ru")
    assert result["ok"], result
    assert result["findings"] == result["pages"] == 40
    assert result["pdf_policy"] == "overview-v1" and result["lang"] == "ru"
    for key in ("findings", "pages"):
        assert result["projection"]["collections"][key] == {
            "source": 40,
            "displayed": 32,
            "omitted": 8,
            "first_ordinal": 0,
            "end_ordinal_exclusive": 32,
            "display_bytes": result["projection"]["collections"][key]["display_bytes"],
            "max_rows": 32,
            "max_bytes": pdf_stream.MAX_DISPLAY_BYTES,
        }
    assert all(Path(path).is_file() for path in result["outputs"])
    manifest = json.loads(Path(result["manifest"]).read_text())
    bundle = Path(result["manifest"]).parent
    for name, metadata in manifest["files"].items():
        data = (bundle / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == metadata["sha256"]
        assert len(data) == metadata["bytes"]
    complete = json.loads((bundle / "audit.json").read_text())
    assert complete["issues"] == collections["/issues"]
    assert complete["pages"] == collections["/pages"]
    assert complete["groups"] == collections["/groups"]
    assert complete["suppressed_issues"] == collections["/suppressed_issues"]
    assert complete["summary"]["sitemap"]["missing"] == collections["/summary/sitemap/missing"]
    for name in ("findings.csv", "findings.pages.csv"):
        with (bundle / name).open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.reader(source, delimiter=";"))
        assert len(rows) == 41
        assert "https://example.test/39" in str(rows[-1])
    scope_csv = (bundle / "findings.scope.csv").read_text()
    assert "suppressed-1" in scope_csv
    assert "Saved title evidence" in scope_csv and "Input unavailable" in scope_csv
    assert renderer[0]["coverage"]["groups"]["capabilities"]["projected_count"] == 2
    assert manifest["complete_machine_export"] is True
    assert manifest["source"]["collections"]["/groups"] == 1
    assert renderer[0]["summary"]["counts"]["findings"]["source_count"] == 40
    assert renderer[0]["summary"]["counts"]["findings"]["projected_count"] == 32
    assert (
        renderer[0]["coverage"]["groups"]["skipped"]["records"][0]["reason"] == "Input unavailable"
    )
    links = [
        annotation.get_object()["/A"]["/URI"]
        for annotation in PdfReader(target).pages[0]["/Annots"]
    ]
    assert set(links) == {"report.files/" + name for name in renderer[0]["artifacts"]}
    assert not (bundle / "report.pdf").exists()


def test_policy_is_explicit_and_only_applies_to_streamed_pdf(tmp_path):
    scan, _ = _fixture(tmp_path)
    target = tmp_path / "report.pdf"
    result = build_report(scan, "pdf", str(target))
    assert not result["ok"] and "explicit pdf_policy" in result["error"]
    for fmt, policy in (("csv", "overview-v1"), ("pdf", "first-ten")):
        result = build_report(scan, fmt, str(target), pdf_policy=policy)
        assert not result["ok"] and "pdf_policy" in result["error"]
    with AuditV2Reader(scan) as reader:
        result = build_report(
            reader.materialize_legacy(), "pdf", str(target), pdf_policy="overview-v1"
        )
    assert not result["ok"] and "requires a streamed audit.v2" in result["error"]
    assert not target.exists() and not target.with_suffix(".files").exists()


@pytest.mark.parametrize("failure", ["csv", "json", "budget", "disk", "pdf", "publish", "sync"])
def test_failed_overview_leaves_no_partial_package(tmp_path, monkeypatch, renderer, failure):
    scan, _ = _fixture(tmp_path)
    target = tmp_path / "report.pdf"
    if failure == "csv":

        def fail(document, path, **kwargs):
            path.write_text("partial companion")
            raise OSError("injected CSV failure")

        monkeypatch.setattr("seohead.reports.csvfile.write", fail)
    elif failure == "json":

        def broken_json(_reader):
            yield '{"partial":'
            raise OSError("injected JSON export failure")

        monkeypatch.setattr(AuditV2Reader, "document_chunks", broken_json)
    elif failure == "budget":
        monkeypatch.setattr(pdf_stream, "MAX_OUTPUT_BYTES", 10)
    elif failure == "disk":
        from types import SimpleNamespace

        monkeypatch.setattr(pdf_stream.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    elif failure == "pdf":
        monkeypatch.setattr(
            "seohead.reports.pdf_output.write_pdf_report",
            lambda *_a, **_k: {"ok": False, "error": "renderer unavailable"},
        )
    elif failure == "sync":
        monkeypatch.setattr(
            "seohead.core.filesystem.fsync_directory",
            lambda *_a: (_ for _ in ()).throw(OSError("injected fsync failure")),
        )
    else:
        monkeypatch.setattr(
            pdf_stream.os,
            "link",
            lambda *_a: (_ for _ in ()).throw(OSError("injected publish failure")),
        )
    result = build_report(scan, "pdf", str(target), pdf_policy="overview-v1")
    assert not result["ok"], result
    assert not target.exists() and not target.with_suffix(".files").exists()
    assert not list(tmp_path.glob(".seohead-overview-*"))


def test_overview_refuses_existing_output_without_changing_it(tmp_path, renderer):
    scan, _ = _fixture(tmp_path)
    target = tmp_path / "report.pdf"
    target.write_bytes(b"prior report")
    result = build_report(scan, "pdf", str(target), pdf_policy="overview-v1")
    assert not result["ok"] and target.read_bytes() == b"prior report"
    assert not renderer


def test_model_prefix_byte_bound_is_disclosed_without_losing_full_counts(tmp_path, monkeypatch):
    scan, _ = _fixture(tmp_path)
    monkeypatch.setattr(pdf_stream, "MAX_DISPLAY_BYTES", 1)
    with AuditV2Reader(scan) as reader:
        model = pdf_stream.build_overview_model(reader)
    assert model["findings"] == model["pages"] == []
    for counts in model["projection"]["collections"].values():
        assert counts["source"] == counts["omitted"] == 40
        assert counts["displayed"] == 0
    assert model["omissions"]


def test_overview_banner_precedes_full_source_metric_cards(tmp_path):
    from seohead.reports.audit_pdf import render_audit_pdf_html

    scan, _ = _fixture(tmp_path)
    with AuditV2Reader(scan) as reader:
        model = pdf_stream.build_overview_model(reader)
    model["artifacts"] = ["audit.json", "manifest.json"]
    html = render_audit_pdf_html(model)
    assert html.index("PDF overview") < html.index('<div class="metric-grid">')
    assert "not a representative sample" in html
    assert "Full source" in html and "Omitted from PDF" in html
    assert "32 rows; 2097152 bytes" in html
    assert "<strong>40</strong>" in html
    assert pdf_stream.ARTIFACT_LINK_PREFIX + "audit.json" in html


def test_overview_refuses_existing_companions_and_preserves_them(tmp_path, renderer):
    scan, _ = _fixture(tmp_path)
    target = tmp_path / "report.pdf"
    bundle = target.with_suffix(".files")
    bundle.mkdir()
    prior = bundle / "audit.json"
    prior.write_text("prior evidence")
    result = build_report(scan, "pdf", str(target), pdf_policy="overview-v1")
    assert not result["ok"] and prior.read_text() == "prior evidence"
    assert not target.exists() and not renderer


def test_overview_metadata_budget_is_explicit(tmp_path, monkeypatch):
    scan, _ = _fixture(tmp_path)
    monkeypatch.setattr(pdf_stream, "MAX_METADATA_BYTES", 1)
    result = build_report(scan, "pdf", str(tmp_path / "report.pdf"), pdf_policy="overview-v1")
    assert not result["ok"] and "metadata exceeds" in result["error"]
    assert not (tmp_path / "report.pdf").exists()


def test_relative_companion_links_escape_output_names(tmp_path, renderer):
    from pypdf import PdfReader

    scan, _ = _fixture(tmp_path)
    target = tmp_path / "report #1.pdf"
    result = build_report(scan, "pdf", str(target), pdf_policy="overview-v1")
    assert result["ok"], result
    links = [
        annotation.get_object()["/A"]["/URI"]
        for annotation in PdfReader(target).pages[0]["/Annots"]
    ]
    assert "report%20%231.files/audit.json" in links


def test_large_nested_summary_stays_complete_without_consuming_pdf_metadata_budget(
    tmp_path, monkeypatch, renderer
):
    histogram = {str(index): index for index in range(2000)}
    nested = {"click_depth": {"histogram": histogram, "max": 1999}}
    scan, _ = _fixture(tmp_path, summary_extra={"internal_linking": nested})
    monkeypatch.setattr(pdf_stream, "MAX_METADATA_BYTES", 4096)
    result = build_report(scan, "pdf", str(tmp_path / "report.pdf"), pdf_policy="overview-v1")
    assert result["ok"], result
    document = json.loads((Path(result["manifest"]).parent / "audit.json").read_text())
    assert document["summary"]["internal_linking"] == nested
    assert renderer[0]["summary"]["source"]["internal_linking"] == {
        "state": "retained_in_companion",
        "reference": "audit.json#/summary/internal_linking",
    }


def test_portable_links_preserve_internal_destination_closure(tmp_path):
    pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        TextStringObject,
    )

    from tests.test_pdf_validation import _write_text_pdf

    path = tmp_path / "navigation.pdf"
    _write_text_pdf(path, ["Navigation evidence"])
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(path))
    writer.add_named_destination("evidence", 0)
    writer.add_outline_item("Evidence", 0)
    writer.add_annotation(
        0,
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject([FloatObject(value) for value in (0, 0, 50, 10)]),
                NameObject("/Dest"): TextStringObject("evidence"),
            }
        ),
    )
    writer.add_uri(0, pdf_stream.ARTIFACT_LINK_PREFIX + "audit.json", [0, 20, 50, 30])
    with path.open("wb") as output:
        writer.write(output)
    pdf_stream._portable_links(path, "report.files", ["audit.json"])
    reader = PdfReader(path)
    assert reader.get_destination_page_number(reader.named_destinations["evidence"]) == 0
    assert reader.get_destination_page_number(reader.outline[0]) == 0
    annotations = [item.get_object() for item in reader.pages[0]["/Annots"]]
    assert any(item.get("/Dest") == "evidence" for item in annotations)
    assert any(item.get("/A", {}).get("/URI") == "report.files/audit.json" for item in annotations)
