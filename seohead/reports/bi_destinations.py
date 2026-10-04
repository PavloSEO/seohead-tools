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
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from seohead.reports.bi import BI_SCHEMA_VERSION, MANIFEST_FORMAT

SHEETS_MAX_CELLS = 10_000_000
HOST_CONFIG_ENV = "SEOHEAD_BI_DESTINATIONS_FILE"
_HOST_CLIENTS: dict[tuple[str, str], Any] = {}
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
BIGQUERY_SCOPE = "https://www.googleapis.com/auth/bigquery"
_GOOGLE_RETRIES = 3
_GOOGLE_CHUNK_ROWS = 1_000


class BIDestinationError(ValueError):
    """A local BI package cannot safely feed an optional destination."""


class BIDestinationCommitUncertain(BIDestinationError):
    """Google may have committed a request after the client lost its response."""


def register_host_client(destination: str, target: str, client: Any) -> None:
    """Register a host-owned, already-authorized client outside CLI/MCP JSON."""
    _HOST_CLIENTS[(destination, target)] = client


def _require_dataset_mapping(
    supplied: Any, datasets: dict[str, Any], *, label: str, id_name: str
) -> dict[str, dict[str, Any]]:
    """Require one closed, host-owned mapping for every declared package dataset."""
    if not isinstance(supplied, dict) or set(supplied) != set(datasets):
        raise BIDestinationError(f"{label} mapping must name every BI package dataset exactly once")
    mapped: dict[str, dict[str, Any]] = {}
    for name, value in supplied.items():
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
                        else json.dumps(body, separators=(",", ":")).encode("utf-8")
                        if body is not None
                        else None
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
    ):
        if operation != "replace":
            raise BIDestinationError(
                "Google Sheets append is unavailable: use replace so every package is reconciled"
            )
        mapping = _require_dataset_mapping(
            self.worksheets, datasets, label="Google Sheets worksheet", id_name="worksheet_id"
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
        token_supplier=None,
        fetcher=None,
    ) -> None:
        super().__init__(token_supplier=token_supplier, fetcher=fetcher)
        self.target = target
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.tables = tables
        self.location = location

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
    ) -> dict[str, Any]:
        if operation not in {"replace", "append"}:
            raise BIDestinationError("BigQuery operation must be replace or append")
        mapping = _require_dataset_mapping(
            self.tables, datasets, label="BigQuery table", id_name="table_id"
        )
        for name, table in mapping.items():
            if not isinstance(table.get("table_id"), str) or not table["table_id"]:
                raise BIDestinationError(f"BigQuery table mapping for {name!r} is invalid")
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
                marker in str(exc).lower() for marker in ("alreadyexists", "duplicate", "http 409")
            ):
                raise
            result = {"jobReference": payload["jobReference"]}
        return self._wait_job(result.get("jobReference", {}).get("jobId", job_id))

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
        ndjson = b"".join(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            for value in objects
        )
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
                    "labels": {"seohead": "bi", "dataset": dataset[:63]},
                }
            },
            media=ndjson,
        )
        output = ((result.get("statistics") or {}).get("load") or {}).get("outputRows")
        if str(output) != str(len(rows)):
            raise BIDestinationError("BigQuery load acknowledgement does not conserve staged rows")
        transaction["rows"][dataset] += len(rows)
        transaction["load_parts"][dataset] += 1

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
                            "labels": {"seohead": "bi", "dataset": name[:63]},
                        }
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
            return GoogleBigQueryClient(target, project_id, dataset_id, tables, location=location)
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
    return {
        "format": "seohead.bi-destination-preview.v1",
        "state": "ready_to_apply",
        "destination": destination,
        "target": target,
        "operation": operation,
        "package_schema_version": manifest["schema_version"],
        "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "datasets": {
            name: {"rows": info["rows"], "columns": info["fields"], "bytes": info["bytes"]}
            for name, info in datasets.items()
        },
        "required_action": "rerun with apply=true after reviewing this exact target and operation",
    }


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
    manifest_sha256 = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    transaction = client.begin(
        target=target,
        operation=operation,
        schema_version=BI_SCHEMA_VERSION,
        datasets=manifest["datasets"],
        package_sha256=manifest_sha256,
    )
    written = {}
    commit_started = False
    try:
        for name, info in datasets.items():
            count = 0
            first_chunk = True
            for rows in _csv_chunks(root, manifest["datasets"][name], size=_GOOGLE_CHUNK_ROWS):
                client.write(transaction, name, rows)
                count += len(rows) - (1 if first_chunk else 0)
                first_chunk = False
            if count != info["rows"]:
                raise BIDestinationError(f"destination row conservation failed for {name!r}")
            written[name] = count
        commit_started = True
        publication = client.commit(transaction)
    except BIDestinationCommitUncertain as exc:
        if not commit_started:
            abort = getattr(client, "abort", None)
            if abort is not None:
                abort(transaction)
            raise
        return {
            "format": "seohead.bi-destination-apply.v1",
            "target": target,
            "operation": operation,
            "state": "commit_uncertain",
            "reason": str(exc),
            "rows": written,
        }
    except BaseException:
        abort = getattr(client, "abort", None)
        if abort is not None:
            abort(transaction)
        raise
    result = {
        "format": "seohead.bi-destination-apply.v1",
        "target": target,
        "operation": operation,
        "state": "committed",
        "manifest_sha256": manifest_sha256,
        "dataset_sha256": _dataset_hashes(manifest),
        "rows": written,
        "input_rows": {name: info["rows"] for name, info in datasets.items()},
        "skipped_rows": {name: 0 for name in datasets},
        "failed_rows": {name: 0 for name in datasets},
        "row_conservation": "verified",
    }
    if isinstance(publication, dict):
        result.update(publication)
    return result


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
