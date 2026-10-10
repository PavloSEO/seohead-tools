"""AMP pairing checks read from the AMP target a desktop page declares (#1020)."""

from __future__ import annotations

import csv

from bs4 import BeautifulSoup

from seohead.checks.parser import extract_amphtml, parse_html
from seohead.sf.config import load_config
from seohead.sf.core.context import AuditContext
from seohead.sf.core.loader import load_exports
from seohead.sf.core.rules import run_rules

BASE = "https://example.test"
DESKTOP = f"{BASE}/article"
AMP = f"{BASE}/article/amp"
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
    "amphtml Link Element",
]
AMP_CODES = {
    "AMP_NON_200",
    "AMP_MISSING_CANONICAL",
    "AMP_MISSING_RETURN_LINK",
    "AMP_NON_INDEXABLE_CANONICAL",
    "AMP_INDEXABLE",
}


def _row(url, *, status=200, indexable=True, canonical="", amphtml="", digest="h"):
    return [
        url,
        "text/html",
        str(status),
        "OK" if status < 300 else "Error",
        "Indexable" if indexable else "Non-Indexable",
        "" if indexable else "Noindex",
        f"Distinct synthetic title for {url}",
        f"Distinct synthetic heading for {url}",
        canonical,
        digest,
        amphtml,
    ]


def _run(tmp_path, rows):
    exports = tmp_path / "exports"
    exports.mkdir(parents=True)
    with (exports / "internal_all.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLS)
        writer.writerows(rows)
    ctx = AuditContext(load_exports(str(exports)), load_config(None))
    ctx.skip_unsupported(set(load_exports(str(exports)).frames))
    run_rules(ctx)
    return ctx


def _found(ctx):
    return {issue.check for issue in ctx.issues if issue.check in AMP_CODES}


def _skipped(ctx):
    return {item.id: item.reason for item in ctx.skipped if item.id in AMP_CODES}


def _desktop(amp=AMP):
    return _row(DESKTOP, amphtml=amp)


def test_healthy_pair_reports_only_the_indexable_notice(tmp_path):
    ctx = _run(
        tmp_path,
        [_desktop(), _row(AMP, canonical=DESKTOP)],
    )
    assert _found(ctx) == {"AMP_INDEXABLE"}
    assert "AMP_NON_200" not in _found(ctx)
    assert "AMP_MISSING_RETURN_LINK" not in _found(ctx)
    assert "AMP_NON_INDEXABLE_CANONICAL" not in _found(ctx)


def test_amp_page_not_200_is_reported_and_its_body_is_not_judged(tmp_path):
    ctx = _run(tmp_path, [_desktop(), _row(AMP, status=404)])
    assert _found(ctx) == {"AMP_NON_200"}
    assert "AMP_INDEXABLE" not in _found(ctx)


def test_missing_canonical_on_the_amp_page(tmp_path):
    ctx = _run(tmp_path, [_desktop(), _row(AMP, indexable=False)])
    assert "AMP_MISSING_CANONICAL" in _found(ctx)


def test_canonical_that_names_another_page_is_a_missing_return_link(tmp_path):
    ctx = _run(
        tmp_path,
        [_desktop(), _row(AMP, canonical=f"{BASE}/elsewhere", indexable=False)],
    )
    assert "AMP_MISSING_RETURN_LINK" in _found(ctx)
    assert "AMP_MISSING_CANONICAL" not in _found(ctx)


def test_canonical_to_a_non_indexable_page(tmp_path):
    ctx = _run(
        tmp_path,
        [
            _desktop(),
            _row(AMP, canonical=f"{BASE}/hidden"),
            _row(f"{BASE}/hidden", indexable=False),
        ],
    )
    assert "AMP_NON_INDEXABLE_CANONICAL" in _found(ctx)


def test_uncaptured_amp_target_is_a_named_skip_not_a_finding(tmp_path):
    ctx = _run(tmp_path, [_desktop()])
    assert _found(ctx) == set()
    skipped = _skipped(ctx)
    assert set(skipped) == AMP_CODES
    assert all("not captured" in reason for reason in skipped.values())


def test_no_amphtml_column_skips_every_code(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir(parents=True)
    with (exports / "internal_all.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLS[:-1])
        writer.writerow(_row(DESKTOP, amphtml="")[:-1])
    ctx = AuditContext(load_exports(str(exports)), load_config(None))
    ctx.skip_unsupported(set(load_exports(str(exports)).frames))
    run_rules(ctx)
    assert set(_skipped(ctx)) == AMP_CODES
    assert not _found(ctx)


def test_one_amp_page_shared_by_two_desktop_pages_is_reported_once(tmp_path):
    ctx = _run(
        tmp_path,
        [
            _row(DESKTOP, amphtml=AMP),
            _row(f"{BASE}/article-2", amphtml=AMP),
            _row(AMP, status=500),
        ],
    )
    non_200 = [issue for issue in ctx.issues if issue.check == "AMP_NON_200"]
    assert len(non_200) == 1
    assert non_200[0].details["desktop_urls"] == [DESKTOP, f"{BASE}/article-2"]


# ── parser: the declared target is resolved, empty or inert declarations are not targets ──


def test_extract_amphtml_resolves_the_first_declared_target():
    html = (
        '<html><head><link rel="amphtml" href="/article/amp">'
        '<link rel="amphtml" href="/second"></head><body></body></html>'
    )
    assert parse_html(html, DESKTOP)["amphtml"] == AMP


def test_extract_amphtml_ignores_empty_href_and_template_content():
    empty = '<html><head><link rel="amphtml" href=""></head></html>'
    inert = '<html><head><template><link rel="amphtml" href="/x"></template></head></html>'
    assert parse_html(empty, DESKTOP)["amphtml"] == ""
    assert parse_html(inert, DESKTOP)["amphtml"] == ""
    assert extract_amphtml(
        BeautifulSoup('<link rel="amphtml" href="/a">', "html.parser"), DESKTOP
    ) == (f"{BASE}/a")
