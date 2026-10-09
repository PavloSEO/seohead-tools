#!/usr/bin/env python3
"""Write or verify the committed synthetic reporting-pack worksheets.

    python scripts/generate_reporting_pack_worksheets.py          # regenerate
    python scripts/generate_reporting_pack_worksheets.py --check   # exit 1 if stale (CI)

Worksheets are produced by seohead.reports.reporting_pack.build_worksheets from
docs/examples/audit.json and the committed synthetic provider inputs under
docs/examples/reporting-pack/sources/.  The build is byte-deterministic: unchanged
inputs and toolkit source produce identical CSV bytes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run as a plain script (sys.path[0] is scripts/, not ROOT), so an editable
# install elsewhere on the machine could otherwise shadow this checkout's package.
sys.path.insert(0, str(ROOT))

from seohead.reports.reporting_pack import build_worksheets  # noqa: E402

AUDIT = ROOT / "docs" / "examples" / "audit.json"
SOURCES = ROOT / "docs" / "examples" / "reporting-pack" / "sources"
WORKSHEETS = ROOT / "docs" / "examples" / "reporting-pack" / "worksheets"
SEARCH_METRIC = "clicks"


def _build(out_dir: Path) -> None:
    build_worksheets(
        audit=AUDIT,
        provider_inputs=sorted(SOURCES.glob("*.json")),
        search_metric=SEARCH_METRIC,
        out_dir=out_dir,
    )


def _tree_files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail if the committed worksheets differ from a fresh build",
    )
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="reporting-pack-", dir=WORKSHEETS.parent))
    try:
        built = scratch / "worksheets"
        _build(built)
        fresh = _tree_files(built)
        if args.check:
            committed = _tree_files(WORKSHEETS) if WORKSHEETS.is_dir() else {}
            if fresh != committed:
                print(f"{WORKSHEETS} is stale: run 'python {sys.argv[0]}' and commit the result")
                return 1
            return 0
        if WORKSHEETS.exists():
            shutil.rmtree(WORKSHEETS)
        built.rename(WORKSHEETS)
        print(f"wrote {WORKSHEETS} ({len(fresh)} files)")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
