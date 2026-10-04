"""Disabled-by-default Telegram Bot API adapter for the guided scan contract.

This module exposes callable update and report-delivery wiring only.  It
creates no listener, polling loop, account, or background thread.  Operators
must explicitly construct it with a Bot API token reference and pass updates
from their own approved service boundary.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from seohead.bot.contract import Action
from seohead.bot.report_delivery import DeliveryAmbiguous, DeliveryUnavailable
from seohead.bot.wizard import Event, Reply, WizardSession
from seohead.job_contracts import OpenedArtifact

_ENV = re.compile(r"env:[A-Z_][A-Z0-9_]{0,127}\Z")
_CALLBACK_ACTIONS = frozenset({"back", "edit", "help", "cancel", "confirm", "rerun"})
_DESTINATION = re.compile(r"telegram:-?[1-9][0-9]{0,18}\Z")


class TelegramUnavailable(RuntimeError):
    """The configured Bot API transport cannot safely complete an operation."""


class TelegramAmbiguous(TelegramUnavailable):
    """A request timed out or disconnected after it might have reached Telegram."""


@dataclass(frozen=True)
class TelegramBotConfig:
    """A service-owned Bot API endpoint and token reference, never a token value."""

    token: str
    api_base: str = "https://api.telegram.org"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.api_base)
        if not _ENV.fullmatch(self.token):
            raise ValueError("Telegram token must be an env:NAME reference")
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Telegram API base must be an absolute HTTPS origin")

    def resolve_token(self) -> str:
        value = os.environ.get(self.token[4:])
        if not value:
            raise TelegramUnavailable(f"configured Telegram token {self.token} is unavailable")
        return value


class TelegramChatAuthorizationStore:
    """Private explicit actor/chat mapping; no chat discovery or polling."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("Telegram authorization store must be private")
        with sqlite3.connect(self.path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS chat_grants (
                    subject TEXT NOT NULL, chat_id TEXT NOT NULL,
                    PRIMARY KEY(subject, chat_id)
                )"""
            )
        os.chmod(self.path, 0o600)

    def grant(self, subject: str, chat_id: str) -> None:
        if not subject or not _chat_id(chat_id):
            raise ValueError("subject and non-zero chat_id are required")
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT OR IGNORE INTO chat_grants(subject,chat_id) VALUES(?,?)", (subject, chat_id)
            )

    def revoke(self, subject: str, chat_id: str) -> None:
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM chat_grants WHERE subject=? AND chat_id=?", (subject, chat_id))

    def allows(self, subject: str, chat_id: str) -> bool:
        with sqlite3.connect(self.path) as con:
            return (
                con.execute(
                    "SELECT 1 FROM chat_grants WHERE subject=? AND chat_id=?", (subject, chat_id)
                ).fetchone()
                is not None
            )


@dataclass
class TelegramBotClient:
    """Small synchronous Bot API caller used only when an operator invokes it."""

    config: TelegramBotConfig
    client: httpx.Client
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be within 0..300")

    def call(
        self, method: str, *, data: dict[str, Any], files: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method not in {"sendMessage", "answerCallbackQuery", "sendDocument"}:
            raise TelegramUnavailable("unsupported Telegram Bot API method")
        try:
            response = self.client.post(
                f"{self.config.api_base}/bot{self.config.resolve_token()}/{method}",
                data=data,
                files=files,
                timeout=self.timeout_seconds,
            )
        except httpx.TransportError as exc:
            raise TelegramAmbiguous("Telegram Bot API delivery outcome is unknown") from exc
        except httpx.HTTPError as exc:
            raise TelegramUnavailable("configured Telegram Bot API is unavailable") from exc
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise TelegramUnavailable("Telegram Bot API returned invalid JSON") from exc
        if response.status_code != 200 or body.get("ok") is not True:
            raise TelegramUnavailable("Telegram Bot API rejected the operation")
        result = body.get("result")
        if not isinstance(result, (dict, bool)):
            raise TelegramUnavailable("Telegram Bot API result has an unsupported shape")
        return body


def telegram_subject(user_id: str) -> str:
    if not user_id.isdecimal() or int(user_id) <= 0:
        raise ValueError("Telegram user id must be positive decimal text")
    return f"telegram:{user_id}"


def telegram_destination(chat_id: str) -> str:
    if not _chat_id(chat_id):
        raise ValueError("Telegram chat id must be non-zero decimal text")
    return f"telegram:{chat_id}"


def _chat_id(value: str) -> bool:
    return bool(re.fullmatch(r"-?[1-9][0-9]{0,18}", value))


def _reply_markup(reply: Reply) -> str | None:
    if not reply.buttons:
        return None
    rows = []
    for button in reply.buttons:
        if button in _CALLBACK_ACTIONS:
            callback = f"a:{button}"
        elif button == "start":
            callback = "a:confirm"
        elif button == "new scan":
            callback = "a:rerun"
        else:
            callback = f"v:{button}"
        if len(callback.encode("utf-8")) > 64:
            raise TelegramUnavailable("guided callback token is too long")
        rows.append([{"text": button, "callback_data": callback}])
    return json.dumps({"inline_keyboard": rows}, separators=(",", ":"))


def _event(update: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    """Extract actor/chat and one bounded event from a Bot API update payload."""
    message = update.get("message")
    callback = update.get("callback_query")
    if isinstance(message, dict):
        chat = message.get("chat", {}).get("id")
        actor = message.get("from", {}).get("id")
        text = message.get("text")
        if isinstance(chat, int) and isinstance(actor, int) and isinstance(text, str) and chat != 0:
            return str(actor), str(chat), "answer", text
    if isinstance(callback, dict):
        message = callback.get("message", {})
        chat = message.get("chat", {}).get("id") if isinstance(message, dict) else None
        actor = callback.get("from", {}).get("id")
        data = callback.get("data")
        callback_id = callback.get("id")
        if (
            isinstance(chat, int)
            and isinstance(actor, int)
            and isinstance(data, str)
            and isinstance(callback_id, str)
            and chat != 0
        ):
            return str(actor), str(chat), "callback", data
    raise TelegramUnavailable("unsupported Telegram update shape")


@dataclass
class TelegramGuidedAdapter:
    """Turn one trusted update into wizard output; caller owns webhook/polling."""

    client: TelegramBotClient
    chats: TelegramChatAuthorizationStore
    session_for: Callable[[str, str], WizardSession]

    def handle_update(self, update: dict[str, Any]) -> Reply:
        user_id, chat_id, kind, value = _event(update)
        subject = telegram_subject(user_id)
        if not self.chats.allows(subject, chat_id):
            raise PermissionError("Telegram chat is not authorized for this actor")
        session = self.session_for(subject, chat_id)
        if kind == "answer":
            event = Event(Action.ANSWER, value)
        else:
            assert value is not None
            if value.startswith("a:") and value[2:] in _CALLBACK_ACTIONS:
                event = Event(Action(value[2:]))
            elif value.startswith("v:") and 0 < len(value[2:]) <= 256:
                event = Event(Action.ANSWER, value[2:])
            else:
                raise TelegramUnavailable("unsupported guided callback")
            callback = update["callback_query"]
            self.client.call("answerCallbackQuery", data={"callback_query_id": callback["id"]})
        reply = session.handle(event)
        data: dict[str, Any] = {"chat_id": chat_id, "text": reply.text}
        if reply.notice:
            data["text"] += f"\n\n{reply.notice}"
        markup = _reply_markup(reply)
        if markup is not None:
            data["reply_markup"] = markup
        self.client.call("sendMessage", data=data)
        return reply

    def publish_progress(
        self, subject: str, chat_id: str, session: WizardSession, text: str
    ) -> Reply:
        """Publish worker-supplied progress without inventing totals or status."""
        if not self.chats.allows(subject, chat_id):
            raise PermissionError("Telegram chat is not authorized for this actor")
        reply = session.handle(Event(Action.PROGRESS, text))
        self.client.call("sendMessage", data={"chat_id": chat_id, "text": reply.text})
        return reply


@dataclass
class TelegramDocumentTransport:
    """`AuthorizedReportDelivery.send` implementation for one authorized chat."""

    client: TelegramBotClient

    def send(self, destination: str, opened: OpenedArtifact, receipt: str) -> None:
        if not _DESTINATION.fullmatch(destination):
            raise DeliveryUnavailable("Telegram delivery destination is invalid")
        if not receipt or len(receipt) > 128:
            raise DeliveryUnavailable("delivery receipt is invalid")
        try:
            body = self.client.call(
                "sendDocument",
                data={
                    "chat_id": destination.split(":", 1)[1],
                    "caption": f"SEOHEAD retained report receipt: {receipt}",
                },
                files={"document": (opened.filename, opened.handle, opened.media_type)},
            )
        except TelegramAmbiguous as exc:
            raise DeliveryAmbiguous(str(exc)) from exc
        except TelegramUnavailable as exc:
            raise DeliveryUnavailable(str(exc)) from exc
        result = body["result"]
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise DeliveryUnavailable("Telegram Bot API did not confirm a document message")
