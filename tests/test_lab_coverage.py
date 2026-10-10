"""Unused CSS/JS from coverage payloads — offline, synthetic payloads only."""

from __future__ import annotations

from seohead.checks.lab_coverage import analyze_css_coverage, analyze_js_coverage


def _js_entry(url: str, source: str, functions: list[dict]) -> dict:
    return {"url": url, "source": source, "functions": functions}


def test_js_inner_range_that_never_ran_is_unused():
    entry = _js_entry(
        "https://example.test/app.js",
        "x" * 100,
        [
            {"functionName": "", "ranges": [{"startOffset": 0, "endOffset": 100, "count": 1}]},
            {"functionName": "f", "ranges": [{"startOffset": 20, "endOffset": 60, "count": 0}]},
        ],
    )
    result = analyze_js_coverage([entry])
    assert result["resources"] == [
        {
            "url": "https://example.test/app.js",
            "total_chars": 100,
            "unused_chars": 40,
            "unused_ratio": 0.4,
        }
    ]
    assert result["findings"] == []
    assert result["skipped"] == []


def test_js_uncalled_function_is_fully_unused():
    entry = _js_entry(
        "https://example.test/cold.js",
        "y" * 50,
        [{"functionName": "cold", "ranges": [{"startOffset": 0, "endOffset": 50, "count": 0}]}],
    )
    result = analyze_js_coverage([entry])
    assert result["resources"][0]["unused_chars"] == 50
    assert result["resources"][0]["unused_ratio"] == 1.0


def test_js_inner_range_that_ran_overrides_outer_unused_range():
    entry = _js_entry(
        "https://example.test/nested.js",
        "z" * 100,
        [
            {"functionName": "", "ranges": [{"startOffset": 0, "endOffset": 100, "count": 0}]},
            {"functionName": "hot", "ranges": [{"startOffset": 10, "endOffset": 20, "count": 3}]},
        ],
    )
    result = analyze_js_coverage([entry])
    assert result["resources"][0]["unused_chars"] == 90


def test_js_finding_needs_both_ratio_and_size():
    entry = _js_entry(
        "https://example.test/app.js",
        "x" * 100,
        [
            {"functionName": "", "ranges": [{"startOffset": 0, "endOffset": 100, "count": 1}]},
            {"functionName": "f", "ranges": [{"startOffset": 20, "endOffset": 60, "count": 0}]},
        ],
    )
    # Default size floor keeps a small dead block out of the findings.
    assert analyze_js_coverage([entry])["findings"] == []
    flagged = analyze_js_coverage([entry], ratio_threshold=0.3, min_chars=30)
    assert len(flagged["findings"]) == 1
    assert "https://example.test/app.js" in flagged["findings"][0]
    assert "40%" in flagged["findings"][0]


def test_js_entries_without_source_are_skipped_with_reason():
    result = analyze_js_coverage(
        [
            {"url": "https://example.test/no-source.js", "functions": []},
            {"url": "https://example.test/empty.js", "source": "", "functions": []},
        ]
    )
    assert result["resources"] == []
    assert [item["url"] for item in result["skipped"]] == [
        "https://example.test/no-source.js",
        "https://example.test/empty.js",
    ]


def test_css_ranges_are_used_and_complement_is_unused():
    entry = {
        "url": "https://example.test/site.css",
        "text": "a" * 100,
        "ranges": [{"start": 0, "end": 30}, {"start": 20, "end": 50}],
    }
    result = analyze_css_coverage([entry])
    assert result["resources"][0]["unused_chars"] == 50
    assert result["resources"][0]["unused_ratio"] == 0.5


def test_css_ranges_outside_the_text_are_clamped():
    entry = {
        "url": "https://example.test/s.css",
        "text": "b" * 100,
        "ranges": [{"start": 90, "end": 500}],
    }
    result = analyze_css_coverage([entry])
    assert result["resources"][0]["unused_chars"] == 90


def test_css_finding_reported_when_thresholds_met():
    entry = {
        "url": "https://example.test/s.css",
        "text": "c" * 100,
        "ranges": [{"start": 0, "end": 50}],
    }
    assert analyze_css_coverage([entry])["findings"] == []
    flagged = analyze_css_coverage([entry], min_chars=10)
    assert flagged["findings"] == [
        "https://example.test/s.css is 50% unused (50 of 100 characters never used)"
    ]


def test_css_entries_without_text_are_skipped():
    result = analyze_css_coverage([{"url": "https://example.test/x.css", "ranges": []}])
    assert result["resources"] == []
    assert result["skipped"] == [
        {"url": "https://example.test/x.css", "reason": "coverage payload has no source text"}
    ]


def test_totals_sum_across_resources():
    result = analyze_css_coverage(
        [
            {"url": "https://example.test/a.css", "text": "a" * 10, "ranges": []},
            {
                "url": "https://example.test/b.css",
                "text": "b" * 30,
                "ranges": [{"start": 0, "end": 10}],
            },
        ]
    )
    assert result["total_chars"] == 40
    assert result["unused_chars"] == 30
