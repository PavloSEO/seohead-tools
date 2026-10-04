from seohead.projects.monitoring import configure, run, status
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
