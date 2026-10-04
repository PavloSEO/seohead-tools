import pytest

from seohead.projects.monitoring import configure, run, schedule, status
from seohead.projects.workspace import create_project


def test_disabled_monitor_keeps_quiet_and_actionable_runs_distinct(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    policy = {
        "enabled": False,
        "urls": ["https://example.test/a"],
        "max_urls": 1,
        "max_requests": 2,
        "full_refresh_every": 7,
    }
    configured = configure(str(project), policy)
    quiet = run(
        str(project),
        "scan:one",
        [{"url": "https://example.test/a", "changes": [{"severity": "notice"}]}],
        configured["revision"],
    )
    assert quiet["run"]["state"] == "quiet" and quiet["notification"] == "none"
    active = run(
        str(project),
        "scan:two",
        [{"url": "https://example.test/a", "changes": [{"severity": "warning"}]}],
        quiet["revision"],
    )
    assert active["run"]["state"] == "actionable"
    assert status(str(project))["last_run"]["scan_id"] == "scan:two"


def test_monitor_claims_are_disabled_by_default_and_recover_without_a_timer(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    policy = {
        "enabled": False,
        "urls": ["https://example.test/a", "https://example.test/b"],
        "max_urls": 1,
        "max_requests": 2,
        "max_render_requests": 1,
        "full_refresh_every": 2,
        "interval_seconds": 60,
        "suppression_runs": 2,
        "severity_threshold": "warning",
    }
    configured = configure(project, policy)
    assert configured["due"]["state"] == "disabled"
    with pytest.raises(ValueError, match="disabled"):
        schedule(project, action="start", expected_revision=configured["revision"])

    policy["enabled"] = True
    configured = configure(project, policy, configured["revision"])
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    assert claimed["scheduled"] is False and claimed["runner"]["state"] == "running"
    with pytest.raises(ValueError, match="overlapping"):
        schedule(project, action="start", expected_revision=claimed["revision"])
    recovered = schedule(project, action="recover", expected_revision=claimed["revision"])
    assert recovered["runner"]["state"] == "interrupted"


def test_full_refresh_keeps_unavailable_evidence_without_inventing_resolutions(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": True,
            "urls": ["https://example.test/a", "https://example.test/b"],
            "max_urls": 1,
            "max_requests": 2,
            "full_refresh_every": 2,
        },
    )
    retained = run(
        project,
        "scan:full",
        [
            {"url": "https://example.test/a", "changes": [], "qualifier": "fresh"},
            {"url": "https://example.test/b", "changes": [], "qualifier": "unavailable"},
        ],
        configured["revision"],
    )
    assert retained["run"]["state"] == "partial"
    assert retained["run"]["recoveries"] == []
    assert retained["run"]["baseline"]["scan_id"] == "scan:full"
    assert retained["due"]["mode"] == "full"


def test_claimed_full_refresh_keeps_its_plan_and_enforces_request_budgets(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": True,
            "urls": ["https://example.test/a", "https://example.test/b"],
            "max_urls": 1,
            "max_requests": 2,
            "max_render_requests": 1,
            "full_refresh_every": 2,
        },
    )
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    retained = run(
        project,
        "scan:full-revalidated",
        [
            {
                "url": "https://example.test/a",
                "changes": [],
                "qualifier": "revalidated",
                "cache_state": "revalidated",
            },
            {
                "url": "https://example.test/b",
                "changes": [],
                "qualifier": "stale",
                "cache_state": "cached",
            },
        ],
        claimed["revision"],
    )
    assert retained["run"]["mode"] == "full"
    assert retained["run"]["observations"][0]["cache_state"] == "revalidated"
    assert status(project)["runner"]["state"] == "idle"


def test_alert_suppression_window_expires_after_the_declared_number_of_runs(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": False,
            "urls": ["https://example.test/a"],
            "max_urls": 1,
            "max_requests": 1,
            "full_refresh_every": 7,
            "suppression_runs": 1,
        },
    )
    change = {"kind": "canonical_changed", "severity": "warning"}
    first = run(
        project,
        "scan:one",
        [{"url": "https://example.test/a", "changes": [change]}],
        configured["revision"],
    )
    suppressed = run(
        project,
        "scan:two",
        [{"url": "https://example.test/a", "changes": [change]}],
        first["revision"],
    )
    quiet = run(
        project,
        "scan:three",
        [{"url": "https://example.test/a", "changes": []}],
        suppressed["revision"],
    )
    repeated = run(
        project,
        "scan:four",
        [{"url": "https://example.test/a", "changes": [change]}],
        quiet["revision"],
    )
    assert first["run"]["alerts"]
    assert suppressed["run"]["alerts"] == []
    assert repeated["run"]["alerts"]


def test_per_url_snapshots_compare_all_seo_monitoring_fields_with_provenance(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": False,
            "urls": ["https://example.test/a"],
            "max_urls": 1,
            "max_requests": 1,
            "full_refresh_every": 7,
        },
    )
    baseline = {
        "status": 200,
        "indexability": "indexable",
        "canonical": "https://example.test/a",
        "robots": "index,follow",
        "metadata": {"title": "Before"},
        "content": "before-sha256",
        "links": {"internal": 2},
    }
    first = run(
        project,
        "scan:baseline",
        [{"url": "https://example.test/a", "changes": [], "measurement": baseline}],
        configured["revision"],
    )
    changed = run(
        project,
        "scan:after",
        [
            {
                "url": "https://example.test/a",
                "changes": [],
                "qualifier": "revalidated",
                "measurement": {
                    "status": 301,
                    "indexability": "noindex",
                    "canonical": "https://example.test/b",
                    "robots": "noindex,nofollow",
                    "metadata": {"title": "After"},
                    "content": "after-sha256",
                    "links": {"internal": 1},
                },
            }
        ],
        first["revision"],
    )
    changes = changed["run"]["observations"][0]["changes"]
    assert {change["field"] for change in changes} == {
        "status",
        "indexability",
        "canonical",
        "robots",
        "metadata",
        "content",
        "links",
    }
    assert {change["baseline_scan_id"] for change in changes} == {"scan:baseline"}
    assert changed["run"]["observations"][0]["measured_at"].endswith("Z")


