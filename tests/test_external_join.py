"""External data joins: crawl vs. a user-supplied CSV, on URL. Pure, no network."""

import pytest

from seohead.checks.external_join import (
    ExternalJoinError,
    join_external_data,
    load_csv_rows,
    normalize_join_key,
    orphan_urls,
)


def _pages(*urls):
    return [{"url": u} for u in urls]


def _rows(*urls):
    return [{"url": u, "clicks": "10"} for u in urls]


# --- normalize_join_key ------------------------------------------------------


def test_normalize_join_key_is_stable_across_default_port_and_trailing_slash():
    assert normalize_join_key("https://Example.com:443/page/") == normalize_join_key(
        "https://example.com/page"
    )


def test_normalize_join_key_treats_http_and_https_as_different_by_default():
    assert normalize_join_key("http://example.com/a") != normalize_join_key("https://example.com/a")


def test_normalize_join_key_ignore_scheme_is_explicit_opt_in():
    assert normalize_join_key("http://example.com/a", ignore_scheme=True) == normalize_join_key(
        "https://example.com/a", ignore_scheme=True
    )


def test_normalize_join_key_keeps_query_string_by_default():
    assert normalize_join_key("https://example.com/a?x=1") != normalize_join_key(
        "https://example.com/a"
    )


def test_normalize_join_key_ignore_query_is_explicit_opt_in():
    assert normalize_join_key(
        "https://example.com/a?utm_source=x", ignore_query=True
    ) == normalize_join_key("https://example.com/a", ignore_query=True)


def test_normalize_join_key_is_case_sensitive_on_the_path_by_default():
    assert normalize_join_key("https://example.com/Page") != normalize_join_key(
        "https://example.com/page"
    )


def test_normalize_join_key_casefold_path_is_explicit_opt_in():
    assert normalize_join_key("https://example.com/Page", casefold_path=True) == normalize_join_key(
        "https://example.com/page", casefold_path=True
    )


def test_normalize_join_key_returns_none_for_blank_or_relative_input():
    assert normalize_join_key("") is None
    assert normalize_join_key(None) is None
    assert normalize_join_key("/relative/path") is None
    assert normalize_join_key("not a url") is None


# --- join_external_data: both non-join directions ----------------------------


def test_matching_urls_join():
    pages = _pages("https://example.com/a", "https://example.com/b")
    rows = _rows("https://example.com/a", "https://example.com/b")
    result = join_external_data(pages, rows)
    assert result["summary"]["joined"] == 2
    assert result["crawl_only"] == []
    assert result["external_only"] == []


def test_a_crawled_url_with_no_external_row_is_reported_as_crawl_only():
    pages = _pages("https://example.com/a", "https://example.com/only-crawled")
    rows = _rows("https://example.com/a")
    result = join_external_data(pages, rows)
    assert result["crawl_only"] == ["https://example.com/only-crawled"]
    assert result["external_only"] == []
    assert result["summary"]["crawl_only"] == 1


def test_an_external_row_with_no_crawled_url_is_reported_as_external_only():
    pages = _pages("https://example.com/a")
    rows = _rows("https://example.com/a", "https://example.com/never-crawled")
    result = join_external_data(pages, rows)
    assert result["external_only"] == [{"url": "https://example.com/never-crawled", "clicks": "10"}]
    assert result["crawl_only"] == []
    assert result["summary"]["external_only"] == 1


def test_both_non_join_directions_are_reported_simultaneously_and_disjointly():
    pages = _pages("https://example.com/shared", "https://example.com/crawl-only")
    rows = _rows("https://example.com/shared", "https://example.com/row-only")
    result = join_external_data(pages, rows)
    assert result["crawl_only"] == ["https://example.com/crawl-only"]
    assert [row["url"] for row in result["external_only"]] == ["https://example.com/row-only"]
    assert result["summary"]["joined"] == 1


def test_joining_normalizes_both_sides_before_matching():
    pages = _pages("https://Example.com:443/a/")
    rows = _rows("https://example.com/a")
    result = join_external_data(pages, rows)
    assert result["summary"]["joined"] == 1
    assert result["crawl_only"] == []
    assert result["external_only"] == []


def test_unjoinable_rows_are_counted_separately_from_a_clean_non_match():
    pages = _pages("https://example.com/a")
    rows = [{"url": "", "clicks": "1"}, {"url": "not a url", "clicks": "2"}]
    result = join_external_data(pages, rows)
    assert result["external_only"] == []  # a bad key is not a clean non-match
    assert len(result["unkeyable_rows"]) == 2
    assert result["crawl_only"] == ["https://example.com/a"]


def test_unjoinable_pages_are_counted_separately():
    pages = [{"url": ""}, {"url": "https://example.com/a"}]
    rows = _rows("https://example.com/a")
    result = join_external_data(pages, rows)
    assert result["unkeyable_pages"] == [""]
    assert result["summary"]["unkeyable_pages"] == 1


