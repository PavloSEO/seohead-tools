"""Offline preflight contracts for optional BI destinations.

Neither planner authenticates, opens a network connection, creates a cloud
resource, or writes a spreadsheet/table.  The returned plan is the exact review
artifact a future explicit apply operation must reconcile with the package.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from seohead.reports.bi import BI_SCHEMA_VERSION, MANIFEST_FORMAT

SHEETS_MAX_CELLS = 10_000_000
HOST_CONFIG_ENV = "SEOHEAD_BI_DESTINATIONS_FILE"
_HOST_CLIENTS: dict[tuple[str, str], Any] = {}
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class BIDestinationError(ValueError):
    """A local BI package cannot safely feed an optional destination."""


def register_host_client(destination: str, target: str, client: Any) -> None:
    """Register a host-owned, already-authorized client outside CLI/MCP JSON."""
    _HOST_CLIENTS[(destination, target)] = client


class GoogleSheetsAppendClient:
    """Host-owned staging-sheet REST client; commit swaps sheets atomically."""

    def __init__(
        self,
        target: str,
        spreadsheet_id: str,
        worksheet_id: int,
        worksheet_title: str,
        token_supplier=None,
        fetcher=None,
    ) -> None:
        self.target, self.spreadsheet_id = target, spreadsheet_id
        self.worksheet_id, self.worksheet_title = worksheet_id, worksheet_title
        self.token_supplier, self.fetcher = token_supplier, fetcher
        self.headers: dict[str, list[str]] = {}

    def authorize_target(self, target: str) -> bool:
        return target == self.target

    def begin(self, *, target: str, operation: str, schema_version: str):
        if operation != "replace":
            raise BIDestinationError("Google Sheets client supports transactional replace only")
        response = self._post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            {
                "requests": [
                    {"addSheet": {"properties": {"title": f"__seohead_stage_{schema_version}"}}}
                ]
            },
        )
        try:
            stage = response["replies"][0]["addSheet"]["properties"]
            return {
                "target": target,
                "schema_version": schema_version,
                "stage_id": stage["sheetId"],
                "stage_title": stage["title"],
            }
        except (KeyError, IndexError, TypeError) as exc:
            raise BIDestinationError("Google Sheets staging response is invalid") from exc

    def write(self, transaction, dataset: str, rows: list[list[str]]) -> None:
        if len(rows) == 1 and dataset not in self.headers:
            self.headers[dataset] = rows[0]
            return
        if dataset not in self.headers:
            raise BIDestinationError("Sheets rows arrived before their header")
        body = {"majorDimension": "ROWS", "values": rows}
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{transaction['stage_title']}!A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
        response = self._post(url, body)
        if "updates" not in response:
            raise BIDestinationError("Google Sheets append returned an invalid response")

    def _post(self, url, body):
        if self.token_supplier is None:
            from seohead.data_sources.gsc import service_account_access_token

            token = service_account_access_token(SHEETS_SCOPE)
        else:
            token = self.token_supplier(SHEETS_SCOPE)
        request = {"url": url, "body": body, "authorization": f"Bearer {token}"}
        if self.fetcher is not None:
            response = self.fetcher(request)
        else:
            raw = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                method="POST",
                headers={
                    "Authorization": request["authorization"],
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(raw, timeout=30) as stream:
                response = json.loads(stream.read().decode())
        if not isinstance(response, dict):
            raise BIDestinationError("Google Sheets response is invalid")
        return response

    def commit(self, transaction) -> None:
        self._post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            {
                "requests": [
                    {"deleteSheet": {"sheetId": self.worksheet_id}},
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": transaction["stage_id"],
                                "title": self.worksheet_title,
                            },
                            "fields": "title",
                        }
                    },
                ]
            },
        )

    def abort(self, transaction) -> None:
        self._post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            {"requests": [{"deleteSheet": {"sheetId": transaction["stage_id"]}}]},
        )


def resolve_host_client(destination: str, target: str) -> Any:
    """Resolve an exact allowlisted host target without reading credentials from input."""
    from seohead.data_sources.credentials import CONFIG_ROOT

    path = Path(os.environ.get(HOST_CONFIG_ENV, CONFIG_ROOT / "bi-destinations.json"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BIDestinationError("BI destination host configuration is unavailable") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BIDestinationError("BI destination host configuration is invalid") from exc
    allowed = (
        ((config.get(destination) or {}).get("targets") or {}) if isinstance(config, dict) else {}
    )
    if (
        target not in allowed
        or not isinstance(allowed[target], dict)
        or not allowed[target].get("enabled")
    ):
        raise BIDestinationError("destination target is not host-allowlisted")
    registered = _HOST_CLIENTS.get((destination, target))
    if registered is not None:
        return registered
    target_config = allowed[target]
    if destination == "sheets" and target_config.get("kind") == "google_sheets_service_account":
        spreadsheet_id = target_config.get("spreadsheet_id")
        worksheet_id = target_config.get("worksheet_id")
        worksheet_title = target_config.get("worksheet_title")
        if (
            isinstance(spreadsheet_id, str)
            and isinstance(worksheet_id, int)
            and isinstance(worksheet_title, str)
        ):
            return GoogleSheetsAppendClient(target, spreadsheet_id, worksheet_id, worksheet_title)
    raise BIDestinationError("host has no authorized client for the allowlisted target")


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


def filter_package(
    package: str | Path,
    *,
    dataset: str,
    out_dir: str | Path,
    where: dict[str, list[str]] | None = None,
    columns: list[str] | None = None,
    max_rows_per_file: int = 250_000,
) -> dict[str, Any]:
    """Stream one exact-filtered BI dataset to a new local CSV package.

    Predicates are closed equality sets over declared CSV fields; no SQL, regex,
    formulas or inferred segment is accepted.  An empty result remains an
    explicitly complete selected population, never an unavailable measurement.
    """
    if type(max_rows_per_file) is not int or max_rows_per_file < 1:
        raise BIDestinationError("max_rows_per_file must be a positive integer")
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    if dataset not in datasets:
        raise BIDestinationError("dataset is not declared by the BI package")
    declared = [field["name"] for field in manifest["datasets"][dataset]["fields"]]
    selected = list(columns or declared)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(declared):
        raise BIDestinationError("columns must be a non-empty unique subset of declared fields")
    predicates = where or {}
    if not isinstance(predicates, dict) or set(predicates) - set(declared):
        raise BIDestinationError("where keys must be declared fields")
    if any(
        not isinstance(values, list) or any(not isinstance(value, str) for value in values)
        for values in predicates.values()
    ):
        raise BIDestinationError("where values must be lists of exact string values")
    destination = Path(out_dir).absolute()
    if destination.is_symlink() or os.path.lexists(destination) or not destination.parent.is_dir():
        raise BIDestinationError("out_dir must be a new child of an existing non-symlink directory")
    output_parts = []
    total = part_rows = 0
    stream = writer = path = None
    with tempfile.TemporaryDirectory(prefix=".seohead-bi-filter-", dir=destination.parent) as temp:
        stage = Path(temp)

        def open_part():
            nonlocal stream, writer, path, part_rows
            path = stage / f"{dataset}-{len(output_parts) + 1:04d}.csv"
            stream = path.open("w", encoding="utf-8", newline="")
            writer = csv.DictWriter(stream, fieldnames=selected, lineterminator="\n")
            writer.writeheader()
            part_rows = 0

        def close_part():
            nonlocal stream
            if stream is None:
                return
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            content = path.read_bytes()
            output_parts.append(
                {
                    "path": path.name,
                    "rows": part_rows,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
            stream = None

        open_part()
        for part in manifest["datasets"][dataset]["partitions"]:
            with (root / part["path"]).open(encoding="utf-8", newline="") as source:
                for row in csv.DictReader(source):
                    if all(row[key] in allowed for key, allowed in predicates.items()):
                        if part_rows >= max_rows_per_file:
                            close_part()
                            open_part()
                        writer.writerow({key: row[key] for key in selected})
                        part_rows += 1
                        total += 1
        close_part()
        result = {
            "format": "seohead.bi-filter.v1",
            "source_schema_version": manifest["schema_version"],
            "dataset": dataset,
            "columns": selected,
            "where": predicates,
            "row_count": total,
            "partitions": output_parts,
        }
        (stage / "manifest.json").write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(stage, destination)
    return {"ok": True, "output_directory": str(destination), **result}
