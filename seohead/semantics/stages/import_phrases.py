"""Import a user-provided CSV of phrases without discarding existing evidence."""

from __future__ import annotations

import csv
from pathlib import Path

from seohead.semantics.norm import normalize

NUMERIC_FIELDS = ("base", "quoted", "exact", "impr", "pos_y", "pos_g")


def run(store, cfg, file):
    path = Path(file)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(4096)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(stream, dialect=dialect, strict=True)
        phrase_field = next(
            (
                key
                for key in ("norm", "phrase", "query", "фраза", "Запрос")
                if key in (reader.fieldnames or [])
            ),
            None,
        )
        if phrase_field is None:
            raise ValueError("CSV needs a norm, phrase or query column")
        rows = []
        try:
            parsed_rows = list(reader)
        except csv.Error as error:
            raise ValueError(f"malformed CSV: {error}") from error
        for line, row in enumerate(parsed_rows, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"wrong column count on CSV line {line}")
            phrase = normalize(row[phrase_field])
            if not phrase:
                continue
            values = {}
            for key in NUMERIC_FIELDS:
                value = row.get(key)
                if value is not None and value.strip() not in ("", "NULL", "null", "--"):
                    try:
                        values[key] = int(value)
                    except ValueError as error:
                        raise ValueError(f"invalid {key} on CSV line {line}") from error
                    if values[key] < 0:
                        raise ValueError(f"negative {key} on CSV line {line}")
            rows.append((phrase, values))
    added = 0
    for phrase, values in rows:
        added += store.upsert(phrase, src="import", region=str((cfg.get("regions") or [225])[0]))
        existing = store.db.execute("SELECT * FROM phrases WHERE norm=?", (phrase,)).fetchone()
        missing = {key: value for key, value in values.items() if existing[key] is None}
        if missing:
            store.set_fields(phrase, **missing)
        if any(values.get(key, 0) > 0 for key in ("impr", "pos_y", "pos_g")):
            store.set_status(phrase, "kept", reason="observed search demand", stage="import")
    store.commit()
    print(f"import: {len(rows)} rows, {added} new phrases")
    return {"rows": len(rows), "added": added}
