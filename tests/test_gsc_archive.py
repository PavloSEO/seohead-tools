import json
import sqlite3

import pytest

from seohead.data_sources import gsc_archive
from seohead.data_sources.gsc_archive import Archive


def result(rows):
    return {
        "ok": True,
        "rows": rows,
        "status": 200,
        "reason": None,
        "response_aggregation_type": "byProperty",
        "metadata": {},
    }


def test_plan_resume_and_property_separation(tmp_path):
    path = tmp_path / "archive.sqlite"
    archive = Archive(path)
    archive.prepare("sc-domain:example.com", "2026-01-01", "2026-01-02")
    archive.prepare("sc-domain:blog.example.com", "2026-01-01", "2026-01-02")
    archive.prepare("sc-domain:example.com", "2026-01-01", "2026-01-02")
    assert archive.status()["states"] == {"pending": 12}
    assert archive.db.execute("SELECT DISTINCT provider,engine,date_timezone FROM jobs").fetchall()[
        0
    ][:] == ("gsc", "google", "America/Los_Angeles")
    calls = []

    def fake(site, **kwargs):
        calls.append((site, kwargs))
        return result(
            [
                {
                    "keys": ["2026-01-01"],
                    "clicks": 3,
                    "impressions": 20,
                    "ctr": 0.15,
                    "position": None,
                }
            ]
        )

    archive.run_batch(1, pause=0, fetcher=fake)
    assert archive.status()["rows"] == 1
    assert archive.db.execute("SELECT position FROM facts").fetchone()[0] is None
    assert (
        archive.db.execute("SELECT count(*) FROM jobs WHERE dataset!='availability'").fetchone()[0]
        == 6
    )
    archive.close()
    archive = Archive(path)
    assert archive.status()["rows"] == 1
    assert archive.db.execute("SELECT count(*) FROM jobs WHERE state='complete'").fetchone()[0] == 1
    archive.close()


