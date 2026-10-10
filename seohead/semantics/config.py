"""Project configuration for one semantic core.

A semantic project is a directory holding ``project.yaml``, ``seeds.txt`` and the SQLite
database. Passing a SEOHEAD project workspace (a directory with ``project.json``) places the
semantic core in its ``semantics/`` subdirectory, so the workspace always knows where its
core lives. ``project.yaml`` names the database file (``db: sya.db`` by default), which is the
path the project keeps to its semantic database. Secrets never live here: provider keys are
resolved by ``seohead.data_sources.credentials``.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULTS = {
    "db": "sya.db",
    "regions": ["225"],  # 225 = all of Russia; city regions are separate passes.
    "min_base": 20,  # minimum base frequency for a kept phrase
    # Base frequency required to expand a phrase again. Expansion has no natural end: a low
    # floor makes the frontier explode, so large pools need 300..1000 to converge.
    "reexpand_floor": 100,
    "max_depth": 3,
    "num": 300,  # Wordstat phrases per seed (maximum 2000)
    "rps": 5,
    "preset": "generic",  # named filter preset; intent/stop/info override it
    "intent": "",
    "stop": "",
    "info": "",
    "any_intent": False,  # true keeps every phrase regardless of topic intent
    # Frequent topic tokens removed before word-graph clustering. Project-specific.
    "anchor_words": [],
    "exact_cut_base": 20,  # base < 20 and exact < 10 -> dead
    "exact_cut_exact": 10,
    "serp_overlap": 3,  # shared top-10 URLs needed to join two phrases
    "budget_arsenkin_gate": 2000,  # exact asks for --yes above this many new phrases
}


def data_dir(project: str | Path) -> Path:
    """Resolve the semantic directory for a project path without creating it."""
    if not str(project).strip():
        raise ValueError("--project must name a directory")
    root = Path(project).expanduser()
    if (root / "project.json").is_file():
        return root / "semantics"
    return root


def _strip_comment(line):
    """Keep hashes inside quoted scalars and regular expressions."""
    quote = None
    escaped = False
    for i, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None:
            return line[:i]
    return line


def _parse_scalar(v):
    v = v.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
    return v.strip("\"'")


def load(project: str | Path) -> dict:
    """Read the flat ``key: value`` / ``[a, b]`` YAML subset without a YAML dependency."""
    cfg = dict(DEFAULTS)
    directory = data_dir(project)
    p = directory / "project.yaml"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = _strip_comment(line).rstrip()
            if not line or ":" not in line or line[0] in " \t":
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            if v.strip():
                cfg[k] = _parse_scalar(v)
    cfg["_project"] = directory.name if directory.name != "semantics" else directory.parent.name
    cfg["_dir"] = str(directory)
    return cfg


def db_path(project: str | Path) -> Path:
    """The database path recorded in ``project.yaml``, relative to the semantic directory."""
    directory = data_dir(project)
    configured = Path(str(load(project)["db"])).expanduser()
    return configured if configured.is_absolute() else directory / configured


TEMPLATE = """# seohead semantics project config
# Provider keys never live here; see `seohead sources doctor`.
db: sya.db                   # semantic database of this project (relative to this file)
seeds_file: seeds.txt        # one seed phrase per line
regions: [225]               # Yandex region id; run each city as its own pass
min_base: 20
reexpand_floor: 100
max_depth: 3
num: 300
preset: generic              # generic | seo; intent/stop/info below override it
anchor_words: []             # frequent topic words removed before word-graph clustering
# intent:  custom topic-intent regex
# stop:    extra stop-list regex
# info:    informational-intent regex (routes phrases to the blog core)
# any_intent: false
exact_cut_base: 20
exact_cut_exact: 10
serp_overlap: 3
budget_arsenkin_gate: 2000
"""


def ensure_project(project: str | Path) -> Path:
    d = data_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    cfgp = d / "project.yaml"
    if not cfgp.exists():
        cfgp.write_text(TEMPLATE, encoding="utf-8")
    seeds = d / "seeds.txt"
    if not seeds.exists():
        seeds.write_text("", encoding="utf-8")
    return d
