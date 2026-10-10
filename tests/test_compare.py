"""Crawl-to-crawl comparison: fixed vs merely-not-recrawled must not look alike.

The distinction between "left" and "disappeared" is the entire value of this
module. A naive diff of two finding sets cannot make it; this one can because
it also looks at which URLs were actually crawled each time.
"""

import json

import pytest

from seohead.mcp.handlers import compare_crawls
from seohead.sf.core.compare import CompareError, compare, preflight


def _audit(urls, issues, **run):
    return {
        "run": {"generated_at": "t", **run},
        "pages": [{"url": u} for u in urls],
        "issues": [{"check": c, "target_url": u} for c, u in issues],
    }


def test_a_fixed_page_lands_in_left_not_disappeared():
    """Still crawled, no longer matching — a real fix."""
    before = _audit(["https://e.com/a"], [("BROKEN", "https://e.com/a")])
    after = _audit(["https://e.com/a"], [])
    result = compare(before, after)
    assert [i["target_url"] for i in result["left"]] == ["https://e.com/a"]
    assert result["disappeared"] == []


def test_an_uncrawled_page_lands_in_disappeared_not_left():
    """Not in this crawl at all — the fix is unproven, not achieved."""
    before = _audit(["https://e.com/a"], [("BROKEN", "https://e.com/a")])
    after = _audit(["https://e.com/b"], [])  # a was never re-crawled
    result = compare(before, after)
    assert result["left"] == []
    assert [i["target_url"] for i in result["disappeared"]] == ["https://e.com/a"]


def test_a_genuinely_new_page_with_a_finding_is_appeared_not_entered():
    before = _audit(["https://e.com/a"], [])
    after = _audit(["https://e.com/a", "https://e.com/new"], [("BROKEN", "https://e.com/new")])
    result = compare(before, after)
    assert [i["target_url"] for i in result["appeared"]] == ["https://e.com/new"]
    assert result["entered"] == []


def test_a_new_finding_on_a_previously_crawled_page_is_entered():
    before = _audit(["https://e.com/a"], [])
    after = _audit(["https://e.com/a"], [("BROKEN", "https://e.com/a")])
    result = compare(before, after)
    assert [i["target_url"] for i in result["entered"]] == ["https://e.com/a"]
    assert result["appeared"] == []


def test_an_unchanged_finding_appears_in_no_bucket():
    before = _audit(["https://e.com/a"], [("BROKEN", "https://e.com/a")])
    after = _audit(["https://e.com/a"], [("BROKEN", "https://e.com/a")])
    result = compare(before, after)
    assert result["summary"] == {
        "entered": 0,
        "left": 0,
        "appeared": 0,
        "disappeared": 0,
        "by_check": {},
    }


def test_the_four_sets_are_disjoint_and_exhaustive():
    before = _audit(
        ["https://e.com/a", "https://e.com/b", "https://e.com/c"],
        [("X", "https://e.com/a"), ("X", "https://e.com/b")],
    )
    after = _audit(
        ["https://e.com/a", "https://e.com/c", "https://e.com/d"],
        [("X", "https://e.com/c"), ("X", "https://e.com/d")],
    )
    result = compare(before, after)
    all_urls = [
        i["target_url"]
        for bucket in ("entered", "left", "appeared", "disappeared")
        for i in result[bucket]
    ]
    assert sorted(all_urls) == [
        "https://e.com/a",
        "https://e.com/b",
        "https://e.com/c",
        "https://e.com/d",
    ]
    assert len(all_urls) == len(set(all_urls))  # disjoint


def test_by_check_summary_matches_the_bucket_contents():
    before = _audit(["https://e.com/a"], [("BROKEN", "https://e.com/a")])
    after = _audit(["https://e.com/a"], [])
    result = compare(before, after)
    assert result["summary"]["by_check"]["BROKEN"]["left"] == 1


def test_output_is_deterministic_regardless_of_input_dict_order():
    before = _audit(["https://e.com/b", "https://e.com/a"], [("X", "https://e.com/b")])
    after = _audit(["https://e.com/a", "https://e.com/b"], [("X", "https://e.com/a")])
    r1 = compare(before, after)
    r2 = compare(before, after)
    assert r1["entered"] == r2["entered"]
    assert [i["target_url"] for i in r1["entered"]] == ["https://e.com/a"]


# ── preflight warnings ────────────────────────────────────────────────────


