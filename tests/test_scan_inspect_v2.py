"""Public native-v2 inspection keeps bounded read-only pagination."""

import hashlib
import sqlite3

import pytest

from seohead.servers.handlers import scan_inspect
from seohead.storage import ScanError
from seohead.storage.native_scan import NativeScan
from tests.test_scan_native import _metadata, _record, _runtime


def test_native_v2_inspection_conserves_pages_without_mutation(tmp_path):
    path = tmp_path / "v2.sqlite"
    with NativeScan.create(
        path, format_version="scan.v2", **_metadata(**{"storage.format_version": "scan.v2"})
    ) as scan:
        for suffix in ("", "next"):
            url = "https://example.test/" + suffix
            scan.enqueue([(url, int(bool(suffix)))])
            scan.commit_page(scan.claim(1)[0], _record(url), runtime=_runtime())
        scan.finish_capture()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    first = scan_inspect(input_path=str(path), limit=1)
    second = scan_inspect(input_path=str(path), limit=1, offset=first["next_offset"])
    assert [first["rows"][0]["url"], second["rows"][0]["url"]] == [
        "https://example.test/",
        "https://example.test/next",
    ]
    assert first["has_more"] is True and second["has_more"] is False
    tiny = scan_inspect(input_path=str(path), max_bytes=1)
    assert tiny["rows"] == [] and tiny["truncated"] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    with sqlite3.connect(path) as corrupt:
        corrupt.execute("UPDATE frontier SET state='queued' WHERE state='done'")
    with pytest.raises(ScanError, match="frontier"):
        scan_inspect(input_path=str(path))
