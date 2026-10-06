"""The anomaly scanner: contradictions inside one run's own artifacts.

Each rule here exists because a defect shipped past 1600 tests and was caught by a person
reading a number that could not be true. The fixture below carries one instance of each, so a
regression in a rule is a failing test rather than another afternoon of reading reports.
"""

from __future__ import annotations

import json

from seohead.servers import handlers
from seohead.tools import logscan


def _write_run(tmp_path, pages, audit, decisions=None):
    (tmp_path / "pages.jsonl").write_text(
        "\n".join(json.dumps(p) for p in pages) + "\n", encoding="utf-8"
    )
    (tmp_path / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    if decisions is not None:
        (tmp_path / "decisions.jsonl").write_text(
            "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
        )
    return str(tmp_path)


def _page(url, **kw):
    base = {
        "url": url,
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "size_bytes": 1000,
        "word_count": 100,
        "text_ratio": 40.0,
        "representation": "static",
    }
    base.update(kw)
    return base


def _clean_run(tmp_path):
    pages = [_page(f"https://example.com/p{i}") for i in range(4)]
    audit = {
        "summary": {"by_check": {"THIN_CONTENT": 1}},
        "issues": [{"check": "THIN_CONTENT", "target_url": "https://example.com/p0"}],
    }
    return _write_run(tmp_path, pages, audit)


def test_a_clean_run_reports_nothing(tmp_path):
    result = logscan.scan(logscan.load_run(_clean_run(tmp_path)))
    assert result["anomaly_count"] == 0
    assert result["anomalies"] == []
    # A clean result must not be mistakable for an unchecked one.
    assert len(result["rules_run"]) == len(logscan.RULES)
    assert result["read"]["pages"] == 4


# ── #99: a size that disagrees with the bytes ────────────────────────────────


def test_a_recorded_size_that_disagrees_with_the_downloaded_file(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    images = tmp_path / "images"
    images.mkdir()
    (images / "photo.webp").write_bytes(b"x" * 738968)
    (images / "manifest.json").write_text(
        json.dumps({"images": [{"url": "https://example.com/photo.webp", "path": "photo.webp"}]}),
        encoding="utf-8",
    )
    pages = [
        _page(
            "https://example.com/photo.webp",
            content_type="image/webp",
            size_bytes=1270855,  # the inflated figure the decoded-text measurement produced
            word_count=0,
            text_ratio=None,
        )
    ]
    _write_run(run_dir, pages, {"summary": {"by_check": {}}, "issues": []})

    result = logscan.scan(logscan.load_run(str(run_dir), str(images)))

    found = [a for a in result["anomalies"] if a["rule"] == "size_matches_file"]
    assert len(found) == 1
    assert found[0]["observed"] == 1270855
    assert found[0]["expected"] == 738968


def test_a_text_ratio_over_one_hundred_percent(tmp_path):
    pages = [_page("https://example.com/", text_ratio=173.0)]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []})
    result = logscan.scan(logscan.load_run(run))
    assert [a["rule"] for a in result["anomalies"]] == ["text_ratio_is_a_percentage"]


def test_words_counted_on_a_page_with_no_bytes(tmp_path):
    pages = [_page("https://example.com/", size_bytes=0, word_count=250, text_ratio=None)]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []})
    result = logscan.scan(logscan.load_run(run))
    assert [a["rule"] for a in result["anomalies"]] == ["words_without_bytes"]


# ── #94: a check compared against the wrong population ───────────────────────


def test_a_finding_about_a_url_the_run_never_fetched(tmp_path):
    pages = [_page("https://example.com/")]
    audit = {
        "summary": {"by_check": {"URL_NOT_IN_SITEMAP": 1}},
        "issues": [{"check": "URL_NOT_IN_SITEMAP", "target_url": "https://wa.me/123"}],
    }
    run = _write_run(tmp_path, pages, audit)
    result = logscan.scan(logscan.load_run(run))
    found = [a for a in result["anomalies"] if a["rule"] == "findings_are_about_crawled_urls"]
    assert len(found) == 1
    assert found[0]["target"] == "https://wa.me/123"


def test_a_check_that_fires_more_often_than_there_are_pages(tmp_path):
    pages = [_page(f"https://example.com/p{i}") for i in range(3)]
    audit = {
        "summary": {"by_check": {"URL_NOT_IN_SITEMAP": 9}},
        "issues": [
            {"check": "URL_NOT_IN_SITEMAP", "target_url": f"https://example.com/p{i % 3}"}
            for i in range(9)
        ],
    }
    run = _write_run(tmp_path, pages, audit)
    result = logscan.scan(logscan.load_run(run))
    found = [a for a in result["anomalies"] if a["rule"] == "check_within_its_population"]
    assert len(found) == 1
    assert found[0]["observed"] == 9
    assert found[0]["expected"] == 3


