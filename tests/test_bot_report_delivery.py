"""Synthetic authorized retained-report delivery; no messaging transport is used."""

from __future__ import annotations

import socket

import httpx
import pytest

from seohead.bot import (
    AuthorizedReportDelivery,
    DeliveryReceipts,
    DeliveryUnavailable,
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

    def flaky(destination, opened):
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("synthetic transport interrupted")
        sent.append((destination, opened.handle.read(16)))

    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(backend, {"alpha"}, {"requester"}, receipts, flaky)
    with pytest.raises(RuntimeError, match="interrupted"):
        delivery.deliver("alpha", job_id, "requester", ReportProfile("json"))
    receipt = delivery.deliver("alpha", job_id, "requester", ReportProfile("json"))
    restarted = AuthorizedReportDelivery(backend, {"alpha"}, {"requester"}, receipts, flaky)
    assert restarted.deliver("alpha", job_id, "requester", ReportProfile("json")) == receipt
    assert len(sent) == 1 and sent[0][0] == "requester"


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
        delivery.preview("alpha", job_id, ReportProfile("pdf"))
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