def test_cancelled_claim_rejects_observations_until_an_explicit_restart(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": True,
            "urls": ["https://example.test/a"],
            "max_urls": 1,
            "max_requests": 1,
            "full_refresh_every": 2,
        },
    )
    claimed = schedule(project, action="start", expected_revision=configured["revision"])
    cancelled = schedule(project, action="cancel", expected_revision=claimed["revision"])
    with pytest.raises(ValueError, match="restart"):
        run(
            project,
            "cancelled-pass",
            [{"url": "https://example.test/a", "changes": []}],
            cancelled["revision"],
        )
    restarted = schedule(project, action="start", expected_revision=cancelled["revision"])
    retained = run(
        project,
        "restarted-pass",
        [{"url": "https://example.test/a", "changes": []}],
        restarted["revision"],
    )
    assert retained["run"]["coverage"]["complete"] is True


def test_incremental_cursor_retries_missing_urls_then_advances_and_records_next_due(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    monkeypatch.setattr("seohead.projects.monitoring._now", lambda: "2026-10-04T00:00:00Z")
    configured = configure(
        project,
        {
            "enabled": True,
            "urls": [
                "https://example.test/a",
                "https://example.test/b",
                "https://example.test/c",
            ],
            "max_urls": 2,
            "max_requests": 3,
            "full_refresh_every": 3,
            "interval_seconds": 60,
        },
    )
    full = run(
        project,
        "full",
        [{"url": url, "changes": []} for url in configured["policy"]["urls"]],
        configured["revision"],
    )
    assert full["due"]["planned_urls"] == ["https://example.test/a", "https://example.test/b"]
    incomplete = run(
        project,
        "incomplete",
        [{"url": "https://example.test/a", "changes": []}],
        full["revision"],
    )
    assert incomplete["run"]["coverage"] == {
        "planned_url_count": 2,
        "observed_url_count": 1,
        "unobserved_url_count": 1,
        "unobserved_urls": ["https://example.test/b"],
        "fresh_count": 1,
        "revalidated_count": 0,
        "stale_count": 0,
        "unavailable_count": 0,
        "partial_count": 0,
        "failed_count": 0,
        "failures": [],
        "complete": False,
    }
    assert incomplete["run"]["state"] == "partial"
    assert incomplete["due"]["planned_urls"] == ["https://example.test/a", "https://example.test/b"]
    complete = run(
        project,
        "incremental",
        [
            {"url": "https://example.test/a", "changes": []},
            {"url": "https://example.test/b", "changes": []},
        ],
        incomplete["revision"],
    )
    assert complete["due"]["planned_urls"] == ["https://example.test/c", "https://example.test/a"]
    assert status(project)["runner"]["next_due_at"] == "2026-10-04T00:01:00Z"


def test_cached_data_cannot_be_declared_fresh_and_failed_coverage_stays_explicit(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": False,
            "urls": ["https://example.test/a"],
            "max_urls": 1,
            "max_requests": 1,
            "full_refresh_every": 2,
        },
    )
    with pytest.raises(ValueError, match="inconsistent"):
        run(
            project,
            "bad-cache",
            [
                {
                    "url": "https://example.test/a",
                    "changes": [],
                    "qualifier": "fresh",
                    "cache_state": "cached",
                }
            ],
            configured["revision"],
        )
    failed = run(
        project,
        "failed",
        [
            {
                "url": "https://example.test/a",
                "changes": [],
                "qualifier": "failed",
                "failure_reason": "connection reset",
            }
        ],
        configured["revision"],
    )
    assert failed["run"]["state"] == "failed"
    assert failed["coverage"]["failures"] == [
        {"url": "https://example.test/a", "reason": "connection reset"}
    ]
    assert failed["run"]["alerts"] == failed["run"]["recoveries"] == []


def test_recovery_is_derived_only_from_a_fresh_measured_return(tmp_path):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    configured = configure(
        project,
        {
            "enabled": False,
            "urls": ["https://example.test/a"],
            "max_urls": 1,
            "max_requests": 1,
            "full_refresh_every": 2,
        },
    )
    baseline = run(
        project,
        "baseline",
        [{"url": "https://example.test/a", "changes": [], "measurement": {"status": 200}}],
        configured["revision"],
    )
    broken = run(
        project,
        "broken",
        [{"url": "https://example.test/a", "changes": [], "measurement": {"status": 500}}],
        baseline["revision"],
    )
    recovered = run(
        project,
        "recovered",
        [{"url": "https://example.test/a", "changes": [], "measurement": {"status": 200}}],
        broken["revision"],
    )
    assert broken["run"]["alerts"][0]["changes"][0]["kind"] == "status_changed"
    assert recovered["run"]["alerts"] == []
    assert recovered["run"]["recoveries"][0]["change"]["kind"] == "recovered"
