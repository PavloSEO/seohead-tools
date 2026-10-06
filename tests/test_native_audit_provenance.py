"""Native audit headers preserve measured corpus completeness and identity."""

from contextlib import closing

import pytest

from seohead.storage import open_scan
from tests.test_scan_hreflang_graph import BASE, _capture, _html, _Response


@pytest.mark.parametrize("oversized", [False, True])
def test_native_header_carries_exact_retained_corpus_completeness(tmp_path, oversized):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(body='<a href="/other">Next</a>')),
            BASE + "other": _Response(_html(body="x" * (5000 if oversized else 10))),
        },
        max_body_bytes=512,
    )
    with closing(open_scan(tmp_path / "scan.sqlite")) as reader:
        stored = reader.execute(
            "SELECT scan_uuid,corpus_partial,source_kind,config_fingerprint FROM scan WHERE singleton=1"
        ).fetchone()
    assert audit["run"]["corpus_partial"] is bool(stored["corpus_partial"])
    assert audit["run"]["corpus_partial"] is oversized
    for name in ("scan_uuid", "source_kind", "config_fingerprint"):
        assert audit["run"][name] == stored[name]
