"""Offline preflight contracts for optional BI destinations.

Neither planner authenticates, opens a network connection, creates a cloud
resource, or writes a spreadsheet/table.  The returned plan is the exact review
artifact a future explicit apply operation must reconcile with the package.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any

from seohead.reports.bi import (
    BI_SCHEMA_VERSION,
    DATASET_SPECS,
    DEFAULT_BYTES_PER_PARTITION,
    DEFAULT_MAX_OUTPUT_BYTES,
    MANIFEST_FORMAT,
    MAX_BYTES_PER_PARTITION,
    MAX_CELL_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_PARTITIONS,
    MAX_PROJECTION_INDEX_BYTES,
    MAX_ROWS_PER_PARTITION,
    MIN_FREE_DISK_BYTES,
    BIExportError,
    Field,
    _cell_text,
    _OutputBudget,
    _PartitionWriter,
    _safe_dimension_fields,
)

SHEETS_MAX_CELLS = 10_000_000
HOST_CONFIG_ENV = "SEOHEAD_BI_DESTINATIONS_FILE"
_HOST_CLIENTS: dict[tuple[str, str], Any] = {}
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery"
_GOOGLE_RETRIES = 3
_GOOGLE_CHUNK_ROWS = 1_000
_GOOGLE_REQUEST_BYTES = 4 * 1024 * 1024
_GOOGLE_CHUNK_SOURCE_BYTES = _GOOGLE_REQUEST_BYTES // 2
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_CELL_CHARS = 32_767
MAX_XLSX_SHEETS = 10_000
DESTINATION_STATE_FORMAT = "seohead.bi-destination-state.v1"
DESTINATION_STATE_DIRECTORY = ".seohead-destination-state"


class BIDestinationError(ValueError):
    """A local BI package cannot safely feed an optional destination."""


class BIDestinationCommitUncertain(BIDestinationError):
    """Google may have committed a request after the client lost its response."""


def _state_identity(*, destination: str, target: str, operation: str, manifest_sha256: str) -> str:
    """Return a stable non-secret identity for one resumable destination attempt."""
    encoded = json.dumps(
        {
            "destination": destination,
            "target": target,
            "operation": operation,
            "manifest_sha256": manifest_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_path(
    root: Path, *, destination: str, target: str, operation: str, manifest_sha256: str
) -> Path:
    """Keep durable progress beside the local package without altering its manifest."""
    return (
        root
        / DESTINATION_STATE_DIRECTORY
        / (
            _state_identity(
                destination=destination,
                target=target,
                operation=operation,
                manifest_sha256=manifest_sha256,
            )
            + ".json"
        )
    )


def _save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomically retain a restartable, credential-free publication checkpoint."""
    try:
        encoded = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise BIDestinationError("destination transaction cannot be persisted safely") from exc
    try:
        path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".state-", delete=False) as stream:
            stream.write(encoded)
            temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        with suppress(UnboundLocalError, FileNotFoundError):
            temporary.unlink()
        raise BIDestinationError("destination progress state cannot be persisted") from exc


def _load_state(path: Path, *, expected: dict[str, str]) -> dict[str, Any] | None:
    """Load only the checkpoint belonging to this exact package and target."""
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise BIDestinationError("destination progress state is not a regular file")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BIDestinationError("destination progress state is unreadable") from exc
    if not isinstance(state, dict) or state.get("format") != DESTINATION_STATE_FORMAT:
        raise BIDestinationError("destination progress state has an unsupported format")
    if any(state.get(key) != value for key, value in expected.items()):
        raise BIDestinationError("destination progress state belongs to another package or target")
    if not isinstance(state.get("transaction"), (dict, str, int, float, list, type(None))):
        raise BIDestinationError("destination progress state has an invalid transaction")
    if not isinstance(state.get("datasets"), dict):
        raise BIDestinationError("destination progress state has no dataset accounting")
    return state


def _new_state(
    *,
    destination: str,
    target: str,
    operation: str,
    manifest_sha256: str,
    datasets: dict[str, dict[str, Any]],
    transaction: Any,
) -> dict[str, Any]:
    return {
        "format": DESTINATION_STATE_FORMAT,
        "destination": destination,
        "target": target,
        "operation": operation,
        "manifest_sha256": manifest_sha256,
        "status": "staging",
        "transaction": transaction,
        "pending": None,
        "datasets": {
            name: {
                "input_rows": info["rows"],
                "written_rows": 0,
                "skipped_rows": 0,
                "failed_rows": 0,
                "next_chunk": 0,
            }
            for name, info in datasets.items()
        },
    }


