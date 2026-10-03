"""Terminal lifecycle and finished_at stamping for drained frontiers (#712).

A capture whose accepted frontier all reached a terminal outcome must read as
finished -- partial evidence is named by ``crawl_partial``, not by pretending
the run is still interrupted. Cancel, error, and resume paths keep work that is
still queued or inflight from ever receiving a finished timestamp.
"""

from __future__ import annotations

import pytest

from seohead.storage import ScanError
from seohead.storage.native_scan import NativeScan
from tests.test_scan_native import _metadata, _record, _runtime


def _header(path):
    return dict(NativeScan.inspect(path)["scan"])


def test_empty_queue_finishes_with_a_timestamp(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        assert scan.finish_capture() is True
        with pytest.raises(ScanError, match="immutable"):
            scan.finish_capture()
    header = _header(path)
    assert header["lifecycle"] == "finished"
    assert header["finish_reason"] == "finished"
    assert header["finished_at"] is not None


def test_drained_final_batch_finishes_exactly_once(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        assert scan.finish_capture() is True
    header = _header(path)
    assert header["lifecycle"] == "finished"
    assert header["finished_at"] is not None
    assert header["crawl_partial"] == 0


def test_drained_but_partial_evidence_still_finishes(tmp_path):
    """Issue #712: omitted observations must not leave finished_at empty."""
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(
            scan.claim(1)[0],
            _record(),
            runtime=_runtime(),
            partial_reasons=("link_observations_omitted",),
        )
        assert scan.con.execute("SELECT crawl_partial FROM scan").fetchone()[0] == 1
        assert scan.finish_capture() is True
    header = _header(path)
    assert header["lifecycle"] == "finished"
    assert header["crawl_partial"] == 1
    assert header["finished_at"] is not None


def test_exclusion_only_frontier_is_a_drained_queue(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.seed_frontier(
            [
                {
                    "requested_url": "https://example.test/a",
                    "frontier_url": "https://example.test/a",
                    "depth": 0,
                    "reason": "outside_host",
                    "source": "sitemap",
                    "reserve_query": True,
                    "seed": True,
                }
            ]
        )
        assert scan.finish_capture() is True
    header = _header(path)
    assert header["lifecycle"] == "finished"
    assert header["finished_at"] is not None


def test_queued_or_inflight_work_never_stamps_finished(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0), ("https://example.test/next", 1)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        scan.interrupt("operator_cancelled")
        assert scan.finish_capture(reason="interrupted") is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "interrupted"
    assert header["finished_at"] is None


def test_abort_before_any_accepted_work_is_not_finished(tmp_path):
    """An empty frontier after an error stop is not a drained queue."""
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.interrupt("robots.txt unavailable")
        assert scan.finish_capture(reason="robots_unavailable") is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "robots_unavailable"
    assert header["finished_at"] is None


def test_error_stop_after_the_last_page_is_not_finished(tmp_path):
    """A drained queue after an error circuit is a stopped capture, not a finished one."""
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        scan.interrupt("origin stopped responding or refused repeatedly")
        assert scan.finish_capture(reason="errors") is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "errors"
    assert header["finished_at"] is None


def test_inflight_only_frontier_is_not_finished(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.claim(1)
        scan.interrupt("operator_cancelled")
        assert scan.finish_capture(reason="interrupted") is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finished_at"] is None


def test_resume_or_finalize_preserves_an_aborted_empty_capture(tmp_path):
    """Reopening an aborted run must finalize the file, not stamp it finished."""
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.interrupt("robots.txt unavailable")
        assert scan.finish_capture(reason="robots_unavailable") is True
    with NativeScan.open(path) as scan:
        assert scan.resume_or_finalize() is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "robots_unavailable"
    assert header["finished_at"] is None


def test_resume_or_finalize_preserves_a_stopped_drained_capture(tmp_path):
    """An error stop with nothing left to requeue stays interrupted on reopen."""
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        scan.interrupt("origin stopped responding or refused repeatedly")
        assert scan.finish_capture(reason="errors") is True
    with NativeScan.open(path) as scan:
        assert scan.resume_or_finalize() is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "errors"
    assert header["finished_at"] is None


def test_recovery_after_interrupt_finishes_when_the_queue_drains(tmp_path):
    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0), ("https://example.test/next", 1)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        scan.interrupt("operator_cancelled")
    assert _header(path)["lifecycle"] == "interrupted"
    with NativeScan.open(path) as scan:
        scan.begin_collection()
        scan.commit_page(scan.claim(1)[0], _record("https://example.test/next"), runtime=_runtime())
        assert scan.finish_capture() is True
    header = _header(path)
    assert header["lifecycle"] == "finished"
    assert header["finished_at"] is not None


def test_blocked_finalization_still_never_stamps_finished(tmp_path):
    import sqlite3

    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        reader = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM pages").fetchone()
        try:
            assert scan.finish_capture(timeout_seconds=0.1) is False
        finally:
            reader.close()
        assert scan.resume_or_finalize() is True
    header = _header(path)
    assert header["lifecycle"] == "finished"
    assert header["finished_at"] is not None


def test_blocked_finalization_preserves_an_explicit_error_stop(tmp_path):
    """A reader-blocked checkpoint must not rewrite 'errors' into a retryable
    'finalization_blocked' that a later retry reads as a drained queue."""
    import sqlite3

    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        scan.interrupt("origin stopped responding")
        reader = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM pages").fetchone()
        try:
            assert scan.finish_capture(reason="errors", timeout_seconds=0.1) is False
        finally:
            reader.close()
        header = _header(path)
        assert header["lifecycle"] == "interrupted"
        assert header["finish_reason"] == "errors"
        assert header["finished_at"] is None
        assert scan.resume_or_finalize() is True
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "errors"
    assert header["finished_at"] is None
    assert header["crawl_partial"] == 1


def test_blocked_finish_without_audit_preserves_an_explicit_stop(tmp_path):
    import sqlite3

    path = tmp_path / "scan.sqlite"
    with NativeScan.create(path, **_metadata()) as scan:
        scan.enqueue([("https://example.test/", 0)])
        scan.commit_page(scan.claim(1)[0], _record(), runtime=_runtime())
        scan.interrupt("origin stopped responding")
        reader = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM pages").fetchone()
        try:
            assert scan.finish_without_audit(timeout_seconds=0.1) is False
        finally:
            reader.close()
    header = _header(path)
    assert header["lifecycle"] == "interrupted"
    assert header["finish_reason"] == "origin stopped responding"
    assert header["finished_at"] is None
