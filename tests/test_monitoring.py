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
