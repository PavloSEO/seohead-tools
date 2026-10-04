"""Offline Bot API wire checks; no listener, polling, or real account is used."""

from __future__ import annotations

import io
from urllib.parse import parse_qs

import httpx
import pytest

from seohead.bot.contract import State
from seohead.bot.report_delivery import DeliveryUnavailable
from seohead.bot.telegram_adapter import (
    TelegramBotClient,
    TelegramBotConfig,
    TelegramChatAuthorizationStore,
    TelegramDocumentTransport,
    TelegramGuidedAdapter,
    TelegramUnavailable,
    telegram_destination,
    telegram_subject,
)
from seohead.bot.wizard import WizardSession
from seohead.job_contracts import OpenedArtifact


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
    transport = TelegramDocumentTransport(client)
    transport.send(
        telegram_destination("-10042"),
        OpenedArtifact(io.BytesIO(b"report"), 6, "report.json", "application/json"),
        "receipt-1",
    )
    assert requests[-1].url.path.endswith("/sendDocument")
    assert b"receipt-1" in requests[-1].content and b"report" in requests[-1].content


def test_config_and_document_confirmation_are_strict(monkeypatch):
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
    with pytest.raises(DeliveryUnavailable, match="did not confirm"):
        TelegramDocumentTransport(no_message).send(
            telegram_destination("1"),
            OpenedArtifact(io.BytesIO(b"report"), 6, "report.json", "application/json"),
            "receipt-1",
        )
