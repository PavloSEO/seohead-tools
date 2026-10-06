"""Native analyzer input and nonempty retained derivations remain streamable."""

from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace

from seohead.crawl.collect import PageRecord
from seohead.crawl.evidence import build_evidence
from seohead.sf.core.corpus_derivations import derive
from seohead.sf.core.normalize import INTERNAL_FIELD_MAP, iter_records_from_df
from seohead.storage import open_scan
from tests.test_scan_hreflang_graph import BASE, _capture, _html, _Response


def test_native_input_rows_are_lazy_and_match_export_normalization():
    rows = [
        PageRecord(url=BASE, status_code=200, content_type="text/html", title="Home"),
        PageRecord(url=BASE + "fr/", status_code=200, content_type="text/html"),
    ]
    result = SimpleNamespace(pages=rows, robots_blocked=[rows[1].url])
    expected = build_evidence(result, inlink_counts={BASE: (2, 1)}, stored_graph_available=True)
    observed = build_evidence(
        result,
        inlink_counts={BASE: (2, 1)},
        stored_graph_available=True,
        streaming=True,
        is_robots_blocked=lambda url: url == rows[1].url,
    )
    frame = observed["frames"]["internal_all"]
    assert not isinstance(frame, list)
    assert type(frame).__name__ == "_PageEvidenceRows"
    assert list(iter_records_from_df(frame, INTERNAL_FIELD_MAP)) == list(
        iter_records_from_df(expected["frames"]["internal_all"], INTERNAL_FIELD_MAP)
    )


def test_nonempty_streamed_corpus_matches_legacy_and_lifts_all_collections(tmp_path, monkeypatch):
    import seohead.sf.core.corpus_derivations as core
    from seohead.sf.core.evidence_contract import attach_saved_corpus_header

    group = (("en", "/"), ("fr", "/fr/"), ("x-default", "/"))
    _capture(
        tmp_path,
        {
            BASE: _Response(
                _html(
                    *group,
                    body='<main>Home content for the fixture</main><a href="/fr/">French</a>',
                )
            ),
            BASE + "fr/": _Response(
                _html(*group, body="<main>French content for the fixture</main>")
            ),
        },
    )
    with closing(open_scan(tmp_path / "scan.sqlite")) as con:
        expected = derive(con)
        monkeypatch.setattr(
            core,
            "_page_index",
            lambda *_: (_ for _ in ()).throw(AssertionError("eager page index")),
        )
        observed = derive(con, streaming=True)
        try:
            assert len(observed["internationalization"]["declarations"]) == 6
            collections = {}
            header = attach_saved_corpus_header(
                {"schema_version": "2.0", "summary": {}},
                con,
                derived=observed,
                collections=collections,
            )
            assert len(collections) == 6
            assert (
                header["summary"]["saved_corpus_derivations"]["internationalization"][
                    "declarations"
                ]
                == []
            )
            # A header can be serialized without reading a complete evidence population.
            json.dumps(header)
            from seohead.sf.core.models import _set_collection

            for pointer, rows in collections.items():
                first = list(rows)
                assert first == list(rows)
                _set_collection(header, pointer, first)
            assert header["summary"]["saved_corpus_derivations"] == expected
        finally:
            path = observed.owner.path
            observed.close()
        from pathlib import Path

        assert not Path(path).exists()