def test_a_partial_before_crawl_warns_about_appeared_findings():
    """Issue #212: a truncated baseline poisons "appeared", not "disappeared" —
    a URL it never reached looks brand new to it, not gone from the current
    crawl."""
    before = _audit(["https://e.com/a"], [("X", "https://e.com/a")], crawl_partial=True)
    after = _audit([], [])
    warnings = preflight(before, after)
    assert any("appeared" in w and "before" in w for w in warnings)


def test_a_partial_after_crawl_warns_about_disappeared_findings():
    before = _audit(["https://e.com/a"], [])
    after = _audit([], [], crawl_partial=True)
    warnings = preflight(before, after)
    assert any("disappeared" in w and "after" in w for w in warnings)


def test_an_invalid_crawl_warns_plainly():
    before = _audit([], [], crawl_valid=False)
    after = _audit(["https://e.com/a"], [])
    warnings = preflight(before, after)
    assert any("before" in w and "invalid" in w for w in warnings)


def test_differing_results_affecting_config_is_flagged_by_name():
    before = _audit(["https://e.com/a"], [], crawl_config={"robots.policy": "respect"})
    after = _audit(["https://e.com/a"], [], crawl_config={"robots.policy": "ignore"})
    warnings = preflight(before, after)
    assert any("robots.policy" in w for w in warnings)


def test_differing_results_affecting_config_is_refused_unless_forced():
    """A configuration change is not a site change, so it needs an explicit override."""
    before = _audit(["https://e.com/a"], [], crawl_config={"robots.policy": "respect"})
    after = _audit(["https://e.com/a"], [], crawl_config={"robots.policy": "ignore"})

    with pytest.raises(CompareError, match=r"robots\.policy"):
        compare(before, after)

    forced = compare(before, after, force=True)
    assert any("robots.policy" in warning for warning in forced["warnings"])


def test_identical_config_produces_no_config_warning():
    cfg = {"robots.policy": "respect", "limits.max_urls": 200}
    before = _audit(["https://e.com/a"], [], crawl_config=cfg)
    after = _audit(["https://e.com/a"], [], crawl_config=dict(cfg))
    assert preflight(before, after) == []


def test_no_config_present_on_either_side_warns_unknown_comparability():
    """#287: a missing manifest is an unknown comparison basis, not an established match --
    the same distinction crawl_partial already draws. Silently treating "neither side
    recorded a config" as "therefore they match" is exactly the bug: it let a native crawl
    and an SF audit, or two runs under different settings, compare as if nothing differed."""
    before = _audit(["https://e.com/a"], [])
    after = _audit(["https://e.com/a"], [])
    warnings = preflight(before, after)
    assert any("no crawl configuration" in w for w in warnings)


def test_one_side_missing_crawl_config_warns_by_name():
    """#287's reported case: a native crawl with a recorded manifest compared against a
    run that has none (an SF audit, or a crawl_config write that never happened) must not
    look like a match just because only one side has anything to compare."""
    before = _audit(
        ["https://e.com/a"],
        [("TITLE_TOO_SHORT", "https://e.com/a")],
        crawl_config={"rendering.mode": "raw"},
    )
    after = _audit(["https://e.com/a"], [])
    warnings = preflight(before, after)
    assert any("after" in w and "no crawl configuration" in w for w in warnings)


def test_differing_sf_profiles_warn_even_without_a_full_manifest():
    """#287: SF audits never record crawl_config, but they do record their export
    profile -- a known, if partial, signal that check coverage differed between runs."""
    before = _audit(["https://e.com/a"], [("TITLE_TOO_SHORT", "https://e.com/a")], profile="full")
    after = _audit(["https://e.com/a"], [], profile="lite")
    warnings = preflight(before, after)
    assert any("profile" in w and "full" in w and "lite" in w for w in warnings)


def test_identical_profile_does_not_add_a_profile_warning():
    before = _audit(["https://e.com/a"], [], profile="full")
    after = _audit(["https://e.com/a"], [], profile="full")
    warnings = preflight(before, after)
    assert not any("profile" in w for w in warnings)


def test_warnings_are_included_in_the_compare_result():
    before = _audit([], [], crawl_valid=False)
    after = _audit(["https://e.com/a"], [])
    result = compare(before, after)
    assert result["warnings"]


# ── refusal ─────────────────────────────────────────────────────────────


def test_a_document_missing_pages_is_refused_by_name():
    before = {"run": {}, "issues": []}
    after = _audit(["https://e.com/a"], [])
    with pytest.raises(CompareError, match="before"):
        compare(before, after)