def test_join_key_can_be_relaxed_by_the_caller_to_match_more():
    pages = _pages("https://example.com/a")
    rows = _rows("http://example.com/a?utm_source=newsletter")
    strict = join_external_data(pages, rows)
    assert strict["summary"]["joined"] == 0

    def loose_key(url):
        return normalize_join_key(url, ignore_scheme=True, ignore_query=True)

    relaxed = join_external_data(pages, rows, key_fn=loose_key)
    assert relaxed["summary"]["joined"] == 1


def test_empty_inputs_produce_empty_disjoint_sets():
    result = join_external_data([], [])
    assert result["summary"] == {
        "pages": 0,
        "rows": 0,
        "joined": 0,
        "crawl_only": 0,
        "external_only": 0,
        "unkeyable_pages": 0,
        "unkeyable_rows": 0,
    }


# --- orphan_urls: enters the crawl, not just a side report -------------------


def test_orphan_urls_is_the_external_only_urls_sorted():
    pages = _pages("https://example.com/a")
    rows = _rows(
        "https://example.com/a", "https://example.com/z-orphan", "https://example.com/a-orphan"
    )
    result = join_external_data(pages, rows)
    assert orphan_urls(result) == ["https://example.com/a-orphan", "https://example.com/z-orphan"]


def test_orphan_urls_is_empty_when_everything_joined():
    pages = _pages("https://example.com/a")
    rows = _rows("https://example.com/a")
    result = join_external_data(pages, rows)
    assert orphan_urls(result) == []


def test_orphan_urls_defaults_to_the_crawl_origins():
    result = join_external_data(
        _pages("https://example.com/a"),
        _rows("https://foreign.example/traffic-only"),
    )
    assert result["external_only"]
    assert result["crawl_origins"] == ["https://example.com"]
    assert orphan_urls(result) == []


def test_orphan_urls_allows_an_explicit_multi_origin_audit_scope():
    result = join_external_data(
        _pages("https://example.com/a"),
        _rows("https://foreign.example/traffic-only"),
    )
    assert orphan_urls(
        result, allowed_origins={"https://example.com", "https://foreign.example"}
    ) == ["https://foreign.example/traffic-only"]


def test_orphan_urls_keeps_a_second_origin_when_the_crawl_includes_it():
    result = join_external_data(
        _pages("https://example.com/a", "https://foreign.example/seen"),
        _rows("https://foreign.example/traffic-only"),
    )
    assert orphan_urls(result) == ["https://foreign.example/traffic-only"]


def test_orphan_urls_accepts_one_explicit_origin_as_a_string():
    result = join_external_data(
        _pages("https://example.com/a"),
        _rows("https://foreign.example/traffic-only"),
    )
    assert orphan_urls(result, allowed_origins="https://foreign.example") == [
        "https://foreign.example/traffic-only"
    ]


# --- load_csv_rows ------------------------------------------------------------


def test_load_csv_rows_reads_every_column(tmp_path):
    csv_path = tmp_path / "gsc.csv"
    csv_path.write_text("url,clicks,impressions\nhttps://example.com/a,10,100\n", encoding="utf-8")
    rows = load_csv_rows(str(csv_path), url_column="url")
    assert rows == [{"url": "https://example.com/a", "clicks": "10", "impressions": "100"}]


def test_load_csv_rows_rejects_a_missing_url_column_by_name(tmp_path):
    csv_path = tmp_path / "gsc.csv"
    csv_path.write_text("page,clicks\nhttps://example.com/a,10\n", encoding="utf-8")
    with pytest.raises(ExternalJoinError, match="url"):
        load_csv_rows(str(csv_path), url_column="url")


def test_load_csv_rows_strips_a_byte_order_mark(tmp_path):
    csv_path = tmp_path / "gsc.csv"
    csv_path.write_bytes("﻿url,clicks\nhttps://example.com/a,10\n".encode())
    rows = load_csv_rows(str(csv_path), url_column="url")
    assert rows[0]["url"] == "https://example.com/a"


def test_end_to_end_csv_join(tmp_path):
    csv_path = tmp_path / "gsc.csv"
    csv_path.write_text(
        "url,clicks\nhttps://example.com/a,10\nhttps://example.com/never-crawled,5\n",
        encoding="utf-8",
    )
    rows = load_csv_rows(str(csv_path), url_column="url")
    pages = _pages("https://example.com/a", "https://example.com/only-crawled")
    result = join_external_data(pages, rows)
    assert result["summary"]["joined"] == 1
    assert result["crawl_only"] == ["https://example.com/only-crawled"]
    assert orphan_urls(result) == ["https://example.com/never-crawled"]
