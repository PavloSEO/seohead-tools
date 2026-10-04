"""Offline preflight contracts for optional BI destinations.

Neither planner authenticates, opens a network connection, creates a cloud
resource, or writes a spreadsheet/table.  The returned plan is the exact review
artifact a future explicit apply operation must reconcile with the package.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from seohead.reports.bi import BI_SCHEMA_VERSION, MANIFEST_FORMAT

SHEETS_MAX_CELLS = 10_000_000


class BIDestinationError(ValueError):
    """A local BI package cannot safely feed an optional destination."""


def _manifest(package: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(package)
    if root.is_symlink() or not root.is_dir():
        raise BIDestinationError("package must be an existing non-symlink BI package directory")
    path = root / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise BIDestinationError("package has no regular manifest.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BIDestinationError(f"package manifest is unreadable: {exc}") from exc
    if value.get("format") != MANIFEST_FORMAT or value.get("schema_version") != BI_SCHEMA_VERSION:
        raise BIDestinationError("package has an unsupported BI manifest/schema version")
    if not isinstance(value.get("datasets"), dict):
        raise BIDestinationError("package manifest has no dataset declarations")
    return root, value


def _verify_partitions(root: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    datasets: dict[str, dict[str, Any]] = {}
    for name, dataset in manifest["datasets"].items():
        if not isinstance(dataset, dict) or not isinstance(dataset.get("fields"), list):
            raise BIDestinationError(f"dataset {name!r} has no declared fields")
        rows = bytes_count = 0
        for part in dataset.get("partitions") or []:
            path = root / str(part.get("path") or "")
            if path.parent != root or path.is_symlink() or not path.is_file():
                raise BIDestinationError(f"dataset {name!r} has an invalid partition path")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != part.get("sha256"):
                raise BIDestinationError(
                    f"dataset {name!r} partition checksum does not match manifest"
                )
            rows += int(part.get("rows") or 0)
            bytes_count += len(content)
        if rows != dataset.get("row_count") or bytes_count != dataset.get("bytes"):
            raise BIDestinationError(f"dataset {name!r} partition totals do not match manifest")
        datasets[name] = {"rows": rows, "fields": len(dataset["fields"]), "bytes": bytes_count}
    return datasets


def sheets_plan(package: str | Path, *, max_cells: int = SHEETS_MAX_CELLS) -> dict[str, Any]:
    """Preflight one-worksheet-per-dataset Sheets import without an API call."""
    if type(max_cells) is not int or not 1 <= max_cells <= SHEETS_MAX_CELLS:
        raise BIDestinationError(f"max_cells must be 1..{SHEETS_MAX_CELLS}")
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    worksheets = []
    used_cells = 0
    for name, info in datasets.items():
        rows_with_header = info["rows"] + 1
        cells = rows_with_header * info["fields"]
        used_cells += cells
        worksheets.append(
            {
                "worksheet": name,
                "rows": info["rows"],
                "header_rows": 1,
                "columns": info["fields"],
                "cells": cells,
                "operation": "append_or_replace_requires_explicit_apply",
            }
        )
    return {
        "format": "seohead.bi-sheets-plan.v1",
        "network": False,
        "apply": False,
        "package_schema_version": manifest["schema_version"],
        "worksheets": worksheets,
        "required_cells": used_cells,
        "capacity_cells": max_cells,
        "state": "ready" if used_cells <= max_cells else "unavailable",
        "reason": None
        if used_cells <= max_cells
        else "package exceeds declared Sheets cell capacity",
        "required_authorization": "explicit spreadsheet target, least-privilege Google grant, and apply confirmation",
    }


def bigquery_plan(
    package: str | Path, *, dataset: str, operation: str = "replace"
) -> dict[str, Any]:
    """Describe an optional BigQuery load without a project, credential, or write."""
    if not isinstance(dataset, str) or not dataset:
        raise BIDestinationError("dataset is required for a BigQuery plan")
    if operation not in {"replace", "append"}:
        raise BIDestinationError("operation must be 'replace' or 'append'")
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    return {
        "format": "seohead.bi-bigquery-plan.v1",
        "network": False,
        "apply": False,
        "billing_required": True,
        "package_schema_version": manifest["schema_version"],
        "dataset": dataset,
        "operation": operation,
        "tables": [
            {"table": f"seohead_{name}_v1", "rows": info["rows"], "columns": info["fields"]}
            for name, info in datasets.items()
        ],
        "required_authorization": "explicit billed project/dataset target and separate cost plus apply confirmation",
    }


def _csv_chunks(root: Path, dataset: dict[str, Any], size: int = 1_000):
    """Yield a header once and bounded rows from every verified partition."""
    header = None
    chunk = []
    for part in dataset["partitions"]:
        with (root / part["path"]).open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream)
            current_header = next(rows)
            if header is None:
                header = current_header
                yield [header]
            elif current_header != header:
                raise BIDestinationError("dataset partition headers disagree")
            for row in rows:
                chunk.append(row)
                if len(chunk) == size:
                    yield chunk
                    chunk = []
    if chunk:
        yield chunk


def apply_with_client(
    package: str | Path,
    *,
    target: str,
    operation: str,
    client: Any,
    apply: bool = False,
) -> dict[str, Any]:
    """Stream a package through an injected, already-authorized transaction client.

    A concrete Google client belongs outside this core.  It must implement
    ``authorize_target``, ``begin``, ``write`` and ``commit``; an exception triggers
    its optional ``abort`` hook, preserving the prior target until commit.
    """
    if not apply:
        raise BIDestinationError("apply=True is required for a destination write")
    if operation not in {"replace", "append"}:
        raise BIDestinationError("operation must be 'replace' or 'append'")
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    if not isinstance(target, str) or not target:
        raise BIDestinationError("an explicit destination target is required")
    if client.authorize_target(target) is not True:
        raise BIDestinationError("injected client did not authorize the requested target")
    transaction = client.begin(target=target, operation=operation, schema_version=BI_SCHEMA_VERSION)
    written = {}
    try:
        for name, info in datasets.items():
            count = 0
            first_chunk = True
            for rows in _csv_chunks(root, manifest["datasets"][name]):
                client.write(transaction, name, rows)
                count += len(rows) - (1 if first_chunk else 0)
                first_chunk = False
            if count != info["rows"]:
                raise BIDestinationError(f"destination row conservation failed for {name!r}")
            written[name] = count
        client.commit(transaction)
    except BaseException:
        abort = getattr(client, "abort", None)
        if abort is not None:
            abort(transaction)
        raise
    return {
        "format": "seohead.bi-destination-apply.v1",
        "target": target,
        "operation": operation,
        "rows": written,
    }
