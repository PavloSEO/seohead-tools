"""Profile audit assembly over a completed synthetic native capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from seohead.crawl.sql_sitemap import prepare_sitemap_reconciliation
from seohead.servers.handlers import _audit_crawl_result
from seohead.servers.scan_handlers import _rebuild_page_result
from seohead.storage.native_scan import NativeScan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan", type=Path)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--streaming", action="store_true")
    args = parser.parse_args()
    # The caller supplies a disposable copy of a running retained capture.
    # Offline mode below performs no collection or evidence mutation.
    with NativeScan.open(args.scan) as scan:
        config = json.loads(scan.con.execute("SELECT config_json FROM scan").fetchone()[0])
        result = _rebuild_page_result(scan, page_view=True)
        result.start_page_evidence = {"html": "<html></html>", "outlinks": 1, "external_outlinks": 0}
        result.resumed = True
        result.cache_replay = False
        result.cache_stats = {}
        with prepare_sitemap_reconciliation(scan.con, start_url="https://million-crawl.test/p/0") as reconciliation:
            _audit_crawl_result(
                result,
                settings=config,
                url="https://million-crawl.test/p/0",
                sitemap_seed={"sitemap_url": "https://million-crawl.test/sitemap-index.xml", "sitemap_urls": [], "declared": []},
                discovery={"mode": "profile", "directive_policy": "respect", "robots_blocked": 0},
                stored_scan=scan,
                stored_sitemap=reconciliation,
                offline=True,
                streaming=args.streaming,
            )


if __name__ == "__main__":
    main()
