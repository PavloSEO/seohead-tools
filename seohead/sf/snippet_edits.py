"""Parse a CSV of snippet edits (URL, title, description) into validated records.

First slice of #1010 child 7. Pure parsing: no network, no file I/O, no pixel
measurement. The CLI/MCP entry point and the export-as-task step are separate
slices and are not part of this module.

Rules: the header must name ``url`` and at least one of ``title`` / ``description``
(case-insensitive, ``;`` or ``,`` delimiter). An empty cell means "leave this
field unchanged". A row with no edited field, a non-http(s) URL, or a URL that
already appeared is reported as an error and skipped; the other rows still load.
"""

from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import urlsplit

MAX_CHARS = 5_000_000
EDITABLE = ("title", "description")


def parse_snippet_csv(text: str) -> dict[str, Any]:
    """Return ``{"edits": [...], "errors": [...]}``; raise ``ValueError`` on bad input."""
    if not isinstance(text, str):
        raise ValueError("snippet CSV must be text")
    if len(text) > MAX_CHARS:
        raise ValueError(f"snippet CSV is larger than {MAX_CHARS} characters")
    text = text.lstrip("﻿")
    first_line = text.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except StopIteration:
        raise ValueError("snippet CSV is empty") from None
    if "url" not in header or not any(name in header for name in EDITABLE):
        raise ValueError("snippet CSV needs a 'url' column and 'title' or 'description'")

    edits: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        cells = {name: (row[i].strip() if i < len(row) else "") for i, name in enumerate(header)}
        url = cells["url"]
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            errors.append({"line": line, "message": "url must be an absolute http(s) URL"})
            continue
        if url in seen:
            errors.append({"line": line, "message": "duplicate url"})
            continue
        fields = {name: cells[name] for name in EDITABLE if cells.get(name)}
        if not fields:
            errors.append({"line": line, "message": "no title or description to change"})
            continue
        seen.add(url)
        edits.append({"line": line, "url": url, **fields})
    return {"edits": edits, "errors": errors}
