"""Synthetic authorized retained-report delivery; no messaging transport is used."""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from seohead.bot import (
    AuthorizedReportDelivery,
    DeliveryReceipts,
    DeliveryUnavailable,
    JobOwnershipStore,
    ProjectAuthorizationStore,
    ReportProfile,
)
from seohead.recon import net
from seohead.remote_api.backend import RemoteProjectLimits, SQLiteJobBackend
from seohead.remote_api.contracts import ScanSubmission


class _Body(httpx.SyncByteStream):
    def __iter__(self):
        yield b"<html><head><title>Synthetic</title></head><body>report evidence</body></html>"


def _complete_job(monkeypatch, tmp_path):
    def resolve(host, port, *, type):
        assert host == "public.example.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    def fetch(_self, request):
        return httpx.Response(
            200,
            stream=_Body(),
            headers={"content-type": "text/html"},
            request=request,
        )

    monkeypatch.setattr(net.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fetch)
    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    request = ScanSubmission(
        target_url="https://public.example.test/", options={"max_urls": 1, "max_requests": 20}
    )
    job = backend.submit(
        "alpha",
        "synthetic-actor",
        "synthetic-delivery",
        request.fingerprint(),
        request,
        request.options.effective_config(),
    ).job
    assert backend.run_one("worker-a").state == "finished"
    return backend, job.job_id


def test_delivery_retries_then_persists_receipt_across_adapter_restart(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    sent = []
    attempts = [0]

    def flaky(destination, opened, receipt):
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("synthetic transport interrupted")
        sent.append((destination, receipt, opened.handle.read(16)))

    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(backend, {"alpha"}, {"requester"}, receipts, flaky)
    with pytest.raises(RuntimeError, match="interrupted"):
        delivery.deliver("alpha", job_id, "requester", ReportProfile("json"))
    restarted = AuthorizedReportDelivery(backend, {"alpha"}, {"requester"}, receipts, flaky)
    receipt = restarted.deliver("alpha", job_id, "requester", ReportProfile("json"))
    assert restarted.deliver("alpha", job_id, "requester", ReportProfile("json")) == receipt
    assert len(sent) == 1 and sent[0][:2] == ("requester", receipt)


def test_receipt_claim_prevents_duplicate_send_until_a_failed_attempt_is_released(tmp_path):
    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    first, state = receipts.reserve("job", "artifact", "requester")
    second, concurrent = receipts.reserve("job", "artifact", "requester")
    assert state == "claimed" and (second, concurrent) == (first, "in_progress")
    receipts.retry("job", "artifact", "requester", first)
    assert receipts.reserve("job", "artifact", "requester") == (first, "claimed")


def test_delivery_refuses_foreign_destination_partial_job_and_unknown_profile(
    monkeypatch, tmp_path
):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda *_: None,
    )
    with pytest.raises(PermissionError, match="destination"):
        delivery.deliver("alpha", job_id, "other", ReportProfile("json"))
    with pytest.raises(DeliveryUnavailable, match="unavailable"):
        ReportProfile("unknown")
    queued = ScanSubmission(target_url="https://public.example.test/", options={"max_urls": 1})
    pending = backend.submit(
        "alpha",
        "synthetic-actor",
        "synthetic-pending",
        queued.fingerprint(),
        queued,
        queued.options.effective_config(),
    ).job
    with pytest.raises(DeliveryUnavailable, match="complete"):
        delivery.preview("alpha", pending.job_id, ReportProfile("json"))


def test_findings_only_json_is_rendered_from_retained_evidence(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    sent = []
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda destination, opened, receipt: sent.append(
            (destination, receipt, json.loads(opened.handle.read()))
        ),
    )
    profile = ReportProfile("json", findings_only=True, severities=("critical",))
    receipt = delivery.deliver("alpha", job_id, "requester", profile)
    assert sent[0][:2] == ("requester", receipt)
    document = sent[0][2]
    rows = document.get("findings", document.get("issues"))
    assert isinstance(rows, list)
    assert all(row.get("severity") == "critical" for row in rows)
    assert document["summary"]["finding_view"]["coverage_note"]


def test_profile_receipts_are_distinct_and_size_limit_is_honest(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(
        backend, {"alpha"}, {"requester"}, receipts, lambda *_: None, max_file_bytes=1
    )
    with pytest.raises(DeliveryUnavailable, match="size limit"):
        delivery.deliver("alpha", job_id, "requester", ReportProfile("json", findings_only=True))
    full = delivery.preview("alpha", job_id, ReportProfile("json"))
    filtered = delivery.preview("alpha", job_id, ReportProfile("json", findings_only=True))
    assert full.artifact_id != filtered.artifact_id


def test_unavailable_pdf_renderer_never_marks_a_delivery_success(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(
        backend, {"alpha"}, {"requester"}, receipts, lambda *_: None
    )
    profile = ReportProfile("pdf")
    monkeypatch.setattr(
        "seohead.reports.build_report",
        lambda *_args, **_kwargs: {"ok": False, "error": "synthetic PDF renderer unavailable"},
    )
    with pytest.raises(DeliveryUnavailable, match="renderer unavailable"):
        delivery.deliver("alpha", job_id, "requester", profile)
    artifact = delivery.preview("alpha", job_id, profile)
    _receipt, state = receipts.reserve(job_id, artifact.artifact_id, "requester")
    assert state == "claimed"


def test_xlsx_profile_is_built_offline_from_the_retained_audit(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    delivered = []
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda _destination, opened, _receipt: delivered.append(
            (opened.filename, opened.media_type, opened.handle.read(2))
        ),
    )
    delivery.deliver("alpha", job_id, "requester", ReportProfile("xlsx", findings_only=True))
    assert delivered == [
        (
            "report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK",
        )
    ]


def test_durable_delivery_ownership_rejects_a_foreign_subject(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    ownership = JobOwnershipStore(tmp_path / "ownership.sqlite")
    ownership.record(job_id, "requester-a", "alpha")
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda *_: None,
        subject="requester-b",
        ownership=ownership,
    )
    with pytest.raises(PermissionError, match="job is not authorized"):
        delivery.preview("alpha", job_id, ReportProfile("json"))


def test_revoked_subject_cannot_deliver_a_previously_owned_job(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    ownership = JobOwnershipStore(tmp_path / "ownership.sqlite")
    ownership.record(job_id, "requester", "alpha")
    authorization = ProjectAuthorizationStore(tmp_path / "grants.sqlite")
    authorization.grant("requester", "alpha")
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda *_: None,
        subject="requester",
        ownership=ownership,
        authorization=authorization,
    )
    assert delivery.preview("alpha", job_id, ReportProfile("json")).kind == "audit_json"
    authorization.revoke("requester", "alpha")
    with pytest.raises(PermissionError, match="project is not authorized"):
        delivery.preview("alpha", job_id, ReportProfile("json"))
