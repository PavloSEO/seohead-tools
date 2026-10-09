"""Offline graph checks for canonical destinations and homepage patterns (#824)."""

from __future__ import annotations

import csv
import json
import re
import sys
import types

from seohead.sf.config import load_config
from seohead.sf.core.aggregate import aggregate
from seohead.sf.core.audit import run_audit
from seohead.sf.core.context import AuditContext
from seohead.sf.core.loader import load_exports
from seohead.sf.core.rules import run_rules
from seohead.sf.tasks import build_tasks

COLS = [
    "Address",
    "Content Type",
    "Status Code",
    "Status",
    "Indexability",
    "Indexability Status",
    "Title 1",
    "H1-1",
    "Canonical Link Element 1",
    "Hash",
]
BASE = "https://example.test"
HOME = f"{BASE}/"


def _row(
    url: str,
    *,
    status: int | None = 200,
    indexability: str = "Indexable",
    canonical: str = "",
    title: str = "A distinct synthetic page title",
    h1: str = "A distinct synthetic page heading",
    digest: str = "hash",
) -> list[str]:
    status_text = "" if status is None else ("OK" if status < 300 else "Error")
    return [
        url,
        "text/html",
        "" if status is None else str(status),
        status_text,
        indexability,
        "" if indexability == "Indexable" else "Non-Indexable",
        title,
        h1,
        canonical,
        digest,
    ]


