"""Recorded URL hygiene facts: session values stay redacted and slash pairs need evidence."""

import csv

from seohead.sf.core.audit import run_audit

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
    url: str, *, status: int = 200, canonical: str = "", redirect: str = "", title: str = "A useful title for the page"
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
