"""Offline Bot API wire checks; no listener, polling, or real account is used."""

from __future__ import annotations

import io
from urllib.parse import parse_qs

import httpx
import pytest

from seohead.bot import JobOwnershipStore, ProjectAuthorizationStore
from seohead.bot.contract import State
from seohead.bot.report_delivery import DeliveryReceipts, DeliveryUnavailable
from seohead.bot.telegram_adapter import (
    TelegramAuthorizedSessions,
    TelegramBotClient,
    TelegramBotConfig,
    TelegramChatAuthorizationStore,
    TelegramDocumentTransport,
    TelegramGuidedAdapter,
    TelegramSessionBindingStore,
    TelegramUnavailable,
    telegram_destination,
    telegram_subject,
)
from seohead.bot.wizard import Action, Event, WizardSession
from seohead.job_contracts import OpenedArtifact
from seohead.remote_api.backend import RemoteProjectLimits, SQLiteJobBackend


class _Submitter:
    def submit(self, _spec):
        return "a" * 32

    def cancel(self, _job_id):
        return True


def _client(monkeypatch, requests, *, result=True):
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "synthetic-token")
    return TelegramBotClient(
        TelegramBotConfig("env:SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "https://telegram.example.test"),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request)
                    or httpx.Response(200, json={"ok": True, "result": result})
                )
            )
        ),
    )


def _update(text):
    return {"update_id": 1, "message": {"chat": {"id": -10042}, "from": {"id": 7}, "text": text}}


def _authorized_sessions(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs",
        {"alpha": RemoteProjectLimits(), "beta": RemoteProjectLimits()},
        producer_build="a" * 40,
    )
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    projects = ProjectAuthorizationStore(tmp_path / "projects.sqlite")
    ownership = JobOwnershipStore(tmp_path / "ownership.sqlite")
    bindings = TelegramSessionBindingStore(tmp_path / "sessions.sqlite")
    subject = telegram_subject("7")
    chats.grant(subject, "-10042")
    projects.grant(subject, "alpha")
    return (
        TelegramAuthorizedSessions(backend, chats, projects, ownership, bindings),
        chats,
        projects,
        subject,
    )


def test_authorized_update_renders_keyboard_and_callback_is_acknowledged(monkeypatch, tmp_path):
    requests = []
    client = _client(monkeypatch, requests)
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    subject = telegram_subject("7")
    chats.grant(subject, "-10042")
    sessions = {}

    def session_for(actor, chat):
        return sessions.setdefault((actor, chat), WizardSession(_Submitter(), projects=("alpha",)))

    adapter = TelegramGuidedAdapter(client, chats, session_for)
    reply = adapter.handle_update(_update("https://example.com/"))
    assert reply.state == State.AWAITING_PROJECT
    assert requests[-1].url.path.endswith("/sendMessage")
    rendered = parse_qs(requests[-1].content.decode())["reply_markup"][0]
    assert '"callback_data":"v:alpha"' in rendered
    callback = {
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 7},
            "data": "v:alpha",
            "message": {"chat": {"id": -10042}},
        },
    }
    reply = adapter.handle_update(callback)
    assert reply.state == State.AWAITING_POLICY
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests[-2:]] == [
        "answerCallbackQuery",
        "sendMessage",
    ]


def test_unauthorized_chat_never_reaches_bot_api(monkeypatch, tmp_path):
    requests = []
    adapter = TelegramGuidedAdapter(
        _client(monkeypatch, requests),
        TelegramChatAuthorizationStore(tmp_path / "chats.sqlite"),
        lambda *_: WizardSession(_Submitter()),
    )
    with pytest.raises(PermissionError, match="not authorized"):
        adapter.handle_update(_update("https://example.com/"))
    assert requests == []


def test_foreign_actor_in_an_authorized_chat_never_reaches_bot_api(monkeypatch, tmp_path):
    requests = []
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    chats.grant(telegram_subject("7"), "-10042")
    adapter = TelegramGuidedAdapter(
        _client(monkeypatch, requests), chats, lambda *_: WizardSession(_Submitter())
    )
    forged = _update("https://example.com/")
    forged["message"]["from"]["id"] = 8
    with pytest.raises(PermissionError, match="not authorized"):
        adapter.handle_update(forged)
    assert requests == []


def test_authorized_session_factory_binds_actor_chat_and_current_projects(tmp_path):
    sessions, _chats, projects, subject = _authorized_sessions(tmp_path)
    session = sessions.session_for(subject, "-10042")
    assert session._submitter.subject == subject
    assert session.handle(Event(Action.ANSWER, "https://example.com/")).buttons[0] == "alpha"
    denied = session.handle(Event(Action.ANSWER, "new"))
    assert denied.state == State.AWAITING_PROJECT
    assert "Unknown project" in denied.notice

    projects.grant(subject, "beta")
    reset = sessions.session_for(subject, "-10042")
    assert reset is not session
    assert reset.state == State.AWAITING_SITE
    assert "Project access changed" in sessions.pop_restart_notice(subject, "-10042")
    assert reset.handle(Event(Action.ANSWER, "https://example.com/")).buttons[:2] == (
        "alpha",
        "beta",
    )


