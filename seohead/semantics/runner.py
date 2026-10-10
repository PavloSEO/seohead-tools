"""Shared stage dispatch for the CLI and the MCP tools."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from seohead.data_sources import spend
from seohead.semantics import PAID_STAGES, STAGES, config
from seohead.semantics.store import Store

# Stages whose phrase table changes and is exported to pool.csv/pool.json afterwards.
_EXPORTING = frozenset(
    {
        "import",
        "collect",
        "clean",
        "graph",
        "exact",
        "cluster",
        "synonyms",
        "sitematch",
        "relevance",
    }
)
_MODULES = {"import": "import_phrases"}


def _store(project, create=False, readonly=False):
    db = config.db_path(project)
    if not create and not db.is_file():
        raise FileNotFoundError(
            f"semantic database not found: {db}\n"
            f"Initialize it with: seohead semantics init --project {project}"
        )
    return Store(db, readonly=readonly)


def _status(store, project):
    counts = store.counts()
    following = (
        "collect"
        if counts["_total"] == 0
        else "clean"
        if counts.get("new", 0)
        else "graph"
        if counts.get("kept", 0) and not counts.get("review", 0)
        else "exact"
        if counts["_exact"] < counts.get("kept", 0)
        else "cluster"
    )
    return {
        "project": str(project),
        "database": str(config.db_path(project)),
        "counts": counts,
        "spend": store.spend(),
        "next_stage": following,
    }


def execute(stage: str, project: str | Path, **options: Any) -> dict[str, Any]:
    """Run one registered stage against a project directory and return its summary."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
    args = SimpleNamespace(**{"out": None, **options})
    directory = config.data_dir(project)
    if stage == "init":
        config.ensure_project(project)
        with _store(project, create=True):
            pass
        print(f"init OK: {directory}")
        return {"project": str(project), "stage": "init", "directory": str(directory)}
    out = Path(args.out) if args.out else directory
    with _store(project, readonly=stage == "status") as store:
        if stage == "status":
            result = _status(store, project)
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(f"=== {project} ===")
                print(" | ".join(f"{key}={value}" for key, value in result["counts"].items()))
                print(f"provider units: {result['spend']}")
                print(f"next: seohead semantics {result['next_stage']} --project {project}")
            return result
        if stage == "export":
            count = store.export(out)
            print(f"export: {count} phrases -> {out}")
            return {"project": str(project), "stage": "export", "phrases": count}
        cfg = config.load(project)
        module = importlib.import_module(f"seohead.semantics.stages.{_MODULES.get(stage, stage)}")
        tags = {"semantics_project": str(directory), "semantics_stage": stage}
        with spend.context(**tags) if stage in PAID_STAGES else contextlib.nullcontext():
            summary = _run_stage(module, stage, store, cfg, directory, out, args)
        if stage in _EXPORTING:
            store.export(out)
        return {
            "project": str(project),
            "stage": stage,
            "result": summary,
            "counts": store.counts(),
            "spend": store.spend(),
        }


def _run_stage(module, stage, store, cfg, directory, out, args):
    if stage == "collect":
        seed_file = directory / cfg.get("seeds_file", "seeds.txt")
        seeds = (
            [line.strip() for line in seed_file.read_text(encoding="utf-8").splitlines()]
            if seed_file.exists()
            else []
        )
        seeds = [s for s in seeds if s]
        if not seeds and not args.resume:
            raise ValueError(f"no seeds: {seed_file}")
        return module.run(
            store,
            cfg,
            seeds=seeds,
            max_seeds=args.max_seeds,
            max_phrases=args.max_phrases,
            resume=args.resume,
        )
    if stage == "import":
        return module.run(store, cfg, args.file)
    if stage == "exact":
        return module.run(store, cfg, yes=args.yes, limit=args.limit)
    if stage == "cluster":
        return module.run(store, cfg, limit=args.limit, method=args.method)
    if stage in ("graph", "competitors", "report", "excel", "mine"):
        return module.run(store, cfg, out)
    return module.run(store, cfg)


def run_quiet(
    stage: str,
    project: str | Path,
    *,
    allowed: frozenset[str] | tuple[str, ...],
    confirm_paid: bool = False,
    **options: Any,
) -> dict[str, Any]:
    """Run a stage for an MCP caller; progress lines go to ``logs`` instead of stdout.

    A paid stage runs only with ``confirm_paid=True``; without it the reply names the stage
    and spends nothing. Failures are returned as ``ok: false`` rather than raised.
    """
    if stage not in allowed:
        return {"ok": False, "error": f"stage must be one of: {', '.join(sorted(allowed))}"}
    if stage in PAID_STAGES and not confirm_paid:
        return {
            "ok": False,
            "error": f"{stage} calls a paid provider; repeat with confirm_paid=true",
            "paid": True,
        }
    options.setdefault("json", True)
    options.setdefault("resume", False)
    options.setdefault("max_seeds", 0)
    options.setdefault("max_phrases", 100000)
    options.setdefault("limit", 0)
    options.setdefault("yes", False)
    options.setdefault("method", "serp")
    with contextlib.redirect_stdout(io.StringIO()) as logs:
        try:
            result = execute(stage, project, **options)
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as exc:
            return {"ok": False, "error": str(exc), "logs": logs.getvalue()}
    return {"ok": True, "result": result, "logs": logs.getvalue()}