def test_a_document_missing_issues_is_refused_by_name():
    before = _audit(["https://e.com/a"], [])
    after = {"run": {}, "pages": []}
    with pytest.raises(CompareError, match="after"):
        compare(before, after)


# ── partial baseline (issue #212) ────────────────────────────────────────
#
# A truncated baseline cannot prove a URL it never reached is genuinely new
# -- only that it wasn't seen. The whole value of compare mode is telling a
# fix apart from a deletion; letting a partial baseline manufacture false
# "appeared" findings breaks exactly that.


def test_a_partial_baseline_does_not_report_a_preexisting_url_as_appeared():
    before = _audit(["https://e.com/home"], [], crawl_partial=True)
    after = _audit(
        ["https://e.com/home", "https://e.com/preexisting-404"],
        [("BROKEN_PAGE_4XX", "https://e.com/preexisting-404")],
    )
    result = compare(before, after)
    assert result["appeared"] == []
    assert [i["target_url"] for i in result["entered"]] == ["https://e.com/preexisting-404"]


def test_a_partial_baseline_warns_about_appeared_not_disappeared():
    before = _audit(["https://e.com/home"], [], crawl_partial=True)
    after = _audit(["https://e.com/home"], [])
    warnings = preflight(before, after)
    assert any("appeared" in w and "before" in w for w in warnings)
    assert not any("disappeared" in w and "before" in w for w in warnings)


def test_a_full_baseline_still_reports_a_genuinely_new_url_as_appeared():
    """crawl_partial absent (a complete baseline) keeps the useful signal."""
    before = _audit(["https://e.com/home"], [])
    after = _audit(["https://e.com/home", "https://e.com/new"], [("BROKEN", "https://e.com/new")])
    result = compare(before, after)
    assert [i["target_url"] for i in result["appeared"]] == ["https://e.com/new"]


# ── partial after crawl (issue #458) ─────────────────────────────────────
#
# Symmetric to the partial-baseline cases above: a partial after crawl cannot
# prove a before-only URL is genuinely gone, only that it was not reached.


def test_a_partial_after_crawl_does_not_report_an_unreached_url_as_disappeared():
    before = _audit(
        ["https://x.com/a", "https://x.com/b"],
        [("MISSING_TITLE", "https://x.com/a")],
    )
    after = _audit(["https://x.com/b"], [], crawl_partial=True)  # /a never reached this run
    result = compare(before, after)
    assert result["disappeared"] == []
    assert [i["target_url"] for i in result["left"]] == ["https://x.com/a"]


def test_a_full_after_crawl_still_reports_a_genuinely_gone_url_as_disappeared():
    """crawl_partial absent (a complete after crawl) keeps the useful signal."""
    before = _audit(
        ["https://x.com/a", "https://x.com/b"],
        [("MISSING_TITLE", "https://x.com/a")],
    )
    after = _audit(["https://x.com/b"], [])  # full crawl, /a genuinely gone
    result = compare(before, after)
    assert [i["target_url"] for i in result["disappeared"]] == ["https://x.com/a"]
    assert result["left"] == []


def test_a_partial_after_crawl_still_reports_a_reached_url_as_left():
    """The URL IS present in the after crawl — not the unproven case."""
    before = _audit(
        ["https://x.com/a", "https://x.com/b"],
        [("MISSING_TITLE", "https://x.com/a")],
    )
    after = _audit(["https://x.com/a", "https://x.com/b"], [], crawl_partial=True)
    result = compare(before, after)
    assert [i["target_url"] for i in result["left"]] == ["https://x.com/a"]
    assert result["disappeared"] == []


# ── audit-wide findings (issue #213) ─────────────────────────────────────
#
# A finding with no target_url (e.g. TITLE_TEMPLATED) describes the crawl as
# a whole, not a page — it must still participate in the delta instead of
# being silently dropped, but it cannot appear/disappear since there is no
# page whose presence changed.


def _global_audit(urls, checks):
    return {
        "run": {},
        "pages": [{"url": u} for u in urls],
        "issues": [{"check": c, "target_url": None} for c in checks],
    }


def test_a_new_audit_wide_finding_is_entered_not_dropped():
    before = _global_audit(["https://e.com/a"], [])
    after = _global_audit(["https://e.com/a"], ["TITLE_TEMPLATED"])
    result = compare(before, after)
    assert result["summary"]["entered"] == 1
    assert [i["check"] for i in result["entered"]] == ["TITLE_TEMPLATED"]
    assert result["summary"]["by_check"]["TITLE_TEMPLATED"]["entered"] == 1


