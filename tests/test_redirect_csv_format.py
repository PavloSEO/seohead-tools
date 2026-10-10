"""CSV redirect rows: source, target, 301, quoted when a field holds a comma."""

from __future__ import annotations

import csv
import io

import pytest

from seohead.checks.redirects import generate_rule, generate_rules


def test_csv_row_has_source_target_and_permanent_status():
    row = generate_rule("https://example.com/old", "https://example.com/new", "csv")
    assert next(csv.reader(io.StringIO(row))) == ["/old", "https://example.com/new", "301"]


def test_csv_row_quotes_fields_containing_commas():
    row = generate_rule("/a,b", "/c", "csv")
    assert next(csv.reader(io.StringIO(row))) == ["/a,b", "/c", "301"]


def test_csv_batch_uses_default_url_and_reports_missing_target():
    rules = generate_rules(
        [
            {"old_url": "/one", "to": "/two"},
            {"old_url": "/three", "redirect_to_default": True},
            {"old_url": "/four"},
        ],
        fmt="csv",
        default_url="/home",
    )
    assert rules[0] == "/one,/two,301"
    assert rules[1] == "/three,/home,301"
    assert rules[2].startswith("ERROR: Target URL is required")


@pytest.mark.parametrize("fmt", ["apache", "nginx"])
def test_existing_formats_are_unchanged(fmt):
    rule = generate_rule("/old", "/new", fmt)
    assert "/old" in rule and "/new" in rule
