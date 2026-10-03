"""Remediation ledger schema, identity and ingest tests (issue #788)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from seohead import __version__
from seohead.crawl.collect import PageRecord
from seohead.crawl.settings import manifest
from seohead.projects.workspace import create_project
from seohead.storage import ScanError, import_run, open_scan
from seohead.storage.ledger import (
    APPLICATION_ID,
    LedgerError,
    canonical_url,
    create_ledger,
    finding_key,
    ingest_scan,
    ledger_summary,
    note_source_missing,
    occurrence_key,
    open_ledger,
    read_cases,
)
from seohead.storage.native_scan import NativeScan
from tests.test_scan_native import _metadata, _runtime

BUILD = "25fd2ed032a31d63c5811722619e35c14b631476"
SITE = "https://example.test/"
HOME = "https://example.test"
A = "https://example.test/a"
B = "https://example.test/b"
DEAD1 = "https://example.test/dead-1"
DEAD2 = "https://example.test/dead-2"
PAGES = [HOME + "/", A, B]


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _user_version(path: Path) -> int:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _project(tmp_path: Path) -> Path:
    directory = tmp_path / "project"
    create_project(directory, SITE)
    return directory


def _ledger(tmp_path: Path, *, project_dir: Path | None = None) -> Path:
    return create_ledger(
        tmp_path / "ledger.sqlite",
        project_dir=project_dir or _project(tmp_path),
        producer_build=BUILD,
    )


def _issue(ordinal, check, *, target=None, count=1, locations=None, **fields):
    issue = {
        "id": ordinal,
        "check": check,
        "severity": fields.pop("severity", "warning"),
        "source": "native",
        "message": fields.pop("message", "synthetic finding"),
        "occurrences_count": count,
    }
    if target is not None:
        issue["target_url"] = target
    if locations is not None:
        issue["locations"] = locations
    issue.update(fields)
    return issue


def _document(scan, meta, *, issues, page_urls, groups=None, generated_at, partial):
    return {
        "schema_version": "2.0",
        "tool": {"name": "seohead", "version": meta["writer_version"]},
        "run": {
            "input_mode": "crawl",
            "source": meta["start_url"],
            "generated_at": generated_at,
            "crawl_config": manifest(meta["config"]),
            "crawl_partial": partial,
            "scan_uuid": scan.con.execute("SELECT scan_uuid FROM scan").fetchone()[0],
        },
        "summary": {
            "totals": {},
            "by_severity": {"critical": 0, "warning": 0, "notice": 0},
            "by_check": {},
            "health_score": 100,
        },
        "issues": issues,
        "pages": [
            {"url": url, "status_code": 200, "metrics": {"representation": "static"}}
            for url in page_urls
        ],
        "groups": groups or [],
    }


def _scan(
    path: Path,
    *,
    issues,
    page_urls=PAGES,
    groups=None,
    generated_at="2026-10-01T00:00:00Z",
    config_overrides=None,
    start_url=SITE,
    partial=False,
    v2=False,
) -> Path:
    overrides = (
        {
            "storage.format_version": "scan.v2",
            "resources.fetch": True,
            **(config_overrides or {}),
        }
        if v2
        else dict(config_overrides or {})
    )
    meta = _metadata(**overrides)
    meta["start_url"] = start_url
    kwargs = {"format_version": "scan.v2"} if v2 else {}
    with NativeScan.create(path, **kwargs, **meta) as scan:
        scan.enqueue([(url, 0) for url in page_urls])
        for _ in page_urls:
            lease = scan.claim(1)[0]
            scan.commit_page(
                lease,
                vars(
                    PageRecord(
                        url=lease.url,
                        content_type="text/html",
                        title="synthetic",
                        crawl_depth=0,
                    )
                ),
                runtime=_runtime(),
            )
        if partial:
            scan.enqueue([("https://example.test/never-crawled", 1)])
            scan.interrupt("test budget")
        scan.save_audit(
            _document(
                scan,
                meta,
                issues=issues,
                page_urls=page_urls,
                groups=groups,
                generated_at=generated_at,
                partial=partial,
            )
        )
        if not partial:
            scan.finish_capture("test")
    return path


def _legacy_page(url: str) -> dict:
    """One complete pages.jsonl record for a legacy_directory.v1 import."""
    return {
        "url": url,
        "status_code": 200,
        "content_type": "text/html",
        "size_bytes": 1024,
        "response_time": 0.1,
        "redirect_url": "",
        "title": "synthetic",
        "meta_description": "",
        "h1": "",
        "h1_2": "",
        "h2": "",
        "canonical": "",
        "meta_robots": "",
        "x_robots": "",
        "og_title": "",
        "og_description": "",
        "og_image": "",
        "word_count": 0,
        "text_ratio": 0.0,
        "crawl_depth": 0,
        "content_encoding": "",
        "charset": "",
        "doctype": "",
        "viewport": "",
        "title_outside_head": False,
        "meta_description_outside_head": False,
        "canonical_outside_head": False,
        "directives_outside_head": False,
        "hreflang_outside_head": False,
        "head_count": 1,
        "body_count": 1,
        "head_not_first": False,
        "invalid_head_elements": "",
        "outlinks": 0,
        "external_outlinks": 0,
        "jsonld_blocks_found": 0,
        "jsonld_blocks_parsed": 0,
        "error": "",
        "error_kind": "",
        "cache_status": "",
        "representation": "legacy_fragment",
        "final_url": url,
        "redirect_chain": [],
    }


def _legacy_scan(path: Path, *, issues, page_urls, groups=None, partial=False) -> Path:
    """Build a legacy_import artifact (no retained bodies/responses) via import_run."""
    source = path.with_suffix("")
    source.mkdir()
    document = {
        "schema_version": "2.0",
        "tool": {"name": "seohead", "version": __version__},
        "run": {
            "input_mode": "crawl",
            "source": SITE,
            "generated_at": "2026-09-01T00:00:00Z",
            "crawl_config": {"speed": {"min_delay_seconds": 0}},
            "crawl_partial": partial,
        },
        "summary": {
            "totals": {},
            "by_severity": {"critical": 0, "warning": 0, "notice": 0},
            "by_check": {},
            "health_score": 100,
        },
        "issues": issues,
        "pages": [{"url": url, "status_code": 200} for url in page_urls],
        "groups": groups or [],
    }
    (source / "audit.json").write_text(json.dumps(document, indent=2))
    (source / "pages.jsonl").write_text(
        "".join(json.dumps(_legacy_page(url)) + "\n" for url in page_urls)
    )
    (source / "links.jsonl").write_text("")
    return import_run(source, path, producer_build=BUILD)


def _counts(ledger: Path) -> dict:
    return ledger_summary(ledger)["counts"]


def _history_text(ledger: Path) -> str:
    return json.dumps(read_cases(ledger), sort_keys=True)


def _occurrences(cases: dict) -> list[dict]:
    return [occurrence for finding in cases["findings"] for occurrence in finding["occurrences"]]


@pytest.mark.parametrize("v2", [False, True], ids=["scan.v1", "scan.v2"])
def test_repeating_one_scan_is_idempotent_and_never_touches_its_bytes(tmp_path, v2):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        v2=v2,
        issues=[
            _issue("ISSUE-000001", "TITLE_MISSING", target=A, status_code=200),
            _issue("ISSUE-000002", "META_DESCRIPTION_MISSING", target=B),
        ],
    )
    digest_before, version_before = _file_sha(scan), _user_version(scan)

    first = ingest_scan(ledger, scan)
    history_before, summary_before = _history_text(ledger), _counts(ledger)
    second = ingest_scan(ledger, scan)

    assert first["new_source"] and first["ledger_revision"] >= 1
    assert second["already_recorded"] is True
    assert second["new_source"] is False
    assert second["ledger_revision"] == first["ledger_revision"]
    assert _history_text(ledger) == history_before
    assert _counts(ledger) == summary_before
    assert summary_before["finding"] == 2
    assert summary_before["occurrence"] == 2
    assert summary_before["distinct_affected_urls"] == 2
    assert summary_before["observation"] == 2
    assert _file_sha(scan) == digest_before
    assert _user_version(scan) == version_before
    opened = open_scan(scan)
    opened.close()


def test_distinct_checks_urls_subjects_and_representations(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[
            _issue("ISSUE-000001", "CHECK_ONE", target=A),
            _issue("ISSUE-000002", "CHECK_ONE", target=B),
            _issue("ISSUE-000003", "CHECK_TWO", target=A),
            _issue("ISSUE-000004", "BROKEN_LINK", target=DEAD1, locations=[{"source_url": A}]),
            _issue("ISSUE-000005", "BROKEN_LINK", target=DEAD2, locations=[{"source_url": A}]),
            _issue("ISSUE-000006", "TITLE_MISSING", target=A, representation="static"),
            _issue("ISSUE-000007", "TITLE_MISSING", target=A, representation="rendered"),
        ],
    )
    ingest_scan(ledger, scan)
    counts = _counts(ledger)
    assert counts["finding"] == 6
    assert counts["occurrence"] == 9
    assert counts["affected_url"] == 8
    assert counts["distinct_affected_urls"] == 4
    keys = {occurrence["occurrence_key"] for occurrence in _occurrences(read_cases(ledger))}
    assert len(keys) == 9
    page_occurrences = [
        occurrence
        for occurrence in _occurrences(read_cases(ledger, url=A))
        if occurrence["subject_value"] == A
    ]
    # Six distinct cases share /a: two findings' primaries, two broken-link
    # sources under different subjects, and a static/rendered pair.
    assert len(page_occurrences) == 6


def test_changed_observation_appends_history_without_rekeying(tmp_path):
    ledger = _ledger(tmp_path)
    first_scan = _scan(
        tmp_path / "first.sqlite",
        issues=[
            _issue(
                "ISSUE-000001",
                "CHECK_ONE",
                target=A,
                status_code=200,
                message="first wording",
            )
        ],
    )
    later_scan = _scan(
        tmp_path / "later.sqlite",
        issues=[
            _issue(
                "ISSUE-000009",
                "CHECK_ONE",
                target=A,
                severity="critical",
                status_code=404,
                message="second wording",
            )
        ],
        generated_at="2026-10-02T00:00:00Z",
        config_overrides={"speed.min_delay_seconds": 0.25},
    )
    ingest_scan(ledger, first_scan)
    ingest_scan(ledger, later_scan)
    cases = read_cases(ledger, check="CHECK_ONE")
    assert len(cases["findings"]) == 1
    occurrence = cases["findings"][0]["occurrences"][0]
    observations = occurrence["observations"]
    assert [item["observation_revision"] for item in observations] == [1, 2]
    assert (
        observations[0]["source_scan"]["scan_uuid"] != observations[1]["source_scan"]["scan_uuid"]
    )
    assert observations[0]["payload_sha256"] != observations[1]["payload_sha256"]
    assert observations[0]["basis_changed"] is None
    assert observations[1]["basis_changed"] == {
        "config_fingerprint": True,
        "producer_version": False,
        "producer_revision": False,
    }
    projections = cases["findings"][0]["projections"]
    assert {row["severity"] for row in projections} == {"warning", "critical"}
    assert {row["status_code"] for row in projections} == {200, 404}
    assert occurrence["current_state"] == "detected"


def test_ordinal_reorder_and_group_change_preserve_identity_and_membership(tmp_path):
    ledger = _ledger(tmp_path)
    groups_one = [
        {"group_id": "GRP-1", "check": "CHECK_ONE", "urls": [A], "count": 1},
        {"group_id": "GRP-2", "check": "CHECK_TWO", "urls": [B], "count": 1},
    ]
    groups_two = [
        {"group_id": "GRP-3", "check": "CHECK_TWO", "urls": [B], "count": 1},
        {"group_id": "GRP-4", "check": "CHECK_ONE", "urls": [A], "count": 1},
    ]
    scan_one = _scan(
        tmp_path / "one.sqlite",
        issues=[
            _issue("ISSUE-000001", "CHECK_ONE", target=A, group_id="GRP-1"),
            _issue("ISSUE-000002", "CHECK_TWO", target=B, group_id="GRP-2"),
        ],
        groups=groups_one,
    )
    scan_two = _scan(
        tmp_path / "two.sqlite",
        issues=[
            _issue("ISSUE-000001", "CHECK_TWO", target=B, group_id="GRP-3"),
            _issue("ISSUE-000002", "CHECK_ONE", target=A, group_id="GRP-4"),
        ],
        groups=groups_two,
        generated_at="2026-10-02T00:00:00Z",
    )
    ingest_scan(ledger, scan_one)
    ingest_scan(ledger, scan_two)
    cases = read_cases(ledger)
    assert len(cases["findings"]) == 2
    for finding in cases["findings"]:
        assert len(finding["occurrences"]) == 1
        assert [
            item["observation_revision"] for item in finding["occurrences"][0]["observations"]
        ] == [1, 2]
        ordinals = {item["issue_ordinal"] for item in finding["occurrences"][0]["observations"]}
        assert ordinals == {"ISSUE-000001", "ISSUE-000002"}
        memberships = {row["group_ref"] for row in finding["group_memberships"]}
        expected = {"GRP-1", "GRP-4"} if finding["check"] == "CHECK_ONE" else {"GRP-2", "GRP-3"}
        assert memberships == expected


def test_capped_locations_aggregate_counts_and_unavailable_evidence_are_data(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[
            _issue(
                "ISSUE-000001",
                "BROKEN_LINK",
                target=DEAD1,
                count=600,
                locations=[{"source_url": A}],
            ),
            _issue("ISSUE-000002", "GRAPH_WIDE_CHECK", count=600),
        ],
    )
    ingest_scan(ledger, scan)
    cases = read_cases(ledger)
    assert len(cases["findings"]) == 2
    link = next(f for f in cases["findings"] if f["check"] == "BROKEN_LINK")
    templated = next(f for f in cases["findings"] if f["check"] == "GRAPH_WIDE_CHECK")
    projection = link["projections"][0]
    assert (projection["occurrences_count"], projection["coverage_state"]) == (600, "capped")
    assert projection["enumerated_count"] == 2
    # Only the two named members exist; the unenumerated remainder is never fabricated.
    assert len(link["occurrences"]) == 2
    assert len(link["affected_urls"]) == 2
    assert {item["discriminator_type"] for item in link["occurrences"]} == {
        "primary",
        "subject",
    }
    dead = next(o for o in link["occurrences"] if o["subject_value"] == DEAD1)
    source = next(o for o in link["occurrences"] if o["subject_value"] == A)
    assert dead["observations"][0]["evidence_state"] == "unavailable"
    assert dead["observations"][0]["evidence_reason"]
    assert source["observations"][0]["evidence_state"] == "measured"
    assert templated["subject_type"] == "scope"
    assert templated["subject_value"] == "site"
    assert templated["affected_urls"] == []
    assert templated["projections"][0]["coverage_state"] == "aggregate_only"
    assert templated["occurrences"][0]["representation"] == "scope"
    assert templated["occurrences"][0]["current_state"] == "detected"


def test_later_scan_omission_never_resolves_baseline_cases(tmp_path):
    ledger = _ledger(tmp_path)
    first = _scan(
        tmp_path / "first.sqlite",
        issues=[
            _issue("ISSUE-000001", "CHECK_ONE", target=A),
            _issue("ISSUE-000002", "GRAPH_WIDE_CHECK", count=5),
        ],
    )
    later = _scan(
        tmp_path / "later.sqlite",
        page_urls=[HOME + "/"],
        issues=[_issue("ISSUE-000001", "GRAPH_WIDE_CHECK", count=5)],
        generated_at="not-a-timestamp",
        partial=True,
    )
    ingest_scan(ledger, first)
    ingest_scan(ledger, later)
    cases = read_cases(ledger)
    assert len(cases["findings"]) == 2
    missing = next(f for f in cases["findings"] if f["check"] == "CHECK_ONE")
    scoped = next(f for f in cases["findings"] if f["check"] == "GRAPH_WIDE_CHECK")
    assert missing["current_state"] == "detected"
    assert len(missing["occurrences"][0]["observations"]) == 1
    scoped_observations = scoped["occurrences"][0]["observations"]
    assert len(scoped_observations) == 2
    later_observation = scoped_observations[1]
    assert later_observation["source_scan"]["crawl_partial"] is True
    assert later_observation["observed_at_state"] == "unknown"
    assert later_observation["observed_at"] is None
    counts = _counts(ledger)
    assert counts["finding"] == 2
    assert counts["occurrence"] == 2


def test_legacy_import_records_imported_projection_and_partial_scope(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _legacy_scan(
        tmp_path / "legacy.sqlite",
        page_urls=[HOME + "/", A],
        issues=[
            _issue(
                "ISSUE-000001",
                "BROKEN_LINK",
                target=DEAD1,
                count=2,
                locations=[{"source_url": A}],
            )
        ],
        partial=True,
    )
    result = ingest_scan(ledger, scan)
    cases = read_cases(ledger)
    finding = cases["findings"][0]
    source = next(o for o in finding["occurrences"] if o["subject_value"] == A)
    assert source["observations"][0]["evidence_state"] == "imported_projection"
    assert source["representation"] == "legacy_fragment"
    dead = next(o for o in finding["occurrences"] if o["subject_value"] == DEAD1)
    assert dead["observations"][0]["evidence_state"] == "unavailable"
    assert dead["observations"][0]["source_scan"]["crawl_partial"] is True
    summary = ledger_summary(ledger)
    assert summary["counts"]["source_scan"] == 1
    assert result["format_version"] == "scan.v1"
    assert result["evidence_revision"] == 1


def test_wrong_site_scan_is_refused_without_touching_the_ledger(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "foreign.sqlite",
        start_url="https://other.test/",
        page_urls=["https://other.test/"],
        issues=[_issue("ISSUE-000001", "CHECK_ONE", target="https://other.test/a")],
    )
    before = _history_text(ledger)
    with pytest.raises(LedgerError, match="site"):
        ingest_scan(ledger, scan)
    assert _history_text(ledger) == before
    assert _counts(ledger)["source_scan"] == 0
    assert ledger_summary(ledger)["ledger_revision"] == 0


def test_conflicting_bytes_under_one_revision_are_refused(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[_issue("ISSUE-000001", "CHECK_ONE", target=A)],
    )
    ingest_scan(ledger, scan)
    forgery = tmp_path / "forgery.sqlite"
    forgery.write_bytes(scan.read_bytes())
    con = sqlite3.connect(forgery)
    document = json.loads(con.execute("SELECT document_json FROM audit").fetchone()[0])
    document["issues"][0]["message"] = "rewritten history"
    raw = json.dumps(document, ensure_ascii=False, indent=2)
    con.execute(
        "UPDATE audit SET document_json=?, sha256=?",
        (raw, hashlib.sha256(raw.encode()).hexdigest()),
    )
    con.commit()
    con.close()
    before = _history_text(ledger)
    with pytest.raises(LedgerError, match="alternate truth"):
        ingest_scan(ledger, forgery)
    assert _history_text(ledger) == before
    opened = open_scan(forgery)
    opened.close()


def test_tampered_audit_digest_fails_source_validation(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[_issue("ISSUE-000001", "CHECK_ONE", target=A)],
    )
    con = sqlite3.connect(scan)
    document = json.loads(con.execute("SELECT document_json FROM audit").fetchone()[0])
    document["issues"][0]["message"] = "rewritten history"
    con.execute("UPDATE audit SET document_json=?", (json.dumps(document, indent=2),))
    con.commit()
    con.close()
    with pytest.raises(ScanError):
        ingest_scan(ledger, scan)
    assert _counts(ledger)["source_scan"] == 0


def test_scan_without_audit_is_refused(tmp_path):
    ledger = _ledger(tmp_path)
    path = tmp_path / "scan.sqlite"
    meta = _metadata()
    with NativeScan.create(path, **meta) as scan:
        scan.finish_without_audit()
    with pytest.raises(ScanError, match="no current audit"):
        ingest_scan(ledger, path)


def test_unknown_and_future_ledger_versions_refuse_without_mutation(tmp_path):
    ledger = _ledger(tmp_path)
    con = sqlite3.connect(ledger)
    con.execute("PRAGMA user_version=2")
    con.commit()
    con.close()
    digest = _file_sha(ledger)
    for write in (False, True):
        with pytest.raises(LedgerError, match="user_version"):
            open_ledger(ledger, write=write)
    assert _file_sha(ledger) == digest
    foreign = tmp_path / "foreign.sqlite"
    foreign.write_bytes(b"not a ledger")
    with pytest.raises(LedgerError):
        open_ledger(foreign)
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(LedgerError):
        open_ledger(missing)
    assert not missing.exists()


def _v0_stub(path: Path, *, project_uuid: str, target: str) -> None:
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA application_id={APPLICATION_ID}")
    con.execute("PRAGMA user_version=0")
    con.execute(
        "CREATE TABLE ledger_meta(singleton INTEGER PRIMARY KEY CHECK (singleton=1),"
        "format_version TEXT NOT NULL,ledger_uuid TEXT NOT NULL,project_uuid TEXT NOT NULL,"
        "site_target TEXT NOT NULL,writer_version TEXT NOT NULL,writer_revision TEXT NOT NULL,"
        "created_at TEXT NOT NULL)"
    )
    con.execute(
        "INSERT INTO ledger_meta VALUES(1,'ledger.v0',?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            project_uuid,
            target,
            __version__,
            BUILD,
            "2026-09-01T00:00:00Z",
        ),
    )
    con.commit()
    con.close()


def test_v0_stub_migrates_on_explicit_write_and_passes_integrity(tmp_path):
    project = _project(tmp_path)
    ledger = tmp_path / "stub.sqlite"
    project_uuid = json.loads((project / "project.json").read_text())["project_uuid"]
    _v0_stub(ledger, project_uuid=project_uuid, target=SITE)
    with pytest.raises(LedgerError, match="user_version"):
        open_ledger(ledger)
    con = open_ledger(ledger, write=True)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchone() is None
    assert con.execute("SELECT COUNT(*) FROM site WHERE role='primary'").fetchone()[0] == 1
    con.close()
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[_issue("ISSUE-000001", "CHECK_ONE", target=A)],
    )
    assert ingest_scan(ledger, scan)["ok"]
    assert _counts(ledger)["finding"] == 1


def test_failed_migration_rolls_back_atomically(tmp_path):
    ledger = tmp_path / "bad-stub.sqlite"
    _v0_stub(ledger, project_uuid="not-a-uuid", target=SITE)
    digest = _file_sha(ledger)
    with pytest.raises(LedgerError):
        open_ledger(ledger, write=True)
    assert _file_sha(ledger) == digest
    assert _user_version(ledger) == 0
    con = sqlite3.connect(ledger)
    row = con.execute("SELECT format_version FROM ledger_meta").fetchone()
    con.close()
    assert row[0] == "ledger.v0"


def test_missing_source_artifact_is_recorded_and_restored(tmp_path):
    ledger = _ledger(tmp_path)
    scan = _scan(
        tmp_path / "scan.sqlite",
        issues=[_issue("ISSUE-000001", "CHECK_ONE", target=A)],
    )
    result = ingest_scan(ledger, scan)
    noted = note_source_missing(ledger, result["source_scan_id"], reason="pruned by retention")
    assert noted["ledger_revision"] == result["ledger_revision"] + 1
    cases = read_cases(ledger)
    observation = cases["findings"][0]["occurrences"][0]["observations"][0]
    assert observation["source_scan"]["artifact_state"] == "missing"
    again = ingest_scan(ledger, scan)
    # Re-ingest is observation-idempotent, but restoring 'present' is a real
    # change to the binding's availability flag, so the revision advances once.
    assert again["recorded"]["observations"] == 0
    assert again["ledger_revision"] == noted["ledger_revision"] + 1
    cases = read_cases(ledger)
    observation = cases["findings"][0]["occurrences"][0]["observations"][0]
    assert observation["source_scan"]["artifact_state"] == "present"


def test_create_ledger_binds_project_and_never_overwrites(tmp_path):
    project = _project(tmp_path)
    ledger = _ledger(tmp_path, project_dir=project)
    summary = ledger_summary(ledger)
    assert summary["format_version"] == "ledger.v1"
    assert summary["ledger_revision"] == 0
    assert summary["sites"][0]["role"] == "primary"
    assert summary["sites"][0]["host"] == "example.test"
    with pytest.raises(LedgerError, match="never overwrite"):
        _ledger(tmp_path, project_dir=project)
    with pytest.raises((LedgerError, ValueError)):
        create_ledger(
            tmp_path / "other.sqlite",
            project_dir=tmp_path / "missing-project",
            producer_build=BUILD,
        )
    with pytest.raises(LedgerError, match="Git SHA"):
        create_ledger(
            tmp_path / "third.sqlite",
            project_dir=project,
            producer_build="not-a-sha",
        )
    assert not (tmp_path / "third.sqlite").exists()


def test_identity_keys_are_stable_and_exclude_observation_fields(tmp_path):
    base = dict(
        project_uuid=str(uuid.uuid4()),
        site_host="example.test",
        check="CHECK_ONE",
        subject_type="url",
        subject_value=canonical_url(A + "/"),
    )
    key = finding_key(**base)
    assert key == finding_key(**base)
    assert key != finding_key(**{**base, "check": "CHECK_TWO"})
    assert key != finding_key(**{**base, "subject_value": canonical_url(B)})
    assert key != finding_key(**{**base, "subject_type": "scope", "subject_value": "site"})
    occurrence = dict(
        base, representation="static", discriminator_type="primary", discriminator_value=""
    )
    assert occurrence_key(**occurrence) == occurrence_key(**occurrence)
    assert occurrence_key(**occurrence) != occurrence_key(
        **{**occurrence, "representation": "rendered"}
    )
    assert occurrence_key(**occurrence) != occurrence_key(
        **{
            **occurrence,
            "discriminator_type": "subject",
            "discriminator_value": DEAD1,
        }
    )
    assert canonical_url(" https://EXAMPLE.test/a/ ") == A
    with pytest.raises(LedgerError):
        canonical_url("   ")
