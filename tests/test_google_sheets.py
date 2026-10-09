"""Sheets transport regressions with API-shaped, stateful offline responses."""

from __future__ import annotations

import copy
import csv
import json
import re
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from seohead.reports.bi import export_bi
from seohead.reports.bi_destinations import (
    BIDestinationCommitUncertain,
    BIDestinationError,
    GoogleSheetsClient,
    apply_with_client,
    destination_preview,
    filter_package,
    sheets_plan,
)


class SheetsDouble:
    """Model RAW values and trimmed ValueRange reads without network/authentication."""

    def __init__(self, mapping):
        self.sheets = {
            value["worksheet_id"]: {
                "sheetId": value["worksheet_id"],
                "title": value["worksheet_title"],
                "gridProperties": {"rowCount": 100, "columnCount": 100},
            }
            for value in mapping.values()
        }
        self.values = {value["worksheet_title"]: [["previous value"]] for value in mapping.values()}
        self.requests = []

    def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        url, body = request["url"], request["body"]
        if request["method"] == "GET" and "/values/" not in url:
            return {"sheets": [{"properties": value} for value in self.sheets.values()]}
        if request["method"] == "GET":
            assert url.endswith("?valueRenderOption=UNFORMATTED_VALUE")
            title, start, _, end = self.range(url.split("/values/", 1)[1].split("?", 1)[0])
            rows = copy.deepcopy(self.values.get(title, [])[start - 1 : end])
            for row in rows:
                while row and row[-1] == "":
                    row.pop()
            while rows and not rows[-1]:
                rows.pop()
            return {"values": rows}
        if url.endswith("/values:batchUpdate"):
            assert body["valueInputOption"] == "RAW"
            data = body["data"][0]
            title, start, _, end = self.range(data["range"])
            rows = self.values.setdefault(title, [])
            rows.extend([[] for _ in range(max(0, end - len(rows)))])
            rows[start - 1 : end] = copy.deepcopy(data["values"])
            return {"totalUpdatedRows": len(data["values"])}
        replies = []
        for item in body["requests"]:
            if "addSheet" in item:
                properties = dict(item["addSheet"]["properties"], sheetId=max(self.sheets) + 1)
                self.sheets[properties["sheetId"]] = properties
                replies.append({"addSheet": {"properties": properties}})
            elif "updateCells" in item:
                self.values[self.sheets[item["updateCells"]["range"]["sheetId"]]["title"]] = []
            elif "copyPaste" in item:
                source = self.sheets[item["copyPaste"]["source"]["sheetId"]]["title"]
                target = self.sheets[item["copyPaste"]["destination"]["sheetId"]]["title"]
                self.values[target] = copy.deepcopy(self.values[source])
            elif "deleteSheet" in item:
                properties = self.sheets.pop(item["deleteSheet"]["sheetId"])
                self.values.pop(properties["title"], None)
            elif "updateSheetProperties" in item:
                properties = item["updateSheetProperties"]["properties"]
                self.sheets[properties["sheetId"]].update(properties)
            else:
                raise AssertionError(item)
        return {"replies": replies}

    @staticmethod
    def range(value):
        match = re.fullmatch(r"'([^']+)'!A(\d+):([A-Z]+)(\d+)", value)
        assert match, value
        title, start, width, end = match.groups()
        return title, int(start), width, int(end)


def _selected(tmp_path, columns):
    package = tmp_path / "package"
    export_bi(
        audit={
            "schema": "seohead.site-audit/1",
            "url": "https://example.test/",
            "site": {},
            "pages": [{"url": "https://example.test/", "status_code": 200}],
            "findings": [],
            "summary": {
                "pages_checked": 1,
                "findings_total": 0,
                "tools_run": [],
                "tools_failed": [],
            },
        },
        out_dir=package,
    )
    selected = tmp_path / "selected"
    filter_package(package, dataset="pages", out_dir=selected, columns=columns)
    return selected


def _client(mapping, remote):
    return GoogleSheetsClient(
        "synthetic", "sheet-id", mapping, token_supplier=lambda _scope: "test-token", fetcher=remote
    )