def _write_exports(tmp_path, rows: list[list[str]]):
    exports = tmp_path / "exports"
    exports.mkdir(parents=True)
    with (exports / "internal_all.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLS)
        writer.writerows(rows)
    return exports


def _audit(tmp_path, rows):
    exports = _write_exports(tmp_path, rows)
    return run_audit(input_mode="parse-exports", exports_dir=str(exports), log=lambda _line: None)


def _homepage_rows():
    return [
        _row(HOME, title="Example home", h1="Example home heading", digest="home"),
        _row(
            f"{BASE}/about/team",
            canonical=HOME,
            title="About the team",
            h1="Meet our team",
            digest="about",
        ),
        _row(
            f"{BASE}/catalog/pumps",
            canonical=HOME,
            title="Pump catalogue",
            h1="Browse pumps",
            digest="catalog",
        ),
        _row(
            f"{BASE}/news/release",
            canonical=HOME,
            title="Product release news",
            h1="Release announcement",
            digest="news",
        ),
    ]


def test_known_canonical_4xx_and_5xx_are_distinct_from_missing_targets(tmp_path):
    missing = f"{BASE}/missing"
    failing = f"{BASE}/unavailable"
    absent = f"{BASE}/not-captured"
    result = _audit(
        tmp_path,
        [
            _row(f"{BASE}/page-404", canonical=missing),
            _row(missing, status=404, indexability="Non-Indexable"),
            _row(f"{BASE}/page-500", canonical=failing),
            _row(failing, status=503, indexability="Non-Indexable"),
            _row(f"{BASE}/page-unknown", canonical=absent),
        ],
    )

    errors = [issue for issue in result.issues if issue.check == "CANONICAL_TARGET_ERROR"]
    assert {issue.target_url for issue in errors} == {f"{BASE}/page-404", f"{BASE}/page-500"}
    by_source = {issue.target_url: issue for issue in errors}
    assert (
        by_source[f"{BASE}/page-404"].details["canonical_target_responses"][0]["status_code"] == 404
    )
    assert (
        by_source[f"{BASE}/page-500"].details["canonical_target_responses"][0]["status_code"] == 503
    )
    assert all(issue.details["status_coverage"] == "partial" for issue in errors)
    assert all(issue.details["unclassified_target_count"] == 1 for issue in errors)
    assert not {
        issue.target_url for issue in result.issues if issue.check == "CANONICAL_NON_INDEXABLE"
    } & {
        f"{BASE}/page-404",
        f"{BASE}/page-500",
    }


def test_missing_or_unknown_target_status_is_named_unavailable_not_an_http_error(tmp_path):
    absent = f"{BASE}/not-captured"
    result = _audit(
        tmp_path,
        [_row(f"{BASE}/source", canonical=absent)],
    )

    assert not [issue for issue in result.issues if issue.check == "CANONICAL_TARGET_ERROR"]
    skipped = {item.id: item.reason for item in result.skipped}
    assert "CANONICAL_TARGET_ERROR" in skipped
    assert "not captured" in skipped["CANONICAL_TARGET_ERROR"]

    unknown_status = _audit(
        tmp_path / "unknown-status",
        [
            _row(f"{BASE}/source", canonical=absent),
            _row(absent, status=None, indexability="Non-Indexable"),
        ],
    )
    assert not [issue for issue in unknown_status.issues if issue.check == "CANONICAL_TARGET_ERROR"]
    assert "CANONICAL_TARGET_ERROR" in {item.id for item in unknown_status.skipped}
    # The independent indexability evidence may still support this older check;
    # it does not invent an HTTP status.
    non_indexable = next(
        issue for issue in unknown_status.issues if issue.check == "CANONICAL_NON_INDEXABLE"
    )
    assert non_indexable.details["canonical_target_evidence"][0]["status_code"] is None


def test_redirect_and_successful_normalized_variant_keep_existing_status_semantics(tmp_path):
    target = f"{BASE}/target"
    result = _audit(
        tmp_path,
        [
            _row(f"{BASE}/to-redirect", canonical=target),
            _row(target, status=301, indexability="Non-Indexable"),
            _row(f"{BASE}/to-success", canonical=f"{BASE}/alias"),
            _row(f"{BASE}/alias", status=404, indexability="Non-Indexable"),
            _row(f"{BASE}/alias/", status=200),
        ],
    )

    fired = {(issue.check, issue.target_url) for issue in result.issues}
    assert ("CANONICAL_TO_REDIRECT", f"{BASE}/to-redirect") in fired
    assert ("CANONICAL_TARGET_ERROR", f"{BASE}/to-redirect") not in fired
    assert ("CANONICAL_TARGET_ERROR", f"{BASE}/to-success") not in fired


def test_homepage_group_requires_cross_section_distinct_page_evidence_and_carries_urls(tmp_path):
    result = _audit(tmp_path, _homepage_rows())
    findings = [issue for issue in result.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"]
    finding = findings[0]

    assert len(findings) == 3
    assert finding.details["canonical_target_status_code"] == 200
    assert finding.details["canonical_target_indexability"] == "Indexable"
    assert finding.details["source_sections"] == ["about", "catalog", "news"]
    expected_sources = {
        f"{BASE}/about/team",
        f"{BASE}/catalog/pumps",
        f"{BASE}/news/release",
    }
    assert {issue.target_url for issue in findings} == expected_sources
    assert all(
        issue.status_code == 200 and issue.locations[0]["canonical_target_url"] == HOME
        for issue in findings
    )

    # The common audit result serializes the evidence and task generation keeps
    # the exact URLs and status details for a downstream fix list.
    document = result.to_json()
    task = next(
        task
        for task in build_tasks(document, None)["tasks"]
        if task["check"] == "CANONICAL_HOMEPAGE_GROUP"
    )
    assert set(task["urls"]) == expected_sources
    assert len(task["reproductions"]) == len(expected_sources)
    assert all(
        any(url in reproduction for reproduction in task["reproductions"])
        for url in expected_sources
    )


def test_self_home_pagination_and_under_threshold_pages_do_not_form_homepage_group(tmp_path):
    rows = [
        _row(HOME, canonical=HOME, title="Home", h1="Home heading"),
        _row(f"{BASE}/about/team", canonical=HOME, title="Team", h1="Our team"),
        _row(f"{BASE}/catalog/pumps", canonical=HOME, title="Pumps", h1="Pump catalogue"),
        _row(f"{BASE}/news/page/2/", canonical=HOME, title="News page 2", h1="News page 2"),
        _row(f"{BASE}/blog?page=3", canonical=HOME, title="Blog page 3", h1="Blog page 3"),
    ]
    result = _audit(tmp_path, rows)
    assert not [issue for issue in result.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"]

    # Three pages with unique titles/headings but all inside one section are not
    # evidence of unrelated site sections.
    same_section = _audit(
        tmp_path / "same-section",
        [
            _row(HOME, title="Home", h1="Home heading"),
            _row(f"{BASE}/blog/a", canonical=HOME, title="Blog A", h1="Heading A"),
            _row(f"{BASE}/blog/b", canonical=HOME, title="Blog B", h1="Heading B"),
            _row(f"{BASE}/blog/c", canonical=HOME, title="Blog C", h1="Heading C"),
        ],
    )
    assert not [issue for issue in same_section.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"]


def test_configured_727_patterns_are_exempted_through_the_shared_matcher(tmp_path, monkeypatch):
    matcher_module = types.ModuleType("seohead.core.canonical_policy")

    def matching_rule(policy, category, url):
        return next(
            (rule for rule in policy.get(category, []) if re.search(rule["pattern"], url)),
            None,
        )

    matcher_module.matching_canonical_rule = matching_rule
    monkeypatch.setitem(sys.modules, "seohead.core.canonical_policy", matcher_module)
    exports = load_exports(str(_write_exports(tmp_path, _homepage_rows())))
    config = load_config(None)
    config["canonical_policy"] = {
        "pagination": [],
        "filters": [{"pattern": r"/catalog/pumps$", "policy": "landing", "target": HOME}],
    }
    ctx = AuditContext(exports, config)
    ctx.skip_unsupported(set(exports.frames))
    run_rules(ctx)

    assert not [issue for issue in ctx.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"]


def test_cli_and_mcp_emit_the_same_canonical_graph_findings(tmp_path):
    from seohead.cli import main as cli_main
    from seohead.mcp.sf_mcp import _do_run

    exports = _write_exports(tmp_path, _homepage_rows())
    cli_out = tmp_path / "cli-report"
    mcp_out = tmp_path / "mcp-report"

    assert (
        cli_main(
            [
                "sf",
                "run",
                "--exports-dir",
                str(exports),
                "--out",
                str(cli_out),
                "--tasks",
            ]
        )
        == 0
    )
    _do_run("parse-exports", str(exports), out=str(mcp_out))

    cli_audit = json.loads((cli_out / "audit.json").read_text(encoding="utf-8"))
    mcp_audit = json.loads((mcp_out / "audit.json").read_text(encoding="utf-8"))

    def project_findings(audit):
        return [
            (item["check"], item["target_url"], item["details"])
            for item in audit["issues"]
            if item["check"] in {"CANONICAL_TARGET_ERROR", "CANONICAL_HOMEPAGE_GROUP"}
        ]

    assert project_findings(cli_audit) == project_findings(mcp_audit)


def test_partial_crawl_withholds_homepage_group_and_names_the_gap(tmp_path):
    exports = load_exports(str(_write_exports(tmp_path, _homepage_rows())))
    ctx = AuditContext(exports, load_config(None))
    ctx.skip_unsupported(set(exports.frames))
    run_rules(ctx)

    result = aggregate(
        ctx,
        {"input_mode": "crawl", "crawl_partial": True},
        {},
        {},
    )
    assert not [issue for issue in result.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"]
    skipped = {item.id: item.reason for item in result.skipped}
    assert "CANONICAL_HOMEPAGE_GROUP" in skipped
    assert "crawl is partial" in skipped["CANONICAL_HOMEPAGE_GROUP"]


def test_homepage_group_names_missing_target_and_page_topic_evidence(tmp_path):
    missing_home = _audit(tmp_path / "home-absent", _homepage_rows()[1:])
    assert not [issue for issue in missing_home.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"]
    skipped = {item.id: item.reason for item in missing_home.skipped}
    assert "CANONICAL_HOMEPAGE_GROUP" in skipped
    assert "homepage target(s)" in skipped["CANONICAL_HOMEPAGE_GROUP"]

    rows = _homepage_rows()
    rows[0][4] = ""  # status is known, but the homepage's indexability is not
    unknown_home_indexability = _audit(tmp_path / "home-indexability", rows)
    assert not [
        issue
        for issue in unknown_home_indexability.issues
        if issue.check == "CANONICAL_HOMEPAGE_GROUP"
    ]
    skipped = {item.id: item.reason for item in unknown_home_indexability.skipped}
    assert "CANONICAL_HOMEPAGE_GROUP" in skipped

    rows = _homepage_rows()
    rows[1][6] = ""  # one possible group member has no title evidence
    unknown_source_topic = _audit(tmp_path / "source-topic", rows)
    assert not [
        issue for issue in unknown_source_topic.issues if issue.check == "CANONICAL_HOMEPAGE_GROUP"
    ]
    skipped = {item.id: item.reason for item in unknown_source_topic.skipped}
    assert "CANONICAL_HOMEPAGE_GROUP" in skipped
    assert "source page(s)" in skipped["CANONICAL_HOMEPAGE_GROUP"]