def test_a_check_that_deliberately_names_uncrawled_urls_is_not_flagged(tmp_path):
    """SITEMAP_ORPHAN is *about* a URL nothing linked to; that is not a contradiction."""
    pages = [_page("https://example.com/")]
    audit = {
        "summary": {"by_check": {"SITEMAP_ORPHAN": 1}},
        "issues": [{"check": "SITEMAP_ORPHAN", "target_url": "https://example.com/declared"}],
    }
    run = _write_run(tmp_path, pages, audit)
    assert logscan.scan(logscan.load_run(run))["anomaly_count"] == 0


def test_follow_and_nofollow_inlinks_names_an_edge_not_a_crawled_page(tmp_path):
    """#285: the finding names the destination of an in-host edge that received both a
    followed and a nofollow link -- exactly the situation that can leave it outside
    pages.jsonl (a nofollow-only path to it, a query-variant cap, an out-of-scope
    exclusion). That is a page-versus-edge distinction, not a crawl gap this rule can
    honestly report, so it belongs in the same exempt set as BROKEN_LINK."""
    pages = [_page("https://example.com/")]
    audit = {
        "summary": {"by_check": {"FOLLOW_AND_NOFOLLOW_INLINKS": 1}},
        "issues": [
            {
                "check": "FOLLOW_AND_NOFOLLOW_INLINKS",
                "target_url": "https://example.com/blog?tag=target",
            }
        ],
    }
    run = _write_run(tmp_path, pages, audit)
    assert logscan.scan(logscan.load_run(run))["anomaly_count"] == 0


# ── #95: a canonical whose twin answered ─────────────────────────────────────


def test_canonical_reported_as_a_redirect_while_that_url_answered_200(tmp_path):
    pages = [
        _page("https://example.com/post"),
        _page("https://example.com/author/name/"),
        _page(
            "https://example.com/author/name",
            status_code=301,
            word_count=0,
            text_ratio=None,
            size_bytes=0,
        ),
    ]
    audit = {
        "summary": {"by_check": {"CANONICAL_TO_REDIRECT": 1}},
        "issues": [
            {
                "check": "CANONICAL_TO_REDIRECT",
                "target_url": "https://example.com/post",
                "details": {"canonical": "https://example.com/author/name/"},
            }
        ],
    }
    run = _write_run(tmp_path, pages, audit)
    result = logscan.scan(logscan.load_run(run))
    rules = {a["rule"] for a in result["anomalies"]}
    assert "canonical_to_redirect_has_no_answering_twin" in rules


# ── #134: decisions.jsonl tells the truth about a run's own scope check ──────


def test_a_url_excluded_as_outside_host_whose_host_is_the_crawl_host(tmp_path):
    """The contradiction issue #134 asks for by name: invisible in audit.json — which only
    ever records the ``outside_host`` *count* in ``run.excluded`` — because the URL and the
    host it was compared against exist only in the decision log."""
    pages = [_page("https://example.com/")]
    decisions = [
        {
            "type": "exclude",
            "url": "https://example.com/off-by-mistake",
            "reason": "outside_host",
            "host": "example.com",
        }
    ]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []}, decisions)
    result = logscan.scan(logscan.load_run(run))
    found = [
        a for a in result["anomalies"] if a["rule"] == "outside_host_exclusion_matches_its_own_host"
    ]
    assert len(found) == 1
    assert found[0]["target"] == "https://example.com/off-by-mistake"
    assert found[0]["observed"] == "example.com"


def test_a_genuine_cross_host_exclusion_is_not_flagged(tmp_path):
    pages = [_page("https://example.com/")]
    decisions = [
        {
            "type": "exclude",
            "url": "https://other.example/x",
            "reason": "outside_host",
            "host": "example.com",
        }
    ]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []}, decisions)
    result = logscan.scan(logscan.load_run(run))
    assert result["anomaly_count"] == 0
    assert result["read"]["decisions"] == 1


# ── bookkeeping contradictions ───────────────────────────────────────────────


def test_a_summary_that_disagrees_with_its_own_rows(tmp_path):
    pages = [_page(f"https://example.com/p{i}") for i in range(5)]
    audit = {
        "summary": {"by_check": {"THIN_CONTENT": 4}},
        "issues": [{"check": "THIN_CONTENT", "target_url": "https://example.com/p0"}],
    }
    run = _write_run(tmp_path, pages, audit)
    result = logscan.scan(logscan.load_run(run))
    found = [a for a in result["anomalies"] if a["rule"] == "summary_matches_detail"]
    assert len(found) == 1
    assert found[0]["observed"] == 4
    assert found[0]["expected"] == 1


