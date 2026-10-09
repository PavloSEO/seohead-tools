"""Evidence-bound hreflang relations from synthetic retained native scans (#825)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from seohead import __version__
from seohead.crawl.settings import load
from seohead.crawl.sqlite_adapter import crawl_to_scan
from seohead.sf.core.audit import run_audit
from seohead.storage import read_audit
from tests.test_scan_reanalysis_integration import _runtime_versions, _save_native_audit

BASE = "https://example.test/"


class _Response:
    def __init__(self, body: str, *, status: int = 200, headers: dict | None = None):
        self.text = body
        self.content = body.encode()
        self.status_code = status
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


def _html(*declarations: tuple[str, str], body: str = "", extra_head: str = "") -> str:
    links = "".join(
        f'<link rel="alternate" hreflang="{lang}" href="{target}">' for lang, target in declarations
    )
    return f"<html><head>{extra_head}{links}</head><body>{body}</body></html>"


def _capture(
    tmp_path: Path,
    pages: dict[str, _Response],
    *,
    max_urls: int = 2,
    max_body_bytes: int = 5 * 1024 * 1024,
) -> dict:
    settings = load(
        overrides={
            "speed.min_delay_seconds": 0,
            "limits.max_urls": max_urls,
            "limits.max_depth": 2,
            "resources.fetch": False,
            "storage.body_mode": "captured_entity_bytes",
            "storage.max_body_bytes": max_body_bytes,
        }
    )

    def fetcher(url: str) -> _Response:
        if url == BASE + "robots.txt":
            return _Response("User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
        assert url in pages, f"unexpected network target: {url}"
        return pages[url]

    scan = tmp_path / "scan.sqlite"
    crawl_to_scan(
        BASE,
        scan_out=str(scan),
        settings=settings,
        producer_version=__version__,
        producer_revision="a" * 40,
        runtime_versions=_runtime_versions(),
        fetcher=fetcher,
        sleeper=lambda _seconds: None,
    )
    _save_native_audit(scan, settings)
    return read_audit(scan)


def _relations(audit: dict) -> list[dict]:
    return audit["summary"]["saved_corpus_derivations"]["internationalization"]["declarations"]


def _issues(audit: dict, check: str) -> list[dict]:
    return [issue for issue in audit["issues"] if issue["check"] == check]


def _skipped(audit: dict) -> set[str]:
    return {item["id"] for item in audit["run"]["checks_skipped"]}


def test_complete_reciprocal_group_is_measured_without_a_false_finding(tmp_path):
    group = (("en", "/"), ("fr-CA", "/fr/"), ("x-default", "/"))
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(*group, body='<a href="/fr/">French</a>')),
            BASE + "fr/": _Response(_html(*group)),
        },
    )
    international = audit["summary"]["saved_corpus_derivations"]["internationalization"]
    assert international["coverage"]["state"] == "complete"
    assert {item["reciprocity"]["state"] for item in _relations(audit)} == {"present"}
    assert not _issues(audit, "HREFLANG_MISSING_RETURN_LINK")
    assert not _issues(audit, "HREFLANG_NOINDEX_TARGET")
    assert "HREFLANG_MISSING_RETURN_LINK" not in _skipped(audit)


def test_complete_target_without_any_declaration_proves_missing_return(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(
                _html(("en", "/"), ("fr-CA", "/fr/"), body='<a href="/fr/">French</a>')
            ),
            BASE + "fr/": _Response(_html()),
        },
    )
    missing = _issues(audit, "HREFLANG_MISSING_RETURN_LINK")
    assert len(missing) == 1 and missing[0]["target_url"] == BASE + "fr/"
    relation = next(item for item in _relations(audit) if item["target"] == BASE + "fr/")
    assert relation["reciprocity"]["state"] == "missing"
    assert relation["lang"] == "fr-CA" and relation["target_identity"] == BASE + "fr"
    assert relation["raw_href"] == "/fr/"


def test_noindex_target_and_conflicting_duplicate_labels_keep_context(tmp_path):
    root = _html(
        ("en", "/"),
        ("fr-CA", "/fr/"),
        ("fr-CA", "/fr/"),
        ("fr-CA", "/other/"),
        body='<a href="/fr/">French</a>',
    )
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(root),
            BASE + "fr/": _Response(
                _html(
                    ("fr-CA", "/fr/"),
                    body="French page",
                    extra_head='<meta name="robots" content="noindex">',
                )
            ),
        },
    )
    noindex = _issues(audit, "HREFLANG_NOINDEX_TARGET")
    assert any(issue["target_url"] == BASE for issue in noindex)
    missing = _issues(audit, "HREFLANG_MISSING_RETURN_LINK")
    relation = next(
        entry
        for issue in missing
        for entry in issue["details"]["relations"]
        if entry["target"] == BASE + "fr/"
    )
    assert relation["hreflang"] == "fr-CA"
    assert relation["label_context"] == {
        "targets_for_label": [BASE + "fr", BASE + "other"],
        "labels_for_target": ["fr-CA"],
        "duplicate_pair_count": 2,
    }
    unknown = next(item for item in _relations(audit) if item["target"] == BASE + "other/")
    assert unknown["reciprocity"]["state"] == "unmeasured"


def test_reciprocal_urls_keep_conflicting_labels_and_self_reference_checks(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(("en", "/"), ("fr", "/fr/"), body='<a href="/fr/">French</a>')),
            BASE + "fr/": _Response(_html(("de", "/fr/"), ("en", "/"))),
        },
    )
    assert not _issues(audit, "HREFLANG_MISSING_RETURN_LINK")
    assert any(
        issue["target_url"] == BASE
        for issue in _issues(audit, "HREFLANG_INCONSISTENT_CONFIRMATION")
    )
    assert not _issues(audit, "HREFLANG_MISSING_SELF_REFERENCE")


def test_unfetched_and_body_unavailable_targets_never_become_missing_returns(tmp_path):
    source = _html(("en", "/"), ("fr", "/fr/"), body='<a href="/fr/">French</a>')
    partial = _capture(tmp_path / "partial", {BASE: _Response(source)}, max_urls=1)
    target = next(item for item in _relations(partial) if item["target"] == BASE + "fr/")
    assert partial["run"]["crawl_partial"]
    assert target["target_observation"]["state"] == "unmeasured"
    assert target["reciprocity"]["state"] == "unmeasured"
    assert not _issues(partial, "HREFLANG_MISSING_RETURN_LINK")
    assert "HREFLANG_MISSING_RETURN_LINK" in _skipped(partial)

    missing_body = _capture(
        tmp_path / "omitted",
        {
            BASE: _Response(source),
            BASE + "fr/": _Response(_html(("fr", "/fr/"), body="x" * 2_000)),
        },
        max_body_bytes=500,
    )
    target = next(item for item in _relations(missing_body) if item["target"] == BASE + "fr/")
    assert target["target_observation"]["body_state"] == "omitted"
    assert target["reciprocity"]["state"] == "unmeasured"
    assert not _issues(missing_body, "HREFLANG_MISSING_RETURN_LINK")
    assert not any(
        issue["target_url"] == BASE + "fr/"
        for issue in _issues(missing_body, "HREFLANG_MISSING_XDEFAULT")
    )
    assert "HREFLANG_MISSING_RETURN_LINK" in _skipped(missing_body)


def test_observed_redirect_is_broken_target_not_a_missing_return(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(("en", "/"), ("fr", "/fr/"), body='<a href="/fr/">French</a>')),
            BASE + "fr/": _Response(
                "", status=301, headers={"content-type": "text/html", "location": "/new/"}
            ),
        },
    )
    broken = _issues(audit, "HREFLANG_BROKEN_TARGET")
    assert any(issue["target_url"] == BASE for issue in broken)
    assert not _issues(audit, "HREFLANG_MISSING_RETURN_LINK")


@pytest.mark.parametrize("indexability_reason", ["noindex", "None"])
def test_export_noindex_evidence_reaches_the_shared_audit(tmp_path, indexability_reason):
    exports = tmp_path / "exports"
    exports.mkdir()
    with (exports / "internal_all.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Address",
                "Content Type",
                "Status Code",
                "Status",
                "Indexability",
                "Indexability Status",
                "Meta Robots 1",
            ]
        )
        writer.writerow([BASE, "text/html", 200, "OK", "Indexable", "", ""])
        writer.writerow(
            [
                BASE + "fr/",
                "text/html",
                200,
                "OK",
                "Non-Indexable",
                indexability_reason,
                "none" if indexability_reason == "None" else "noindex",
            ]
        )
    with (exports / "all_hreflang.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Source", "Destination", "Hreflang"])
        writer.writerow([BASE, BASE + "fr/", "fr"])
    result = run_audit(input_mode="parse-exports", exports_dir=str(exports), log=lambda _: None)
    assert any(issue.check == "HREFLANG_NOINDEX_TARGET" for issue in result.issues)


def test_export_redirect_target_is_unmeasured_for_reciprocity(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    with (exports / "internal_all.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        writer.writerow([BASE, "text/html", 200, "OK", "Indexable"])
        writer.writerow([BASE + "fr/", "text/html", 301, "Moved", "Non-Indexable"])
    with (exports / "all_hreflang.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Source", "Destination", "Hreflang"])
        writer.writerow([BASE, BASE + "fr/", "fr"])
    result = run_audit(input_mode="parse-exports", exports_dir=str(exports), log=lambda _: None)
    assert not [issue for issue in result.issues if issue.check == "HREFLANG_MISSING_RETURN_LINK"]
    assert "HREFLANG_MISSING_RETURN_LINK" in {item.id for item in result.skipped}
    assert any(issue.check == "HREFLANG_BROKEN_TARGET" for issue in result.issues)


def test_report_builder_keeps_the_native_graph_finding(tmp_path):
    from seohead.cli import main
    from seohead.mcp.handlers import report_build
    from seohead.mcp.mcp_server import build_server

    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(("en", "/"), ("fr", "/fr/"), body='<a href="/fr/">French</a>')),
            BASE + "fr/": _Response(_html()),
        },
    )
    built = report_build(
        str(tmp_path / "scan.sqlite"), fmt="json", out=str(tmp_path / "report.json")
    )
    assert built["ok"]
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert _issues(report, "HREFLANG_MISSING_RETURN_LINK") == _issues(
        audit, "HREFLANG_MISSING_RETURN_LINK"
    )
    cli_out = tmp_path / "cli-report.json"
    assert (
        main(
            [
                "report-build",
                "--audit",
                str(tmp_path / "scan.sqlite"),
                "--format",
                "json",
                "--out",
                str(cli_out),
            ]
        )
        == 0
    )
    tool = build_server()._tool_manager.get_tool("seo_report_build")
    mcp_out = tmp_path / "mcp-report.json"
    assert tool.fn(audit=str(tmp_path / "scan.sqlite"), fmt="json", out=str(mcp_out))["ok"]
    for path in (cli_out, mcp_out):
        assert _issues(
            json.loads(path.read_text(encoding="utf-8")), "HREFLANG_MISSING_RETURN_LINK"
        ) == _issues(audit, "HREFLANG_MISSING_RETURN_LINK")
