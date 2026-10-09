"""Synthetic authorized retained-report delivery; no messaging transport is used."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest

import seohead.integrations.bot.report_delivery as report_delivery_module
from seohead.integrations.bot import (
    AuthorizedReportDelivery,
    DeliveryAmbiguous,
    DeliveryReceipts,
    DeliveryUnavailable,
    JobOwnershipStore,
    ProjectAuthorizationStore,
    ReportProfile,
)
from seohead.integrations.bot.telegram_adapter import (
    TelegramBotClient,
    TelegramBotConfig,
    TelegramChatAuthorizationStore,
    TelegramDocumentTransport,
)
from seohead.integrations.remote_api.backend import RemoteProjectLimits, SQLiteJobBackend
from seohead.integrations.remote_api.contracts import ScanSubmission
from seohead.recon import net


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
    with pytest.raises(DeliveryUnavailable, match="size limit"):
        delivery.preview("alpha", job_id, ReportProfile("json"))
    delivery.max_file_bytes = 50 * 1024 * 1024
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
    with pytest.raises(DeliveryUnavailable, match="renderer unavailable"):
        delivery.preview("alpha", job_id, profile)
    result = backend.get_result("alpha", job_id)
    source = delivery._source_artifact(result, "audit_json")
    artifact = delivery._delivery_artifact(source, profile)
    _receipt, state = receipts.reserve(job_id, artifact.artifact_id, "requester")
    assert state == "claimed"


def test_partial_retained_result_never_enters_delivery(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    result = backend.get_result("alpha", job_id)
    artifact = next(item for item in result.artifacts if item.kind == "audit_md")
    backend.artifact_path("alpha", job_id, artifact.artifact_id).unlink()
    assert backend.get_result("alpha", job_id).coverage == "partial"
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda *_: pytest.fail("partial work must not reach a transport"),
    )
    with pytest.raises(DeliveryUnavailable, match="complete retained audit"):
        delivery.preview("alpha", job_id, ReportProfile("json"))


def test_ambiguous_telegram_upload_stays_pending_after_restart(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "synthetic-token")
    client = TelegramBotClient(
        TelegramBotConfig("env:SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "https://telegram.example.test"),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout("synthetic timeout"))
            )
        ),
    )
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    chats.grant("telegram:42", "42")
    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"telegram:42"},
        receipts,
        TelegramDocumentTransport(client, chats, "telegram:42").send,
    )
    with pytest.raises(DeliveryAmbiguous, match="outcome is unknown"):
        delivery.deliver("alpha", job_id, "telegram:42", ReportProfile("json"))
    restarted = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"telegram:42"},
        receipts,
        TelegramDocumentTransport(client, chats, "telegram:42").send,
    )
    with pytest.raises(DeliveryUnavailable, match="already in progress"):
        restarted.deliver("alpha", job_id, "telegram:42", ReportProfile("json"))


def test_explicit_telegram_denial_releases_a_receipt_for_manual_retry(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "synthetic-token")
    client = TelegramBotClient(
        TelegramBotConfig("env:SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "https://telegram.example.test"),
        httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(400, json={"ok": False}))
        ),
    )
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    chats.grant("telegram:42", "42")
    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"telegram:42"},
        receipts,
        TelegramDocumentTransport(client, chats, "telegram:42").send,
    )
    with pytest.raises(DeliveryUnavailable, match="rejected"):
        delivery.deliver("alpha", job_id, "telegram:42", ReportProfile("json"))
    artifact = delivery.preview("alpha", job_id, ReportProfile("json"))
    _receipt, state = receipts.reserve(job_id, artifact.artifact_id, "telegram:42")
    assert state == "claimed"


def test_revoked_telegram_chat_cannot_receive_retained_report(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    requests = []
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "synthetic-token")
    client = TelegramBotClient(
        TelegramBotConfig("env:SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "https://telegram.example.test"),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request)
                    or httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
                )
            )
        ),
    )
    subject = "telegram:42"
    ownership = JobOwnershipStore(tmp_path / "ownership.sqlite")
    ownership.record(job_id, subject, "alpha")
    projects = ProjectAuthorizationStore(tmp_path / "grants.sqlite")
    projects.grant(subject, "alpha")
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    chats.grant(subject, "42")
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"telegram:42"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        TelegramDocumentTransport(client, chats, subject).send,
        subject=subject,
        ownership=ownership,
        authorization=projects,
    )
    chats.revoke(subject, "42")
    with pytest.raises(DeliveryUnavailable, match="chat is not authorized"):
        delivery.deliver("alpha", job_id, "telegram:42", ReportProfile("json"))
    assert requests == []


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
    original_directory = report_delivery_module.tempfile.TemporaryDirectory
    created = []

    def tracked_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        directory = original_directory(*args, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(report_delivery_module.tempfile, "TemporaryDirectory", tracked_directory)
    delivery.deliver("alpha", job_id, "requester", ReportProfile("xlsx", findings_only=True))
    assert delivered == [
        (
            "report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK",
        )
    ]
    assert created and all(not path.exists() for path in created)


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


def test_project_authorized_delivery_requires_durable_owner(monkeypatch, tmp_path):
    backend, _job_id = _complete_job(monkeypatch, tmp_path)
    authorization = ProjectAuthorizationStore(tmp_path / "grants.sqlite")
    with pytest.raises(ValueError, match=r"durable.*ownership"):
        AuthorizedReportDelivery(
            backend,
            {"alpha"},
            {"requester"},
            DeliveryReceipts(tmp_path / "receipts.sqlite"),
            lambda *_: None,
            subject="requester",
            authorization=authorization,
        )


def test_preview_measures_exact_empty_population_and_preserves_source(monkeypatch, tmp_path):
    import hashlib

    backend, job_id = _complete_job(monkeypatch, tmp_path)
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"requester"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        lambda *_: None,
    )
    source = next(
        a for a in backend.get_result("alpha", job_id).artifacts if a.kind == "audit_json"
    )
    path = backend.artifact_path("alpha", job_id, source.artifact_id)
    before = path.read_bytes()
    profile = ReportProfile("json", checks=("no-such-synthetic-check",))
    preview = delivery.preview("alpha", job_id, profile)
    assert preview.source_sha256 == hashlib.sha256(before).hexdigest()
    assert preview.selected_rows == 0 and preview.source_rows > 0
    assert preview.population_state == "empty" and preview.coverage == "complete"
    captured = []
    delivery.send = lambda _to, opened, _receipt: captured.append(opened.handle.read())
    delivery.deliver("alpha", job_id, "requester", profile)
    assert len(captured[0]) == preview.size_bytes
    assert json.loads(captured[0])["summary"]["finding_view"]["state"] == "empty"
    assert path.read_bytes() == before


def test_process_exit_during_upload_keeps_uncertain_claim(monkeypatch, tmp_path):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    calls = []

    def exit_during_send(*_):
        calls.append(True)
        raise SystemExit("synthetic process death")

    receipts = DeliveryReceipts(tmp_path / "receipts.sqlite")
    delivery = AuthorizedReportDelivery(
        backend, {"alpha"}, {"requester"}, receipts, exit_during_send
    )
    with pytest.raises(SystemExit):
        delivery.deliver("alpha", job_id, "requester", ReportProfile("json"))
    restarted = AuthorizedReportDelivery(
        backend, {"alpha"}, {"requester"}, DeliveryReceipts(receipts.path), exit_during_send
    )
    with pytest.raises(DeliveryUnavailable, match="already in progress"):
        restarted.deliver("alpha", job_id, "requester", ReportProfile("json"))
    assert len(calls) == 1


@pytest.mark.parametrize("body", [{"ok": True, "result": True}, {"ok": True}, []])
def test_unconfirmed_document_response_is_never_replayed(monkeypatch, tmp_path, body):
    backend, job_id = _complete_job(monkeypatch, tmp_path)
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "synthetic-token")
    requests = []
    client = TelegramBotClient(
        TelegramBotConfig("env:SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "https://telegram.example.test"),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200, json=body)
            )
        ),
    )
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    chats.grant("telegram:7", "7")
    delivery = AuthorizedReportDelivery(
        backend,
        {"alpha"},
        {"telegram:7"},
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        TelegramDocumentTransport(client, chats, "telegram:7").send,
    )
    with pytest.raises(DeliveryAmbiguous):
        delivery.deliver("alpha", job_id, "telegram:7", ReportProfile("json"))
    with pytest.raises(DeliveryUnavailable, match="already in progress"):
        delivery.deliver("alpha", job_id, "telegram:7", ReportProfile("json"))
    assert len(requests) == 1