def test_pages_measured_two_ways_where_only_some_say_which(tmp_path):
    pages = [_page("https://example.com/a"), _page("https://example.com/b", representation="")]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []})
    result = logscan.scan(logscan.load_run(run))
    assert [a["rule"] for a in result["anomalies"]] == ["representation_is_recorded"]


# ── the handler ──────────────────────────────────────────────────────────────


def test_the_handler_refuses_a_directory_with_no_artifacts(tmp_path):
    out = handlers.log_scan(run=str(tmp_path))
    assert out["ok"] is False
    assert "no audit.json or pages.jsonl" in out["error"]


def test_the_handler_reads_a_run_directory(tmp_path):
    out = handlers.log_scan(run=_clean_run(tmp_path))
    assert out["ok"] is True
    assert out["anomaly_count"] == 0


def test_repeated_copies_of_one_contradiction_are_capped_but_counted(tmp_path):
    pages = [_page(f"https://example.com/p{i}", text_ratio=150.0) for i in range(30)]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []})
    result = logscan.scan(logscan.load_run(run), max_per_rule=5)
    assert len(result["anomalies"]) == 5
    assert result["by_rule"]["text_ratio_is_a_percentage"] == 30


# ── the CLI gate ─────────────────────────────────────────────────────────────


def test_the_cli_exits_two_when_a_run_contradicts_itself(tmp_path, capsys):
    from seohead.cli import main

    pages = [_page("https://example.com/", text_ratio=150.0)]
    run = _write_run(tmp_path, pages, {"summary": {"by_check": {}}, "issues": []})
    assert main(["log-scan", "--run", run]) == 2
    assert "text_ratio_is_a_percentage" in capsys.readouterr().out


def test_the_cli_exits_zero_on_a_clean_run(tmp_path):
    from seohead.cli import main

    assert main(["log-scan", "--run", _clean_run(tmp_path)]) == 0


def test_scan_names_a_check_that_describes_most_of_the_site(tmp_path):
    """Issue #98: the report's own implausibility list must reach log-scan, so a run
    whose findings are dominated by one check is caught without a person reading it."""
    from seohead.tools.logscan import RunArtifacts, scan

    run = RunArtifacts(
        audit={
            "summary": {
                "by_check": {"URL_NOT_IN_SITEMAP": 392},
                "implausible_checks": [
                    {"check": "URL_NOT_IN_SITEMAP", "pages": 124, "share": 0.743}
                ],
            },
            "issues": [],
            "run": {},
        }
    )

    result = scan(run)

    named = [a for a in result["review"] if a["rule"] == "check_describes_most_of_the_site"]
    assert len(named) == 1
    assert named[0]["target"] == "URL_NOT_IN_SITEMAP"
    assert "74%" in named[0]["message"]
    # It is a prompt to look, not a contradiction: it must not reach the bucket
    # that makes log-scan exit non-zero.
    assert not [a for a in result["anomalies"] if a["rule"] == "check_describes_most_of_the_site"]


def test_scan_stays_quiet_when_no_check_dominates(tmp_path):
    """An empty implausibility list is the ordinary case and must produce no anomaly —
    a scanner that cried on every run would be ignored on the run that mattered."""
    from seohead.tools.logscan import RunArtifacts, scan

    run = RunArtifacts(
        audit={"summary": {"by_check": {}, "implausible_checks": []}, "issues": [], "run": {}}
    )

    result = scan(run)

    assert not [a for a in result["anomalies"] if a["rule"] == "check_describes_most_of_the_site"]


