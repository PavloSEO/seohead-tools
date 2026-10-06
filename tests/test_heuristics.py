"""Heuristics beyond SF: HTML weight outliers and derived metrics."""

from __future__ import annotations

import csv

import pytest

from seohead.sf.core import heuristics
from seohead.sf.core.audit import run_audit
from tests.conftest import issues_of

_TEMPLATE_COLS = [
    "Address",
    "Content Type",
    "Status Code",
    "Indexability",
    "Title 1",
    "Meta Description 1",
    "H1-1",
    "Canonical Link Element 1",
    "Size (bytes)",
    "Word Count",
    "Text Ratio",
]


def _templated_title_issues(tmp_path, titles):
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_TEMPLATE_COLS)
        for index, title in enumerate(titles):
            url = f"https://example.com/p{index}"
            writer.writerow(
                [url, "text/html", 200, "Indexable", title, "d" * 80, "Heading", url, 1000, 500, 20]
            )
    res = run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)
    return [i.details for i in res.issues if i.check == "TITLE_TEMPLATED"]


def test_large_html_outlier_flagged(result):
    issues = issues_of(result, "LARGE_HTML")
    urls = {i.target_url for i in issues}
    # 300 KB page against a ~75 KB median is the outlier
    assert "https://example.com/no-title" in urls
    issue = next(i for i in issues if i.target_url == "https://example.com/no-title")
    assert issue.details["size_bytes"] == 300000
    assert issue.details["ratio"] > 3
    assert issue.details["rank"] == 1


@pytest.mark.parametrize("disk_backed", (False, True))
def test_weight_ranks_keep_ties_missing_sizes_and_all_flagged_urls(
    tmp_path, monkeypatch, disk_backed
):
    import builtins

    from seohead.sf.config import load_config
    from seohead.sf.core.context import AuditContext
    from seohead.sf.core.loader import load_exports
    from seohead.sf.core.models import Page

    values = [("z", 10000), ("a", 10000), ("missing", ""), ("zero", 0), ("b", 5000)]
    with (tmp_path / "internal_all.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["Address", "Content Type", "Status Code", "Indexability", "Size (bytes)", "Word Count"]
        )
        writer.writerows(
            [f"https://example.test/{name}", "text/html", 200, "Indexable", size, 100]
            for name, size in values
        )
    config = load_config(None)
    config["thresholds"]["large_html_abs_kb"] = 1
    ctx = AuditContext(load_exports(str(tmp_path)), config, disk_backed_pages=disk_backed)

    def scalar_sorted(items, *args, **kwargs):
        result = builtins.sorted(items, *args, **kwargs)
        assert not any(isinstance(item, Page) for item in result)
        return result

    monkeypatch.setattr(heuristics, "sorted", scalar_sorted, raising=False)
    try:
        stats = heuristics.check_html_weight(ctx)
        assert stats["count"] == 3 and stats["median"] == 10000
        findings = {issue.target_url: issue for issue in ctx.issues if issue.check == "LARGE_HTML"}
        assert {
            url.rsplit("/", 1)[-1]: issue.details["rank"] for url, issue in findings.items()
        } == {"z": 1, "a": 2, "b": 3}
        assert ctx.page_by_url["https://example.test/z"].metrics["bytes_per_word"] == 100
        assert ctx.page_by_url["https://example.test/b"].metrics["size_vs_median_ratio"] == 0.5
    finally:
        ctx.close()


def test_size_stats_in_summary(result):
    stats = result.summary.get("size_stats_bytes")
    assert stats and stats["count"] >= 4
    assert stats["max"] == 300000


def test_derived_metrics_on_pages(result):
    page = next(p for p in result.pages if p.url == "https://example.com/page-b")
    assert page.metrics["bytes_per_word"] is not None
    assert page.metrics["size_vs_median_ratio"] is not None
    # private record must be stripped before serialization
    assert "_record" not in page.metrics


def test_dom_metrics_depth_and_nodes():
    html = "<html><body><div><p><span>hi</span></p></div></body></html>"
    depth, nodes = heuristics._dom_metrics(html)
    assert nodes == 5  # html, body, div, p, span
    assert depth == 4  # html=0 .. span=4


def test_title_templated_detects_shared_prefix(tmp_path):
    """#206: only counting suffixes missed a whole advertised half of the heuristic."""
    issues = _templated_title_issues(tmp_path, [f"SEOHEAD — page {i}" for i in range(5)])
    assert len(issues) == 1
    assert issues[0]["direction"] == "prefix"
    assert issues[0]["token"] == "SEOHEAD"


