"""Synthetic offline contract tests for third-party crawl CSV import."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from seohead.crawl import external_import
from seohead.crawl.external_import import ExternalCrawlImportError, import_third_party_crawl

FIXTURES = Path(__file__).parent / "fixtures_third_party_crawl"


def test_complete_bundle_keeps_foreign_identity_and_counts_each_dataset():
    result = import_third_party_crawl(FIXTURES / "full" / "manifest.json")

    assert result["schema_version"] == "third_party_crawl.v1"
    assert result["evidence_kind"] == "third_party_crawl_export"
    assert result["compatibility"]["scan_v1"] is False
    assert result["compatibility"]["sf_audit_json"] is False
    assert result["source"]["product"] == "FixtureSpider"
    assert result["crawl_completeness"] == {
        "state": "source_claimed_complete",
        "verified_by_importer": False,
        "reason": "synthetic fixture source claim",
    }
    assert result["summary"]["rows_by_dataset"] == {
        "pages": 3,
        "links": 2,
        "statuses": 3,
        "redirects": 2,
    }
    assert result["import_coverage"]["state"] == "partial"
    assert not Path(result["provenance"]["manifest_file"]).is_absolute()
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


@pytest.mark.parametrize("invalid_path", (None, "", 17))
def test_invalid_manifest_path_has_a_contract_error(invalid_path):
    with pytest.raises(ExternalCrawlImportError, match="manifest_path"):
        import_third_party_crawl(invalid_path)


def test_url_normalization_preserves_raw_urls_and_reports_collisions():
    result = import_third_party_crawl(FIXTURES / "full" / "manifest.json")
    pages = result["datasets"]["pages"]

    assert pages["records"][1]["url"] == "https://EXAMPLE.test:443/products/"
    assert pages["records"][1]["url_key"] == "https://example.test/products"
    assert pages["records"][2]["url"] == "https://example.test/products#top"
    assert pages["records"][2]["url_key"] == pages["records"][1]["url_key"]
    assert pages["duplicate_summary"] == {"duplicate_groups": 1, "duplicate_rows": 1}
    assert pages["row_count"] == 3


def test_link_status_and_redirect_duplicates_are_counted_but_never_removed():
    result = import_third_party_crawl(FIXTURES / "full" / "manifest.json")

    for name, rows in (("links", 2), ("statuses", 3), ("redirects", 2)):
        dataset = result["datasets"][name]
        assert dataset["row_count"] == rows
        assert len(dataset["records"]) == rows
        assert dataset["duplicate_summary"] == {"duplicate_groups": 1, "duplicate_rows": 1}


def test_complete_field_coverage_means_all_rows_in_export_have_valid_values():
    result = import_third_party_crawl(FIXTURES / "full" / "manifest.json")
    coverage = result["datasets"]["pages"]["field_coverage"]["status_code"]

    assert coverage["state"] == "complete"
    assert coverage["scope"].startswith("values in the supplied CSV rows only")
    assert coverage["rows"] == coverage["present"] == 3
    assert coverage["missing"] == coverage["invalid"] == 0


def test_partial_bundle_preserves_missing_columns_bad_values_and_dataset_gaps():
    result = import_third_party_crawl(FIXTURES / "partial" / "manifest.json")
    pages = result["datasets"]["pages"]

    assert result["crawl_completeness"]["state"] == "source_claimed_partial"
    assert result["import_coverage"]["state"] == "partial"
    assert pages["records"][0]["url"] == "/relative-path"
    assert pages["records"][0]["url_key"] is None
    assert pages["records"][0]["field_errors"][0]["field"] == "url"
    assert pages["records"][1]["status_code"] is None
    assert pages["records"][1]["unparsed_values"]["status_code"] == "999"
    assert pages["field_coverage"]["title"]["state"] == "unavailable"
    assert pages["field_coverage"]["canonical_url"]["state"] == "unavailable"
    assert pages["field_coverage"]["status_code"]["state"] == "partial"

    statuses = result["datasets"]["statuses"]
    assert statuses["state"] == "unavailable"
    assert statuses["field_coverage"]["status_code"]["state"] == "not_exported"
    assert "did not export" in statuses["reason"]

    links = result["datasets"]["links"]
    assert links["row_count"] == 1
    assert links["records"][0]["destination_url"] is None
    assert links["field_coverage"]["destination_url"]["state"] == "partial"

    redirects = result["datasets"]["redirects"]
    assert redirects["field_coverage"]["hop"]["state"] == "unavailable"


def test_importer_never_returns_embedded_url_credentials(tmp_path):
    manifest_path = _copy_bundle(tmp_path)
    root = manifest_path.parent
    pages = (root / "pages.csv").read_text()
    (root / "pages.csv").write_text(
        pages.replace("https://example.test/", "https://user:secret@example.test/", 1)
    )
    result = import_third_party_crawl(manifest_path)
    record = result["datasets"]["pages"]["records"][0]
    assert record["url"] is None
    assert record["url_key"] is None
    assert "secret" not in json.dumps(result)


def _copy_bundle(tmp_path: Path, *, partial: bool = False, name: str = "bundle") -> Path:
    source = FIXTURES / ("partial" if partial else "full")
    target = tmp_path / name
    shutil.copytree(source, target)
    return target / "manifest.json"


def test_unknown_schema_dataset_and_field_mappings_are_rejected(tmp_path):
    manifest = _copy_bundle(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["schema_version"] = "future.v9"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ExternalCrawlImportError, match="unsupported manifest schema_version"):
        import_third_party_crawl(manifest)

    raw["schema_version"] = external_import.MANIFEST_VERSION
    raw["datasets"]["unrecognized"] = {"file": "pages.csv", "columns": {}}
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ExternalCrawlImportError, match="unknown dataset"):
        import_third_party_crawl(manifest)

    del raw["datasets"]["unrecognized"]
    raw["datasets"]["pages"]["columns"]["made_up"] = "Title"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ExternalCrawlImportError, match="unknown field"):
        import_third_party_crawl(manifest)

    raw["datasets"]["pages"]["columns"].pop("made_up")
    raw["source"]["crawl_state"] = []
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ExternalCrawlImportError, match="crawl_state"):
        import_third_party_crawl(manifest)


def test_duplicate_manifest_keys_and_source_headers_are_rejected(tmp_path):
    manifest = _copy_bundle(tmp_path, name="headers")
    manifest.write_text(
        '{"schema_version":"third_party_crawl_manifest.v1",'
        '"schema_version":"third_party_crawl_manifest.v1"}'
    )
    with pytest.raises(ExternalCrawlImportError, match="duplicate JSON key"):
        import_third_party_crawl(manifest)

    manifest = _copy_bundle(tmp_path)
    pages = manifest.parent / "pages.csv"
    pages.write_text("Address,Address\nhttps://example.test/,https://example.test/\n")
    with pytest.raises(ExternalCrawlImportError, match="duplicate headers"):
        import_third_party_crawl(manifest)


def test_manifest_rejects_unpaired_unicode_surrogates(tmp_path):
    manifest = _copy_bundle(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["source"]["product"] = "fixture-\ud800"
    manifest.write_text(json.dumps(raw))

    with pytest.raises(ExternalCrawlImportError, match="Unicode surrogate"):
        import_third_party_crawl(manifest)


@pytest.mark.parametrize("bad_path", ("../outside.csv", "/tmp/outside.csv", "C:\\outside.csv"))
def test_manifest_rejects_absolute_and_traversal_dataset_paths(tmp_path, bad_path):
    manifest = _copy_bundle(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["datasets"]["pages"]["file"] = bad_path
    manifest.write_text(json.dumps(raw))

    with pytest.raises(ExternalCrawlImportError, match="relative path inside"):
        import_third_party_crawl(manifest)


def test_manifest_rejects_symlinked_dataset_file(tmp_path):
    manifest = _copy_bundle(tmp_path)
    target = manifest.parent / "linked-pages.csv"
    target.symlink_to(manifest.parent / "pages.csv")
    raw = json.loads(manifest.read_text())
    raw["datasets"]["pages"]["file"] = "linked-pages.csv"
    manifest.write_text(json.dumps(raw))

    with pytest.raises(ExternalCrawlImportError, match="symlink"):
        import_third_party_crawl(manifest)


def test_one_csv_cannot_be_reused_for_multiple_dataset_roles(tmp_path):
    manifest = _copy_bundle(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["datasets"]["statuses"]["file"] = "pages.csv"
    raw["datasets"]["statuses"]["columns"] = {"url": "Address"}
    manifest.write_text(json.dumps(raw))

    with pytest.raises(ExternalCrawlImportError, match="assigned to more than one dataset"):
        import_third_party_crawl(manifest)


def test_oversized_source_file_is_refused_before_parsing(tmp_path, monkeypatch):
    manifest = _copy_bundle(tmp_path)
    monkeypatch.setattr(external_import, "MAX_TOTAL_CSV_BYTES", 8)

    with pytest.raises(ExternalCrawlImportError, match="byte import budget"):
        import_third_party_crawl(manifest)


def test_row_limit_is_explicit_and_does_not_silently_truncate(tmp_path, monkeypatch):
    manifest = _copy_bundle(tmp_path)
    monkeypatch.setattr(external_import, "MAX_ROWS_PER_DATASET", 1)

    with pytest.raises(ExternalCrawlImportError, match="row dataset limit"):
        import_third_party_crawl(manifest)


def test_input_files_remain_byte_identical_after_import():
    root = FIXTURES / "full"
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    import_third_party_crawl(root / "manifest.json")

    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


def test_handler_cli_and_mcp_contract_share_the_same_core(tmp_path, capsys):
    from seohead import cli
    from seohead.servers import handlers

    manifest = FIXTURES / "full" / "manifest.json"
    direct = handlers.crawl_import(str(manifest))
    assert direct["ok"] is True
    assert direct["schema_version"] == "third_party_crawl.v1"
    assert handlers.crawl_import() == {
        "ok": False,
        "error": "manifest_path must be a non-empty local path",
    }

    exit_code = cli.main(["crawl-import", "--manifest", str(manifest)])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["schema_version"] == direct["schema_version"]
    assert output["summary"] == direct["summary"]

    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    exit_code = cli.main(["crawl-import", "--manifest", str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out)["ok"] is False
    assert "manifest fields" in json.loads(captured.out)["error"]


def test_cli_input_json_form_is_also_supported(capsys):
    from seohead import cli

    manifest = str(FIXTURES / "partial" / "manifest.json")
    exit_code = cli.main(["crawl-import", "--input", json.dumps({"manifest_path": manifest})])

    assert exit_code == 0
    assert (
        json.loads(capsys.readouterr().out)["crawl_completeness"]["state"]
        == "source_claimed_partial"
    )


def test_mcp_tool_uses_the_shared_handler_and_declares_read_only():
    pytest.importorskip("mcp")
    import asyncio

    from seohead.servers.mcp_server import build_server

    tool = next(
        item
        for item in build_server()._tool_manager.list_tools()
        if item.name == "seo_crawl_import"
    )
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is False
    result = asyncio.run(tool.run({"manifest_path": str(FIXTURES / "full" / "manifest.json")}))
    assert result["schema_version"] == "third_party_crawl.v1"
