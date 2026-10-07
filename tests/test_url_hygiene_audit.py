"""Recorded URL hygiene facts: session values stay redacted and slash pairs need evidence."""

import csv
import gc
import tracemalloc
import weakref

import pandas as pd

from seohead.sf.config import load_config
from seohead.sf.core import context as context_module
from seohead.sf.core.audit import run_audit
from seohead.sf.core.context import AuditContext
from seohead.sf.core.loader import LoadedExports
from seohead.sf.core.models import Page
from seohead.sf.core.rules import check_url_hygiene

_COLUMNS = [
    "Address",
    "Content Type",
    "Status Code",
    "Status",
    "Indexability",
    "Indexability Status",
    "Title 1",
    "Meta Description 1",
    "H1-1",
    "Canonical Link Element 1",
    "Redirect URL",
]


def _row(
    url: str,
    *,
    status: int = 200,
    canonical: str = "",
    redirect: str = "",
    title: str = "A useful title for the page",
) -> list[str]:
    return [
        url,
        "text/html",
        str(status),
        "OK",
        "Indexable" if status == 200 else "Non-Indexable",
        "" if status == 200 else "Redirected",
        title,
        "A sufficiently long description for the test page.",
        "Heading",
        canonical or url,
        redirect,
    ]


def _audit(tmp_path, rows):
    exports = tmp_path / "exports"
    exports.mkdir()
    with (exports / "internal_all.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_COLUMNS)
        writer.writerows(rows)
    return run_audit(
        input_mode="parse-exports", exports_dir=str(exports), log=lambda _message: None
    )


def test_session_parameter_name_is_kept_while_value_is_redacted(tmp_path):
    result = _audit(tmp_path, [_row("https://example.com/account?PHPSESSID=secret-token&id=42")])
    finding = next(issue for issue in result.issues if issue.check == "URL_SESSION_ID")
    assert (
        finding.target_url
        == "https://example.com/account?PHPSESSID=%5Bredacted%5D&id=%5Bredacted%5D"
    )
    assert finding.details == {"parameter_names": ["PHPSESSID"], "url_values_redacted": True}
    assert "secret-token" not in str(finding.details)


def test_legitimate_id_and_tracking_parameters_are_not_called_session_identifiers(tmp_path):
    result = _audit(tmp_path, [_row("https://example.com/product?id=42&utm_source=newsletter")])
    assert "URL_SESSION_ID" not in {issue.check for issue in result.issues}


def test_slash_pair_requires_two_indexable_pages_without_convergence(tmp_path):
    result = _audit(
        tmp_path,
        [_row("https://example.com/guide"), _row("https://example.com/guide/")],
    )
    finding = next(
        issue for issue in result.issues if issue.check == "URL_TRAILING_SLASH_INCONSISTENT"
    )
    assert finding.occurrences_count == 2
    assert finding.details["observed_variants"] == [
        "https://example.com/guide",
        "https://example.com/guide/",
    ]
    assert finding.details["status_codes"] == {
        "https://example.com/guide": 200,
        "https://example.com/guide/": 200,
    }
    assert finding.details["canonical"] == {
        "https://example.com/guide": "https://example.com/guide",
        "https://example.com/guide/": "https://example.com/guide/",
    }


def test_slash_redirect_convergence_is_not_reported(tmp_path):
    result = _audit(
        tmp_path,
        [
            _row("https://example.com/guide"),
            _row("https://example.com/guide/", status=301, redirect="https://example.com/guide"),
        ],
    )
    assert "URL_TRAILING_SLASH_INCONSISTENT" not in {issue.check for issue in result.issues}


def test_case_sensitive_siblings_are_not_folded_into_a_slash_pair(tmp_path):
    result = _audit(
        tmp_path,
        [_row("https://example.com/Guide"), _row("https://example.com/guide/")],
    )
    assert "URL_TRAILING_SLASH_INCONSISTENT" not in {issue.check for issue in result.issues}


def test_slash_pair_preserves_a_raw_non_string_canonical_value():
    """The report payload remains faithful even when the source field is malformed."""

    class Context:
        def __init__(self):
            self.pages = [
                Page(
                    url="https://example.com/guide",
                    status_code=200,
                    indexability="Indexable",
                    metrics={"_record": {"canonical": 17}},
                ),
                Page(
                    url="https://example.com/guide/",
                    status_code=200,
                    indexability="Indexable",
                    metrics={"_record": {"canonical": "https://example.com/guide/"}},
                ),
            ]
            self.issues = []

        def indexable_html_pages(self):
            return ()

        def add(self, check, **kwargs):
            self.issues.append((check, kwargs))

        def skip(self, _check, _reason):
            pass

    ctx = Context()
    check_url_hygiene(ctx)
    assert ctx.issues == [
        (
            "URL_TRAILING_SLASH_INCONSISTENT",
            {
                "target_url": "https://example.com/guide",
                "occurrences_count": 2,
                "details": {
                    "observed_variants": [
                        "https://example.com/guide",
                        "https://example.com/guide/",
                    ],
                    "status_codes": {
                        "https://example.com/guide": 200,
                        "https://example.com/guide/": 200,
                    },
                    "canonical": {
                        "https://example.com/guide": 17,
                        "https://example.com/guide/": "https://example.com/guide/",
                    },
                },
            },
        )
    ]


def test_disk_backed_slash_pair_scan_keeps_page_references_bounded(monkeypatch):
    """#817: slash-pair facts must not pin a disk-backed page population in memory."""

    created: list[weakref.ReferenceType[Page]] = []
    original_page = context_module.Page

    class TrackedPage(original_page):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(weakref.ref(self))

    monkeypatch.setattr(context_module, "Page", TrackedPage)
    peaks = []
    for pair_count in (512, 1024, 2048):
        records = [
            {
                "Address": f"https://example.test/guide-{index}{suffix}",
                "Content Type": "text/html",
                "Status Code": 200,
                "Status": "OK",
                "Indexability": "Indexable",
                "Canonical Link Element 1": f"https://example.test/guide-{index}{suffix}",
                "Redirect URL": "",
            }
            for index in range(pair_count)
            for suffix in ("", "/")
        ]
        ctx = AuditContext(
            LoadedExports(frames={"internal_all": pd.DataFrame(records)}),
            load_config(None),
            disk_backed_pages=True,
        )
        assert ctx._disk_pages is not None
        ctx._disk_pages._cache_limit = 1
        captured_live_counts = []
        add = ctx.add

        def record_first_issue(check, *, _captured=captured_live_counts, _add=add, **kwargs):
            if not _captured:
                gc.collect()
                _captured.append(sum(page() is not None for page in created))
            return _add(check, **kwargs)

        ctx.add = record_first_issue
        tracemalloc.start()
        try:
            check_url_hygiene(ctx)
            _current, peak = tracemalloc.get_traced_memory()
            peaks.append(peak)
            assert len(ctx.issues) == pair_count
            assert captured_live_counts and captured_live_counts[0] <= 3
        finally:
            tracemalloc.stop()
            ctx.close()
            del ctx, records
            gc.collect()
    assert peaks[1] < peaks[0] * 3
    assert peaks[2] < peaks[1] * 3


def test_literal_template_markers_are_reported_but_ordinary_copy_is_not(tmp_path):
    result = _audit(
        tmp_path,
        [
            _row("https://example.com/draft", title="{{TODO}} final title"),
            _row("https://example.com/roadmap", title="Coming soon: our product roadmap"),
        ],
    )
    finding = next(issue for issue in result.issues if issue.check == "PLACEHOLDER_MARKER")
    assert finding.target_url == "https://example.com/draft"
    assert finding.details == {
        "observed": [{"field": "title", "markers": ["{{TODO}}"], "value": "{{TODO}} final title"}]
    }
    assert "PLACEHOLDER_MARKER" not in {
        issue.check for issue in result.issues if issue.target_url == "https://example.com/roadmap"
    }