def test_title_templated_still_detects_shared_suffix(tmp_path):
    issues = _templated_title_issues(tmp_path, [f"Page {i} — SEOHEAD" for i in range(5)])
    assert len(issues) == 1
    assert issues[0]["direction"] == "suffix"
    assert issues[0]["token"] == "SEOHEAD"


def test_title_templated_silent_without_a_separator_or_majority(tmp_path):
    issues = _templated_title_issues(
        tmp_path,
        [
            "Industrial Pumps for Sale",
            "Replacement Seals and Gaskets",
            "Water Treatment Systems",
            "Valve Actuators Overview",
            "Flow Meters and Sensors",
        ],
    )
    assert issues == []


def _dom_context(tmp_path, stored_count, total_count, depth_max=10, nodes_max=1000):
    from seohead.sf.core.context import AuditContext
    from seohead.sf.core.loader import LoadedExports

    rows = [
        {
            "Address": f"https://example.com/page{i}.html",
            "Content Type": "text/html; charset=utf-8",
            "Status Code": 200,
            "Indexability": "Indexable",
        }
        for i in range(total_count)
    ]
    exports = LoadedExports({"internal_all": __import__("pandas").DataFrame(rows)})
    html_dir = tmp_path / "example.com"
    html_dir.mkdir()
    for i in range(stored_count):
        (html_dir / f"page{i}.html").write_text(
            "<html><body><div><p><span>x</span></p></div></body></html>", encoding="utf-8"
        )
    ctx = AuditContext(
        exports,
        {
            "input": {"html_store_dir": str(tmp_path)},
            "thresholds": {"dom_depth_max": depth_max, "dom_nodes_max": nodes_max},
        },
    )
    return ctx


def test_dom_low_coverage_skips_with_ratio(tmp_path):
    """#455: 1 of 10 pages stored must not read as a clean DOM check."""
    ctx = _dom_context(tmp_path, stored_count=1, total_count=10)
    heuristics.check_dom(ctx)
    assert ctx.issues == []
    reasons = {s.id: s.reason for s in ctx.skipped}
    assert "1 of 10" in reasons["DOM_TOO_DEEP"]
    assert "1 of 10" in reasons["DOM_TOO_MANY_NODES"]


def test_dom_full_coverage_stays_silent(tmp_path):
    """Negative control: full coverage with nothing over threshold stays clean, no skip."""
    ctx = _dom_context(tmp_path, stored_count=10, total_count=10)
    heuristics.check_dom(ctx)
    assert ctx.issues == []
    assert ctx.skipped == []


def test_dom_low_coverage_findings_carry_coverage_detail(tmp_path):
    """When a low-coverage sample does exceed a threshold, the finding is still tagged."""
    ctx = _dom_context(tmp_path, stored_count=1, total_count=10, depth_max=1)
    heuristics.check_dom(ctx)
    assert len(ctx.issues) >= 1
    assert all("html_coverage" in i.details for i in ctx.issues)


def test_html_index_matches_by_path_and_basename(tmp_path):
    host_dir = tmp_path / "example.com" / "blog"
    host_dir.mkdir(parents=True)
    f = host_dir / "post.html"
    f.write_text("<html></html>", encoding="utf-8")
    index = heuristics._build_html_index(str(tmp_path))
    # host+path key and basename key both resolve
    assert heuristics._match_html_file(index, "https://example.com/blog/post.html") == str(f)
    assert heuristics._match_html_file(index, "https://other/zzz/post.html") == str(f)
    assert heuristics._match_html_file(index, "https://example.com/missing.html") is None


def test_disk_dom_pass_keeps_every_matched_source_and_coverage_note(tmp_path):
    from seohead.sf.core.context import AuditContext

    legacy = _dom_context(tmp_path, stored_count=3, total_count=4, depth_max=1, nodes_max=2)
    legacy.thresholds["html_store_coverage_min"] = 0.9
    disk = AuditContext(legacy.exports, legacy.config, disk_backed_pages=True)
    try:
        heuristics.check_dom(legacy)
        heuristics.check_dom(disk)
        assert [item.to_json() for item in disk.issues] == [
            item.to_json() for item in legacy.issues
        ]
        assert len(disk.issues) == 6
        assert all("3 of 4" in item.details["html_coverage"] for item in disk.issues)
        assert [page.metrics["dom_nodes"] for page in disk.pages] == [5, 5, 5, None]
    finally:
        legacy.close()
        disk.close()
