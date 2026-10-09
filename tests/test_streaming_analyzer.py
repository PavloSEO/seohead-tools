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


def test_native_streaming_writer_preserves_nonempty_collections_without_frames(
    tmp_path, monkeypatch
):
    import pandas as pd

    import tests.test_scan_hreflang_graph as fixture
    from seohead.crawl.sql_sitemap import prepare_sitemap_reconciliation
    from seohead.crawl.sqlite_adapter import retained_start_gate
    from seohead.mcp.handlers import _audit_crawl_result
    from seohead.mcp.scan_handlers import _rebuild_page_result
    from seohead.storage.audit_v2 import AuditV2Reader
    from seohead.storage.native_scan import NativeScan

    owners = []

    def save_streaming(path, settings):
        def no_frame(*_args, **_kwargs):
            raise AssertionError("native streaming audit must not construct a DataFrame")

        with monkeypatch.context() as patch:
            patch.setattr(pd, "DataFrame", no_frame)
            with NativeScan.open(path) as scan:
                result = _rebuild_page_result(scan, page_view=True)
                result.start_page_evidence = retained_start_gate(scan, settings)
                with prepare_sitemap_reconciliation(scan.con, start_url=BASE) as sitemap:
                    _response, (header, collections) = _audit_crawl_result(
                        result,
                        settings=settings,
                        url=BASE,
                        sitemap_seed={"sitemap_url": None, "sitemap_urls": [], "declared": []},
                        discovery={
                            "mode": "spider",
                            "directive_policy": settings["robots"]["policy"],
                            "robots_blocked": 0,
                        },
                        stored_scan=scan,
                        stored_sitemap=sitemap,
                        streaming=True,
                        offline=True,
                    )
                    owners.extend({rows._context_owner for rows in collections.values()})
                    assert (
                        header["summary"]["saved_corpus_derivations"]["internationalization"][
                            "declarations"
                        ]
                        == []
                    )
                    assert list(
                        collections[
                            "/summary/saved_corpus_derivations/internationalization/declarations"
                        ]
                    )
                    scan.save_audit_v2(header, collections)
                    for owner in owners:
                        owner.close()
                assert scan.finish_capture("streaming fixture complete")

    monkeypatch.setattr(fixture, "_save_native_audit", save_streaming)
    group = (("en", "/"), ("fr", "/fr/"))
    audit = fixture._capture(
        tmp_path,
        {
            BASE: _Response(
                _html(
                    *group, body='<main>Captured first fixture body</main><a href="/fr/">French</a>'
                )
            ),
            BASE + "fr/": _Response(
                _html(*group, body="<main>Captured second fixture body</main>")
            ),
        },
    )
    assert len(audit["pages"]) == 2
    assert (
        len(audit["summary"]["saved_corpus_derivations"]["internationalization"]["declarations"])
        == 4
    )
    with AuditV2Reader(tmp_path / "scan.sqlite") as reader:
        assert reader.count("/summary/saved_corpus_derivations/structured/items") == 2
        assert (
            reader.count("/summary/saved_corpus_derivations/internationalization/declarations") == 4
        )
    assert all(owner._saved_corpus is None and owner._disk_pages is None for owner in owners)