def test_authorized_session_factory_submits_only_bound_project(tmp_path):
    sessions, _chats, _projects, subject = _authorized_sessions(tmp_path)
    session = sessions.session_for(subject, "-10042")
    session.handle(Event(Action.ANSWER, "https://example.com/"))
    session.handle(Event(Action.ANSWER, "alpha"))
    session.handle(Event(Action.ANSWER, "quick"))
    assert session.handle(Event(Action.ANSWER, "json")).state == State.PREVIEW
    session.handle(Event(Action.CONFIRM))
    assert session.handle(Event(Action.CONFIRM)).state == State.RUNNING
    assert session.job_id is not None
    assert sessions.ownership.project_for(session.job_id, subject) == "alpha"


def test_authorized_session_factory_refuses_resume_after_restart(tmp_path):
    sessions, chats, projects, subject = _authorized_sessions(tmp_path)
    first = sessions.session_for(subject, "-10042")
    assert (
        first.handle(Event(Action.ANSWER, "https://example.com/")).state == State.AWAITING_PROJECT
    )
    restarted = TelegramAuthorizedSessions(
        sessions.backend, chats, projects, sessions.ownership, sessions.bindings
    )
    fresh = restarted.session_for(subject, "-10042")
    assert fresh is not first
    assert fresh.state == State.AWAITING_SITE
    assert fresh.draft == {}
    assert "Service restarted" in restarted.pop_restart_notice(subject, "-10042")


def test_authorized_adapter_announces_discarded_draft_after_restart(monkeypatch, tmp_path):
    sessions, chats, projects, _subject = _authorized_sessions(tmp_path)
    requests = []
    client = _client(monkeypatch, requests)
    assert (
        sessions.adapter(client).handle_update(_update("https://example.com/")).state
        == State.AWAITING_PROJECT
    )
    restarted = TelegramAuthorizedSessions(
        sessions.backend, chats, projects, sessions.ownership, sessions.bindings
    )
    update = _update("https://example.com/")
    update["update_id"] = 2
    reply = restarted.adapter(client).handle_update(update)
    assert reply.state == State.AWAITING_PROJECT
    assert "Service restarted" in reply.notice
    assert len(requests) == 2


def test_authorized_session_factory_denies_revoked_chat_or_project(tmp_path):
    sessions, chats, projects, subject = _authorized_sessions(tmp_path)
    assert sessions.session_for(subject, "-10042")
    chats.revoke(subject, "-10042")
    with pytest.raises(PermissionError, match="chat is not authorized"):
        sessions.session_for(subject, "-10042")
    chats.grant(subject, "-10042")
    projects.revoke(subject, "alpha")
    with pytest.raises(PermissionError, match="no authorized projects"):
        sessions.session_for(subject, "-10042")


def test_authorized_session_factory_binds_delivery_to_same_actor_chat(monkeypatch, tmp_path):
    sessions, chats, projects, subject = _authorized_sessions(tmp_path)
    delivery = sessions.report_delivery(
        _client(monkeypatch, []),
        DeliveryReceipts(tmp_path / "receipts.sqlite"),
        subject,
        "-10042",
    )
    assert delivery.subject == subject
    assert delivery._projects == frozenset({"alpha"})
    assert delivery._destinations == frozenset({"telegram:-10042"})
    chats.revoke(subject, "-10042")
    with pytest.raises(PermissionError, match="chat is not authorized"):
        sessions.report_delivery(
            _client(monkeypatch, []),
            DeliveryReceipts(tmp_path / "another-receipts.sqlite"),
            subject,
            "-10042",
        )
    chats.grant(subject, "-10042")
    projects.revoke(subject, "alpha")
    with pytest.raises(PermissionError, match="no authorized projects"):
        sessions.report_delivery(
            _client(monkeypatch, []),
            DeliveryReceipts(tmp_path / "last-receipts.sqlite"),
            subject,
            "-10042",
        )


def test_chat_grant_survives_restart_and_revoke_takes_effect(monkeypatch, tmp_path):
    requests = []
    path = tmp_path / "chats.sqlite"
    subject = telegram_subject("7")
    initial = TelegramChatAuthorizationStore(path)
    initial.grant(subject, "-10042")
    restarted = TelegramChatAuthorizationStore(path)
    assert restarted.allows(subject, "-10042")
    adapter = TelegramGuidedAdapter(
        _client(monkeypatch, requests),
        restarted,
        lambda *_: WizardSession(_Submitter()),
    )
    restarted.revoke(subject, "-10042")
    with pytest.raises(PermissionError, match="not authorized"):
        adapter.handle_update(_update("https://example.com/"))
    assert requests == []


