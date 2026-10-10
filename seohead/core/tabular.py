"""Formula-safe text for CSV and XLSX cells, shared by every tabular writer."""

from __future__ import annotations

from typing import Any

# Characters that Excel, Google Sheets, and most spreadsheet importers treat as
# the start of a formula. Tab and CR are included because both `csv.writer`
# and openpyxl pass them through as literal leading bytes, and some importers
# apply the same formula rule to them as to `=`/`+`/`-`/`@`.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def neutralize_formula(value: Any) -> Any:
    """Defuse CSV/XLSX formula injection (CWE-1236) in one cell value.

    A finding's text or a page's title/H1/canonical is copied verbatim from a
    crawled site (see :func:`seohead.checks.parser.document_title`) into the
    XLSX and CSV reports, so the audited site — not this tool's operator —
    controls the string. Excel and Google Sheets evaluate any cell starting
    with `=`, `+`, `-`, or `@` as a formula, which turns a page title into a
    live `HYPERLINK(...)` exfiltration or a legacy DDE payload the moment the
    report is opened. Prefixing a leading apostrophe is the standard
    neutralizer: the string no longer starts with a formula-leading
    character, so both `csv.writer` and openpyxl's own type autodetection
    write it as plain text.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEADS):
        return "'" + value
    return value
