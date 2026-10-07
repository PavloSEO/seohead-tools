"""Build the committed synthetic reporting-pack worksheets.

The Looker Studio consumer connects one Google Sheets worksheet per BI
dataset.  This module regenerates those worksheets from a supplied synthetic
audit plus committed ``seohead.reporting-pack-source.v1`` provider inputs, so
the example fixture always matches the versioned ``seohead.bi-manifest.v1``
projection.  It performs no network, provider or Google call.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from seohead.data_sources.evidence_import import normalize_inline
from seohead.reports import bi

SOURCE_FORMAT = "seohead.reporting-pack-source.v1"
MAX_SOURCE_BYTES = 1_000_000
WORKSHEET_FORMAT = "seohead.reporting-pack-worksheets.v1"


class ReportingPackError(ValueError):
    """A synthetic reporting-pack input or output is outside its contract."""


def load_provider_input(path: str | Path) -> dict[str, Any]:
    """Return the normalized evidence document for one committed source file."""
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ReportingPackError("provider input must be an existing regular JSON file")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ReportingPackError("provider input could not be read") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise ReportingPackError("provider input exceeds the 1 MiB fixture bound")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportingPackError("provider input must be valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("format") != SOURCE_FORMAT:
        raise ReportingPackError(f"provider input requires format {SOURCE_FORMAT}")
    mapping = document.get("mapping")
    if not isinstance(mapping, dict):
        raise ReportingPackError("provider input requires an evidence-mapping.v1 object")
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ReportingPackError("provider input requires a non-empty row list")
    return normalize_inline(rows, manifest=mapping)


def build_worksheets(
    *,
    audit: str | Path,
    provider_inputs: list[str | Path],
    search_metric: str | None,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Publish the native BI CSV partitions and their manifest to ``out_dir``.

    ``out_dir`` must not exist. Temporary normalized provider inputs are removed
    after the BI exporter publishes the complete package. Partition paths,
    counts and checksums remain unchanged and usable by existing BI consumers.
    The result is byte-deterministic for unchanged inputs and toolkit source.
    """
    destination = Path(out_dir)
    if destination.is_symlink() or destination.exists():
        raise ReportingPackError("worksheet output directory must be a new path")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ReportingPackError("worksheet output parent must be a real directory")
    work = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=parent))
    try:
        normalized: list[Path] = []
        for index, path in enumerate(provider_inputs):
            document = load_provider_input(path)
            normalized_path = work / f"provider-{index:02d}.json"
            normalized_path.write_text(
                json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                encoding="utf-8",
            )
            normalized.append(normalized_path)
        result = bi.export_bi(
            audit=audit,
            provider_joins=normalized,
            search_metric=search_metric,
            out_dir=destination,
        )
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        worksheets = {
            name: {"rows": dataset["row_count"], "bytes": dataset["bytes"]}
            for name, dataset in manifest["datasets"].items()
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "format": WORKSHEET_FORMAT,
        "network": False,
        "writes": "local worksheets only",
        "output_directory": str(destination),
        "worksheets": worksheets,
        "run_id": result.get("run_id"),
        "search_metric": search_metric,
    }