def test_progress_and_document_transport_are_confirmed_offline(monkeypatch, tmp_path):
    requests = []
    client = _client(monkeypatch, requests, result={"message_id": 12})
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    subject = telegram_subject("7")
    chats.grant(subject, "-10042")
    session = WizardSession(_Submitter())
    session.state = State.RUNNING
    adapter = TelegramGuidedAdapter(client, chats, lambda *_: session)
    reply = adapter.publish_progress(subject, "-10042", session, "done=4; total unavailable")
    assert reply.text == "Progress: done=4; total unavailable"
    transport = TelegramDocumentTransport(client, chats, subject)
    transport.send(
        telegram_destination("-10042"),
        OpenedArtifact(io.BytesIO(b"report"), 6, "report.json", "application/json"),
        "receipt-1",
    )
    assert requests[-1].url.path.endswith("/sendDocument")
    assert b"receipt-1" in requests[-1].content and b"report" in requests[-1].content


def test_config_and_document_confirmation_are_strict(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="HTTPS origin"):
        TelegramBotConfig("env:SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "http://telegram.example.test")
    with pytest.raises(ValueError, match="non-zero"):
        telegram_destination("0")
    requests = []
    client = _client(monkeypatch, requests, result=True)
    monkeypatch.delenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN")
    with pytest.raises(TelegramUnavailable, match="token"):
        client.call("sendMessage", data={"chat_id": "1", "text": "x"})
    monkeypatch.setenv("SEOHEAD_SYNTHETIC_TELEGRAM_TOKEN", "synthetic-token")
    no_message = _client(monkeypatch, [], result=True)
    chats = TelegramChatAuthorizationStore(tmp_path / "chats.sqlite")
    chats.grant(telegram_subject("1"), "1")
    with pytest.raises(DeliveryUnavailable, match="did not confirm"):
        TelegramDocumentTransport(
            no_message,
            chats,
            telegram_subject("1"),
        ).send(
            telegram_destination("1"),
            OpenedArtifact(io.BytesIO(b"report"), 6, "report.json", "application/json"),
            "receipt-1",
        )


def test_durable_update_dedup_and_same_actor_other_chat_isolation(monkeypatch, tmp_path):
    sessions, chats, projects, subject = _authorized_sessions(tmp_path)
    requests = []
    client = _client(monkeypatch, requests)
    adapter = sessions.adapter(client)
    update = _update("https://example.com/")
    first = adapter.handle_update(update)
    assert adapter.handle_update(update) == first
    assert len(requests) == 1
    restarted = TelegramAuthorizedSessions(
        sessions.backend, chats, projects, sessions.ownership, sessions.bindings
    )
    assert restarted.adapter(client).handle_update(update) == first
    assert len(requests) == 1
    session = sessions.session_for(subject, "-10042")
    for text in ("alpha", "quick", "json"):
        session.handle(Event(Action.ANSWER, text))
    session.handle(Event(Action.CONFIRM))
    session.handle(Event(Action.CONFIRM))
    job = session.job_id
    chats.grant(subject, "-10043")
    assert restarted.status(subject, "-10042", job).job_id == job
    assert restarted.status(subject, "-10043", job) is None
    assert not restarted.cancel(subject, "-10043", job)
    with pytest.raises(PermissionError, match="session"):
        adapter.publish_progress(subject, "-10043", session, "foreign progress")
    chats.revoke(subject, "-10042")
    with pytest.raises(PermissionError, match="chat"):
        session._submitter.status(job)
    with pytest.raises(PermissionError, match="chat"):
        session._submitter.cancel(job)


def test_confirmation_callbacks_are_bound_to_the_current_preview(monkeypatch, tmp_path):
    sessions, _chats, _projects, subject = _authorized_sessions(tmp_path)
    requests = []
    adapter = sessions.adapter(_client(monkeypatch, requests))
    session = sessions.session_for(subject, "-10042")
    for text in ("https://example.com/", "alpha", "quick", "json"):
        session.handle(Event(Action.ANSWER, text))
    dispatch = session._dispatch_id

    def callback(update_id, token):
        return {
            "update_id": update_id,
            "callback_query": {
                "id": str(update_id),
                "from": {"id": 7},
                "message": {"chat": {"id": -10042}},
                "data": token,
            },
        }

    reply = adapter.handle_update(callback(1, f"a:confirm:{dispatch}"))
    assert reply.state == State.CONFIRMING
    with pytest.raises(TelegramUnavailable, match="no longer current"):
        adapter.handle_update(callback(2, f"a:confirm:{dispatch}"))
    assert sessions.backend.list_jobs("alpha", 0, 10) == []
    with pytest.raises(TelegramUnavailable, match="current preview"):
        adapter.handle_update(callback(3, "a:start:old-dispatch"))
    start = callback(4, f"a:start:{dispatch}")
    assert adapter.handle_update(start).state == State.RUNNING
    assert adapter.handle_update(start).state == State.RUNNING
    assert len(sessions.backend.list_jobs("alpha", 0, 10)) == 1