def test_a_resolved_audit_wide_finding_is_left_not_dropped():
    before = _global_audit(["https://e.com/a"], ["TITLE_TEMPLATED"])
    after = _global_audit(["https://e.com/a"], [])
    result = compare(before, after)
    assert result["summary"]["left"] == 1
    assert [i["check"] for i in result["left"]] == ["TITLE_TEMPLATED"]


def test_an_audit_wide_finding_never_lands_in_appeared_or_disappeared():
    before = _global_audit(["https://e.com/a"], [])
    after = _global_audit(["https://e.com/a", "https://e.com/b"], ["TITLE_TEMPLATED"])
    result = compare(before, after)
    assert result["appeared"] == []
    assert result["disappeared"] == []


# ── declared release correspondence (issue #877) ────────────────────────


def _correspondence(*, origins=None, pairs=None):
    return {
        "schema_version": "url-correspondence.v1",
        "origin_map": origins or {},
        "pairs": pairs or [],
    }


def _page(url, **metrics):
    return {
        "url": url,
        "status_code": 200,
        "metrics": {
            "title": "Before title",
            "meta_description": "Before description",
            "h1": ["Before H1"],
            "canonical": url,
            "meta_robots": "index,follow",
            "x_robots": None,
            **metrics,
        },
    }


def test_exact_comparison_remains_the_default_without_a_correspondence():
    before = _audit(["https://before.test/old"], [("X", "https://before.test/old")])
    after = _audit(["https://after.test/new"], [("X", "https://after.test/new")])

    result = compare(before, after)

    assert result["summary"] == {
        "entered": 0,
        "left": 0,
        "appeared": 1,
        "disappeared": 1,
        "by_check": {"X": {"entered": 0, "left": 0, "appeared": 1, "disappeared": 1}},
    }
    assert "release_review" not in result


def test_declared_origin_map_compares_migrated_findings_and_emits_review_facts():
    before_url = "https://before.test/a%2Fb?view=full"
    after_url = "https://after.test/a%2Fb?view=full"
    before = {
        "run": {"generated_at": "before"},
        "pages": [_page(before_url)],
        "issues": [{"check": "X", "target_url": before_url}],
    }
    after = {
        "run": {"generated_at": "after"},
        "pages": [
            _page(
                after_url,
                title="After title",
                meta_description="After description",
                h1=["After H1"],
                canonical=after_url,
                meta_robots="noindex",
            )
        ],
        "issues": [],
    }

    result = compare(
        before,
        after,
        correspondence=_correspondence(origins={"https://before.test": "https://after.test"}),
    )

    assert [issue["target_url"] for issue in result["left"]] == [before_url]
    assert result["disappeared"] == []
    review = result["release_review"]
    assert review["schema_version"] == "release_review.v1"
    assert review["correspondence"]["origin_map"] == {"https://before.test": "https://after.test"}
    pair = review["correspondence"]["pairs"][0]
    assert pair["before_url"] == before_url
    assert pair["after_url"] == after_url
    assert pair["host_changed"] is True
    facts = review["facts"][0]
    assert facts["correspondence_key"] == after_url
    assert facts["before"]["title"] == {
        "value": "Before title",
        "state": "measured",
        "source": "pages.metrics.title",
        "coverage": "recorded",
    }
    assert set(facts["changed"]) == {"title", "description", "h1", "canonical", "robots"}
    assert facts["after"]["robots"]["value"]["meta"] == "noindex"
    assert review["findings"]["left"] == result["left"]


def test_explicit_pair_can_declare_a_path_change_without_title_inference():
    before_url = "https://before.test/legacy"
    after_url = "https://after.test/replacement"
    before = _audit([before_url], [("X", before_url)])
    after = _audit([after_url], [("X", after_url)])

    result = compare(
        before,
        after,
        correspondence=_correspondence(pairs=[{"before": before_url, "after": after_url}]),
    )

    assert result["summary"] == {
        "entered": 0,
        "left": 0,
        "appeared": 0,
        "disappeared": 0,
        "by_check": {},
    }
    assert result["release_review"]["correspondence"]["pairs"][0]["kind"] == "explicit_pair"