def test_native_audit_v2_streams_counts_without_materializing_legacy(tmp_path, monkeypatch):
    from contextlib import closing
    from pathlib import Path

    from seohead.storage import open_scan
    from seohead.storage.audit_v2 import AuditV2Reader, write_audit_v2
    from tests.test_scan_hreflang_graph import BASE, _capture, _html, _Response

    document = _capture(
        tmp_path,
        {
            BASE: _Response(_html(body='<a href="/fr/">French</a>')),
            BASE + "fr/": _Response(_html()),
        },
    )
    path = tmp_path / "scan.sqlite"
    with closing(open_scan(path)) as con:
        scan = con.execute("SELECT * FROM scan").fetchone()
        binding = {
            "scan_uuid": scan["scan_uuid"],
            "evidence_revision": scan["evidence_revision"],
            "analyzer_version": scan["writer_version"],
            "analyzer_revision": scan["writer_revision"],
        }
    header = {**document, "issues": [], "pages": [], "summary": {"by_check": {"TITLE_MISSING": 70}}}
    write_audit_v2(
        path,
        header,
        {
            "/pages": document["pages"],
            "/issues": (
                {"check": "TITLE_MISSING", "target_url": f"{BASE}missing-{index // 2}"}
                for index in range(70)
            ),
        },
        binding,
    )
    monkeypatch.setattr(
        AuditV2Reader,
        "materialize_legacy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy audit materialization")
        ),
    )
    with logscan.load_run(str(path)) as artifacts:
        index_path = Path(artifacts.pages.path)
        assert not isinstance(artifacts.pages, list)
        assert not isinstance(artifacts.audit["issues"], list)
        result = logscan.scan(artifacts, max_per_rule=3)
        assert result["read"]["pages"] == 2
        assert result["read"]["audit"] is True
        assert result["by_rule"]["findings_are_about_crawled_urls"] == 35
        examples = [
            item
            for item in result["anomalies"]
            if item["rule"] == "findings_are_about_crawled_urls"
        ]
        assert len(examples) == 3
        assert "#pages" in examples[0]["sources"]["expected"]
        assert "decisions.jsonl" in result["limitations"][0]
        # Re-reading the source does not retain the previous rule's dedup set.
        assert (
            logscan.scan(artifacts, max_per_rule=0)["by_rule"]["findings_are_about_crawled_urls"]
            == 35
        )
    assert not index_path.exists()
    assert handlers.log_scan(run=str(tmp_path), max_per_rule=1)["read"]["pages"] == 2


def test_site_lastmod_origin_is_not_a_missing_page_but_other_targets_still_are(tmp_path):
    origin = "https://example.test"
    measured = {"all_identical": True, "oldest": "2026-10-04"}
    valid = {
        "check": "SITEMAP_STALE_LASTMOD",
        "source": "sitemap",
        "target_url": origin,
        "details": measured,
    }
    issues = [
        valid,
        {**valid, "target_url": origin + "/uncrawled"},
        {**valid, "target_url": "https://other.test"},
        {**valid, "source": "SF-derived"},
        {**valid, "details": {"all_identical": False}},
        {"check": "TITLE_MISSING", "target_url": origin},
    ]
    audit = {
        "run": {"source": origin + "/p/0"},
        "summary": {"sitemap": {"lastmod": measured}},
        "issues": issues,
    }
    run = _write_run(tmp_path, [_page(origin + "/p/0")], audit)
    findings = logscan.scan(logscan.load_run(run))
    # Two invalid lastmod variants name the same target/check and deduplicate.
    assert findings["by_rule"]["findings_are_about_crawled_urls"] == 4
    valid_only = {**audit, "issues": [valid]}
    _write_run(tmp_path, [_page(origin + "/p/0")], valid_only)
    assert logscan.scan(logscan.load_run(run))["anomaly_count"] == 0
    # Export input paths carry no origin; a measured page must establish it.
    valid_only["run"]["source"] = str(tmp_path)
    _write_run(tmp_path, [_page(origin + "/p/0")], valid_only)
    assert logscan.scan(logscan.load_run(run))["anomaly_count"] == 0


def test_native_v2_site_lastmod_exemption_preserves_a_real_missing_page_finding(tmp_path):
    from contextlib import closing

    from seohead.storage import open_scan, read_audit
    from seohead.storage.audit_v2 import write_audit_v2
    from tests.test_scan_reanalysis_integration import _source

    path = tmp_path / "native.sqlite"
    origin = "https://example.test"
    _source(path, start_url=origin + "/p/0")
    document = read_audit(path)
    measured = {"all_identical": True, "oldest": "2026-10-04"}
    document["summary"] = {"sitemap": {"lastmod": measured}}
    document["issues"] = []
    issues = [
        {
            "check": "SITEMAP_STALE_LASTMOD",
            "source": "sitemap",
            "target_url": origin,
            "details": measured,
        },
        {"check": "TITLE_MISSING", "target_url": origin + "/never-captured"},
    ]
    with closing(open_scan(path)) as con:
        scan = con.execute("SELECT * FROM scan").fetchone()
        binding = {
            "scan_uuid": scan["scan_uuid"],
            "evidence_revision": scan["evidence_revision"],
            "analyzer_version": scan["writer_version"],
            "analyzer_revision": scan["writer_revision"],
        }
    write_audit_v2(path, document, {"/issues": issues}, binding)
    result = handlers.log_scan(run=str(path))
    assert result["anomaly_count"] == 1
    assert result["anomalies"][0]["target"] == origin + "/never-captured"
