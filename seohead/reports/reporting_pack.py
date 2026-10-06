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


def _concat_partitions(package: Path, manifest: dict[str, Any], dataset: str) -> bytes:
    """Return one worksheet CSV with a single header across all partitions."""
    parts = manifest["datasets"][dataset]["partitions"]
    output = bytearray()
    header: bytes | None = None
    for part in parts:
        raw = (package / part["path"]).read_bytes()
        first, _, rest = raw.partition(b"\n")
        if header is None:
            header = first
            output += raw
            continue
        if first != header:
            raise ReportingPackError(f"dataset {dataset} partitions do not share one header")
        if rest:
            output += rest
    if header is None:
        raise ReportingPackError(f"dataset {dataset} has no published partition")
    return bytes(output)


def build_worksheets(
    *,
    audit: str | Path,
    provider_inputs: list[str | Path],
    search_metric: str | None,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Write ``<dataset>.csv`` worksheets plus the package manifest to ``out_dir``.

    ``out_dir`` must not exist.  The BI package is exported into a temporary
    sibling directory and removed afterwards; only the worksheet CSVs and the
    manifest are published.  The result is byte-deterministic for unchanged
    inputs and toolkit source.
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
        package = work / "package"
        result = bi.export_bi(
            audit=audit,
            provider_joins=normalized,
            search_metric=search_metric,
            out_dir=package,
        )
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        destination.mkdir()
        try:
            worksheets = {}
            for dataset in manifest["datasets"]:
                content = _concat_partitions(package, manifest, dataset)
                (destination / f"{dataset}.csv").write_bytes(content)
                worksheets[dataset] = {
                    "rows": manifest["datasets"][dataset]["row_count"],
                    "bytes": len(content),
                }
            (destination / "manifest.json").write_bytes((package / "manifest.json").read_bytes())
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
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
