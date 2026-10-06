"""Retained corpus reads and exclusions preserve rows without list materialization."""

import json
import sqlite3

from seohead.storage.content_evidence import ContextRows, capture_document, derive_duplicates, read
from seohead.storage.structured_evidence import read as read_structured


def test_streaming_payloads_and_duplicate_cap_conserve_all_exclusions():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE context_items(kind TEXT,item_key TEXT,payload_json TEXT)")
    for page_id in reversed(range(1, 14)):
        item = capture_document(
            page_url_id=page_id,
            source_document_id=page_id,
            representation="static",
            html="<html><main><p>One two three four five six seven eight nine ten.</p></main></html>",
            parsed={},
            content_area=None,
            indexable=True,
        )
        con.execute(
            "INSERT INTO context_items VALUES(?,?,?)",
            ("content_evidence", f"page:{page_id}", json.dumps(item)),
        )
    eager = read(con)
    streamed = read(con, streaming=True)
    assert isinstance(streamed["items"], ContextRows)
    assert list(streamed["items"]) == eager["items"] == list(streamed["items"])
    assert streamed["coverage"] == eager["coverage"]
    expected = derive_duplicates(eager["items"], max_candidates=3)
    actual = derive_duplicates(streamed["items"], max_candidates=3, streaming=True)
    assert not isinstance(actual["excluded"], list)
    assert len(actual["excluded"]) == len(expected["excluded"])
    assert list(actual["excluded"]) == list(actual["excluded"])
    actual["excluded"] = list(actual["excluded"])
    assert actual == expected
    assert actual["comparisons"]["eligible_candidates"] <= 3
    assert [row["page_url_id"] for row in actual["excluded"]] == list(range(4, 14))
    assert actual["partial"] is True
    assert read_structured(con, streaming=True)["language"] is not None
    assert list(read_structured(con, streaming=True)["language"]) == []
    con.close()
