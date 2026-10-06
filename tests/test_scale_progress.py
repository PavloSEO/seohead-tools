"""Structured progress conserves independently committed frontier categories."""

import sqlite3

from seohead.crawl.settings import load
from seohead.crawl.sqlite_adapter import crawl_to_scan
from tests.test_crawl_progress import FIXTURE_SITE, _fetcher, _runtime_versions


def test_structured_progress_matches_independent_database_snapshot(tmp_path):
    path = tmp_path / "scan.sqlite"
    samples = []

    def observe(actual):
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as reader:
            expected = {"queued": 0, "inflight": 0, "excluded": 0}
            states = dict(reader.execute("SELECT state,COUNT(*) FROM frontier GROUP BY state"))
            expected.update({key: states.get(key, 0) for key in expected})
            expected["fetched"] = reader.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        assert actual == expected
        samples.append(actual)

    run = crawl_to_scan(
        "https://example.test/",
        scan_out=str(path),
        settings=load(overrides={"speed.min_delay_seconds": 0, "limits.max_urls": 50}),
        producer_version="3.0.0",
        producer_revision="a" * 40,
        runtime_versions=_runtime_versions(),
        fetcher=_fetcher(FIXTURE_SITE),
        sleeper=lambda _seconds: None,
        progress_snapshot=observe,
    )
    assert len(samples) > 2
    assert samples[-1]["fetched"] == run.pages == 6
    assert samples[-1]["queued"] == samples[-1]["inflight"] == 0