def test_empty_dates_do_not_create_daily_jobs(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    archive.prepare("sc-domain:example.com", "2026-01-01", "2026-01-02")
    status = archive.run_batch(6, pause=0, fetcher=lambda *a, **kw: result([]))
    assert status["complete"] and status["rows"] == 0
    assert archive.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 6
    archive.close()


def test_quota_is_resumable_and_not_complete(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    archive.prepare("sc-domain:example.com", "2026-01-01", "2026-01-02")
    status = archive.run_batch(
        6,
        pause=0,
        fetcher=lambda *a, **kw: {
            "ok": False,
            "rows": [],
            "status": 429,
            "reason": "quotaExceeded",
        },
    )
    assert status["requests_this_batch"] == 1 and not status["complete"]
    assert status["states"]["quota_wait"] == 1
    assert (
        archive.db.execute("SELECT next_row FROM jobs WHERE state='quota_wait'").fetchone()[0] == 0
    )
    archive.close()


def test_refuses_unowned_database_without_schema_mutation(tmp_path):
    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE important(value TEXT)")
    with pytest.raises(ValueError, match="not a GSC archive"):
        Archive(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("important",)
        ]


def test_single_writer_and_verified_backup(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    archive.prepare("sc-domain:example.com", "2026-01-01", "2026-01-02")
    with pytest.raises(RuntimeError, match="Another writer"):
        Archive(archive.path)
    proof = archive.backup(tmp_path / "snapshot.sqlite")
    assert proof["rows"] == 0 and len(proof["sha256"]) == 64
    with pytest.raises(ValueError):
        archive.backup(tmp_path / "snapshot.sqlite")
    archive.close()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gsc_archive.time, "time", lambda: now[0])
    monkeypatch.setattr(gsc_archive.time, "sleep", lambda _: None)
    return now


@pytest.mark.parametrize(
    "reason",
    [
        "quotaExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "dailyLimitExceeded",
        "servingLimitExceeded",
    ],
)
def test_quota_blocks_all_properties_persistently(tmp_path, clock, reason):
    path = tmp_path / "archive.sqlite"
    archive = Archive(path)
    archive.prepare("sc-domain:example.test", "2026-01-01", "2026-01-02")
    archive.prepare("sc-domain:other.test", "2026-01-01", "2026-01-02")
    calls = []

    def limited(*args, **kwargs):
        calls.append(kwargs)
        return {"ok": False, "rows": [], "status": 403, "reason": reason}

    status = archive.run_batch(10, pause=0, fetcher=limited)
    assert len(calls) == 1
    assert status["provider_backoff"]["retry_at"] > clock[0]
    archive.close()
    archive = Archive(path)
    assert archive.run_batch(10, pause=0, fetcher=limited)["requests_this_batch"] == 0
    assert len(calls) == 1
    clock[0] = status["provider_backoff"]["retry_at"]
    status = archive.run_batch(12, pause=0, fetcher=lambda *a, **kw: result([]))
    assert status["complete"] and status["requests_this_batch"] == 12
    archive.close()


@pytest.mark.parametrize(
    "status,reason", [(None, "transport_error"), (500, "api_error"), (503, "api_error")]
)
def test_transient_retry_recovers_at_same_page_after_reopen(tmp_path, clock, status, reason):
    path = tmp_path / "archive.sqlite"
    archive = Archive(path)
    with archive.db:
        archive._queue(
            "sc-domain:example.test", "pages", "web", "2026-01-01", "2026-01-01", ["page"]
        )
        archive.db.execute("UPDATE jobs SET next_row=25000")
    failed = archive.run_batch(
        1,
        pause=0,
        fetcher=lambda *a, **kw: {
            "ok": False,
            "rows": [],
            "status": status,
            "reason": reason,
        },
    )
    assert failed["states"] == {"retry_wait": 1}
    assert not failed["complete"]
    retry_at = archive.db.execute("SELECT retry_at FROM jobs").fetchone()[0]
    archive.close()
    archive = Archive(path)
    assert (
        archive.run_batch(1, pause=0, fetcher=lambda *a, **kw: result([]))["requests_this_batch"]
        == 0
    )
    clock[0] = retry_at
    offsets = []

    def recovered(*args, **kwargs):
        offsets.append(kwargs["start_row"])
        return result([{"keys": ["https://example.test/page"], "clicks": 2, "impressions": 9}])

    done = archive.run_batch(1, pause=0, fetcher=recovered)
    assert offsets == [25000]
    assert done["complete"] and done["rows"] == 1
    job = archive.db.execute("SELECT next_row,error,retry_at FROM jobs").fetchone()
    assert tuple(job) == (25001, None, 0)
    assert archive.db.execute("SELECT count(*) FROM requests").fetchone()[0] == 2
    archive.close()


def test_transient_retries_are_bounded(tmp_path, clock):
    archive = Archive(tmp_path / "archive.sqlite")
    with archive.db:
        archive._queue(
            "sc-domain:example.test", "pages", "web", "2026-01-01", "2026-01-01", ["page"]
        )
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(kwargs["start_row"])
        return {"ok": False, "rows": [], "status": 503, "reason": "api_error"}

    for _ in range(gsc_archive.MAX_TRANSIENT_ATTEMPTS):
        archive.run_batch(10, pause=0, fetcher=unavailable)
        clock[0] += 100
    assert calls == [0] * gsc_archive.MAX_TRANSIENT_ATTEMPTS
    status = archive.run_batch(10, pause=0, fetcher=unavailable)
    assert status["requests_this_batch"] == 0
    assert status["states"] == {"failed": 1} and not status["complete"]
    archive.close()


def test_appearance_filter_and_provenance_survive_observations(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    with archive.db:
        archive._queue(
            "sc-domain:example.test",
            "appearances",
            "web",
            "2026-01-01",
            "2026-01-01",
            ["searchAppearance"],
        )
    archive.run_batch(
        1,
        pause=0,
        fetcher=lambda *a, **kw: result(
            [{"keys": ["RICH_RESULTS"], "clicks": 3, "impressions": 10}]
        ),
    )

    def detail(*args, **kwargs):
        assert kwargs["dimension_filter_groups"][0]["filters"][0]["expression"] == "RICH_RESULTS"
        response = result(
            [
                {
                    "keys": ["https://example.test/page", "synthetic query", "usa", "MOBILE"],
                    "clicks": 1,
                    "impressions": 4,
                    "ctr": None,
                    "position": None,
                }
            ]
        )
        response["metadata"] = {"firstIncompleteDate": "2026-01-02"}
        return response

    archive.run_batch(1, pause=0, fetcher=detail)
    row = dict(
        archive.db.execute(
            "SELECT * FROM observations WHERE dataset='appearance_detail'"
        ).fetchone()
    )
    assert row["appearance"] == "RICH_RESULTS"
    assert json.loads(row["filters"])[0]["filters"][0]["expression"] == row["appearance"]
    assert json.loads(row["dimensions"]) == ["page", "query", "country", "device"]
    assert row["request_key"] and row["job_id"] and row["last_requested_at"]
    assert json.loads(row["metadata"]) == {"firstIncompleteDate": "2026-01-02"}
    assert row["provider"] == "gsc" and row["engine"] == "google"
    assert row["ctr"] is None and row["position"] is None
    archive.close()


def test_overlapping_prepare_only_queues_gaps_and_no_duplicate_availability(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    archive.prepare("sc-domain:example.test", "2026-01-02", "2026-01-03")
    archive.prepare("sc-domain:example.test", "2026-01-01", "2026-01-04")
    archive.prepare("sc-domain:example.test", "2026-01-02", "2026-01-04")
    jobs = archive.db.execute(
        "SELECT start_date,end_date FROM jobs WHERE search_type='web' ORDER BY start_date"
    ).fetchall()
    assert [tuple(r) for r in jobs] == [
        ("2026-01-01", "2026-01-01"),
        ("2026-01-02", "2026-01-03"),
        ("2026-01-04", "2026-01-04"),
    ]

    def days(*args, **kwargs):
        begin = int(kwargs["start_date"][-2:])
        end = int(kwargs["end_date"][-2:])
        return result(
            [
                {"keys": [f"2026-01-{d:02}"], "clicks": 1, "impressions": 2}
                for d in range(begin, end + 1)
            ]
        )

    archive.run_batch(18, pause=0, fetcher=days)
    assert (
        archive.db.execute(
            "SELECT count(*) FROM observations WHERE dataset='availability'"
        ).fetchone()[0]
        == 24
    )
    archive.prepare("sc-domain:example.test", "2026-01-01", "2026-01-04")
    assert (
        archive.db.execute("SELECT count(*) FROM jobs WHERE dataset='availability'").fetchone()[0]
        == 18
    )
    archive.close()


def test_source_isolation_in_prepare_execution_and_observations(tmp_path, clock):
    archive = Archive(tmp_path / "archive.sqlite")
    with archive.db:
        archive._queue(
            "sc-domain:example.test", "availability", "web", "2026-01-01", "2026-01-01", ["date"]
        )
        archive.db.execute(
            "UPDATE jobs SET provider='webmaster',engine='google',request_key='foreign-provider'"
        )
        archive._queue(
            "sc-domain:example.test", "availability", "web", "2026-01-01", "2026-01-01", ["date"]
        )
        archive.db.execute(
            "UPDATE jobs SET engine='yandex',request_key='foreign-engine' WHERE provider='gsc'"
        )
        archive._queue(
            "sc-domain:example.test", "availability", "web", "2026-01-01", "2026-01-01", ["date"]
        )
        archive.db.execute(
            "UPDATE jobs SET provider='webmaster',engine='yandex',request_key='webmaster-yandex' "
            "WHERE provider='gsc' AND engine='google'"
        )
        for job in archive.db.execute("SELECT id FROM jobs").fetchall():
            archive.db.execute(
                "INSERT INTO facts VALUES(?, '2026-01-01', 0,0,'','','',8,9,NULL,NULL)", (job[0],)
            )
        archive.db.execute(
            "INSERT INTO archive_meta VALUES('backoff:webmaster:yandex',?)",
            (json.dumps({"retry_at": 999999}),),
        )
    archive.prepare("sc-domain:example.test", "2026-01-01", "2026-01-01")
    status = archive.run_batch(
        6,
        pause=0,
        fetcher=lambda *a, **kw: result([{"keys": ["2026-01-01"], "clicks": 1, "impressions": 2}]),
    )
    assert status["requests_this_batch"] == 6
    other = archive.db.execute(
        "SELECT state,next_row FROM jobs WHERE provider!='gsc' OR engine!='google'"
    ).fetchall()
    assert [tuple(row) for row in other] == [("pending", 0)] * 3
    assert (
        archive.db.execute(
            "SELECT count(*) FROM observations WHERE dataset='availability' AND search_type='web'"
        ).fetchone()[0]
        == 4
    )
    archive.close()


def test_pagination_resume_duplicates_and_cap(tmp_path):
    path = tmp_path / "archive.sqlite"
    archive = Archive(path)
    with archive.db:
        archive._queue(
            "sc-domain:example.test", "pages", "web", "2026-01-01", "2026-01-01", ["page"]
        )
    offsets = []

    def page(*args, **kwargs):
        offsets.append(kwargs["start_row"])
        # One duplicate crossing the page boundary must not distort the provider offset.
        begin = max(kwargs["start_row"] - 1, 0)
        return result(
            [
                {"keys": [f"https://example.test/{i}"], "clicks": 1, "impressions": 2}
                for i in range(begin, begin + 25000)
            ]
        )

    archive.run_batch(1, pause=0, fetcher=page)
    archive.close()
    archive = Archive(path)
    status = archive.run_batch(10, pause=0, fetcher=page)
    assert offsets == [0, 25000]
    assert status["rows"] == 49999 and status["duplicate_rows_observed"] == 1
    assert status["capped_jobs"] == 1 and status["states"] == {"capped": 1}
    assert archive.db.execute("SELECT next_row,received_rows,pages FROM jobs").fetchone()[:] == (
        50000,
        50000,
        2,
    )
    archive.close()


def test_version_one_migration_preserves_facts_and_recovers_provenance(tmp_path, clock):
    path = tmp_path / "archive.sqlite"
    archive = Archive(path)
    filters = [
        {
            "groupType": "and",
            "filters": [
                {
                    "dimension": "searchAppearance",
                    "operator": "equals",
                    "expression": "RICH_RESULTS",
                }
            ],
        }
    ]
    with archive.db:
        for begin, end in [("2026-01-01", "2026-01-02"), ("2026-01-02", "2026-01-03")]:
            archive._queue("sc-domain:example.test", "availability", "web", begin, end, ["date"])
        archive._queue(
            "sc-domain:example.test",
            "appearance_detail",
            "web",
            "2026-01-02",
            "2026-01-02",
            ["page"],
            filters=filters,
        )
        for job in archive.db.execute("SELECT id FROM jobs").fetchall():
            archive.db.execute(
                "INSERT INTO facts VALUES(?, '2026-01-02', 0,0,'','','',8,9,NULL,NULL)", (job[0],)
            )
        archive.db.execute("UPDATE jobs SET state='complete'")
        archive.db.execute(
            "UPDATE jobs SET state='quota_wait',retry_at=2000,error='quotaExceeded' WHERE dataset='appearance_detail'"
        )
        archive.db.execute("DROP VIEW observations")
        archive.db.execute("CREATE VIEW observations AS SELECT * FROM facts")
        archive.db.execute("UPDATE archive_meta SET value='1' WHERE key='schema_version'")
    archive.close()
    archive = Archive(path)
    assert archive.db.execute("SELECT count(*) FROM facts").fetchone()[0] == 3
    assert (
        archive.db.execute(
            "SELECT count(*) FROM observations WHERE dataset='availability'"
        ).fetchone()[0]
        == 1
    )
    assert (
        archive.db.execute(
            "SELECT appearance FROM observations WHERE dataset='appearance_detail'"
        ).fetchone()[0]
        == "RICH_RESULTS"
    )
    assert archive.db.execute(
        "SELECT value FROM archive_meta WHERE key='schema_version'"
    ).fetchone()[0] == str(gsc_archive.SCHEMA_VERSION)
    assert (
        archive.run_batch(1, pause=0, fetcher=lambda *a, **kw: result([]))["requests_this_batch"]
        == 0
    )
    archive.close()
