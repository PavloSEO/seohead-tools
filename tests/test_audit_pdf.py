from __future__ import annotations

import pytest

from seohead.reports.audit_pdf import render_audit_pdf_html


def _model(*, state: str = "partial", findings: list[dict] | None = None) -> dict:
    rows = findings or [
        {
            "source_ref": {
                "collection": "/findings",
                "index": 0,
                "id_if_present": "finding-example-1",
            },
            "record": {
                "severity": "critical",
                "url": "https://example.invalid/каталог/",
                "status_code": 404,
                "occurrences_count": 2,
                "fix_hint": "Restore the missing destination.",
                "details": {"anchor": "Каталог <ссылок>"},
                "locations": [{"source_url": "https://example.invalid/", "anchor": "Каталог"}],
                "remediation_status": "open",
            },
            "display": {
                "check_key": "BROKEN_INTERNAL_LINK",
                "title": "Broken internal link",
                "observation": "The saved audit recorded a missing destination.",
                "reproduction": "The synthetic URL returned HTTP 404.",
            },
        }
    ]
    return {
        "schema": "seohead.technical-audit-pdf/1",
        "source": {
            "kind": "site-audit",
            "schema": "seohead.site-audit/1",
            "domain": "example.invalid",
            "url": "https://example.invalid/",
            "generated_at": "2026-10-03T12:00:00Z",
            "run_id": "synthetic-run-1",
            "diagnostics": [],
        },
        "run": {
            "state": state,
            "scope": {"urls_crawled": 12, "scope_reason": "Synthetic sample"},
            "reasons": ["Synthetic stop after the configured page limit."],
        },
        "summary": {
            "source": {
                "pages_checked": 12,
                "findings_total": len(rows),
                "findings_by_severity": {"critical": 1, "warning": 0, "notice": 0},
            },
            "counts": {
                "findings": {
                    "source_count": len(rows),
                    "declared_count": len(rows),
                    "projected_count": len(rows),
                },
                "pages": {"source_count": 2, "declared_count": 2, "projected_count": 2},
                "checks": {
                    "source_total": 4,
                    "projected_count": 4,
                    "ran": 2,
                    "failed": 1,
                    "skipped": 1,
                    "disabled": 0,
                },
                "backlog": {"source_count": 1, "declared_count": 1, "projected_count": 1},
            },
        },
        "coverage": {
            "state": "partial",
            "source_evidence": [
                {
                    "kind": "population",
                    "state": "partial",
                    "record": {"reason": "Only 12 synthetic URLs."},
                }
            ],
            "source_check_coverage": {"checks_available": 4, "checks_skipped": 1},
            "groups": [
                {"name": "Synthetic group", "state": "measured", "reason": "Synthetic evidence"}
            ],
            "checks": [
                {"state": "failed", "reason": "Synthetic timeout", "record": {"id": "CHECK_A"}},
                {
                    "state": "skipped",
                    "reason": "Synthetic input unavailable",
                    "record": {"id": "CHECK_B"},
                },
            ],
        },
        "findings": rows,
        "pages": [
            {
                "source_ref": {"collection": "/pages", "index": 0},
                "record": {
                    "url": "https://example.invalid/",
                    "status": 200,
                    "title": "Synthetic home",
                },
            },
            {
                "source_ref": {"collection": "/pages", "index": 1},
                "record": {
                    "url": "https://example.invalid/last/",
                    "status": 200,
                    "title": "Final synthetic page",
                },
            },
        ],
        "backlog": {
            "state": "recorded",
            "source_count": 1,
            "declared_count": 1,
            "projected_count": 1,
            "project": "Synthetic project",
            "items": [
                {
                    "id": "task-1",
                    "title": "Review the sample page",
                    "state": "not_run",
                    "verification_status": "not_requested",
                }
            ],
        },
        "omissions": [],
    }


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("en", "Technical SEO audit"), ("ru", "Технический SEO-аудит")],
)
def test_renders_localized_audit_report_with_neutral_branding(lang, expected):
    html = render_audit_pdf_html(
        _model(), lang=lang, brand={"accent": "#1565C0", "name": "Demo label"}
    )

    assert f'<html lang="{lang}">' in html
    assert f"<h1>{expected}</h1>" in html
    assert "Demo label" in html
    assert "13.333in 7.5in" in html
    assert "counter(page)" in html
    assert "<svg" in html
    if lang == "ru":
        assert "Аудит сайта" in html
        assert "Критические" in html
        assert "Число повторений" in html
        assert "Проверка исправления" in html
        assert (
            "\u041d\u0435 \u0437\u0430\u043f\u0443\u0441\u043a\u0430\u043b\u043e\u0441\u044c"
            in html
        )
        assert "not_run" not in html


