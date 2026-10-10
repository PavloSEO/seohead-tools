"""UNLINKED_CANONICAL and PAGINATION_LOOP / UNLINKED_PAGINATION_SERIES.

These are two of issue #15's post-crawl set-difference computations (items 4
and 5): "reachable only via canonical" and "reachable only via rel=next" are
both knowable only once the whole crawl -- and its complete Inlinks column --
is on hand. A third test confirms both are withheld, not merely footnoted, on
a crawl the aggregator has marked partial.
"""

from __future__ import annotations

import csv

from seohead.sf.core.audit import run_audit

COLS = [
    "Address",
    "Content Type",
    "Status Code",
    "Status",
    "Indexability",
    "Indexability Status",
    "Canonical Link Element 1",
    'rel="next" 1',
    "Inlinks",
    "Crawl Depth",
]


def _row(
    url: str,
    canonical: str = "",
    rel_next: str = "",
    inlinks: int = 1,
    depth: int = 1,
    indexable: bool = True,
) -> list[str]:
    indexability = "Indexable" if indexable else "Non-Indexable"
    return [url, "text/html", "200", "OK", indexability, "", canonical, rel_next, inlinks, depth]


def _run(tmp_path, rows):
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    return run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)


def _fired(res, check):
    return {i.target_url: i for i in res.issues if i.check == check}


# -- UNLINKED_CANONICAL ----------------------------------------------------


def test_canonical_target_with_no_inlinks_is_unlinked(tmp_path):
    rows = [
        _row("https://example.com/", inlinks=0, depth=0),  # homepage: never "unlinked"
        _row("https://example.com/a", canonical="https://example.com/b", inlinks=3),
        _row("https://example.com/b", inlinks=0),  # only ever a canonical target
    ]
    res = _run(tmp_path, rows)
    fired = _fired(res, "UNLINKED_CANONICAL")
    assert set(fired) == {"https://example.com/b"}
    assert fired["https://example.com/b"].details["canonicalized_from"] == ["https://example.com/a"]


def test_canonical_target_with_a_real_inlink_does_not_fire(tmp_path):
    rows = [
        _row("https://example.com/a", canonical="https://example.com/b", inlinks=3),
        _row("https://example.com/b", inlinks=2),  # also reached by an ordinary hyperlink
    ]
    res = _run(tmp_path, rows)
    assert _fired(res, "UNLINKED_CANONICAL") == {}


def test_unlinked_canonical_skips_without_a_canonical_column(tmp_path):
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        w.writerow(["https://example.com/", "text/html", "200", "OK", "Indexable"])
    res = run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)
    reasons = {s.id: s.reason for s in res.skipped}
    assert "Canonical" in reasons["UNLINKED_CANONICAL"]


# -- PAGINATION_LOOP / UNLINKED_PAGINATION_SERIES --------------------------


def test_a_pagination_cycle_is_a_loop(tmp_path):
    rows = [
        _row("https://example.com/p1", rel_next="https://example.com/p2", inlinks=5),
        _row("https://example.com/p2", rel_next="https://example.com/p1", inlinks=1),
    ]
    res = _run(tmp_path, rows)
    loops = _fired(res, "PAGINATION_LOOP")
    assert set(loops) == {"https://example.com/p1"}
    assert "UNLINKED_PAGINATION_SERIES" not in {i.check for i in res.issues}


def test_a_terminating_series_with_no_inlink_is_unlinked(tmp_path):
    rows = [
        _row("https://example.com/p1", rel_next="https://example.com/p2", inlinks=0),
        _row("https://example.com/p2", rel_next="https://example.com/p3", inlinks=1),
        _row("https://example.com/p3", inlinks=1),
    ]
    res = _run(tmp_path, rows)
    unlinked = _fired(res, "UNLINKED_PAGINATION_SERIES")
    assert set(unlinked) == {"https://example.com/p1"}
    assert unlinked["https://example.com/p1"].details["series"] == [
        "https://example.com/p1",
        "https://example.com/p2",
        "https://example.com/p3",
    ]
    assert "PAGINATION_LOOP" not in {i.check for i in res.issues}


def test_a_series_head_with_a_real_inlink_is_not_unlinked(tmp_path):
    rows = [
        _row("https://example.com/p1", rel_next="https://example.com/p2", inlinks=2),
        _row("https://example.com/p2", inlinks=1),
    ]
    res = _run(tmp_path, rows)
    assert _fired(res, "UNLINKED_PAGINATION_SERIES") == {}


def test_pagination_checks_skip_without_a_rel_next_column(tmp_path):
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Address", "Content Type", "Status Code", "Status", "Indexability"])
        w.writerow(["https://example.com/", "text/html", "200", "OK", "Indexable"])
    res = run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)
    reasons = {s.id: s.reason for s in res.skipped}
    assert 'rel="next"' in reasons["PAGINATION_LOOP"]
    assert 'rel="next"' in reasons["UNLINKED_PAGINATION_SERIES"]


# -- withheld on a partial crawl (issue #15 design requirement) ------------


