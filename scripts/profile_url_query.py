#!/usr/bin/env python3
"""Read-only latency baseline for the saved-scan page table (scan url-query).

Runs fixed page reads of 100 rows against an existing scan file and reports the median
wall time per case against a 200 ms budget. The scan is opened by url_query in read-only
mode; the script hashes the file before and after and fails if the bytes changed.
Nothing is built or migrated here: point it at a copy of a finished scan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seohead import __version__  # noqa: E402
from seohead.storage import url_query  # noqa: E402

PAGE_ROWS = 100
BUDGET_MS = 200.0
COLUMNS = ["url", "status_code", "title", "word_count"]

# Each case: name, filters, sort, offset. Sort by url is the indexed path; status filter
# plus url sort covers a filtered page; the deep offset shows the cost of skipping rows.
CASES: list[tuple[str, list[dict[str, object]] | None, str | None, int]] = [
    ("first_page_sort_url", None, "url", 0),
    ("first_page_unsorted", None, None, 0),
    ("deep_page_sort_url", None, "url", 500_000),
    (
        "filtered_status_404_sort_url",
        [{"column": "status_code", "op": "eq", "value": 404}],
        "url",
        0,
    ),
]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _total_urls(path: Path) -> int:
    con = sqlite3.connect(f"file:{path.absolute()}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT COUNT(*) FROM urls").fetchone()[0])
    finally:
        con.close()


def _measure(path: str, filters, sort, offset: int, repeat: int) -> dict[str, object]:
    times_ms: list[float] = []
    last: dict[str, object] = {}
    for _ in range(repeat):
        started = time.perf_counter()
        last = url_query.scan_url_query(
            path,
            filters=filters,
            sort=sort,
            columns=COLUMNS,
            offset=offset,
            limit=PAGE_ROWS,
        )
        times_ms.append((time.perf_counter() - started) * 1000)
    median = statistics.median(times_ms)
    return {
        "ok": bool(last.get("ok")),
        "reason_code": last.get("reason_code"),
        "rows": len(last.get("rows", []) or []),
        "median_ms": round(median, 2),
        "min_ms": round(min(times_ms), 2),
        "max_ms": round(max(times_ms), 2),
        "within_budget": bool(last.get("ok")) and median <= BUDGET_MS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path, required=True, help="saved scan file (read-only)")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if not args.scan.is_file():
        parser.error("--scan must name an existing scan file")

    before = _digest(args.scan)
    started = time.perf_counter()
    cases = {
        name: _measure(str(args.scan), filters, sort, offset, args.repeat)
        for name, filters, sort, offset in CASES
    }
    after = _digest(args.scan)
    unchanged = before == after
    report = {
        "format": "seohead.profile-url-query.v1",
        "seohead_version": __version__,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "urls": _total_urls(args.scan),
        "page_rows": PAGE_ROWS,
        "budget_ms": BUDGET_MS,
        "repeat": args.repeat,
        "scan_unchanged": unchanged,
        "cases": cases,
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    ok = unchanged and all(case["within_budget"] for case in cases.values())
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
