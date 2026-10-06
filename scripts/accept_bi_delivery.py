"""Verify bounded local BI delivery from an explicitly supplied retained source.

No network/provider/cloud call is made. This is consumer acceptance at the
supplied source size, not proof of native crawler capacity or a native Looker report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The entry point must select this checkout before importing the editable package.
sys.path.insert(0, str(ROOT))

from seohead.reports import bi, bi_destinations  # noqa: E402
from seohead.servers.bi_handlers import bi_filter  # noqa: E402
from seohead.storage.audit_v2 import audit_v2_path  # noqa: E402


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def source_manifest() -> dict:
    loaded = {}
    for name, module in tuple(sys.modules.items()):
        if name == "seohead" or name.startswith("seohead."):
            filename = getattr(module, "__file__", None)
            if filename is None:
                continue
            path = Path(filename).resolve()
            if not path.is_relative_to(ROOT):
                raise RuntimeError(f"wrong checkout imported before acceptance: {name}")
            loaded[name] = {"path": str(path.relative_to(ROOT)), "sha256": digest(path)}
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if dirty:
        raise RuntimeError("freeze and commit the consumer source before acceptance")
    return {
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "modules": loaded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--scan", type=Path)
    inputs.add_argument("--audit", type=Path)
    parser.add_argument("--provider-join", action="append", default=[], type=Path)
    parser.add_argument("--search-metric", choices=["clicks", "impressions"])
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    frozen = source_manifest()
    inputs = [args.scan or args.audit, *args.provider_join]
    if args.scan and audit_v2_path(args.scan).exists():
        inputs.append(audit_v2_path(args.scan))
    before = {str(path): digest(path) for path in inputs}
    args.out_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    started = time.perf_counter()
    result = bi.export_bi(
        scan=args.scan,
        audit=args.audit,
        provider_joins=args.provider_join,
        search_metric=args.search_metric,
        out_dir=args.out_dir / "package",
    )
    package = args.out_dir / "package"
    selection = bi_filter(
        str(package),
        "findings",
        str(args.out_dir / "selected-findings"),
        where={"severity": ["warning", "critical"]},
        columns=["run_id", "finding_id", "check_id", "severity", "url", "message"],
        max_rows_per_file=3,
        xlsx_out=str(args.out_dir / "findings.xlsx"),
        xlsx_max_rows_per_sheet=2,
    )
    sheets = bi_destinations.sheets_plan(package)
    bigquery = bi_destinations.bigquery_plan(package, dataset="synthetic_reporting")
    manifest = json.loads((package / "manifest.json").read_text())
    for dataset in manifest["datasets"].values():
        assert sum(part["rows"] for part in dataset["partitions"]) == dataset["row_count"]
        assert all(
            digest(package / part["path"]) == part["sha256"] for part in dataset["partitions"]
        )
    after = {str(path): digest(path) for path in inputs}
    if before != after:
        raise RuntimeError("retained source bytes changed during read-only delivery")
    aliases = json.loads(
        (ROOT / "examples/reporting-pack/linking-api-source-aliases.json").read_text()
    )
    owner_review = {
        "format": "seohead.looker-owner-review.v1",
        "native_template_state": "missing",
        "cloud_actions_performed": False,
        "account": "owner must choose the authorized Google account",
        "proposed_new_spreadsheet": "SEOHEAD Tools - Synthetic reporting pack v1 (2026-10-06)",
        "proposed_new_report": "SEOHEAD Tools - Original synthetic template v1",
        "proposed_sharing": "restricted; no public link or additional recipient without owner instruction",
        "package_manifest_sha256": digest(package / "manifest.json"),
        "worksheets": sheets["worksheets"],
        "required_cells": sheets["required_cells"],
        "source_aliases": aliases,
        "blueprint": "examples/reporting-pack/looker-studio-blueprint.json",
        "requested_actions": [
            "Create one new private synthetic Sheet and six named worksheets; import every local partition in order.",
            "Create the original five-page native Looker report and six Sheets sources with the declared aliases.",
            "Verify a fresh copy in a separate authorized session; record actual IDs, link, counts and page/control evidence.",
        ],
        "outside_request": [
            "BigQuery write/billing",
            "customer data",
            "public sharing",
            "scheduled export",
        ],
    }
    (args.out_dir / "looker-owner-review.json").write_text(
        json.dumps(owner_review, indent=2) + "\n"
    )
    proof = {
        "format": "seohead.bi-delivery-acceptance.v1",
        "source": frozen,
        "elapsed_seconds": time.perf_counter() - started,
        "source_hashes": before,
        "source_hashes_unchanged": before == after,
        "export": result,
        "selection": selection,
        "sheets_offline_plan": sheets,
        "bigquery_offline_plan": bigquery,
        "network": False,
        "native_looker_template": "missing",
        "capacity_claim": "only supplied retained-source population; not a native million-URL benchmark",
    }
    try:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        proof["peak_rss_bytes"] = raw if sys.platform == "darwin" else raw * 1024
    except ImportError:
        proof["peak_rss_bytes"] = None
    # Verify every module loaded by the actual workflow still belongs to the frozen checkout.
    proof["source_after"] = source_manifest()
    (args.out_dir / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(
        json.dumps(
            {
                "proof": str(args.out_dir / "proof.json"),
                "source_hashes_unchanged": True,
                "datasets": result["datasets"],
                "native_looker_template": "missing",
            }
        )
    )


if __name__ == "__main__":
    main()
