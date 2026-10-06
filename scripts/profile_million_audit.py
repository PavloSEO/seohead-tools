"""Profile audit assembly over a completed synthetic native capture."""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Executing scripts/ directly must not import another editable checkout.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seohead.crawl.sql_sitemap import prepare_sitemap_reconciliation
from seohead.crawl.sqlite_adapter import retained_start_gate
from seohead.servers.handlers import _audit_crawl_result
from seohead.servers.scan_handlers import _rebuild_page_result
from seohead.storage.native_scan import NativeScan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan", type=Path)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--streaming", action="store_true")
    args = parser.parse_args()
    loaded = {
        name: str(Path(module.__file__).resolve())
        for name, module in sys.modules.items()
        if name.startswith("seohead") and getattr(module, "__file__", None)
    }
    if any(not Path(path).is_relative_to(ROOT / "seohead") for path in loaded.values()):
        raise RuntimeError("profiler imported SEOHEAD modules outside its source checkout")
    print(json.dumps({"source_root": str(ROOT), "loaded_modules": loaded}), flush=True)
    # The caller supplies a disposable copy of a running retained capture.
    # Offline mode below performs no collection or evidence mutation.
    with NativeScan.open(args.scan) as scan:
        row = scan.con.execute("SELECT config_json,start_url FROM scan").fetchone()
        config, start_url = json.loads(row[0]), row[1]
        result = _rebuild_page_result(scan, page_view=True)
        if args.pages < 1 or len(result.pages) != args.pages:
            raise ValueError("--pages must equal the retained page count")
        result.start_page_evidence = retained_start_gate(scan, config) or {}
        result.resumed = True
        result.cache_replay = False
        result.cache_stats = {}
        with prepare_sitemap_reconciliation(scan.con, start_url=start_url) as reconciliation:
            response, audit = _audit_crawl_result(
                result,
                settings=config,
                url=start_url,
                sitemap_seed={"sitemap_url": None, "sitemap_urls": [], "declared": []},
                discovery={"mode": "profile", "directive_policy": "respect", "robots_blocked": 0},
                stored_scan=scan,
                stored_sitemap=reconciliation,
                offline=True,
                streaming=args.streaming,
            )
            if args.streaming:
                _header, collections = audit
                owners = {getattr(rows, "_context_owner", None) for rows in collections.values()}
                for owner in owners:
                    if owner is not None:
                        owner.close()
            print(json.dumps({"summary": response["summary"]}), flush=True)


if __name__ == "__main__":
    main()
