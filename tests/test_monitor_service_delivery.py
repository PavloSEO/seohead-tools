"""Synthetic tests for explicit, injected monitor notice delivery."""

import pytest

from seohead.bot.report_delivery import DeliveryReceipts, DeliveryUnavailable
from seohead.projects.monitoring import configure, deliver, run, schedule
from seohead.projects.service_delivery import MonitorServiceDelivery
from seohead.projects.workspace import create_project


def _project(tmp_path, *, urls=None):
    project = tmp_path / "project"
    create_project(project, "https://example.test/")
    urls = urls or ["https://example.test/a"]
    configured = configure(
        project,
        {
            "enabled": False,
            "urls": urls,
            "max_urls": len(urls),
            "max_requests": len(urls),
            "full_refresh_every": 7,
        },
    )
    return project, configured


def _service(tmp_path, sent, *, enabled=True, recoveries=True):
    return MonitorServiceDelivery(
        project_uuid="project:synthetic",
        allowed_destinations={"service:test"},
        receipts=DeliveryReceipts(tmp_path / "receipts.sqlite"),
        send=lambda destination, payload, receipt: sent.append((destination, payload, receipt)),
        enabled=enabled,
        include_recoveries=recoveries,
    )


def test_delivery_is_explicit_disabled_by_default_and_deduplicated_after_restart(tmp_path):
    project, configured = _project(tmp_path)
    retained = run(
        project,
        "scan:alert",
        [
            {
                "url": "https://example.test/a",
                "changes": [{"kind": "status_changed", "severity": "warning"}],
            }
        ],
        configured["revision"],
    )
    sent = []
    disabled = _service(tmp_path, sent, enabled=False)
    with pytest.raises(DeliveryUnavailable, match="disabled"):
        deliver(
            project,
            scan_id="scan:alert",
            destination="service:test",
            service=disabled,
            expected_revision=retained["revision"],
        )

    first = deliver(
        project,
        scan_id="scan:alert",
        destination="service:test",
        service=_service(tmp_path, sent),
        expected_revision=retained["revision"],
    )
    restarted = deliver(
        project,
        scan_id="scan:alert",
        destination="service:test",
        service=_service(tmp_path, sent),
        expected_revision=first["revision"],
    )
    assert len(sent) == 1
    assert first["delivery"] == "sent"
    assert restarted["receipts"][0]["state"] == "delivered"


def test_partial_cancelled_and_quiet_runs_do_not_send(tmp_path):
    project, configured = _project(
        tmp_path, urls=["https://example.test/a", "https://example.test/b"]
    )
    partial = run(
        project,
        "scan:partial",
        [
            {"url": "https://example.test/a", "changes": []},
            {"url": "https://example.test/b", "changes": [], "qualifier": "unavailable"},
        ],
        configured["revision"],
    )
    sent = []
    service = _service(tmp_path, sent)
    with pytest.raises(DeliveryUnavailable, match="partial"):
        deliver(
            project,
            scan_id="scan:partial",
            destination="service:test",
            service=service,
            expected_revision=partial["revision"],
        )

    cancelled = schedule(project, action="cancel", expected_revision=partial["revision"])
    with pytest.raises(ValueError, match="cancelled"):
        deliver(
            project,
            scan_id="scan:partial",
            destination="service:test",
            service=service,
            expected_revision=cancelled["revision"],
        )
    assert sent == []


def test_recovery_notices_use_the_same_authorized_receipt_path(tmp_path):
    project, configured = _project(tmp_path)
    retained = run(
        project,
        "scan:recovery",
        [
            {
                "url": "https://example.test/a",
                "changes": [{"kind": "recovered", "severity": "warning"}],
            }
        ],
        configured["revision"],
    )
    sent = []
    result = deliver(
        project,
        scan_id="scan:recovery",
        destination="service:test",
        service=_service(tmp_path, sent),
        expected_revision=retained["revision"],
    )
    assert result["receipts"][0]["kind"] == "recovery"
    assert sent[0][1]["event"]["kind"] == "recovery"