def _accounting(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Return complete per-dataset counts without exposing internal cursor state."""
    result: dict[str, dict[str, int]] = {}
    for name, value in state["datasets"].items():
        if not isinstance(value, dict):
            raise BIDestinationError("destination progress state has an invalid dataset entry")
        counts = {}
        for key in ("input_rows", "written_rows", "skipped_rows", "failed_rows"):
            number = value.get(key)
            if type(number) is not int or number < 0:
                raise BIDestinationError("destination progress state has invalid row accounting")
            counts[key] = number
        result[name] = counts
    return result


def _result_from_state(state: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    """Expose restart-safe accounting for committed, failed and uncertain states."""
    accounting = _accounting(state)
    result: dict[str, Any] = {
        "format": "seohead.bi-destination-apply.v1",
        "target": state["target"],
        "operation": state["operation"],
        "state": state["status"],
        "manifest_sha256": state["manifest_sha256"],
        "rows": {name: values["written_rows"] for name, values in accounting.items()},
        "input_rows": {name: values["input_rows"] for name, values in accounting.items()},
        "skipped_rows": {name: values["skipped_rows"] for name, values in accounting.items()},
        "failed_rows": {name: values["failed_rows"] for name, values in accounting.items()},
    }
    if reason:
        result["reason"] = reason
    return result


def register_host_client(destination: str, target: str, client: Any) -> None:
    """Register a host-owned, already-authorized client outside CLI/MCP JSON."""
    _HOST_CLIENTS[(destination, target)] = client


def _require_dataset_mapping(
    supplied: Any,
    datasets: dict[str, Any],
    *,
    label: str,
    id_name: str,
    allow_extra: bool = False,
) -> dict[str, dict[str, Any]]:
    """Require one closed, host-owned mapping for every declared package dataset."""
    if (
        not isinstance(supplied, dict)
        or not set(datasets).issubset(supplied)
        or (not allow_extra and set(supplied) != set(datasets))
    ):
        raise BIDestinationError(f"{label} mapping must name every BI package dataset exactly once")
    mapped: dict[str, dict[str, Any]] = {}
    for name in datasets:
        value = supplied[name]
        if not isinstance(value, dict) or not isinstance(value.get(id_name), (str, int)):
            raise BIDestinationError(f"{label} mapping for {name!r} is invalid")
        mapped[name] = value
    return mapped


class _GoogleRESTClient:
    """Small authenticated JSON REST boundary shared by explicit Google destinations."""

    scope: str

    def __init__(self, *, token_supplier=None, fetcher=None) -> None:
        self.token_supplier = token_supplier
        self.fetcher = fetcher

    def _token(self) -> str:
        if self.token_supplier is not None:
            token = self.token_supplier(self.scope)
        else:
            from seohead.data_sources.gsc import service_account_access_token

            token = service_account_access_token(self.scope)
        if not isinstance(token, str) or not token:
            raise BIDestinationError("Google service-account token supplier returned no token")
        return token

    def _request(
        self,
        method: str,
        url: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        retryable: bool = False,
    ) -> dict[str, Any]:
        """Make a bounded retry only where replaying the same request is safe."""
        for attempt in range(_GOOGLE_RETRIES + 1):
            request = {
                "method": method,
                "url": url,
                "body": body,
                "authorization": f"Bearer {self._token()}",
                "headers": headers or {},
            }
            try:
                if self.fetcher is not None:
                    response = self.fetcher(request)
                else:
                    encoded = (
                        body
                        if isinstance(body, bytes)
                        else json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
                            "utf-8"
                        )
                        if body is not None
                        else None
                    )
                    if encoded is not None and len(encoded) > _GOOGLE_REQUEST_BYTES:
                        raise BIDestinationError(
                            "Google request exceeds the bounded payload size before transport"
                        )
                    request_headers = {
                        "Authorization": request["authorization"],
                        "Content-Type": "application/json",
                        **(headers or {}),
                    }
                    raw = urllib.request.Request(
                        url, data=encoded, method=method, headers=request_headers
                    )
                    with urllib.request.urlopen(raw, timeout=30) as stream:
                        response = json.loads(stream.read().decode())
            except urllib.error.HTTPError as exc:
                if (
                    retryable
                    and exc.code in {429, 500, 502, 503, 504}
                    and attempt < _GOOGLE_RETRIES
                ):
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise BIDestinationError(
                    f"Google API rejected the request with HTTP {exc.code}"
                ) from exc
            except TimeoutError as exc:
                raise BIDestinationCommitUncertain(
                    "Google response timed out; commit state is unknown"
                ) from exc
            except OSError as exc:
                raise BIDestinationCommitUncertain(
                    "Google transport failed; commit state is unknown"
                ) from exc
            if not isinstance(response, dict):
                raise BIDestinationError("Google API response is invalid")
            error = response.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if retryable and code in {429, 500, 502, 503, 504} and attempt < _GOOGLE_RETRIES:
                time.sleep(0.1 * (attempt + 1))
                continue
            if error:
                raise BIDestinationError(f"Google API rejected the request: {error}")
            return response
        raise AssertionError("bounded Google retry loop did not return")


class GoogleSheetsClient(_GoogleRESTClient):
    """Host-owned Sheets replace client that preserves each configured sheet ID."""

    scope = SHEETS_SCOPE

    def __init__(
        self,
        target: str,
        spreadsheet_id: str,
        worksheets: dict[str, dict[str, Any]],
        token_supplier=None,
        fetcher=None,
    ) -> None:
        super().__init__(token_supplier=token_supplier, fetcher=fetcher)
        self.destination = "sheets"
        self.target, self.spreadsheet_id, self.worksheets = target, spreadsheet_id, worksheets

    def authorize_target(self, target: str) -> bool:
        return target == self.target

    def begin(
        self,
        *,
        target: str,
        operation: str,
        schema_version: str,
        datasets: dict[str, Any],
        package_sha256: str,
        selected_projection: bool = False,
    ):
        if operation != "replace":
            raise BIDestinationError(
                "Google Sheets append is unavailable: use replace so every package is reconciled"
            )
        mapping = _require_dataset_mapping(
            self.worksheets,
            datasets,
            label="Google Sheets worksheet",
            id_name="worksheet_id",
            allow_extra=selected_projection,
        )
        for name, value in mapping.items():
            if (
                not isinstance(value.get("worksheet_id"), int)
                or not isinstance(value.get("worksheet_title"), str)
                or not value["worksheet_title"]
            ):
                raise BIDestinationError(f"Google Sheets worksheet mapping for {name!r} is invalid")
        metadata = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}?fields=sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))",
            retryable=True,
        )
        properties = [
            item.get("properties")
            for item in metadata.get("sheets", [])
            if isinstance(item, dict) and isinstance(item.get("properties"), dict)
        ]
        existing = {
            (item.get("sheetId"), item.get("title")): item.get("gridProperties")
            for item in properties
        }
        if any(
            (value["worksheet_id"], value["worksheet_title"]) not in existing
            for value in mapping.values()
        ):
            raise BIDestinationError(
                "Google Sheets target does not contain every configured worksheet ID/title"
            )
        existing_cells = 0
        for grid in existing.values():
            if (
                not isinstance(grid, dict)
                or not isinstance(grid.get("rowCount"), int)
                or not isinstance(grid.get("columnCount"), int)
            ):
                raise BIDestinationError("Google Sheets target capacity is unavailable")
            existing_cells += grid["rowCount"] * grid["columnCount"]
        stage_cells = sum(
            (int(dataset.get("row_count") or 0) + 1) * len(dataset.get("fields") or [])
            for dataset in datasets.values()
        )
        if existing_cells + stage_cells > SHEETS_MAX_CELLS:
            raise BIDestinationError("Google Sheets target cannot hold the required staging cells")
        nonce = package_sha256[:12]
        response = self._request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": f"__seohead_stage_{name}_{nonce}",
                                "gridProperties": {
                                    "rowCount": int(dataset.get("row_count") or 0) + 1,
                                    "columnCount": len(dataset.get("fields") or []),
                                },
                            }
                        }
                    }
                    for name, dataset in datasets.items()
                ]
            },
        )
        try:
            stages = {
                name: response["replies"][index]["addSheet"]["properties"]
                for index, name in enumerate(datasets)
            }
            if any(
                not isinstance(stage.get("sheetId"), int) or not isinstance(stage.get("title"), str)
                for stage in stages.values()
            ):
                raise TypeError("invalid staging sheet")
            return {
                "target": target,
                "schema_version": schema_version,
                "package_sha256": package_sha256,
                "stages": stages,
                "worksheets": mapping,
                "headers": {},
                "rows": {name: 0 for name in datasets},
            }
        except (KeyError, IndexError, TypeError) as exc:
            raise BIDestinationError("Google Sheets staging response is invalid") from exc

    def reconcile_begin(
        self,
        *,
        target: str,
        operation: str,
        schema_version: str,
        datasets: dict[str, Any],
        package_sha256: str,
        selected_projection: bool = False,
    ):
        """Recover deterministic stage sheets after a lost begin response."""
        if operation != "replace":
            raise BIDestinationError("Google Sheets append is unavailable: use replace")
        mapping = _require_dataset_mapping(
            self.worksheets,
            datasets,
            label="Google Sheets worksheet",
            id_name="worksheet_id",
            allow_extra=selected_projection,
        )
        metadata = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}?fields=sheets.properties(sheetId,title)",
            retryable=True,
        )
        properties = [
            item.get("properties")
            for item in metadata.get("sheets", [])
            if isinstance(item, dict) and isinstance(item.get("properties"), dict)
        ]
        nonce = package_sha256[:12]
        expected = {name: f"__seohead_stage_{name}_{nonce}" for name in datasets}
        stages = {
            name: next((item for item in properties if item.get("title") == title), None)
            for name, title in expected.items()
        }
        present = [stage for stage in stages.values() if stage is not None]
        if not present:
            return "not_applied"
        if len(present) != len(expected) or any(
            not isinstance(stage.get("sheetId"), int) or not isinstance(stage.get("title"), str)
            for stage in present
        ):
            return "uncertain"
        return {
            "target": target,
            "schema_version": schema_version,
            "package_sha256": package_sha256,
            "stages": stages,
            "worksheets": mapping,
            "headers": {},
            "rows": {name: 0 for name in datasets},
        }

    def write(self, transaction, dataset: str, rows: list[list[str]]) -> None:
        if dataset not in transaction["stages"]:
            raise BIDestinationError("Google Sheets write named an undeclared dataset")
        if not rows:
            return
        header = transaction["headers"].get(dataset)
        if header is None:
            transaction["headers"][dataset] = rows[0]
            header = rows[0]
        elif len(rows[0]) != len(header):
            raise BIDestinationError("Google Sheets row width differs from the declared header")
        if any(len(row) != len(header) for row in rows):
            raise BIDestinationError("Google Sheets row width differs from the declared header")
        stage = transaction["stages"][dataset]
        title = stage["title"].replace("'", "''")
        start = transaction["rows"][dataset] + 1
        end = transaction["rows"][dataset] + len(rows)
        width = _a1_column(len(header))
        body = {
            "valueInputOption": "RAW",
            "data": [
                {
                    "range": f"'{title}'!A{start}:{width}{end}",
                    "majorDimension": "ROWS",
                    "values": rows,
                }
            ],
        }
        response = self._request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values:batchUpdate",
            body,
            retryable=True,
        )
        if response.get("totalUpdatedRows") != len(rows):
            raise BIDestinationError("Google Sheets did not acknowledge every staged row")
        transaction["rows"][dataset] += len(rows)
        readback = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/'{title}'!A{start}:{width}{end}",
            retryable=True,
        )
        values = readback.get("values")
        if values != rows:
            raise BIDestinationError("Google Sheets staged-row readback does not conserve values")

    def pending_write(
        self, transaction: dict[str, Any], dataset: str, rows: list[list[str]], chunk: int
    ) -> dict[str, Any]:
        """Describe one fixed stage range before its idempotent bounded write."""
        if dataset not in transaction["stages"] or not rows:
            raise BIDestinationError("Google Sheets pending write is invalid")
        header = transaction["headers"].get(dataset) or rows[0]
        if any(len(row) != len(header) for row in rows):
            raise BIDestinationError("Google Sheets row width differs from the declared header")
        stage = transaction["stages"][dataset]
        start = transaction["rows"][dataset] + 1
        end = start + len(rows) - 1
        return {
            "kind": "sheets_write",
            "dataset": dataset,
            "chunk": chunk,
            "start": start,
            "end": end,
            "width": _a1_column(len(header)),
            "stage_title": stage["title"],
            "rows_sha256": hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }

    def reconcile_pending(
        self, transaction: dict[str, Any], pending: dict[str, Any], rows: list[list[str]]
    ) -> str:
        """Read a previously uncertain stage range before any retry is allowed."""
        if pending.get("kind") != "sheets_write":
            raise BIDestinationCommitUncertain("Sheets pending operation requires operator review")
        if pending.get("dataset") not in transaction["stages"] or not rows:
            raise BIDestinationError(
                "Sheets pending write does not match retained transaction state"
            )
        digest = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if digest != pending.get("rows_sha256"):
            raise BIDestinationError("Sheets pending write does not match the local package")
        title = str(pending.get("stage_title", "")).replace("'", "''")
        start, end, width = pending.get("start"), pending.get("end"), pending.get("width")
        if type(start) is not int or type(end) is not int or not isinstance(width, str):
            raise BIDestinationError("Sheets pending write has an invalid range")
        readback = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/'{title}'!A{start}:{width}{end}",
            retryable=True,
        )
        if readback.get("values") != rows:
            return "not_applied"
        dataset = pending["dataset"]
        if transaction["headers"].get(dataset) is None:
            transaction["headers"][dataset] = rows[0]
        transaction["rows"][dataset] += len(rows)
        return "applied"

    def reconcile_commit(self, transaction: dict[str, Any]) -> str:
        """Only retry a timed-out atomic switch when every staged sheet still exists."""
        metadata = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}?fields=sheets.properties(sheetId,title)",
            retryable=True,
        )
        present = {
            (item.get("properties") or {}).get("sheetId")
            for item in metadata.get("sheets", [])
            if isinstance(item, dict) and isinstance(item.get("properties"), dict)
        }
        expected = {stage["sheetId"] for stage in transaction.get("stages", {}).values()}
        if expected and expected.issubset(present):
            return "not_applied"
        raise BIDestinationCommitUncertain(
            "Sheets final switch cannot be reconciled from retained staging sheets; operator review is required"
        )

    def commit(self, transaction) -> dict[str, Any]:
        requests: list[dict[str, Any]] = []
        for name, stage in transaction["stages"].items():
            header = transaction["headers"].get(name)
            if header is None:
                raise BIDestinationError(f"Google Sheets dataset {name!r} has no header")
            original = transaction["worksheets"][name]
            requests.extend(
                [
                    {
                        "updateCells": {
                            "range": {"sheetId": original["worksheet_id"]},
                            "fields": "userEnteredValue",
                        }
                    },
                    {
                        "copyPaste": {
                            "source": {
                                "sheetId": stage["sheetId"],
                                "startRowIndex": 0,
                                "endRowIndex": transaction["rows"][name],
                                "startColumnIndex": 0,
                                "endColumnIndex": len(header),
                            },
                            "destination": {
                                "sheetId": original["worksheet_id"],
                                "startRowIndex": 0,
                                "startColumnIndex": 0,
                            },
                            "pasteType": "PASTE_VALUES",
                            "pasteOrientation": "NORMAL",
                        }
                    },
                ]
            )
        requests.extend(
            {"deleteSheet": {"sheetId": stage["sheetId"]}}
            for stage in transaction["stages"].values()
        )
        self._request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            {"requests": requests},
        )
        return {
            "publication": "atomic_spreadsheet_batch_update",
            "remote_verification": "each bounded staged range was read back before publication",
        }

    def abort(self, transaction) -> None:
        stages = transaction.get("stages") or {}
        if not stages:
            return
        self._request(
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
            {
                "requests": [
                    {"deleteSheet": {"sheetId": stage["sheetId"]}} for stage in stages.values()
                ]
            },
        )


# The old name remains import-compatible for hosts that loaded the prior packet.
GoogleSheetsAppendClient = GoogleSheetsClient


def _a1_column(width: int) -> str:
    if type(width) is not int or width < 1:
        raise BIDestinationError("Google Sheets requires at least one column")
    letters = ""
    while width:
        width, remainder = divmod(width - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _bq_type(field_type: str) -> str:
    return {
        "boolean": "BOOL",
        "integer": "INT64",
        "number": "FLOAT64",
        "date": "DATE",
        "json": "JSON",
        "string": "STRING",
    }.get(field_type, "")


def _bq_value(value: str, field_type: str) -> Any:
    """Decode a formula-safe BI CSV cell only into its declared BigQuery type."""
    if value == "":
        return None
    try:
        if field_type == "boolean":
            if value not in {"true", "false"}:
                raise ValueError
            return value == "true"
        if field_type == "integer":
            return int(value)
        if field_type == "number":
            return float(value)
        if field_type == "json":
            return json.loads(value)
        return value
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BIDestinationError(
            f"BI CSV value is invalid for declared {field_type!r} field"
        ) from exc


class GoogleBigQueryClient(_GoogleRESTClient):
    """Explicit BigQuery REST client with per-table staging and job reconciliation.

    BigQuery commits each completed load or copy job atomically.  A replace of a
    multi-dataset BI package therefore has atomic publication *per table*, never
    a fictional all-table transaction.  Staging tables remain until a failed run
    is cleaned up; a completed copy preserves the configured destination table.
    """

    scope = BIGQUERY_SCOPE

    def __init__(
        self,
        target: str,
        project_id: str,
        dataset_id: str,
        tables: dict[str, dict[str, Any]],
        *,
        location: str | None = None,
        cost_authorized: bool = False,
        token_supplier=None,
        fetcher=None,
    ) -> None:
        super().__init__(token_supplier=token_supplier, fetcher=fetcher)
        self.destination = "bigquery"
        self.target = target
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.tables = tables
        self.location = location
        self.cost_authorized = cost_authorized

    def authorize_target(self, target: str) -> bool:
        return target == self.target

    def _url(self, suffix: str) -> str:
        return f"https://bigquery.googleapis.com/bigquery/v2/projects/{self.project_id}/{suffix}"

    def _job_url(self, job_id: str) -> str:
        suffix = f"jobs/{job_id}"
        if self.location:
            suffix += f"?location={self.location}"
        return self._url(suffix)

    def begin(
        self,
        *,
        target: str,
        operation: str,
        schema_version: str,
        datasets: dict[str, Any],
        package_sha256: str,
        selected_projection: bool = False,
    ) -> dict[str, Any]:
        if operation not in {"replace", "append"}:
            raise BIDestinationError("BigQuery operation must be replace or append")
        if self.cost_authorized is not True:
            raise BIDestinationError(
                "BigQuery apply requires a host-owned cost_authorized=true for this project and dataset"
            )
        mapping = _require_dataset_mapping(
            self.tables,
            datasets,
            label="BigQuery table",
            id_name="table_id",
            allow_extra=selected_projection,
        )
        for name, table in mapping.items():
            if not isinstance(table.get("table_id"), str) or not table["table_id"]:
                raise BIDestinationError(f"BigQuery table mapping for {name!r} is invalid")
        dataset_metadata = self._request(
            "GET", self._url(f"datasets/{self.dataset_id}"), retryable=True
        )
        reference = dataset_metadata.get("datasetReference")
        if not isinstance(reference, dict) or reference.get("datasetId") != self.dataset_id:
            raise BIDestinationError(
                "BigQuery target dataset permission check did not confirm the target"
            )
        token = package_sha256[:16]
        return {
            "target": target,
            "schema_version": schema_version,
            "package_sha256": package_sha256,
            "tables": mapping,
            "datasets": datasets,
            "token": token,
            "headers": {},
            "rows": {name: 0 for name in datasets},
            "load_parts": {name: 0 for name in datasets},
            "stage_ids": {name: f"_seohead_stage_{name}_{token}" for name in datasets},
            "operation": operation,
        }

    def _job(
        self, job_id: str, configuration: dict[str, Any], *, media: bytes | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jobReference": {"projectId": self.project_id, "jobId": job_id},
            "configuration": configuration,
        }
        if self.location:
            payload["jobReference"]["location"] = self.location
        try:
            if media is None:
                result = self._request("POST", self._url("jobs"), payload, retryable=True)
            else:
                boundary = f"seohead_{uuid.uuid4().hex}"
                multipart = b"".join(
                    (
                        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                        json.dumps(payload, separators=(",", ":")).encode(),
                        f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
                        media,
                        f"\r\n--{boundary}--\r\n".encode(),
                    )
                )
                result = self._request(
                    "POST",
                    f"https://bigquery.googleapis.com/upload/bigquery/v2/projects/{self.project_id}/jobs?uploadType=multipart",
                    multipart,
                    headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                    retryable=True,
                )
        except BIDestinationError as exc:
            # jobs.insert is idempotent for a deterministic job ID.  An existing
            # job can be observed instead of blindly sending a second data load.
            if not any(
                marker in str(exc).lower()
                for marker in (
                    "alreadyexists",
                    "already exists",
                    "duplicate",
                    "http 409",
                    "'code': 409",
                )
            ):
                raise
            result = {"jobReference": payload["jobReference"]}
        returned_job_id = result.get("jobReference", {}).get("jobId", job_id)
        if returned_job_id != job_id:
            raise BIDestinationError(
                "BigQuery response did not preserve the deterministic job identity"
            )
        return self._wait_job(job_id)

    def _wait_job(self, job_id: str) -> dict[str, Any]:
        for attempt in range(_GOOGLE_RETRIES + 1):
            result = self._request("GET", self._job_url(job_id), retryable=True)
            status = result.get("status")
            if isinstance(status, dict) and status.get("state") == "DONE":
                if status.get("errorResult") or status.get("errors"):
                    raise BIDestinationError(
                        f"BigQuery job {job_id!r} failed: {status.get('errorResult')}"
                    )
                return result
            if attempt < _GOOGLE_RETRIES:
                time.sleep(0.1 * (attempt + 1))
        raise BIDestinationCommitUncertain(
            f"BigQuery job {job_id!r} did not reach DONE within the bounded poll window"
        )

    def write(self, transaction: dict[str, Any], dataset: str, rows: list[list[str]]) -> None:
        if dataset not in transaction["datasets"]:
            raise BIDestinationError("BigQuery write named an undeclared dataset")
        if not rows:
            return
        header = transaction["headers"].get(dataset)
        if header is None:
            transaction["headers"][dataset] = rows[0]
            return
        if any(len(row) != len(header) for row in rows):
            raise BIDestinationError("BigQuery row width differs from the declared header")
        declared = transaction["datasets"][dataset]["fields"]
        if [field.get("name") for field in declared] != header:
            raise BIDestinationError("BigQuery CSV header differs from the package manifest")
        fields = []
        objects = []
        for field in declared:
            kind = _bq_type(field.get("type"))
            if not kind:
                raise BIDestinationError("BI package contains an unsupported BigQuery field type")
            fields.append({"name": field["name"], "type": kind, "mode": "NULLABLE"})
        for row in rows:
            objects.append(
                {
                    field["name"]: _bq_value(value, field["type"])
                    for field, value in zip(declared, row, strict=True)
                }
            )
        part = transaction["load_parts"][dataset]
        stage = transaction["stage_ids"][dataset]
        disposition = "WRITE_TRUNCATE" if part == 0 else "WRITE_APPEND"
        job_id = f"seohead_{transaction['token']}_{dataset}_{part}"
        payload_rows: list[bytes] = []
        payload_bytes = 0
        for value in objects:
            line = (
                json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            )
            payload_bytes += len(line)
            if payload_bytes > _GOOGLE_REQUEST_BYTES:
                raise BIDestinationError(
                    "BigQuery load chunk exceeds the bounded payload size before transport"
                )
            payload_rows.append(line)
        ndjson = b"".join(payload_rows)
        result = self._job(
            job_id,
            {
                "load": {
                    "destinationTable": {
                        "projectId": self.project_id,
                        "datasetId": self.dataset_id,
                        "tableId": stage,
                    },
                    "sourceFormat": "NEWLINE_DELIMITED_JSON",
                    "writeDisposition": disposition,
                    "createDisposition": "CREATE_IF_NEEDED",
                    "schema": {"fields": fields},
                },
                "labels": {"seohead": "bi", "dataset": dataset[:63]},
            },
            media=ndjson,
        )
        output = ((result.get("statistics") or {}).get("load") or {}).get("outputRows")
        if str(output) != str(len(rows)):
            raise BIDestinationError("BigQuery load acknowledgement does not conserve staged rows")
        transaction["rows"][dataset] += len(rows)
        transaction["load_parts"][dataset] += 1

    def pending_write(
        self, transaction: dict[str, Any], dataset: str, rows: list[list[str]], chunk: int
    ) -> dict[str, Any]:
        """Retain a deterministic job identity before sending a bounded load."""
        if dataset not in transaction["datasets"] or not rows:
            raise BIDestinationError("BigQuery pending write is invalid")
        if transaction["headers"].get(dataset) is None:
            return {"kind": "bigquery_header", "dataset": dataset, "chunk": chunk}
        part = transaction["load_parts"][dataset]
        return {
            "kind": "bigquery_load",
            "dataset": dataset,
            "chunk": chunk,
            "job_id": f"seohead_{transaction['token']}_{dataset}_{part}",
            "rows": len(rows),
            "rows_sha256": hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }

    def reconcile_pending(
        self, transaction: dict[str, Any], pending: dict[str, Any], rows: list[list[str]]
    ) -> str:
        """Observe an exact prior load job instead of submitting it a second time."""
        kind = pending.get("kind")
        if kind == "bigquery_header":
            return "not_applied"
        if kind != "bigquery_load" or pending.get("dataset") not in transaction["datasets"]:
            raise BIDestinationCommitUncertain(
                "BigQuery pending operation requires operator review"
            )
        digest = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if digest != pending.get("rows_sha256"):
            raise BIDestinationError("BigQuery pending load does not match the local package")
        job_id = pending.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise BIDestinationError("BigQuery pending load has no job identity")
        result = self._wait_job(job_id)
        output = ((result.get("statistics") or {}).get("load") or {}).get("outputRows")
        if str(output) != str(pending.get("rows")):
            raise BIDestinationError("BigQuery reconciled load does not conserve staged rows")
        dataset = pending["dataset"]
        transaction["rows"][dataset] += len(rows)
        transaction["load_parts"][dataset] += 1
        return "applied"

    def reconcile_commit(self, transaction: dict[str, Any]) -> str:
        """Observe deterministic copy jobs before retrying an uncertain final publish."""
        absent = False
        for name in transaction.get("stage_ids", {}):
            job_id = (
                f"seohead_{transaction['token']}_{name}_publish_"
                f"{transaction['schema_version'].replace('.', '_')}"
            )
            try:
                self._wait_job(job_id)
            except BIDestinationError as exc:
                if "HTTP 404" in str(exc):
                    absent = True
                    continue
                raise
        return "not_applied" if absent else "applied"

    def _table_rows(self, table_id: str) -> int:
        result = self._request(
            "GET", self._url(f"datasets/{self.dataset_id}/tables/{table_id}"), retryable=True
        )
        try:
            return int(result["numRows"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BIDestinationError("BigQuery table verification returned no row count") from exc

    def commit(self, transaction: dict[str, Any]) -> dict[str, Any]:
        copied: list[str] = []
        for name, stage_id in transaction["stage_ids"].items():
            if transaction["headers"].get(name) is None:
                raise BIDestinationError(f"BigQuery dataset {name!r} has no header")
            if transaction["load_parts"][name] == 0:
                fields = []
                for field in transaction["datasets"][name]["fields"]:
                    kind = _bq_type(field.get("type"))
                    if not kind:
                        raise BIDestinationError(
                            "BI package contains an unsupported BigQuery field type"
                        )
                    fields.append({"name": field["name"], "type": kind, "mode": "NULLABLE"})
                result = self._job(
                    f"seohead_{transaction['token']}_{name}_empty",
                    {
                        "load": {
                            "destinationTable": {
                                "projectId": self.project_id,
                                "datasetId": self.dataset_id,
                                "tableId": stage_id,
                            },
                            "sourceFormat": "NEWLINE_DELIMITED_JSON",
                            "writeDisposition": "WRITE_TRUNCATE",
                            "createDisposition": "CREATE_IF_NEEDED",
                            "schema": {"fields": fields},
                        },
                        "labels": {"seohead": "bi", "dataset": name[:63]},
                    },
                    media=b"",
                )
                output = ((result.get("statistics") or {}).get("load") or {}).get("outputRows")
                if str(output) != "0":
                    raise BIDestinationError("BigQuery empty-stage acknowledgement is not empty")
            if self._table_rows(stage_id) != transaction["rows"][name]:
                raise BIDestinationError(
                    "BigQuery staging table does not reconcile with local rows"
                )
            destination = transaction["tables"][name]["table_id"]
            job_id = f"seohead_{transaction['token']}_{name}_publish_{transaction['schema_version'].replace('.', '_')}"
            self._job(
                job_id,
                {
                    "copy": {
                        "sourceTable": {
                            "projectId": self.project_id,
                            "datasetId": self.dataset_id,
                            "tableId": stage_id,
                        },
                        "destinationTable": {
                            "projectId": self.project_id,
                            "datasetId": self.dataset_id,
                            "tableId": destination,
                        },
                        "createDisposition": "CREATE_IF_NEEDED",
                        "writeDisposition": (
                            "WRITE_TRUNCATE"
                            if transaction.get("operation", "replace") == "replace"
                            else "WRITE_APPEND"
                        ),
                    }
                },
            )
            copied.append(name)
        return {
            "publication": "atomic_per_bigquery_table_not_across_datasets",
            "published_datasets": copied,
            "remote_verification": "each staging table numRows matched its acknowledged local rows",
        }

    def abort(self, transaction: dict[str, Any]) -> None:
        for table_id in transaction.get("stage_ids", {}).values():
            try:
                self._request("DELETE", self._url(f"datasets/{self.dataset_id}/tables/{table_id}"))
            except BIDestinationCommitUncertain:
                # Do not claim cleanup when the remote outcome is unknown.
                raise
            except BIDestinationError:
                # A failed/absent staging table never permits touching a destination.
                continue


def _host_target_config(destination: str, target: str) -> dict[str, Any]:
    """Load one exact enabled host target without constructing an auth client."""
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
    return allowed[target]


def _resolved_target_mapping(
    destination: str, target: str, datasets: dict[str, Any], *, selected_projection: bool = False
) -> dict[str, Any]:
    """Return only the non-secret configured target mapping shown before apply."""
    target_config = _host_target_config(destination, target)
    if destination == "sheets" and target_config.get("kind") == "google_sheets_service_account":
        spreadsheet_id = target_config.get("spreadsheet_id")
        worksheets = _require_dataset_mapping(
            target_config.get("worksheets"),
            datasets,
            label="Google Sheets worksheet",
            id_name="worksheet_id",
            allow_extra=selected_projection,
        )
        if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
            raise BIDestinationError("Google Sheets target has no valid spreadsheet ID")
        for name, value in worksheets.items():
            if (
                not isinstance(value.get("worksheet_id"), int)
                or not isinstance(value.get("worksheet_title"), str)
                or not value["worksheet_title"]
            ):
                raise BIDestinationError(f"Google Sheets worksheet mapping for {name!r} is invalid")
        return {
            "kind": "google_sheets_service_account",
            "spreadsheet_id": spreadsheet_id,
            "worksheets": {
                name: {
                    "worksheet_id": value["worksheet_id"],
                    "worksheet_title": value["worksheet_title"],
                }
                for name, value in worksheets.items()
            },
        }
    if destination == "bigquery" and target_config.get("kind") == "google_bigquery_service_account":
        project_id = target_config.get("project_id")
        dataset_id = target_config.get("dataset_id")
        tables = _require_dataset_mapping(
            target_config.get("tables"),
            datasets,
            label="BigQuery table",
            id_name="table_id",
            allow_extra=selected_projection,
        )
        location = target_config.get("location")
        if (
            not isinstance(project_id, str)
            or not isinstance(dataset_id, str)
            or not project_id
            or not dataset_id
        ):
            raise BIDestinationError("BigQuery target has no valid project or dataset ID")
        if location is not None and not isinstance(location, str):
            raise BIDestinationError("BigQuery target location is invalid")
        if any(
            not isinstance(value.get("table_id"), str) or not value["table_id"]
            for value in tables.values()
        ):
            raise BIDestinationError("BigQuery target has an invalid table mapping")
        return {
            "kind": "google_bigquery_service_account",
            "project_id": project_id,
            "dataset_id": dataset_id,
            "location": location,
            "cost_authorized": target_config.get("cost_authorized") is True,
            "tables": {name: {"table_id": value["table_id"]} for name, value in tables.items()},
        }
    raise BIDestinationError("host has no authorized client for the allowlisted target")


def resolve_host_client(destination: str, target: str) -> Any:
    """Resolve an exact allowlisted host target without reading credentials from input."""
    target_config = _host_target_config(destination, target)
    registered = _HOST_CLIENTS.get((destination, target))
    if registered is not None:
        return registered
    if destination == "sheets" and target_config.get("kind") == "google_sheets_service_account":
        spreadsheet_id = target_config.get("spreadsheet_id")
        worksheets = target_config.get("worksheets")
        if isinstance(spreadsheet_id, str) and isinstance(worksheets, dict):
            return GoogleSheetsClient(target, spreadsheet_id, worksheets)
    if destination == "bigquery" and target_config.get("kind") == "google_bigquery_service_account":
        project_id = target_config.get("project_id")
        dataset_id = target_config.get("dataset_id")
        tables = target_config.get("tables")
        location = target_config.get("location")
        if (
            isinstance(project_id, str)
            and isinstance(dataset_id, str)
            and isinstance(tables, dict)
            and (location is None or isinstance(location, str))
        ):
            if target_config.get("cost_authorized") is not True:
                raise BIDestinationError(
                    "BigQuery target is missing host-owned cost_authorized=true"
                )
            return GoogleBigQueryClient(
                target,
                project_id,
                dataset_id,
                tables,
                location=location,
                cost_authorized=True,
            )
    raise BIDestinationError("host has no authorized client for the allowlisted target")


def _manifest(package: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(package)
    if root.is_symlink() or not root.is_dir():
        raise BIDestinationError("package must be an existing non-symlink BI package directory")
    path = root / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise BIDestinationError("package has no regular manifest.json")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BIDestinationError("BI manifest exceeds its byte bound")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BIDestinationError(f"package manifest is unreadable: {exc}") from exc
    if value.get("format") == MANIFEST_FORMAT and value.get("schema_version") == BI_SCHEMA_VERSION:
        if not isinstance(value.get("datasets"), dict):
            raise BIDestinationError("package manifest has no dataset declarations")
        return root, value
    if (
        value.get("format") == "seohead.bi-filter.v1"
        and value.get("source_schema_version") == BI_SCHEMA_VERSION
    ):
        name = value.get("dataset")
        columns = value.get("columns")
        partitions = value.get("partitions")
        row_count = value.get("row_count")
        if (
            not isinstance(name, str)
            or name not in DATASET_SPECS
            or not isinstance(columns, list)
            or not columns
            or not isinstance(partitions, list)
            or type(row_count) is not int
            or row_count < 0
        ):
            raise BIDestinationError("selected BI projection manifest is invalid")
        expected = _fields_for_manifest(name, value.get("metrics_dimension_columns"))
        if (
            any(not isinstance(column, str) for column in columns)
            or len(set(columns)) != len(columns)
            or any(column not in expected for column in columns)
        ):
            raise BIDestinationError("selected BI projection has unsupported or duplicate columns")
        fields = [
            {"name": column, "type": expected[column].type, "nullable": expected[column].nullable}
            for column in columns
        ]
        datasets = {
            name: {
                **(value.get("source_coverage") or {}),
                "fields": fields,
                "partitions": partitions,
                "row_count": row_count,
                "bytes": sum(part.get("bytes", 0) for part in partitions if isinstance(part, dict)),
            }
        }
        companion = value.get("coverage_companion")
        if companion is not None:
            if not isinstance(companion, dict) or companion.get("dataset") != "coverage":
                raise BIDestinationError("selected BI coverage companion is invalid")
            companion_fields = companion.get("fields")
            companion_partitions = companion.get("partitions")
            companion_rows = companion.get("row_count")
            expected_coverage = _fields_for_manifest("coverage", None)
            if (
                not isinstance(companion_fields, list)
                or [field.get("name") for field in companion_fields if isinstance(field, dict)]
                != list(expected_coverage)
                or not isinstance(companion_partitions, list)
                or type(companion_rows) is not int
                or companion_rows < 0
            ):
                raise BIDestinationError("selected BI coverage companion is invalid")
            datasets["coverage"] = {
                key: companion.get(key)
                for key in ("state", "reason", "source_population", "coverage")
            }
            datasets["coverage"].update(
                {
                    "fields": companion_fields,
                    "partitions": companion_partitions,
                    "row_count": companion_rows,
                    "bytes": sum(
                        part.get("bytes", 0)
                        for part in companion_partitions
                        if isinstance(part, dict)
                    ),
                }
            )
        return root, {
            "format": MANIFEST_FORMAT,
            "schema_version": BI_SCHEMA_VERSION,
            "source_package_format": "seohead.bi-filter.v1",
            "selected_projection": True,
            "source": value.get("source"),
            "source_coverage": value.get("source_coverage"),
            "conservation": value.get("conservation"),
            "metrics_dimension_columns": value.get("metrics_dimension_columns"),
            "datasets": datasets,
        }
    raise BIDestinationError("package has an unsupported BI manifest/schema version")


def _fields_for_manifest(name: str, dimensions: Any) -> dict[str, Field]:
    expected = {field.name: field for field in DATASET_SPECS[name][0]}
    if name == "metrics" and dimensions:
        if (
            not isinstance(dimensions, dict)
            or len(dimensions) > 512
            or any(not isinstance(key, str) or not key or len(key) > 1024 for key in dimensions)
            or dimensions != _safe_dimension_fields(dimensions)
        ):
            raise BIDestinationError("metric dimension mapping differs from the BI schema")
        expected.update({field: Field(field, "string", True) for field in dimensions.values()})
    return expected


def _validated_fields(
    name: str, fields: Any, *, selected_projection: bool, dimensions: Any = None
) -> list[str]:
    if name not in DATASET_SPECS or not isinstance(fields, list) or not fields:
        raise BIDestinationError(f"dataset {name!r} has no declared fields")
    expected = _fields_for_manifest(name, dimensions)
    names: list[str] = []
    for value in fields:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise BIDestinationError(f"dataset {name!r} field declaration is invalid")
        field = expected.get(value["name"])
        if (
            field is None
            or value.get("type") != field.type
            or value.get("nullable") is not field.nullable
        ):
            raise BIDestinationError(f"dataset {name!r} fields do not match the BI schema")
        names.append(field.name)
    if len(set(names)) != len(names):
        raise BIDestinationError(f"dataset {name!r} field names are duplicated")
    if not selected_projection and names != list(expected):
        raise BIDestinationError(f"dataset {name!r} fields do not match the complete BI schema")
    return names


def _checksum_and_rows(path: Path, expected_header: list[str]) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    bytes_count = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            bytes_count += len(block)
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            if header != expected_header:
                raise BIDestinationError(
                    "CSV partition header does not match the declared BI schema"
                )
            rows = 0
            for row in reader:
                if len(row) != len(header):
                    raise BIDestinationError("CSV partition row width does not match the BI schema")
                if (
                    sum(len(value.encode("utf-8")) + 4 for value in row)
                    > _GOOGLE_CHUNK_SOURCE_BYTES
                ):
                    raise BIDestinationError("BI CSV row exceeds the bounded Google request size")
                rows += 1
            return digest.hexdigest(), bytes_count, rows
    except (OSError, UnicodeDecodeError, csv.Error, StopIteration) as exc:
        if isinstance(exc, BIDestinationError):
            raise
        raise BIDestinationError("CSV partition cannot be read as a declared BI dataset") from exc


def _verify_partitions(root: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    datasets: dict[str, dict[str, Any]] = {}
    for name, dataset in manifest["datasets"].items():
        if not isinstance(name, str) or not isinstance(dataset, dict):
            raise BIDestinationError("package dataset declaration is invalid")
        fields = _validated_fields(
            name,
            dataset.get("fields"),
            selected_projection=manifest.get("selected_projection") is True,
            dimensions=manifest.get("metrics_dimension_columns"),
        )
        if type(dataset.get("row_count")) is not int or dataset["row_count"] < 0:
            raise BIDestinationError(f"dataset {name!r} row count is invalid")
        if type(dataset.get("bytes")) is not int or dataset["bytes"] < 0:
            raise BIDestinationError(f"dataset {name!r} byte count is invalid")
        rows = bytes_count = 0
        for part in dataset.get("partitions") or []:
            if not isinstance(part, dict):
                raise BIDestinationError(f"dataset {name!r} partition declaration is invalid")
            path = root / str(part.get("path") or "")
            if path.parent != root or path.is_symlink() or not path.is_file():
                raise BIDestinationError(f"dataset {name!r} has an invalid partition path")
            checksum, part_bytes, part_rows = _checksum_and_rows(path, fields)
            if checksum != part.get("sha256"):
                raise BIDestinationError(
                    f"dataset {name!r} partition checksum does not match manifest"
                )
            if type(part.get("rows")) is not int or part["rows"] != part_rows:
                raise BIDestinationError(f"dataset {name!r} partition row count does not match CSV")
            if type(part.get("bytes")) is not int or part["bytes"] != part_bytes:
                raise BIDestinationError(
                    f"dataset {name!r} partition byte count does not match CSV"
                )
            rows += part_rows
            bytes_count += part_bytes
        if rows != dataset.get("row_count") or bytes_count != dataset.get("bytes"):
            raise BIDestinationError(f"dataset {name!r} partition totals do not match manifest")
        datasets[name] = {"rows": rows, "fields": len(fields), "bytes": bytes_count}
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
    """Yield a header once and rows bounded before any remote payload is built."""
    header = None
    chunk: list[list[str]] = []
    chunk_bytes = 0
    for part in dataset["partitions"]:
        with (root / part["path"]).open(encoding="utf-8", newline="") as stream:
            rows = csv.reader(stream)
            current_header = next(rows)
            if header is None:
                header = current_header
                header_bytes = sum(len(value.encode("utf-8")) + 4 for value in header)
                if header_bytes > _GOOGLE_CHUNK_SOURCE_BYTES:
                    raise BIDestinationError(
                        "BI CSV header exceeds the bounded Google request size"
                    )
                yield [header]
            elif current_header != header:
                raise BIDestinationError("dataset partition headers disagree")
            for row in rows:
                row_bytes = sum(len(value.encode("utf-8")) + 4 for value in row)
                if row_bytes > _GOOGLE_CHUNK_SOURCE_BYTES:
                    raise BIDestinationError("BI CSV row exceeds the bounded Google request size")
                if chunk and (
                    len(chunk) >= size or chunk_bytes + row_bytes > _GOOGLE_CHUNK_SOURCE_BYTES
                ):
                    yield chunk
                    chunk = []
                    chunk_bytes = 0
                chunk.append(row)
                chunk_bytes += row_bytes
    if chunk:
        yield chunk


def _dataset_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    """Return stable package identities without re-reading untrusted source paths."""
    hashes = {}
    for name, dataset in manifest["datasets"].items():
        identity = {
            "fields": dataset["fields"],
            "partitions": [
                {key: part.get(key) for key in ("path", "rows", "sha256")}
                for part in dataset.get("partitions") or []
            ],
        }
        hashes[name] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return hashes


def destination_preview(
    package: str | Path, *, target: str, destination: str, operation: str
) -> dict[str, Any]:
    """Show the exact local package, target and operation before an explicit write."""
    if destination not in {"sheets", "bigquery"}:
        raise BIDestinationError("destination must be 'sheets' or 'bigquery'")
    if operation not in {"replace", "append"}:
        raise BIDestinationError("operation must be 'replace' or 'append'")
    if not isinstance(target, str) or not target:
        raise BIDestinationError("an explicit destination target is required")
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    resolved_target = _resolved_target_mapping(
        destination,
        target,
        manifest["datasets"],
        selected_projection=manifest.get("selected_projection") is True,
    )
    return {
        "format": "seohead.bi-destination-preview.v1",
        "state": "ready_to_apply",
        "destination": destination,
        "target": target,
        "resolved_target": resolved_target,
        "operation": operation,
        "package_schema_version": manifest["schema_version"],
        "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "datasets": {
            name: {"rows": info["rows"], "columns": info["fields"], "bytes": info["bytes"]}
            for name, info in datasets.items()
        },
        "required_action": "rerun with apply=true after reviewing this exact target and operation",
    }


def _rows_for_pending(
    root: Path, dataset: dict[str, Any], pending: dict[str, Any]
) -> list[list[str]]:
    """Recreate precisely the one chunk whose remote outcome is uncertain."""
    chunk = pending.get("chunk")
    if type(chunk) is not int or chunk < 0:
        raise BIDestinationError("destination pending state has an invalid chunk cursor")
    for index, rows in enumerate(_csv_chunks(root, dataset, size=_GOOGLE_CHUNK_ROWS)):
        if index == chunk:
            return rows
    raise BIDestinationError("destination pending chunk is absent from the verified package")


def _pending_write(
    client: Any, transaction: Any, dataset: str, rows: list[list[str]], chunk: int
) -> dict[str, Any]:
    describe = getattr(client, "pending_write", None)
    if describe is not None:
        pending = describe(transaction, dataset, rows, chunk)
    else:
        pending = {"kind": "generic_write", "dataset": dataset, "chunk": chunk}
    if not isinstance(pending, dict) or pending.get("dataset") != dataset:
        raise BIDestinationError("destination client returned an invalid pending write")
    pending["data_rows"] = len(rows) - (1 if chunk == 0 else 0)
    return pending


def _advance_pending(state: dict[str, Any], pending: dict[str, Any]) -> None:
    """Record a proven chunk exactly once after write or readback reconciliation."""
    dataset = pending.get("dataset")
    if not isinstance(dataset, str) or dataset not in state["datasets"]:
        raise BIDestinationError("destination pending state names an unknown dataset")
    entry = state["datasets"][dataset]
    if pending.get("chunk") != entry.get("next_chunk"):
        raise BIDestinationError("destination pending state has a non-monotonic cursor")
    data_rows = pending.get("data_rows")
    if type(data_rows) is not int or data_rows < 0:
        raise BIDestinationError("destination pending state has invalid row accounting")
    entry["written_rows"] += data_rows
    entry["next_chunk"] += 1
    state["pending"] = None
    state["status"] = "staging"


def _reconcile_pending(
    *,
    root: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    client: Any,
    checkpoint: Path,
    reconcile: bool,
) -> dict[str, Any] | None:
    """Require an explicit read reconciliation before an uncertain request can continue."""
    pending = state.get("pending")
    if pending is None:
        return None
    if not isinstance(pending, dict):
        raise BIDestinationError("destination progress state has an invalid pending operation")
    if not reconcile:
        state["status"] = "reconciliation_required"
        _save_state(checkpoint, state)
        return _result_from_state(
            state,
            reason="a prior remote operation is uncertain; rerun with reconcile=true before any retry",
        )
    if pending.get("kind") == "commit":
        reconcile_commit = getattr(client, "reconcile_commit", None)
        if reconcile_commit is None:
            state["status"] = "reconciliation_required"
            _save_state(checkpoint, state)
            return _result_from_state(
                state, reason="destination client cannot reconcile an uncertain final publication"
            )
        outcome = reconcile_commit(state["transaction"])
        if outcome == "applied":
            state["pending"] = None
            state["status"] = "committed"
            _save_state(checkpoint, state)
            result = _result_from_state(state)
            result.update(
                {
                    "dataset_sha256": _dataset_hashes(manifest),
                    "row_conservation": "verified",
                    "publication": "reconciled_after_uncertain_commit",
                }
            )
            return result
        if outcome != "not_applied":
            raise BIDestinationCommitUncertain(
                "destination commit reconciliation did not reach a safe verdict"
            )
        state["pending"] = None
        state["status"] = "staging"
        _save_state(checkpoint, state)
        return None
    dataset = pending.get("dataset")
    if not isinstance(dataset, str) or dataset not in manifest["datasets"]:
        raise BIDestinationError("destination pending state names an unknown dataset")
    rows = _rows_for_pending(root, manifest["datasets"][dataset], pending)
    reconcile_write = getattr(client, "reconcile_pending", None)
    if reconcile_write is None:
        state["status"] = "reconciliation_required"
        _save_state(checkpoint, state)
        return _result_from_state(
            state, reason="destination client cannot reconcile the uncertain bounded write"
        )
    outcome = reconcile_write(state["transaction"], pending, rows)
    if outcome == "applied":
        _advance_pending(state, pending)
        _save_state(checkpoint, state)
        return None
    if outcome == "not_applied":
        state["pending"] = None
        state["status"] = "staging"
        _save_state(checkpoint, state)
        return None
    raise BIDestinationCommitUncertain(
        "destination write reconciliation did not reach a safe verdict"
    )


def _begin_transaction(
    *,
    state: dict[str, Any],
    checkpoint: Path,
    client: Any,
    target: str,
    operation: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> None:
    """Call begin only after its durable pre-request checkpoint exists."""
    transaction = client.begin(
        target=target,
        operation=operation,
        schema_version=BI_SCHEMA_VERSION,
        datasets=manifest["datasets"],
        package_sha256=manifest_sha256,
        selected_projection=manifest.get("selected_projection") is True,
    )
    state["transaction"] = transaction
    state["pending"] = None
    state["status"] = "staging"
    _save_state(checkpoint, state)


def _reconcile_begin(
    *,
    state: dict[str, Any],
    checkpoint: Path,
    client: Any,
    target: str,
    operation: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    reconcile: bool,
) -> dict[str, Any] | None:
    """Resolve an interrupted begin before another remote staging attempt."""
    if state.get("status") != "begin_pending":
        return None
    if not reconcile:
        state["status"] = "reconciliation_required"
        _save_state(checkpoint, state)
        return _result_from_state(
            state,
            reason="a prior destination begin is uncertain; rerun with reconcile=true before retry",
        )
    reconcile_begin = getattr(client, "reconcile_begin", None)
    if reconcile_begin is None:
        state["status"] = "reconciliation_required"
        _save_state(checkpoint, state)
        return _result_from_state(
            state,
            reason="destination client cannot reconcile an uncertain begin operation",
        )
    transaction = reconcile_begin(
        target=target,
        operation=operation,
        schema_version=BI_SCHEMA_VERSION,
        datasets=manifest["datasets"],
        package_sha256=manifest_sha256,
        selected_projection=manifest.get("selected_projection") is True,
    )
    if transaction == "not_applied":
        _begin_transaction(
            state=state,
            checkpoint=checkpoint,
            client=client,
            target=target,
            operation=operation,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
        )
        return None
    if transaction == "uncertain":
        state["status"] = "reconciliation_required"
        _save_state(checkpoint, state)
        return _result_from_state(
            state, reason="destination begin reconciliation did not reach a safe verdict"
        )
    state["transaction"] = transaction
    state["pending"] = None
    state["status"] = "staging"
    _save_state(checkpoint, state)
    return None


def apply_with_client(
    package: str | Path,
    *,
    target: str,
    operation: str,
    client: Any,
    apply: bool = False,
    reconcile: bool = False,
) -> dict[str, Any]:
    """Stream a package through a checkpointed, explicitly authorized destination client.

    Every request is preceded by an atomic local checkpoint.  A restart resumes
    known completed chunks.  A timeout or process loss leaves the affected
    request pending and cannot replay it until an explicit reconciliation proves
    that the fixed remote range/job was either applied or not applied.
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
    manifest_sha256 = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    destination = getattr(client, "destination", client.__class__.__name__)
    if not isinstance(destination, str) or not destination:
        destination = client.__class__.__name__
    checkpoint = _state_path(
        root,
        destination=destination,
        target=target,
        operation=operation,
        manifest_sha256=manifest_sha256,
    )
    state = _load_state(
        checkpoint,
        expected={
            "destination": destination,
            "target": target,
            "operation": operation,
            "manifest_sha256": manifest_sha256,
        },
    )
    if state is not None and state.get("status") == "committed":
        result = _result_from_state(state)
        result.update({"dataset_sha256": _dataset_hashes(manifest), "row_conservation": "verified"})
        return result
    if state is not None and state.get("status") == "failed":
        return _result_from_state(state, reason=str(state.get("reason") or "prior write failed"))
    if state is None:
        state = _new_state(
            destination=destination,
            target=target,
            operation=operation,
            manifest_sha256=manifest_sha256,
            datasets=datasets,
            transaction=None,
        )
        state["status"] = "begin_pending"
        state["pending"] = {"kind": "begin"}
        _save_state(checkpoint, state)
        try:
            _begin_transaction(
                state=state,
                checkpoint=checkpoint,
                client=client,
                target=target,
                operation=operation,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
            )
        except BIDestinationError:
            state["status"] = "reconciliation_required"
            state["reason"] = "destination begin failed after its durable pre-request checkpoint"
            _save_state(checkpoint, state)
            raise
    elif state.get("status") == "begin_pending":
        try:
            begun = _reconcile_begin(
                state=state,
                checkpoint=checkpoint,
                client=client,
                target=target,
                operation=operation,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                reconcile=reconcile,
            )
        except BIDestinationError:
            state["status"] = "reconciliation_required"
            state["reason"] = "destination begin reconciliation failed"
            _save_state(checkpoint, state)
            raise
        if begun is not None:
            return begun
    try:
        reconciled = _reconcile_pending(
            root=root,
            manifest=manifest,
            state=state,
            client=client,
            checkpoint=checkpoint,
            reconcile=reconcile,
        )
    except BIDestinationCommitUncertain as exc:
        state["status"] = "reconciliation_required"
        state["reason"] = str(exc)
        _save_state(checkpoint, state)
        return _result_from_state(state, reason=str(exc))
    if reconciled is not None:
        return reconciled
    transaction = state["transaction"]
    try:
        for name, info in datasets.items():
            entry = state["datasets"].get(name)
            if not isinstance(entry, dict) or type(entry.get("next_chunk")) is not int:
                raise BIDestinationError("destination progress state has an invalid chunk cursor")
            for chunk, rows in enumerate(
                _csv_chunks(root, manifest["datasets"][name], size=_GOOGLE_CHUNK_ROWS)
            ):
                if chunk < entry["next_chunk"]:
                    continue
                if chunk > entry["next_chunk"]:
                    raise BIDestinationError("destination chunk cursor is not contiguous")
                pending = _pending_write(client, transaction, name, rows, chunk)
                state["pending"] = pending
                state["status"] = "staging"
                _save_state(checkpoint, state)
                transaction_before_write = copy.deepcopy(transaction)
                try:
                    client.write(transaction, name, rows)
                except BaseException:
                    # The durable pre-request checkpoint is authoritative until a
                    # read reconciliation proves that this exact range/job landed.
                    state["transaction"] = transaction_before_write
                    transaction = transaction_before_write
                    raise
                _advance_pending(state, pending)
                _save_state(checkpoint, state)
            if entry["written_rows"] != info["rows"]:
                raise BIDestinationError(f"destination row conservation failed for {name!r}")
        state["pending"] = {"kind": "commit"}
        state["status"] = "commit_pending"
        _save_state(checkpoint, state)
        publication = client.commit(transaction)
    except BIDestinationCommitUncertain as exc:
        state["status"] = "reconciliation_required"
        state["reason"] = str(exc)
        _save_state(checkpoint, state)
        return _result_from_state(state, reason=str(exc))
    except (KeyboardInterrupt, SystemExit):
        state["status"] = "interrupted"
        _save_state(checkpoint, state)
        raise
    except BIDestinationError as exc:
        pending = state.get("pending")
        if isinstance(pending, dict) and type(pending.get("data_rows")) is int:
            entry = state["datasets"].get(pending.get("dataset"))
            if isinstance(entry, dict):
                entry["failed_rows"] += pending["data_rows"]
        state["status"] = "failed"
        state["reason"] = str(exc)
        _save_state(checkpoint, state)
        abort = getattr(client, "abort", None)
        if abort is not None:
            abort(transaction)
        return _result_from_state(state, reason=str(exc))
    except BaseException:
        state["status"] = "failed"
        state["reason"] = "unexpected destination client failure"
        _save_state(checkpoint, state)
        abort = getattr(client, "abort", None)
        if abort is not None:
            abort(transaction)
        raise
    state["pending"] = None
    state["status"] = "committed"
    _save_state(checkpoint, state)
    result = _result_from_state(state)
    result.update(
        {
            "dataset_sha256": _dataset_hashes(manifest),
            "row_conservation": "verified",
        }
    )
    if isinstance(publication, dict):
        result.update(publication)
    return result


def _segment_page_records(root: Path, dataset: dict[str, Any]):
    """Yield retained raw page records for the project-view segment engine."""
    for part in dataset["partitions"]:
        with (root / part["path"]).open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                url = row.get("url")
                raw = row.get("source_fields_json")
                if not isinstance(url, str) or not url:
                    raise BIDestinationError("BI page source lacks a URL for segment selection")
                try:
                    source = json.loads(raw) if isinstance(raw, str) and raw else {}
                except json.JSONDecodeError as exc:
                    raise BIDestinationError("BI page source has invalid retained fields") from exc
                if not isinstance(source, dict):
                    raise BIDestinationError("BI page source has invalid retained fields")
                yield {**source, "url": url}


def filter_package(
    package: str | Path,
    *,
    dataset: str,
    out_dir: str | Path,
    where: dict[str, list[str]] | None = None,
    columns: list[str] | None = None,
    max_rows_per_file: int = MAX_ROWS_PER_PARTITION,
    max_bytes_per_file: int = DEFAULT_BYTES_PER_PARTITION,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Publish an exact selected CSV view with source coverage and bounded output."""
    if type(max_rows_per_file) is not int or not 1 <= max_rows_per_file <= MAX_ROWS_PER_PARTITION:
        raise BIDestinationError(f"max_rows_per_file must be 1..{MAX_ROWS_PER_PARTITION}")
    if (
        type(max_bytes_per_file) is not int
        or not 1024 <= max_bytes_per_file <= MAX_BYTES_PER_PARTITION
    ):
        raise BIDestinationError(f"max_bytes_per_file must be 1024..{MAX_BYTES_PER_PARTITION}")
    if (
        type(max_output_bytes) is not int
        or not max_bytes_per_file <= max_output_bytes <= MAX_OUTPUT_BYTES
    ):
        raise BIDestinationError(
            "max_output_bytes must cover one partition and stay within the hard bound"
        )
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    if dataset not in datasets:
        raise BIDestinationError("dataset is not declared by the BI package")
    source_dataset = manifest["datasets"][dataset]
    declared = {field["name"]: field for field in source_dataset["fields"]}
    selected = list(declared) if columns is None else columns
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) for item in selected)
        or len(set(selected)) != len(selected)
        or set(selected) - set(declared)
    ):
        raise BIDestinationError("columns must be a non-empty unique subset of declared fields")
    predicates = {} if where is None else where
    virtual_fields = {"segment"} if dataset == "findings" else set()
    if not isinstance(predicates, dict) or set(predicates) - set(declared) - virtual_fields:
        raise BIDestinationError("where keys must be declared fields")
    if any(
        not isinstance(values, list)
        or len(values) > 10_000
        or any(
            not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CELL_BYTES
            for value in values
        )
        for values in predicates.values()
    ):
        raise BIDestinationError("where values must be bounded lists of exact strings")
    if len(json.dumps(predicates, ensure_ascii=False).encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise BIDestinationError("where exceeds the bounded selection definition")
    allowed_values = {key: set(values) for key, values in predicates.items()}
    segment_index = None
    if "segment" in predicates:
        settings = (manifest.get("run") or {}).get("crawl_settings") or {}
        if not isinstance(settings, dict):
            raise BIDestinationError("segment selection requires retained crawl settings")
        try:
            from seohead.projects.finding_views import _segment_definitions
            from seohead.reports.bi_index import PrimarySegmentIndex, projection_index

            definitions = _segment_definitions({"run": {"crawl_config": settings}})
        except (KeyError, TypeError, ValueError) as exc:
            raise BIDestinationError("source segment definitions cannot be evaluated") from exc
        if not definitions:
            raise BIDestinationError(
                "segment selection requires retained analysis or scope segments"
            )
        source_pages = manifest["datasets"].get("pages")
        if not isinstance(source_pages, dict):
            raise BIDestinationError("segment selection requires retained source page rows")
    destination = Path(out_dir).absolute()
    if (
        destination.is_symlink()
        or os.path.lexists(destination)
        or not destination.parent.is_dir()
        or destination.parent.is_symlink()
    ):
        raise BIDestinationError("out_dir must be a new child of an existing non-symlink directory")
    source_manifest_sha256 = _checksum_file(root / "manifest.json")
    source_rows = 0
    selected_states: Counter[str] = Counter()
    source_states: Counter[str] = Counter()
    with ExitStack() as stack:
        if "segment" in predicates:
            index_con = stack.enter_context(
                projection_index(destination.parent, MAX_PROJECTION_INDEX_BYTES)
            )
            segment_index = PrimarySegmentIndex(index_con, definitions)
            segment_index.build(_segment_page_records(root, source_pages))
            known_segments = {"default", *(segment.name for segment in segment_index.order)}
            unknown_segments = sorted(allowed_values["segment"] - known_segments)
            if unknown_segments:
                raise BIDestinationError(
                    f"segment selection names segments absent from the source run: {unknown_segments}"
                )
        temp = stack.enter_context(
            tempfile.TemporaryDirectory(prefix=".seohead-bi-filter-", dir=destination.parent)
        )
        stage = Path(temp)
        budget = _OutputBudget(max_output_bytes)
        writer = _PartitionWriter(
            stage,
            dataset,
            tuple(
                Field(name, declared[name]["type"], declared[name]["nullable"]) for name in selected
            ),
            max_rows_per_file=max_rows_per_file,
            max_bytes_per_file=max_bytes_per_file,
            budget=budget,
        )
        try:
            for part in source_dataset["partitions"]:
                with (root / part["path"]).open(encoding="utf-8", newline="") as stream:
                    for row in csv.DictReader(stream):
                        source_rows += 1
                        state = row.get("state", "not_declared")
                        source_states[state] += 1
                        matches = all(
                            row[key] in allowed
                            for key, allowed in allowed_values.items()
                            if key != "segment"
                        )
                        if matches and "segment" in allowed_values:
                            url = row.get("url")
                            matches = (
                                isinstance(url, str)
                                and segment_index is not None
                                and segment_index.primary(url) in allowed_values["segment"]
                            )
                        if matches:
                            selected_states[state] += 1
                            values = [row[name] for name in selected]
                            for index, name in enumerate(selected):
                                if declared[name]["type"] == "string":
                                    safe = _cell_text(values[index])
                                    writer.neutralized_cells += int(safe != values[index])
                                    values[index] = safe
                            writer.write_cells(values)
            output = writer.finish()
            if source_rows != datasets[dataset]["rows"]:
                raise BIDestinationError("source row conservation changed during selection")
            # Recheck the retained package after streaming; never publish a view over changed bytes.
            if (
                _checksum_file(root / "manifest.json") != source_manifest_sha256
                or _verify_partitions(root, manifest) != datasets
            ):
                raise BIDestinationError("source package changed during selection")
            coverage_companion = None
            if dataset == "findings":
                source_coverage = manifest["datasets"].get("coverage")
                if not isinstance(source_coverage, dict):
                    raise BIDestinationError("source package has no immutable coverage dataset")
                coverage_fields = source_coverage.get("fields")
                coverage_parts = source_coverage.get("partitions")
                if not isinstance(coverage_fields, list) or not isinstance(coverage_parts, list):
                    raise BIDestinationError("source coverage dataset has no field declarations")
                coverage_output = {
                    "row_count": 0,
                    "partition_count": 0,
                    "bytes": 0,
                    "partitions": [],
                }
                for part in coverage_parts:
                    relative = part.get("path") if isinstance(part, dict) else None
                    byte_count = part.get("bytes") if isinstance(part, dict) else None
                    if (
                        not isinstance(relative, str)
                        or type(byte_count) is not int
                        or byte_count < 0
                    ):
                        raise BIDestinationError("source coverage partition is invalid")
                    budget.reserve_bytes(byte_count)
                    source_path, copied_path = root / relative, stage / relative
                    copied_path.parent.mkdir(parents=True, exist_ok=True)
                    with source_path.open("rb") as source, copied_path.open("xb") as copied:
                        shutil.copyfileobj(source, copied, 1024 * 1024)
                    os.chmod(copied_path, 0o600)
                    coverage_output["row_count"] += part["rows"]
                    coverage_output["partition_count"] += 1
                    coverage_output["bytes"] += byte_count
                    coverage_output["partitions"].append(copy.deepcopy(part))
                coverage_output["formula_safe_cells_prefixed"] = source_coverage.get(
                    "formula_safe_cells_prefixed", 0
                )
                if coverage_output["row_count"] != datasets["coverage"]["rows"]:
                    raise BIDestinationError("coverage companion row conservation changed")
                coverage_companion = {
                    "dataset": "coverage",
                    "fields": coverage_fields,
                    **coverage_output,
                    "state": source_coverage.get("state"),
                    "reason": source_coverage.get("reason"),
                    "source_population": source_coverage.get("source_population"),
                    "coverage": source_coverage.get("coverage"),
                    "selection": "complete immutable source coverage; not filtered with findings",
                }
                if (
                    _checksum_file(root / "manifest.json") != source_manifest_sha256
                    or _verify_partitions(root, manifest) != datasets
                ):
                    raise BIDestinationError("source package changed during coverage preservation")
            result = {
                "format": "seohead.bi-filter.v1",
                "source_schema_version": manifest["schema_version"],
                "dataset": dataset,
                "metrics_dimension_columns": manifest.get("metrics_dimension_columns"),
                "columns": selected,
                "where": predicates,
                **output,
                "selection_state": "complete",
                "source_manifest_sha256": source_manifest_sha256,
                "source": {
                    key: manifest.get(key)
                    for key in ("run", "input", "crawl_completeness", "provider_sources", "source")
                },
                "source_coverage": {
                    key: source_dataset.get(key)
                    for key in ("state", "reason", "source_population", "coverage")
                },
                "coverage_companion": coverage_companion,
                "conservation": {
                    "source_rows": source_rows,
                    "selected_rows": output["row_count"],
                    "omitted_rows": source_rows - output["row_count"],
                    "source_states": dict(source_states),
                    "selected_states": dict(selected_states),
                    "omission_reason": "explicit exact-filter selection; not evidence of resolution",
                },
                "resource_bounds": {
                    "max_rows_per_partition": max_rows_per_file,
                    "max_bytes_per_partition": max_bytes_per_file,
                    "max_output_bytes": max_output_bytes,
                    "max_output_partitions": MAX_OUTPUT_PARTITIONS,
                    "max_cell_bytes": MAX_CELL_BYTES,
                    "min_free_disk_bytes": MIN_FREE_DISK_BYTES,
                },
            }
            encoded = (
                json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            if len(encoded) > MAX_MANIFEST_BYTES:
                raise BIDestinationError("selected manifest exceeds its byte bound")
            budget.reserve_bytes(len(encoded))
            (stage / "manifest.json").write_bytes(encoded)
            os.chmod(stage / "manifest.json", 0o600)
            os.replace(stage, destination)
        except BIExportError as exc:
            raise BIDestinationError(str(exc)) from exc
        finally:
            writer.close()
    return {"ok": True, "output_directory": str(destination), **result}


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def export_bi_xlsx(
    package: str | Path,
    *,
    dataset: str,
    out: str | Path,
    max_rows_per_sheet: int = EXCEL_MAX_ROWS - 1,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Publish split worksheets and a durable CSV-range/worksheet index.

    The index is the completion marker. An interrupted workbook without its
    index is incomplete; the immutable selected CSV package remains the fallback.
    """
    if type(max_rows_per_sheet) is not int or not 1 <= max_rows_per_sheet < EXCEL_MAX_ROWS:
        raise BIDestinationError(f"max_rows_per_sheet must be 1..{EXCEL_MAX_ROWS - 1}")
    if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise BIDestinationError("XLSX max_output_bytes is outside the supported bound")
    root, manifest = _manifest(package)
    datasets = _verify_partitions(root, manifest)
    if dataset not in datasets:
        raise BIDestinationError("dataset is not declared by the BI package")
    if (datasets[dataset]["rows"] + max_rows_per_sheet - 1) // max_rows_per_sheet > MAX_XLSX_SHEETS:
        raise BIDestinationError(
            "XLSX worksheet count exceeds the bound; retain the complete CSV package"
        )
    destination = Path(out).absolute()
    index_path = destination.with_suffix(destination.suffix + ".index.json")
    if destination.suffix.casefold() != ".xlsx":
        raise BIDestinationError("out must end in .xlsx")
    if (
        any(path.is_symlink() or os.path.lexists(path) for path in (destination, index_path))
        or not destination.parent.is_dir()
        or destination.parent.is_symlink()
    ):
        raise BIDestinationError(
            "out and its index must be new files under an existing non-symlink directory"
        )
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise BIDestinationError("BI XLSX export requires the reports extra (openpyxl)") from exc
    source_manifest_sha256 = _checksum_file(root / "manifest.json")
    sheet_count = rows_written = rows_in_sheet = 0
    workbook = Workbook(write_only=True)
    sheet = None
    header: list[str] | None = None
    ranges: list[dict[str, Any]] = []
    temporary = None
    index_temporary = None
    published = False

    # UTF-8 CSV bytes understate XML escaping. Bound actual worksheet spool files
    # at each batch and reject Excel's cell limit before openpyxl can truncate it.
    def check_spool() -> None:
        paths = [Path(item._writer.out) for item in workbook.worksheets if item._writer is not None]
        total = sum(path.stat().st_size for path in paths if path.exists())
        if total > max_output_bytes:
            raise BIDestinationError(
                "XLSX worksheet spool exceeds max_output_bytes; retain complete CSV"
            )
        if shutil.disk_usage(destination.parent).free < MIN_FREE_DISK_BYTES:
            raise BIDestinationError("insufficient free disk for XLSX output; retain complete CSV")

    try:
        with tempfile.NamedTemporaryFile(
            prefix=".seohead-bi-xlsx-", suffix=".tmp", dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
        for part in manifest["datasets"][dataset]["partitions"]:
            current_range = None
            with (root / part["path"]).open(encoding="utf-8", newline="") as stream:
                reader = csv.reader(stream)
                current_header = next(reader)
                if header is None:
                    header = current_header
                elif current_header != header:
                    raise BIDestinationError("dataset partition headers disagree")
                for part_row, row in enumerate(reader, 1):
                    if any(len(value) > EXCEL_MAX_CELL_CHARS for value in row):
                        raise BIDestinationError(
                            "CSV cell exceeds Excel's 32767-character limit; retain complete CSV"
                        )
                    if sheet is None or rows_in_sheet >= max_rows_per_sheet:
                        if sheet is not None:
                            sheet.close()
                        sheet_count += 1
                        sheet = workbook.create_sheet(f"{dataset}-{sheet_count:04d}")
                        sheet.append(header)
                        rows_in_sheet = 0
                        current_range = None
                    if current_range is None:
                        current_range = {
                            "partition": part["path"],
                            "partition_sha256": part["sha256"],
                            "source_row_start": part_row,
                            "source_row_end": part_row,
                            "worksheet": sheet.title,
                            "worksheet_row_start": rows_in_sheet + 2,
                            "worksheet_row_end": rows_in_sheet + 2,
                        }
                        if len(ranges) >= MAX_OUTPUT_PARTITIONS + MAX_XLSX_SHEETS:
                            raise BIDestinationError(
                                "XLSX range index exceeds its bound; retain complete CSV"
                            )
                        ranges.append(current_range)
                    else:
                        current_range["source_row_end"] = part_row
                        current_range["worksheet_row_end"] = rows_in_sheet + 2
                    # Strings remain text. No spreadsheet formula is evaluated.
                    sheet.append([_cell_text(value) for value in row])
                    rows_in_sheet += 1
                    rows_written += 1
                    if rows_written % 256 == 1:
                        check_spool()
        if header is None:
            raise BIDestinationError("dataset has no CSV header")
        if rows_written != datasets[dataset]["rows"]:
            raise BIDestinationError("BI XLSX row conservation failed")
        if sheet is None:
            sheet_count = 1
            sheet = workbook.create_sheet(f"{dataset}-{sheet_count:04d}")
            sheet.append(header)
        if not sheet.closed:
            sheet.close()
        check_spool()
        workbook.save(temporary)
        if temporary.stat().st_size > max_output_bytes:
            raise BIDestinationError("XLSX exceeds max_output_bytes; retain complete CSV")
        if (
            _checksum_file(root / "manifest.json") != source_manifest_sha256
            or _verify_partitions(root, manifest) != datasets
        ):
            raise BIDestinationError("source package changed during XLSX export")
        result = {
            "format": "seohead.bi-xlsx.v1",
            "dataset": dataset,
            "rows": rows_written,
            "worksheets": sheet_count,
            "max_rows_per_sheet": max_rows_per_sheet,
            "output": str(destination),
            "source_schema_version": manifest["schema_version"],
            "index": str(index_path),
        }
        index = {
            "format": "seohead.bi-xlsx-index.v1",
            "state": "complete",
            "workbook": destination.name,
            "workbook_sha256": _checksum_file(temporary),
            "workbook_bytes": temporary.stat().st_size,
            "dataset": dataset,
            "source_manifest_sha256": source_manifest_sha256,
            "source_schema_version": manifest["schema_version"],
            "conservation": manifest.get("conservation")
            or {"source_rows": rows_written, "selected_rows": rows_written, "omitted_rows": 0},
            "source_coverage": manifest.get("source_coverage")
            or {
                key: manifest["datasets"][dataset].get(key)
                for key in ("state", "reason", "coverage")
            },
            "rows": rows_written,
            "worksheets": sheet_count,
            "header_rows_per_sheet": 1,
            "ranges": ranges,
            "max_output_bytes": max_output_bytes,
        }
        encoded = (json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        if (
            len(encoded) > MAX_MANIFEST_BYTES
            or temporary.stat().st_size + len(encoded) > max_output_bytes
        ):
            raise BIDestinationError("XLSX with its index exceeds the output byte bound")
        with tempfile.NamedTemporaryFile(
            prefix=".seohead-bi-xlsx-index-", dir=destination.parent, delete=False
        ) as stream:
            index_temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.chmod(index_temporary, 0o600)
        os.replace(temporary, destination)
        temporary = None
        published = True
        os.replace(index_temporary, index_path)
        index_temporary = None
        return result
    except BaseException:
        if published:
            with suppress(FileNotFoundError):
                destination.unlink()
        raise
    finally:
        # Abort writers explicitly: workbook.close alone does not clean write-only spools.
        for item in workbook.worksheets:
            if item._writer is not None:
                if not item.closed:
                    with suppress(Exception):
                        item.close()
                with suppress(FileNotFoundError):
                    item._writer.cleanup()
        workbook.close()
        for path in (temporary, index_temporary):
            if path is not None:
                with suppress(FileNotFoundError):
                    path.unlink()