def test_selected_sheet_preserves_types_and_nulls_with_google_shaped_readback(tmp_path):
    package = _selected(tmp_path, ["url", "status_code", "meta_description"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    result = apply_with_client(
        package,
        target="synthetic",
        operation="replace",
        client=_client(mapping, remote),
        apply=True,
    )
    assert result["state"] == "committed"
    assert remote.values["pages"] == [
        ["url", "status_code", "meta_description"],
        ["https://example.test/", 200, ""],
    ]
    assert type(remote.values["pages"][1][1]) is int
    assert result["rows"] == result["input_rows"] == {"pages": 1}


def test_sheets_raw_values_keep_zero_false_null_and_formula_text_distinct():
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    client = _client(mapping, remote)
    fields = [
        {"name": name, "type": kind}
        for name, kind in [
            ("count", "integer"),
            ("measured", "boolean"),
            ("value", "number"),
            ("text", "string"),
        ]
    ]
    tx = client.begin(
        target="synthetic",
        operation="replace",
        schema_version="seohead.bi.v1",
        datasets={"pages": {"row_count": 3, "fields": fields}},
        package_sha256="f" * 64,
    )
    client.write(tx, "pages", [[field["name"] for field in fields]])
    client.write(
        tx, "pages", [["0", "false", "0.0", "'=SUM(1,2)"], ["", "", "", ""], ["", "", "", ""]]
    )
    client.commit(tx)
    assert remote.values["pages"][1:] == [
        [0, False, 0.0, "'=SUM(1,2)"],
        ["", "", "", ""],
        ["", "", "", ""],
    ]
    assert type(remote.values["pages"][1][0]) is int
    assert type(remote.values["pages"][1][1]) is bool


@pytest.mark.parametrize("failure", [KeyboardInterrupt, BIDestinationCommitUncertain])
def test_sheets_begin_recovers_after_plain_retry_and_error_status_change(tmp_path, failure):
    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    first = _client(mapping, remote)
    original = first.begin

    def lost_begin(**kwargs):
        original(**kwargs)
        raise failure("lost addSheet response")

    first.begin = lost_begin
    with pytest.raises(failure):
        apply_with_client(
            package, target="synthetic", operation="replace", client=first, apply=True
        )
    resumed = _client(mapping, remote)
    before = len(remote.requests)
    waiting = apply_with_client(
        package, target="synthetic", operation="replace", client=resumed, apply=True
    )
    assert waiting["state"] == "reconciliation_required"
    assert len(remote.requests) == before
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=resumed, apply=True, reconcile=True
    )
    assert result["state"] == "committed"
    assert sum("addSheet" in str(request["body"]) for request in remote.requests) == 1


@pytest.mark.parametrize("duplicate", ["worksheet_id", "worksheet_title"])
def test_sheets_rejects_duplicate_targets_in_preview_and_apply(tmp_path, monkeypatch, duplicate):
    package = Path(__file__).resolve().parents[1] / "docs/examples/reporting-pack/worksheets"
    manifest = json.loads((package / "manifest.json").read_text())
    mapping = {
        name: {"worksheet_id": i, "worksheet_title": name}
        for i, name in enumerate(manifest["datasets"])
    }
    mapping["pages"][duplicate] = mapping["cohorts"][duplicate]
    config = tmp_path / "destinations.json"
    config.write_text(
        json.dumps(
            {
                "sheets": {
                    "targets": {
                        "synthetic": {
                            "enabled": True,
                            "kind": "google_sheets_service_account",
                            "spreadsheet_id": "sheet-id",
                            "worksheets": mapping,
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setenv("SEOHEAD_BI_DESTINATIONS_FILE", str(config))
    with pytest.raises(BIDestinationError, match="distinct"):
        destination_preview(package, target="synthetic", destination="sheets", operation="replace")
    calls = []
    client = _client(mapping, lambda request: calls.append(request))
    with pytest.raises(BIDestinationError, match="distinct"):
        client.begin(
            target="synthetic",
            operation="replace",
            schema_version=manifest["schema_version"],
            datasets=manifest["datasets"],
            package_sha256="a" * 64,
        )
    assert calls == []


@pytest.mark.parametrize("changed", ["spreadsheet", "worksheet"])
def test_checkpoint_rejects_changed_target_before_claiming_commit(tmp_path, changed):
    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    client = _client(mapping, remote)
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert result["state"] == "committed"
    calls = len(remote.requests)
    if changed == "spreadsheet":
        client.spreadsheet_id = "a-different-spreadsheet"
    else:
        client.worksheets["pages"]["worksheet_id"] = 123
    with pytest.raises(BIDestinationError, match="another package or target"):
        apply_with_client(
            package, target="synthetic", operation="replace", client=client, apply=True
        )
    assert len(remote.requests) == calls


def test_quota_exhaustion_keeps_stage_and_reconciles_after_quota_recovers(tmp_path, monkeypatch):
    from seohead.reports import bi_destinations

    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    throttled = True
    sleeps = []
    monkeypatch.setattr(bi_destinations.time, "sleep", sleeps.append)
    monkeypatch.setattr(bi_destinations.random, "random", lambda: 0.5)

    def quota(request):
        if throttled and request["url"].endswith("/values:batchUpdate"):
            return {"error": {"code": 429}}
        return remote(request)

    client = _client(mapping, quota)
    first = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert first["state"] == "reconciliation_required"
    assert sleeps == [1.5, 2.5, 4.5]
    assert remote.values["pages"] == [["previous value"]]
    assert first["pending_rows"] == first["input_rows"] == {"pages": 1}
    calls = len(remote.requests)
    waiting = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert waiting["state"] == "reconciliation_required" and len(remote.requests) == calls
    throttled = False
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True, reconcile=True
    )
    assert result["state"] == "committed" and result["pending_rows"] == {"pages": 0}
    assert sum("addSheet" in str(request["body"]) for request in remote.requests) == 1


@pytest.mark.parametrize("retry_after", ["10", "Wed, 21 Oct 2015 07:28:00 GMT"])
def test_google_retry_after_is_respected_within_the_wait_budget(monkeypatch, retry_after):
    from seohead.reports import bi_destinations

    sleeps = []
    monkeypatch.setattr(bi_destinations.time, "sleep", sleeps.append)
    monkeypatch.setattr(bi_destinations.time, "time", lambda: 1445412470)
    monkeypatch.setattr(bi_destinations.random, "random", lambda: 0.5)
    headers = Message()
    headers["Retry-After"] = retry_after
    calls = 0

    def fetch(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(request["url"], 429, "quota", headers, None)
        return {"recovered": True}

    client = _client({}, fetch)
    assert client._request("GET", "https://sheets.googleapis.com/synthetic", retryable=True) == {
        "recovered": True
    }
    assert sleeps == [10.0]


def test_long_retry_after_requires_later_reconciliation_without_sleeping(monkeypatch):
    from seohead.reports import bi_destinations

    monkeypatch.setattr(
        bi_destinations.time, "sleep", lambda _: pytest.fail("wait must stay bounded")
    )
    headers = Message()
    headers["Retry-After"] = "120"
    calls = []

    def fetch(request):
        calls.append(request)
        raise urllib.error.HTTPError(request["url"], 429, "quota", headers, None)

    with pytest.raises(BIDestinationCommitUncertain, match="60-second wait budget"):
        _client({}, fetch)._request(
            "GET", "https://sheets.googleapis.com/synthetic", retryable=True
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "seohead.bi.pages.v999"),
        ("grain", "wrong grain"),
        ("primary_key", ["wrong_key"]),
    ],
)
def test_destination_rejects_dataset_contract_drift_before_transport(tmp_path, field, value):
    selected = _selected(tmp_path, ["url"])
    package = selected.parent / "package"
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["datasets"]["pages"][field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BIDestinationError, match="version, grain or key"):
        sheets_plan(package)


def test_sheet_target_growth_is_budgeted_before_staging_and_applied_atomically(
    tmp_path, monkeypatch
):
    from seohead.reports import bi_destinations

    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    remote.sheets[0]["gridProperties"] = {"rowCount": 1, "columnCount": 1}
    client = _client(mapping, remote)
    monkeypatch.setattr(bi_destinations, "SHEETS_MAX_CELLS", 7)
    with pytest.raises(BIDestinationError, match="staging cells"):
        apply_with_client(
            package, target="synthetic", operation="replace", client=client, apply=True
        )
    assert all(request["method"] == "GET" for request in remote.requests)
    assert remote.values["pages"] == [["previous value"]]
    monkeypatch.setattr(bi_destinations, "SHEETS_MAX_CELLS", 8)
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True, reconcile=True
    )
    assert result["state"] == "committed"
    assert remote.sheets[0]["gridProperties"] == {"rowCount": 2, "columnCount": 2}
    publication = remote.requests[-1]["body"]["requests"]
    assert "updateSheetProperties" in publication[0]
    assert "updateCells" in publication[1]
    assert "copyPaste" in publication[2]


def test_empty_header_failure_does_not_overwrite_the_existing_target(tmp_path):
    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)

    def invalid_readback(request):
        if request["method"] == "GET" and "/values/" in request["url"]:
            return {"values": [["wrong", "header"]]}
        return remote(request)

    result = apply_with_client(
        package,
        target="synthetic",
        operation="replace",
        client=_client(mapping, invalid_readback),
        apply=True,
    )
    assert result["state"] == "failed"
    assert remote.values == {"pages": [["previous value"]]}
    assert result["input_rows"]["pages"] == sum(
        result[key]["pages"] for key in ("rows", "skipped_rows", "failed_rows", "pending_rows")
    )


@pytest.mark.parametrize("applied", [True, False])
def test_atomic_commit_timeout_requires_reconciliation_without_claiming_success(tmp_path, applied):
    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    lost = True

    def uncertain_commit(request):
        nonlocal lost
        if lost and "copyPaste" in str(request["body"]):
            lost = False
            if applied:
                remote(request)
            raise TimeoutError("synthetic atomic response lost")
        return remote(request)

    client = _client(mapping, uncertain_commit)
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert result["state"] == "reconciliation_required"
    assert "row_conservation" not in result
    assert remote.values["pages"] == (
        [["url", "status_code"], ["https://example.test/", 200]]
        if applied
        else [["previous value"]]
    )
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True, reconcile=True
    )
    assert result["state"] == ("reconciliation_required" if applied else "committed")
    assert sum("copyPaste" in str(request["body"]) for request in remote.requests) == 1


@pytest.mark.parametrize("changed", ["manifest", "partition"])
def test_source_mutation_during_staging_never_publishes_against_the_old_manifest(tmp_path, changed):
    package = _selected(tmp_path, ["url", "status_code"])
    mapping = {"pages": {"worksheet_id": 0, "worksheet_title": "pages"}}
    remote = SheetsDouble(mapping)
    client = _client(mapping, remote)
    original = client.begin

    def mutate_after_begin(**kwargs):
        transaction = original(**kwargs)
        if changed == "manifest":
            path = package / "manifest.json"
            path.write_text(path.read_text() + "\n")
        else:
            manifest = json.loads((package / "manifest.json").read_text())
            path = package / manifest["partitions"][0]["path"]
            with path.open(newline="") as stream:
                rows = list(csv.reader(stream))
            rows[1][1] = "201"
            with path.open("w", newline="") as stream:
                csv.writer(stream).writerows(rows)
        return transaction

    client.begin = mutate_after_begin
    result = apply_with_client(
        package, target="synthetic", operation="replace", client=client, apply=True
    )
    assert result["state"] == "failed"
    assert "row_conservation" not in result
    assert remote.values == {"pages": [["previous value"]]}
    assert not any("copyPaste" in str(request["body"]) for request in remote.requests)
