"""Offline navigation pagination and missing evidence remain honest."""

import hashlib

import pytest

from seohead.servers.navigation_handlers import scan_navigation
from seohead.storage import ScanError
from seohead.storage.native_scan import NativeScan
from seohead.tools.navigation import NavigationCapture
from tests.test_native_capture import _renderer
from tests.test_native_render_atomic import _static_page
from tests.test_scan_native import _metadata


def test_navigation_report_pages_observed_and_legacy_documents_without_mutation(tmp_path):
    path = tmp_path / "scan.seohead"
    with NativeScan.create(path, **_metadata()) as scan:
        lease = _static_page(scan)
        for observed in (True, False, True):
            renderer = _renderer(lease.url)
            if observed:
                capture = NavigationCapture(lease.url, "load", 10)
                capture.cdp = True
                capture.add(lease.url + "?route=1", "spa_history_change", "cdp_history")
                capture.finish(success=True)
                renderer["navigation"].update(capture.data)
            scan.commit_render(
                lease.url,
                None,
                html="<html>fixture</html>",
                renderer=renderer,
                captured_at="2026-10-06T10:00:00Z",
            )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    first = scan_navigation(str(path), limit=2)
    assert first["state"] == "partial"
    assert first["has_more"]
    assert first["document_states"] == {"complete": 1, "unavailable": 1}
    second = scan_navigation(str(path), limit=2, offset=first["next_offset"])
    assert second["state"] == "complete"
    assert not second["has_more"]
    assert len({item["document_id"] for item in first["items"] + second["items"]}) == 3
    empty = scan_navigation(str(path), offset=100)
    assert empty["state"] == "unavailable"
    with pytest.raises(ScanError, match="absent"):
        scan_navigation(str(path), document_id=9000)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"limit": 1001}, {"offset": -1}, {"document_id": True}]
)
def test_navigation_report_rejects_invalid_bounds_before_opening(kwargs):
    with pytest.raises(ValueError):
        scan_navigation("unused.seohead", **kwargs)
