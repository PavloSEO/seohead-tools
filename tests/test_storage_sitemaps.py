"""Direct state and negative cases for selected sitemap roots and membership context."""

import pytest

from seohead.storage import ScanError, _url
from seohead.storage.native_scan import NativeScan
from seohead.storage.sitemaps import declare, finish, root_ids, validate_context
from tests.test_scan_native import _metadata


def _scan(tmp_path):
    return NativeScan.create(tmp_path / "scan.sqlite", **_metadata())


def test_declare_appends_roots_in_order_and_rejects_gaps(tmp_path):
    with _scan(tmp_path) as scan:
        first = declare(scan.con, "https://example.test/s1.xml", "explicit", 0)
        second = declare(scan.con, "https://example.test/s2.xml", "robots", 1)
        assert root_ids(scan.con) == {first, second}
        with pytest.raises(ScanError, match="append"):
            declare(scan.con, "https://example.test/s3.xml", "explicit", 5)


def test_declare_rejects_duplicate_root_and_bad_source(tmp_path):
    with _scan(tmp_path) as scan:
        declare(scan.con, "https://example.test/s1.xml", "explicit", 0)
        with pytest.raises(ScanError, match="unique"):
            declare(scan.con, "https://example.test/s1.xml", "explicit", 1)
        with pytest.raises(ScanError, match="invalid selected sitemap root"):
            declare(scan.con, "https://example.test/s2.xml", "guessed", 1)


def test_finish_twice_updates_the_single_summary_row(tmp_path):
    with _scan(tmp_path) as scan:
        sid = declare(scan.con, "https://example.test/s1.xml", "explicit", 0)
        finish(scan.con, sid, True, "")
        finish(scan.con, sid, False, "truncated")
        rows = scan.con.execute(
            "SELECT item_key, completeness, reason FROM context_items "
            "WHERE kind='sitemap_fetch_summary'"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(f"url:{sid}", "partial", "truncated")]


def test_membership_without_selected_root_is_rejected(tmp_path):
    with _scan(tmp_path) as scan:
        member_url = _url(scan.con, "https://example.test/page")
        sitemap_url = _url(scan.con, "https://example.test/unselected.xml")
        payload = {"sitemap_url_id": sitemap_url, "url_id": member_url, "ordinal": 0}
        item = {"kind": "sitemap_declared_url", "item_key": f"sitemap:{sitemap_url}:ordinal:0"}
        with pytest.raises(ScanError, match="no selected root"):
            validate_context(scan.con, item, payload)


def test_membership_with_unknown_sitemap_or_member_is_rejected(tmp_path):
    with _scan(tmp_path) as scan:
        sid = declare(scan.con, "https://example.test/s1.xml", "explicit", 0)
        unknown_member = {"sitemap_url_id": sid, "url_id": 999_999, "ordinal": 0}
        item = {"kind": "sitemap_declared_url", "item_key": f"sitemap:{sid}:ordinal:0"}
        with pytest.raises(ScanError, match="unknown URL"):
            validate_context(scan.con, item, unknown_member)
        unknown_root = {"sitemap_url_id": 999_999, "url_id": sid, "ordinal": 0}
        with pytest.raises(ScanError, match="invalid sitemap URL reference"):
            validate_context(scan.con, dict(item, kind="sitemap_declared_url"), unknown_root)


def test_summary_rejects_shape_and_completeness_mismatch(tmp_path):
    with _scan(tmp_path) as scan:
        sid = declare(scan.con, "https://example.test/s1.xml", "explicit", 0)
        good = {"sitemap_url_id": sid, "response_ids": [], "complete": True, "reason": ""}
        item = {
            "kind": "sitemap_fetch_summary",
            "item_key": f"url:{sid}",
            "completeness": "partial",
        }
        with pytest.raises(ScanError, match="completeness disagrees"):
            validate_context(scan.con, item, good)
        with pytest.raises(ScanError, match="shape"):
            validate_context(scan.con, item, dict(good, extra=1))
