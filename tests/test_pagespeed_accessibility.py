from seohead.data_sources import pagespeed


def test_accessibility_sample_keeps_lighthouse_scope_and_failed_audits():
    sample = pagespeed._parse(
        {
            "lighthouseResult": {
                "finalUrl": "https://example.com/final",
                "lighthouseVersion": "12.1.0",
                "categories": {"accessibility": {"score": 0.82, "title": "Accessibility"}},
                "audits": {
                    "color-contrast": {
                        "score": 0,
                        "displayValue": "2 elements",
                        "scoreDisplayMode": "binary",
                    },
                    "aria-valid-attr": {"score": None, "scoreDisplayMode": "binary"},
                    "manual-check": {"score": None, "scoreDisplayMode": "manual"},
                    "passed": {"score": 1, "scoreDisplayMode": "binary"},
                },
            }
        },
        "https://example.com/",
        "mobile",
    )

    result = sample["accessibility_sample"]
    assert result["scope"] == {
        "url": "https://example.com/",
        "final_url": "https://example.com/final",
        "strategy": "mobile",
        "provider": "pagespeed",
        "engine": "Lighthouse",
        "version": "12.1.0",
    }
    assert result["category"]["score"] == 0.82
    assert result["findings"] == [
        {
            "id": "color-contrast",
            "state": "failed",
            "score": 0,
            "display_value": "2 elements",
            "score_display_mode": "binary",
        },
        {
            "id": "aria-valid-attr",
            "state": "incomplete",
            "score": None,
            "display_value": None,
            "score_display_mode": "binary",
        },
    ]
    assert "not a WCAG conformance" in result["note"]


def test_accessibility_sample_names_missing_category_as_unavailable():
    result = pagespeed.accessibility_sample({"url": "https://example.com/", "strategy": "desktop"})
    assert result["state"] == "unavailable"
    assert result["scope"] == {"url": "https://example.com/", "strategy": "desktop"}
