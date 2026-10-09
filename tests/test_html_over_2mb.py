"""HTML_OVER_2MB is an absolute ceiling, separate from the median-relative LARGE_HTML."""

from __future__ import annotations

import csv

from seohead.sf.core.audit import run_audit
from tests.conftest import issues_of

_COLS = [
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


def _audit(tmp_path, sizes):
    d = tmp_path / "exports"
    d.mkdir()
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_COLS)
        for index, size in enumerate(sizes):
            url = f"https://example.com/p{index}"
            writer.writerow(
                [url, "text/html", 200, "Indexable", f"Title {index}", "d" * 80, "H", url, size, 500, 20]
            )
    return run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)


def test_document_over_2mb_is_flagged_regardless_of_site_median(tmp_path):
    # Every page is 2.5 MB, so none is a median-relative outlier, yet all cross the ceiling.
    result = _audit(tmp_path, [2_500_000, 2_500_000, 2_500_000])
    flagged = {i.target_url for i in issues_of(result, "HTML_OVER_2MB")}
    assert flagged == {f"https://example.com/p{i}" for i in range(3)}


def test_document_under_2mb_is_not_flagged(tmp_path):
    result = _audit(tmp_path, [1_900_000, 1_000, 1_000])
    assert not issues_of(result, "HTML_OVER_2MB")
