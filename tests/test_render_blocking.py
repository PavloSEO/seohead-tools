"""Render Blocking Requests as a native crawl finding (issue #1022, slice S1).

No network: the head is read from the HTML the crawl already has.
"""

from __future__ import annotations

import csv

from seohead.checks.parser import parse_html
from seohead.sf.core.audit import run_audit

TITLE = "A descriptive page title with sufficient length"
DESC = "A meta description deliberately longer than seventy characters to clear the validation threshold."

HEAD_HTML = """<!doctype html><html><head>
<script src="/a.js"></script>
<script src="/b.js" defer></script>
<script type="module" src="/m.js"></script>
<link rel="stylesheet" href="/c.css">
<link rel="stylesheet" href="/print.css" media="print">
<template><script src="/t.js"></script></template>
</head><body><p>x</p></body></html>"""


def test_parse_html_lists_only_blocking_head_resources():
    parsed = parse_html(HEAD_HTML, "https://example.com/page")
    assert parsed["render_blocking"] == [
        "https://example.com/a.js",
        "https://example.com/c.css",
    ]


def test_parse_html_without_head_resources_is_empty():
    parsed = parse_html("<html><head></head><body>x</body></html>", "https://example.com/")
    assert parsed["render_blocking"] == []


COLS = [
    "Address",
    "Content Type",
    "Status Code",
    "Status",
    "Indexability",
    "Title 1",
    "Meta Description 1",
    "H1-1",
    "Canonical Link Element 1",
    "Meta Robots 1",
    "Size (bytes)",
]


def _run(tmp_path, *, extra_cols=(), rows=()):
    d = tmp_path / "exports"
    d.mkdir()
    cols = COLS + list(extra_cols)
    with open(d / "internal_all.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for url, extra in rows:
            base = [
                url,
                "text/html",
                "200",
                "OK",
                "Indexable",
                TITLE,
                DESC,
                "H",
                url,
                "index,follow",
                "5000",
            ]
            w.writerow(base + list(extra))
    return run_audit(input_mode="parse-exports", exports_dir=str(d), log=lambda m: None)


def test_render_blocking_skips_without_column(tmp_path):
    res = _run(tmp_path, rows=[("https://example.com/", [])])
    assert "RENDER_BLOCKING" in {s.id for s in res.skipped}
    assert not [i for i in res.issues if i.check == "RENDER_BLOCKING"]


def test_render_blocking_fires_per_page_with_urls(tmp_path):
    res = _run(
        tmp_path,
        extra_cols=["Render Blocking Resources"],
        rows=[
            ("https://example.com/bad", ["https://example.com/a.css\nhttps://example.com/a.js"]),
            ("https://example.com/good", [""]),
        ],
    )
    fired = [i for i in res.issues if i.check == "RENDER_BLOCKING"]
    assert {i.target_url for i in fired} == {"https://example.com/bad"}
