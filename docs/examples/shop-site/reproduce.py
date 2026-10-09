#!/usr/bin/env python3
"""Build the three-scan «Мебельный магазин» project from the shop test site.

Serves v1, v2, v3 in turn on loopback (own process, stopped after each crawl), crawls each
into one project, then runs the offline steps: compare-crawls 1→2 and 2→3, sf tasks per scan,
and a Search-in-HTML pass for the retired tag-manager marker.

    python docs/examples/shop-site/reproduce.py --out /path/to/shop-project

Requires the ``seohead`` console script next to the running interpreter. The output directory
must not exist yet. The host must resolve to loopback (see README: /etc/hosts line, or
``--host shop.localhost`` as the no-hosts fallback).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SITE = Path(__file__).resolve().with_name("shop_site.py")
VERSIONS = ("v1", "v2", "v3")
CRAWL_CONFIG = {
    # Loopback settings: unlimited depth and full pagination; the polite defaults
    # (depth 5, 5 query variants per path, 0.5 s delay) are meant for real sites.
    "limits": {"max_depth": -1, "max_urls": 5000, "max_query_variants_per_path": 20},
    "speed": {"min_delay_seconds": 0.02, "concurrency": 4},
    "resources": {"fetch": True},
    "sitemaps": {"auto_discover": True},
}


def seohead(*args: str, env: dict | None = None, out: Path | None = None) -> None:
    # The console script next to this interpreter, not ``-m``: from a repo checkout ``-m`` would
    # import the working tree, whose uncommitted state the crawler refuses as scan provenance.
    cmd = [str(Path(sys.executable).with_name("seohead")), *args]
    with open(out, "w") if out else open(os.devnull, "w") as fh:
        subprocess.run(cmd, check=True, stdout=fh, env=env)


def crawl(version: str, out: Path, base: str, port: int, env: dict) -> None:
    ready = out / f"ready-{version}.json"
    srv = subprocess.Popen(
        [
            sys.executable,
            str(SITE),
            "serve",
            "--version",
            version,
            "--port",
            str(port),
            "--base",
            base,
            "--ready-file",
            str(ready),
            "--lifetime",
            "1800",
        ]
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if time.monotonic() > deadline or srv.poll() is not None:
                raise SystemExit(f"shop site {version} did not start")
            time.sleep(0.1)
        seohead(
            "crawl-site",
            "--project",
            str(out / "project"),
            "--config",
            str(out / "crawl-config.json"),
            "--scan-out",
            str(out / "project" / "scans" / f"scan-{version}.sqlite"),
            "--approve-large-crawl",
            "-q",
            env=env,
            out=out / "crawl" / f"crawl-{version}.json",
        )
    finally:
        srv.send_signal(signal.SIGTERM)
        srv.wait(timeout=10)
        ready.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out", required=True, type=Path, help="new directory for the project and outputs"
    )
    ap.add_argument(
        "--host", default="shop.example.test", help="loopback host name (fallback: shop.localhost)"
    )
    ap.add_argument("--port", type=int, default=18431)
    args = ap.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True)
    for sub in ("crawl", "compare", "tasks", "search"):
        (out / sub).mkdir()
    base = f"http://{args.host}:{args.port}"
    env = dict(os.environ, SEOHEAD_ALLOW_PRIVATE_HOSTS=args.host)
    (out / "crawl-config.json").write_text(json.dumps(CRAWL_CONFIG, indent=1))
    seohead(
        "project-new",
        "--directory",
        str(out / "project"),
        "--target",
        base + "/",
        "--label",
        "Мебельный магазин",
    )
    for v in VERSIONS:
        crawl(v, out, base, args.port, env)
    scans = {v: str(out / "project" / "scans" / f"scan-{v}.sqlite") for v in VERSIONS}
    for a, b in itertools.pairwise(VERSIONS):
        seohead(
            "compare-crawls",
            "--before",
            scans[a],
            "--after",
            scans[b],
            "--out-dir",
            str(out / "compare" / f"scan{a[1]}-to-scan{b[1]}"),
        )
    for v in VERSIONS:
        seohead("sf", "tasks", "--json", scans[v], "--out", str(out / "tasks" / f"scan-{v}"), "-q")
        seohead(
            "scan-content-search",
            "--scan",
            scans[v],
            "--query",
            "GTM-K7OLD12",
            "--scope",
            "raw_html",
            "--out-dir",
            str(out / "search" / f"gtm-old-{v}"),
        )
    for v in VERSIONS:
        totals = json.loads((out / "crawl" / f"crawl-{v}.json").read_text())["summary"]["totals"]
        print(v, totals["urls_crawled"], "URLs,", totals["issues_total"], "findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