def test_explicit_pairs_override_an_origin_map_and_collisions_are_refused():
    before = _audit(["https://before.test/a"], [])
    after = _audit(["https://after.test/other"], [])
    with pytest.raises(CompareError, match="closed schema"):
        compare(before, after, correspondence={**_correspondence(), "title_match": True})
    override = compare(
        before,
        after,
        correspondence=_correspondence(
            origins={"https://before.test": "https://after.test"},
            pairs=[{"before": "https://before.test/a", "after": "https://after.test/other"}],
        ),
    )
    assert override["release_review"]["correspondence"]["pairs"][0]["kind"] == "explicit_pair"
    collision_before = _audit(["https://one.test/a", "https://two.test/a"], [])
    with pytest.raises(CompareError, match=r"both .* crawled page"):
        compare(
            collision_before,
            _audit(["https://after.test/a"], []),
            correspondence=_correspondence(
                origins={
                    "https://one.test": "https://after.test",
                    "https://two.test": "https://after.test",
                }
            ),
        )
    with pytest.raises(CompareError, match=r"maps 'https://before\.test/a' more than once"):
        compare(
            before,
            after,
            correspondence=_correspondence(
                pairs=[
                    {"before": "https://before.test/a", "after": "https://after.test/other"},
                    {"before": "https://before.test/a", "after": "https://after.test/another"},
                ]
            ),
        )


def test_unmatched_declared_pair_stays_visible_and_missing_facts_are_not_clean():
    before_url = "https://before.test/old"
    after_url = "https://after.test/new"
    result = compare(
        _audit([], []),
        _audit([], []),
        correspondence=_correspondence(pairs=[{"before": before_url, "after": after_url}]),
    )

    pair = result["release_review"]["correspondence"]["pairs"][0]
    assert pair["state"] == "before_not_crawled"
    assert result["release_review"]["facts"] == []


def test_shared_handler_reads_the_declared_correspondence_file(tmp_path):
    before_url = "https://before.test/old"
    after_url = "https://after.test/new"
    declaration = tmp_path / "url-correspondence.json"
    declaration.write_text(
        json.dumps(_correspondence(pairs=[{"before": before_url, "after": after_url}])),
        encoding="utf-8",
    )

    result = compare_crawls(
        before=_audit([before_url], [("X", before_url)]),
        after=_audit([after_url], []),
        correspondence=str(declaration),
    )

    assert result["summary"]["left"] == 1
    assert result["release_review"]["correspondence"]["pairs"][0]["state"] == "matched"


def test_correspondence_file_rejects_duplicate_json_keys(tmp_path):
    declaration = tmp_path / "url-correspondence.json"
    declaration.write_text(
        '{"schema_version":"url-correspondence.v1","origin_map":{},"pairs":[],"pairs":[]}',
        encoding="utf-8",
    )

    with pytest.raises(CompareError, match="repeats object key"):
        compare(_audit([], []), _audit([], []), correspondence=str(declaration))


def test_url_sets_list_pages_present_on_one_side_only():
    before = _audit(["https://e.com/a", "https://e.com/b"], [])
    after = _audit(["https://e.com/b", "https://e.com/c"], [])
    url_sets = compare(before, after)["url_sets"]
    assert url_sets["only_in_before"] == ["https://e.com/a"]
    assert url_sets["only_in_after"] == ["https://e.com/c"]
    assert url_sets["counts"] == {"only_in_before": 1, "only_in_after": 1}
    assert url_sets["unproven"] is False


def test_url_sets_are_empty_for_identical_crawls():
    audit = _audit(["https://e.com/a"], [])
    url_sets = compare(audit, audit)["url_sets"]
    assert url_sets["only_in_before"] == []
    assert url_sets["only_in_after"] == []
    assert url_sets["counts"] == {"only_in_before": 0, "only_in_after": 0}


def test_url_sets_are_unproven_when_either_crawl_is_partial():
    before = _audit(["https://e.com/a"], [], crawl_partial=True)
    after = _audit(["https://e.com/b"], [])
    url_sets = compare(before, after)["url_sets"]
    assert url_sets["only_in_before"] == ["https://e.com/a"]
    assert url_sets["unproven"] is True


def test_url_sets_use_correspondence_mapped_before_urls(tmp_path):
    declaration = tmp_path / "url-correspondence.json"
    declaration.write_text(
        json.dumps(
            {
                "schema_version": "url-correspondence.v1",
                "origin_map": {"https://old.example": "https://e.com"},
                "pairs": [],
            }
        ),
        encoding="utf-8",
    )
    before = _audit(["https://old.example/a"], [])
    after = _audit(["https://e.com/a"], [])
    url_sets = compare(before, after, correspondence=str(declaration))["url_sets"]
    assert url_sets["only_in_before"] == []
    assert url_sets["only_in_after"] == []
