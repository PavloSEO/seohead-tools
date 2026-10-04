"""Render the row-level coverage-gap reconciliation from the canonical map."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "docs" / "COVERAGE_GAPS.md"
OUT = ROOT / "docs" / "COVERAGE_GAPS_RECONCILIATION.md"
ROW = re.compile(r"^\| (\d+\.\d+) \| (.*?) \|.*?\| (.*?) \| (.*?) \|$")

# Rows whose older map wording predates a concrete registry outcome.
REGISTRY_ROWS = {
    "2.1", "2.2", "2.3", "2.4", "2.5", "6.3", "6.4", "8.2", "8.4", "10.2", "10.4",
    "11.1", "11.2", "11.3", "11.4", "11.5", "11.6", "11.7",
}
PARTIAL_ROWS = {"1.1", "1.4", "1.5", "2.6"}
OUT_OF_SCOPE_ROWS = {"3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.8", "3.9"}


def rows() -> list[tuple[str, str, str]]:
    found = []
    for line in MAP.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            found.append((match.group(1), match.group(2), match.group(3)))
    return found


def status(row_id: str, mode: str) -> str:
    lowered = mode.lower()
    if row_id in REGISTRY_ROWS or "**done**" in lowered:
        return "covered_registry_or_tool"
    if row_id in PARTIAL_ROWS or "partial" in lowered:
        return "partial"
    if row_id in OUT_OF_SCOPE_ROWS or "out of scope" in lowered or "consciously excluded" in lowered:
        return "out_of_scope"
    return "missing"


def render() -> str:
    entries = rows()
    counts = Counter(status(row_id, mode) for row_id, _name, mode in entries)
    lines = [
        "# Coverage gap reconciliation",
        "",
        "Generated from `docs/COVERAGE_GAPS.md` by `scripts/generate_coverage_gap_reconciliation.py`.",
        "A row is covered only when the map names a shipped check/tool or this reconciliation carries a reviewed registry override; partial and out-of-scope are not readiness claims.",
        "",
        f"**{len(entries)} rows:** " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) + ".",
        "",
        "| Row | Name | Reconciled state |",
        "|---|---|---|",
    ]
    lines += [f"| {row_id} | {name} | {status(row_id, mode)} |" for row_id, name, mode in entries]
    lines += [
        "",
        "## Remaining priority order",
        "",
        "1. Required schema fields per type (13.1) — live `schema-check` remains broader than audit-registry parity.",
        "2. JavaScript redirects (5.1) — navigation provenance is captured; a registered audit finding still needs a reviewed emission policy.",
        "3. Disclaimer/editorial-policy evidence (2.7) — no safe generic signal is shipped.",
        "4. Hreflang language/relative/multi-language gaps (7.3, 7.4, 7.9) — raw declaration or graph evidence is still required.",
        "5. Explicit policy-dependent rows such as external dofollow and slug stop words (8.3, 10.3).",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