def test_partial_scope_and_each_coverage_state_are_visible():
    html = render_audit_pdf_html(_model())

    assert "This audit is partial" in html
    assert "Synthetic stop after the configured page limit." in html
    assert "Synthetic timeout" in html
    assert "Synthetic input unavailable" in html
    assert "Only 12 synthetic URLs." in html
    assert "source check coverage" in html
    assert "Synthetic group" in html
    assert "checks_available" in html and "checks_skipped" in html


def test_coverage_reported_does_not_mean_every_check_passed():
    model = _model()
    model["coverage"]["state"] = "reported"
    html = render_audit_pdf_html(model)

    assert "Coverage metadata recorded; individual checks may still be unavailable." in html
    assert "Synthetic timeout" in html
    assert "Synthetic input unavailable" in html


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (
            "en",
            "Completed within the recorded scope; this does not establish exhaustive site coverage.",
        ),
        (
            "ru",
            "Завершён в пределах записанного объёма; это не подтверждает полный обход сайта.",
        ),
    ],
)
def test_complete_run_status_does_not_claim_full_site_coverage(lang, expected):
    model = _model(state="complete")
    model["run"]["scope"]["operation"] = "bounded_site_audit"
    model["run"]["reasons"] = []
    html = render_audit_pdf_html(model, lang=lang)

    assert expected in html
    assert "Bounded site audit" in html if lang == "en" else "Ограниченный аудит сайта" in html


def test_unknown_run_and_absent_counts_remain_unknown():
    model = _model(state="unknown")
    model["summary"]["counts"]["pages"]["projected_count"] = None
    model["summary"]["counts"]["checks"]["ran"] = None
    model["summary"]["counts"]["checks"]["failed"] = None
    model["summary"]["counts"]["checks"]["skipped"] = None
    model["summary"]["counts"]["checks"]["disabled"] = None
    html = render_audit_pdf_html(model)

    assert 'data-state="unknown"' in html
    assert "The source does not establish whether this audit completed." in html
    assert "Not reported" in html
    assert "Check-state counts were not supplied by the source audit." in html


def test_render_escapes_source_text_and_preserves_raw_evidence_fields():
    model = _model()
    model["findings"][0]["record"]["details"] = {"payload": "<script>alert(1)</script>"}
    html = render_audit_pdf_html(model)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "occurrences_count" not in html
    assert "Occurrences count" in html
    assert "https://example.invalid/каталог/" in html
    assert "https://example.invalid/last/" in html


def test_large_synthetic_findings_keep_first_and_last_records_without_caps():
    findings = [
        {
            "source_ref": {
                "collection": "/findings",
                "index": index,
                "id_if_present": f"sample-{index:04d}",
            },
            "record": {
                "severity": "warning",
                "url": f"https://example.invalid/sample-{index:04d}/",
            },
            "display": {"title": f"Synthetic finding {index:04d}"},
        }
        for index in range(150)
    ]
    html = render_audit_pdf_html(_model(findings=findings))

    assert html.count('class="finding-card severity-warning"') == 150
    assert "Synthetic finding 0000" in html
    assert "Synthetic finding 0149" in html
    assert "sample-0149" in html


@pytest.mark.parametrize("model", [{}, {"schema": "seohead.technical-audit-pdf/2"}])
def test_rejects_unrecognized_model_schema(model):
    with pytest.raises(ValueError, match=r"seohead\.technical-audit-pdf/1"):
        render_audit_pdf_html(model)


def test_rejects_unsupported_language():
    with pytest.raises(ValueError, match="unsupported report language"):
        render_audit_pdf_html(_model(), lang="fr")
