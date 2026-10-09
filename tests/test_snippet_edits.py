"""Tests for seohead.sf.snippet_edits (#1010 child 7, slice 1)."""

from __future__ import annotations

import pytest

from seohead.sf.snippet_edits import MAX_CHARS, parse_snippet_csv


def test_comma_csv_loads_edits_and_skips_empty_cells():
    text = "URL,Title,Description\nhttps://a.test/x,Новый тайтл,\nhttps://a.test/y,,Описание\n"
    result = parse_snippet_csv(text)
    assert result["errors"] == []
    assert result["edits"] == [
        {"line": 2, "url": "https://a.test/x", "title": "Новый тайтл"},
        {"line": 3, "url": "https://a.test/y", "description": "Описание"},
    ]


def test_semicolon_csv_with_bom_is_accepted():
    text = "﻿url;title\nhttps://a.test/x;Тайтл\n"
    result = parse_snippet_csv(text)
    assert result["edits"] == [{"line": 2, "url": "https://a.test/x", "title": "Тайтл"}]


def test_bad_rows_are_reported_and_good_rows_still_load():
    text = (
        "url,title\n"
        "ftp://a.test/x,T\n"
        "not-a-url,T\n"
        "https://a.test/y,\n"
        "https://a.test/z,T1\n"
        "https://a.test/z,T2\n"
    )
    result = parse_snippet_csv(text)
    assert [e["url"] for e in result["edits"]] == ["https://a.test/z"]
    assert [(e["line"], e["message"]) for e in result["errors"]] == [
        (2, "url must be an absolute http(s) URL"),
        (3, "url must be an absolute http(s) URL"),
        (4, "no title or description to change"),
        (6, "duplicate url"),
    ]


def test_blank_lines_are_ignored():
    result = parse_snippet_csv("url,title\n\nhttps://a.test/x,T\n\n")
    assert len(result["edits"]) == 1
    assert result["errors"] == []


@pytest.mark.parametrize(
    "text",
    ["", "title,description\nT,D\n", "url,body\nhttps://a.test/x,B\n"],
)
def test_header_without_required_columns_is_rejected(text):
    with pytest.raises(ValueError):
        parse_snippet_csv(text)


def test_non_text_and_oversized_input_are_rejected():
    with pytest.raises(ValueError):
        parse_snippet_csv(b"url,title\n")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        parse_snippet_csv("url,title\n" + "x" * (MAX_CHARS + 1))