def test_unlinked_findings_are_withheld_on_a_partial_crawl(tmp_path):
    rows = [
        _row("https://example.com/a", canonical="https://example.com/b", inlinks=3),
        _row("https://example.com/b", inlinks=0),
        _row("https://example.com/p1", rel_next="https://example.com/p2", inlinks=0),
        _row("https://example.com/p2", inlinks=1),
    ]
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    # A caller-declared partial crawl (URL limit, interrupted run, ...).
    from seohead.sf.config import load_config
    from seohead.sf.core.aggregate import aggregate
    from seohead.sf.core.context import AuditContext
    from seohead.sf.core.loader import load_exports
    from seohead.sf.core.rules import run_rules

    ctx = AuditContext(load_exports(str(d)), load_config(None))
    ctx.skip_unsupported(set(ctx.exports.frames))
    run_rules(ctx)
    result = aggregate(ctx, {"crawl_partial": True}, {}, {})

    assert result.run["crawl_partial"] is True
    fired_checks = {i.check for i in result.issues}
    assert "UNLINKED_CANONICAL" not in fired_checks
    assert "UNLINKED_PAGINATION_SERIES" not in fired_checks
    reasons = {s.id: s.reason for s in result.skipped}
    assert "partial" in reasons["UNLINKED_CANONICAL"]
    assert "partial" in reasons["UNLINKED_PAGINATION_SERIES"]


# -- HREFLANG_UNLINKED_TARGET ----------------------------------------------

HREFLANG_COLS = ["Source", "Destination", "Hreflang"]


def _run_hreflang(tmp_path, rows, hreflang_rows):
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    with open(d / "all_hreflang.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HREFLANG_COLS)
        w.writerows(hreflang_rows)
    return run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)


def test_hreflang_target_with_no_inlinks_is_unlinked(tmp_path):
    rows = [
        _row("https://example.com/", inlinks=0, depth=0),  # homepage: never "unlinked"
        _row("https://example.com/en/", inlinks=2),
        _row("https://example.com/de/", inlinks=0),  # named only by hreflang
    ]
    hreflang = [
        ["https://example.com/en/", "https://example.com/de/", "de"],
        ["https://example.com/de/", "https://example.com/en/", "en"],
    ]
    res = _run_hreflang(tmp_path, rows, hreflang)
    fired = _fired(res, "HREFLANG_UNLINKED_TARGET")
    assert set(fired) == {"https://example.com/de/"}
    assert fired["https://example.com/de/"].details["declared_from"] == ["https://example.com/en/"]


def test_hreflang_self_reference_is_not_an_unlinked_target(tmp_path):
    rows = [_row("https://example.com/en/", inlinks=0)]
    hreflang = [["https://example.com/en/", "https://example.com/en/", "en"]]
    res = _run_hreflang(tmp_path, rows, hreflang)
    assert _fired(res, "HREFLANG_UNLINKED_TARGET") == {}


def test_hreflang_target_with_a_real_inlink_does_not_fire(tmp_path):
    rows = [
        _row("https://example.com/en/", inlinks=2),
        _row("https://example.com/de/", inlinks=3),  # also reached by an ordinary hyperlink
    ]
    hreflang = [["https://example.com/en/", "https://example.com/de/", "de"]]
    res = _run_hreflang(tmp_path, rows, hreflang)
    assert _fired(res, "HREFLANG_UNLINKED_TARGET") == {}


def test_unlinked_hreflang_skips_without_an_hreflang_export(tmp_path):
    rows = [_row("https://example.com/de/", inlinks=0)]
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    res = run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)
    reasons = {s.id: s.reason for s in res.skipped}
    assert "all_hreflang" in reasons["HREFLANG_UNLINKED_TARGET"]


def test_unlinked_hreflang_is_withheld_on_a_partial_crawl(tmp_path):
    from seohead.sf.config import load_config
    from seohead.sf.core.aggregate import aggregate
    from seohead.sf.core.context import AuditContext
    from seohead.sf.core.inlinks import run_inlinks
    from seohead.sf.core.loader import load_exports
    from seohead.sf.core.rules import run_rules

    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(
            [
                _row("https://example.com/en/", inlinks=2),
                _row("https://example.com/de/", inlinks=0),
            ]
        )
    with open(d / "all_hreflang.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HREFLANG_COLS)
        w.writerow(["https://example.com/en/", "https://example.com/de/", "de"])

    ctx = AuditContext(load_exports(str(d)), load_config(None))
    ctx.skip_unsupported(set(ctx.exports.frames))
    run_rules(ctx)
    run_inlinks(ctx)
    result = aggregate(ctx, {"crawl_partial": True}, {}, {})

    assert _fired(result, "HREFLANG_UNLINKED_TARGET") == {}
    reasons = {s.id: s.reason for s in result.skipped}
    assert "partial" in reasons["HREFLANG_UNLINKED_TARGET"]
    reasons_w = {s.id: s.reason for s in result.skipped}
    assert "partial" in reasons_w["HREFLANG_UNLINKED_TARGET"]
