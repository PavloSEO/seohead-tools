"""``seohead semantics <stage> --project DIR``: argument mapping onto the shared runner."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from seohead.semantics import PAID_STAGES, STAGES

_HELP = {
    "init": "create project.yaml, seeds.txt and the SQLite database",
    "import": "import phrases from a CSV (norm/phrase/query column)",
    "collect": "PAID: expand seeds through Yandex Wordstat",
    "clean": "free: statuses, routes and lemma groups from project filters",
    "graph": "free: word graph, homonyms and candidate clusters (anomalies.md)",
    "exact": "PAID: exact !W frequency through Arsenkin, once, at the end",
    "cluster": "PAID unless cached: SERP clustering (Yandex Search API)",
    "competitors": "free: competitor domains and title candidates from cached SERP",
    "synonyms": "PAID: check synonyms.txt candidates in Wordstat",
    "report": "free: report.md and clusters.csv",
    "excel": "free: final XLSX workbook",
    "mine": "web: candidate phrases from competitor page headings",
    "sitematch": "free: send phrases without a catalog.txt term to review",
    "relevance": "free: flag phrases whose SERP has no vendors.txt peer",
    "status": "read-only counters, provider units and the next stage",
    "export": "write pool.csv and pool.json",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seohead semantics",
        description="Accumulating semantic core: one SQLite database per project.",
    )
    sub = parser.add_subparsers(dest="stage", required=True, metavar="<stage>")
    for name in STAGES:
        command = sub.add_parser(name, help=_HELP[name])
        command.add_argument(
            "--project",
            required=True,
            help="semantic project directory, or a SEOHEAD project (uses its semantics/)",
        )
        if name == "status":
            command.add_argument("--json", action="store_true")
        if name in ("export", "graph", "competitors", "report", "excel", "mine"):
            command.add_argument("--out", type=Path, help="output directory")
        if name == "import":
            command.add_argument("--file", type=Path, required=True, help="CSV file")
        if name == "collect":
            command.add_argument("--resume", action="store_true")
            command.add_argument("--max-seeds", type=int, default=0)
            command.add_argument("--max-phrases", type=int, default=100000)
        if name in ("exact", "cluster"):
            command.add_argument("--limit", type=int, default=0, help="debugging only")
        if name == "exact":
            command.add_argument(
                "--yes", action="store_true", help="allow more than budget_arsenkin_gate phrases"
            )
        if name == "cluster":
            command.add_argument("--method", choices=("serp", "louvain"), default="serp")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for field in ("limit", "max_seeds", "max_phrases"):
        if getattr(args, field, 0) < 0:
            parser.error(f"--{field.replace('_', '-')} must be non-negative")
    options = vars(args)
    stage, project = options.pop("stage"), options.pop("project")
    try:
        from seohead.semantics.runner import execute

        execute(stage, project, **options)
    except ModuleNotFoundError as error:
        print(
            f"seohead semantics: missing optional dependency {error.name!r}; "
            "install it with: pip install 'seohead-seotools[semantics]'",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"seohead semantics: {error}", file=sys.stderr)
        return 1
    if stage in PAID_STAGES:
        print("provider charges: seohead spend-report", file=sys.stderr)
    return 0
