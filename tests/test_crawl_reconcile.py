"""Declared-vs-observed sitemap reconciliation. Pure, no network."""

from seohead.crawl.reconcile import reconcile_sitemap


def test_three_sets_are_disjoint_and_never_merged():
    declared = ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
    observed = ["https://example.com/b", "https://example.com/c", "https://example.com/d"]

    report = reconcile_sitemap(declared, observed)

    assert report["in_sitemap_and_linked"] == [
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert report["in_sitemap_not_linked"] == ["https://example.com/a"]
    assert report["linked_not_in_sitemap"] == ["https://example.com/d"]


def test_counts_match_the_underlying_sets():
    declared = ["https://example.com/a", "https://example.com/b"]
    observed = ["https://example.com/b"]
    report = reconcile_sitemap(declared, observed)
    assert report["urls_in_sitemap"] == 2
    assert report["urls_reached_by_links"] == 1


def test_an_orphan_is_never_also_counted_as_missing_from_sitemap():
    """The exact failure mode this module exists to prevent: collapsing two
    different facts ("declared but unreachable" and "reachable but undeclared")
    into one generic "not found" bucket."""
    declared = ["https://example.com/only-in-sitemap"]
    observed = ["https://example.com/only-linked"]
    report = reconcile_sitemap(declared, observed)
    assert report["in_sitemap_not_linked"] == ["https://example.com/only-in-sitemap"]
    assert report["linked_not_in_sitemap"] == ["https://example.com/only-linked"]
    assert "https://example.com/only-in-sitemap" not in report["linked_not_in_sitemap"]
    assert "https://example.com/only-linked" not in report["in_sitemap_not_linked"]


def test_urls_are_compared_after_normalization():
    """A trailing slash or a default port must not manufacture a false mismatch."""
    declared = ["https://Example.com:443/page/"]
    observed = ["https://example.com/page"]
    report = reconcile_sitemap(declared, observed)
    assert report["in_sitemap_and_linked"] == ["https://example.com/page"]
    assert report["in_sitemap_not_linked"] == []
    assert report["linked_not_in_sitemap"] == []


def test_duplicate_urls_are_not_double_counted():
    declared = ["https://example.com/a", "https://example.com/a"]
    observed = ["https://example.com/a"]
    report = reconcile_sitemap(declared, observed)
    assert report["urls_in_sitemap"] == 1


def test_an_unparseable_url_is_dropped_rather_than_raising():
    report = reconcile_sitemap(["not a url", "https://example.com/a"], ["https://example.com/a"])
    assert report["urls_in_sitemap"] == 1


def test_empty_inputs_produce_empty_disjoint_sets():
    report = reconcile_sitemap([], [])
    assert report == {
        "urls_in_sitemap": 0,
        "urls_reached_by_links": 0,
        "in_sitemap_and_linked": [],
        "in_sitemap_not_linked": [],
        "linked_not_in_sitemap": [],
        "linked_not_comparable": [],
        "in_sitemap_not_in_crawl": 0,
        "in_crawl_not_in_sitemap": 0,
    }


def test_count_aliases_match_the_screaming_frog_pipelines_summary_keys():
    """The same counts under the names seohead.sf.core.sitemap_coverage.run_sitemap
    already uses, so a consumer need not branch on which crawl mode produced
    the report."""
    declared = ["https://example.com/a", "https://example.com/b"]
    observed = ["https://example.com/b", "https://example.com/c"]
    report = reconcile_sitemap(declared, observed)
    assert report["in_sitemap_not_in_crawl"] == len(report["in_sitemap_not_linked"])
    assert report["in_crawl_not_in_sitemap"] == len(report["linked_not_in_sitemap"])


def test_a_fully_healthy_sitemap_reports_no_orphans_and_nothing_missing():
    urls = ["https://example.com/a", "https://example.com/b"]
    report = reconcile_sitemap(urls, urls)
    assert report["in_sitemap_and_linked"] == urls
    assert report["in_sitemap_not_linked"] == []
    assert report["linked_not_in_sitemap"] == []


def test_normalized_index_parity_for_url_pairs():
    # Pins the key/value contract of the index helper before it moves between modules.
    from seohead.crawl.reconcile import _normalized_index

    index = _normalized_index(
        [
            "https://Example.com/a/",
            "https://example.com:443/a",
            "https://example.com/a#frag",
            "https://example.com/",
            "",
            "/relative-only",
            "https://example.com/b",
        ]
    )
    assert index == {
        "https://example.com/a": "https://Example.com/a/",
        "https://example.com/": "https://example.com/",
        "https://example.com/b": "https://example.com/b",
    }
