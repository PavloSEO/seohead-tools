"""Per-hop status, next Location and response time in redirect and canonical chains (#930).

The chain walks already follow each hop; these tests pin that every recorded hop says what
it saw (status, where it points next, how long it took), so one URL's chain can be read
from the stored evidence without a second request.
"""

from __future__ import annotations

import seohead.crawl.collect as collect_mod
from seohead.crawl.collect import PageRecord


def _hop(
    url: str, status: int, *, location: str = "", canonical: str = "", took: float
) -> PageRecord:
    rec = PageRecord(url=url)
    rec.status_code = status
    rec.redirect_url = location
    rec.canonical = canonical
    rec.response_time = took
    return rec


def _fake_fetch(responses: dict[str, PageRecord]):
    def fetch_one(url, **_kwargs):
        return responses[url], None

    return fetch_one


def test_each_redirect_hop_records_status_next_location_and_time(monkeypatch):
    responses = {
        "https://example.com/b": _hop(
            "https://example.com/b", 301, location="https://example.com/c", took=0.12
        ),
        "https://example.com/c": _hop("https://example.com/c", 200, took=0.05),
    }
    monkeypatch.setattr(collect_mod, "fetch_one", _fake_fetch(responses))
    record = PageRecord(
        url="https://example.com/a", status_code=301, redirect_url="https://example.com/b"
    )

    collect_mod._resolve_redirect_destination(
        record,
        client=None,
        fetcher=None,
        throttle=None,
        extra_headers=None,
        user_agent="",
        max_response_bytes=0,
        retry_on_timeout=0,
        parse_options=None,
        cache=None,
        wait=lambda: None,
    )

    assert [hop["url"] for hop in record.redirect_chain] == [
        "https://example.com/b",
        "https://example.com/c",
    ]
    first, second = record.redirect_chain
    assert (first["status_code"], first["location"], first["response_time"]) == (
        301,
        "https://example.com/c",
        0.12,
    )
    assert (second["status_code"], second["location"], second["response_time"]) == (200, "", 0.05)
    assert record.final_url == "https://example.com/c"


def test_each_canonical_hop_records_status_canonical_and_time(monkeypatch):
    responses = {
        "https://example.com/x": _hop(
            "https://example.com/x", 200, canonical="https://example.com/y", took=0.2
        ),
        "https://example.com/y": _hop("https://example.com/y", 200, took=0.07),
    }
    monkeypatch.setattr(collect_mod, "fetch_one", _fake_fetch(responses))
    record = PageRecord(url="https://example.com/a", canonical="https://example.com/x")

    collect_mod._resolve_canonical_destination(
        record,
        client=None,
        fetcher=None,
        throttle=None,
        extra_headers=None,
        user_agent="",
        max_response_bytes=0,
        retry_on_timeout=0,
        parse_options=None,
        cache=None,
        sleeper=lambda _s: None,
        wait=lambda: None,
    )

    first, second = record.canonical_chain
    assert (first["canonical"], first["response_time"]) == ("https://example.com/y", 0.2)
    assert (second["canonical"], second["response_time"]) == ("", 0.07)
    assert record.final_canonical == "https://example.com/y"
