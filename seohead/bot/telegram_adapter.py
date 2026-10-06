"""Disabled-by-default Telegram Bot API adapter for the guided scan contract.

This module exposes callable update and report-delivery wiring only.  It
creates no listener, polling loop, account, or background thread.  Operators
must explicitly construct it with a Bot API token reference and pass updates
from their own approved service boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from seohead.bot.contract import Action, State
from seohead.bot.job_adapter import (
    AuthorizedJobSubmitter,
    JobOwnershipStore,
    ProjectAuthorizationStore,
)
from seohead.bot.report_delivery import (
    AuthorizedReportDelivery,
    DeliveryAmbiguous,
    DeliveryReceipts,
    DeliveryUnavailable,
)
from seohead.bot.wizard import Event, Reply, WizardSession
from seohead.job_contracts import JobBackend, JobStatus, OpenedArtifact

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


class TelegramSessionBindingStore:
    """Private actor/chat bindings and exact update receipts; no resumable drafts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("Telegram session binding store must be private")
        with sqlite3.connect(self.path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS session_bindings (
                    subject TEXT NOT NULL, chat_id TEXT NOT NULL,
                    PRIMARY KEY(subject, chat_id)
                )"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS updates (
                    subject TEXT NOT NULL, chat_id TEXT NOT NULL, update_id INTEGER NOT NULL,
                    digest TEXT NOT NULL, reply TEXT,
                    PRIMARY KEY(subject, chat_id, update_id)
                )"""
            )
        os.chmod(self.path, 0o600)

    def claim_update(self, subject: str, chat_id: str, update: dict[str, Any]) -> Reply | None:
        update_id = update.get("update_id")
        if type(update_id) is not int or update_id < 0:
            raise TelegramUnavailable("trusted update requires a nonnegative update_id")
        digest = hashlib.sha256(json.dumps(update, sort_keys=True).encode()).hexdigest()
        with sqlite3.connect(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT digest,reply FROM updates WHERE subject=? AND chat_id=? AND update_id=?",
                (subject, chat_id, update_id),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise TelegramUnavailable("update identity was reused with different content")
                if row[1] is None:
                    raise TelegramUnavailable(
                        "update outcome is uncertain; inspect the retained job before continuing"
                    )
                payload = json.loads(row[1])
                return Reply(
                    State(payload["state"]),
                    payload["text"],
                    tuple(payload["buttons"]),
                    payload["notice"],
                )
            con.execute(
                "INSERT INTO updates VALUES(?,?,?,?,NULL)", (subject, chat_id, update_id, digest)
            )
        return None

    def finish_update(self, subject: str, chat_id: str, update_id: int, reply: Reply) -> None:
        with sqlite3.connect(self.path) as con:
            con.execute(
                "UPDATE updates SET reply=? WHERE subject=? AND chat_id=? AND update_id=?",
                (json.dumps(asdict(reply)), subject, chat_id, update_id),
            )

    def bind(self, subject: str, chat_id: str) -> bool:
        """Bind one actor/chat and return whether a prior process saw it.

        Drafts and selected projects are intentionally absent. A new process
        must create a fresh wizard and cannot resume a previous actor's draft.
        """
        _validate_telegram_subject(subject)
        if not _chat_id(chat_id):
            raise ValueError("Telegram chat id must be non-zero decimal text")
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                "SELECT 1 FROM session_bindings WHERE subject=? AND chat_id=?", (subject, chat_id)
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO session_bindings(subject,chat_id) VALUES(?,?)", (subject, chat_id)
                )
                return False
        return True


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
        uncertain = TelegramAmbiguous if method == "sendDocument" else TelegramUnavailable
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise uncertain("Telegram Bot API returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise uncertain("Telegram Bot API result has an unsupported shape")
        if body.get("ok") is False:
            raise TelegramUnavailable("Telegram Bot API rejected the operation")
        if response.status_code != 200 or body.get("ok") is not True:
            raise uncertain("Telegram Bot API did not confirm the operation")
        result = body.get("result")
        if not isinstance(result, (dict, bool)):
            raise uncertain("Telegram Bot API result has an unsupported shape")
        return body


def telegram_subject(user_id: str) -> str:
    if not user_id.isdecimal() or int(user_id) <= 0:
        raise ValueError("Telegram user id must be positive decimal text")
    return f"telegram:{user_id}"


def _validate_telegram_subject(subject: str) -> None:
    if not subject.startswith("telegram:"):
        raise ValueError("Telegram subject is required")
    telegram_subject(subject.split(":", 1)[1])


def telegram_destination(chat_id: str) -> str:
    if not _chat_id(chat_id):
        raise ValueError("Telegram chat id must be non-zero decimal text")
    return f"telegram:{chat_id}"


def _chat_id(value: str) -> bool:
    return bool(re.fullmatch(r"-?[1-9][0-9]{0,18}", value))


def _reply_markup(reply: Reply, dispatch_id: str | None = None) -> str | None:
    if not reply.buttons:
        return None
    rows = []
    for button in reply.buttons:
        if button in _CALLBACK_ACTIONS:
            callback = f"a:{button}"
        elif button == "start":
            callback = "a:start"
        elif button == "new scan":
            callback = "a:rerun"
        else:
            callback = f"v:{button}"
        if dispatch_id is not None and callback in {"a:start", "a:confirm"}:
            callback += f":{dispatch_id}"
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
    session_notice_for: Callable[[str, str], str | None] | None = None
    bindings: TelegramSessionBindingStore | None = None

    def handle_update(self, update: dict[str, Any]) -> Reply:
        user_id, chat_id, kind, value = _event(update)
        subject = telegram_subject(user_id)
        if not self.chats.allows(subject, chat_id):
            raise PermissionError("Telegram chat is not authorized for this actor")
        session = self.session_for(subject, chat_id)
        if self.bindings is not None:
            previous = self.bindings.claim_update(subject, chat_id, update)
            if previous is not None:
                return previous
        session_notice = (
            self.session_notice_for(subject, chat_id)
            if self.session_notice_for is not None
            else None
        )
        if kind == "answer":
            event = Event(Action.ANSWER, value)
        else:
            assert value is not None
            if self.bindings is not None and value.startswith(("a:start", "a:confirm")):
                token, _, dispatch_id = value.rpartition(":")
                if token not in {"a:start", "a:confirm"} or dispatch_id != session._dispatch_id:
                    raise TelegramUnavailable("confirmation does not match the current preview")
                value = token
            if value == "a:start":
                if session.state != State.CONFIRMING:
                    raise TelegramUnavailable("start is not available for this session")
                event = Event(Action.CONFIRM)
            elif value == "a:confirm" and session.state != State.PREVIEW:
                raise TelegramUnavailable("preview confirmation is no longer current")
            elif value.startswith("a:") and value[2:] in _CALLBACK_ACTIONS:
                event = Event(Action(value[2:]))
            elif value.startswith("v:") and 0 < len(value[2:]) <= 256:
                event = Event(Action.ANSWER, value[2:])
            else:
                raise TelegramUnavailable("unsupported guided callback")
            callback = update["callback_query"]
            self.client.call("answerCallbackQuery", data={"callback_query_id": callback["id"]})
        reply = session.handle(event)
        if session_notice:
            reply = Reply(
                reply.state,
                reply.text,
                reply.buttons,
                notice=(f"{session_notice}\n{reply.notice}" if reply.notice else session_notice),
            )
        if self.bindings is not None:
            self.bindings.finish_update(subject, chat_id, update["update_id"], reply)
        data: dict[str, Any] = {"chat_id": chat_id, "text": reply.text}
        if reply.notice:
            data["text"] += f"\n\n{reply.notice}"
        markup = _reply_markup(reply, session._dispatch_id if self.bindings is not None else None)
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
        if self.bindings is not None:
            if self.session_for(subject, chat_id) is not session:
                raise PermissionError("progress session is not bound to this actor/chat")
            if session.job_id is None or session._submitter.status(session.job_id) is None:
                raise PermissionError("progress job is not authorized")
        reply = session.handle(Event(Action.PROGRESS, text))
        self.client.call("sendMessage", data={"chat_id": chat_id, "text": reply.text})
        return reply


@dataclass
class TelegramAuthorizedSessions:
    """Mandatory actor/chat/project composition for a trusted Telegram service boundary.

    The low-level wire adapter remains callable for a service that already has
    another approved identity layer. This factory is the safe composition for
    a guided scan: it derives the session subject from the update, reads only
    that subject's live grants, creates an ownership-backed submitter, and
    deliberately discards incomplete drafts after a process restart.
    """

    backend: JobBackend
    chats: TelegramChatAuthorizationStore
    projects: ProjectAuthorizationStore
    ownership: JobOwnershipStore
    bindings: TelegramSessionBindingStore
    _sessions: dict[tuple[str, str], WizardSession] = field(default_factory=dict, init=False)
    _session_projects: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict, init=False
    )
    _restart_notices: dict[tuple[str, str], str] = field(default_factory=dict, init=False)

    def _authorize(self, subject: str, chat_id: str) -> tuple[str, ...]:
        _validate_telegram_subject(subject)
        if not self.chats.allows(subject, chat_id):
            raise PermissionError("Telegram chat is not authorized for this actor")
        projects = self.projects.projects_for(subject)
        if not projects:
            raise PermissionError("Telegram actor has no authorized projects")
        return projects

    def session_for(self, subject: str, chat_id: str) -> WizardSession:
        projects = self._authorize(subject, chat_id)
        key = (subject, chat_id)
        current = self._sessions.get(key)
        if current is not None and self._session_projects[key] == projects:
            return current
        access_changed = current is not None
        if current is not None:
            self._restart_notices[key] = (
                "Project access changed; the unconfirmed draft was discarded. Retained jobs remain accessible by ID."
            )
        restarted = self.bindings.bind(subject, chat_id)
        session = WizardSession(
            AuthorizedJobSubmitter(
                self.backend,
                subject,
                projects,
                ownership=self.ownership,
                authorization=self.projects,
                scope=chat_id,
                authorize=lambda: self._authorize(subject, chat_id),
            ),
            projects=projects,
            allow_new_project=False,
        )
        recovered = session._submitter.recover()
        if recovered:
            session.job_id = recovered[-1]
            session.state = State.RUNNING
        self._sessions[key] = session
        self._session_projects[key] = projects
        if restarted and not access_changed:
            self._restart_notices[key] = (
                "Service restarted; the unconfirmed draft was discarded. Retained jobs remain accessible by ID."
            )
        if recovered:
            self._restart_notices[key] = (
                "Recovered an already confirmed dispatch; no new scan was created."
            )
        return session

    def status(self, subject: str, chat_id: str, job_id: str) -> JobStatus | None:
        """Read an owned job after restart without resuming a draft."""
        return self.session_for(subject, chat_id)._submitter.status(job_id)

    def cancel(self, subject: str, chat_id: str, job_id: str) -> bool:
        """Cancel only a currently granted job initiated from this chat."""
        return self.session_for(subject, chat_id)._submitter.cancel(job_id)

    def pop_restart_notice(self, subject: str, chat_id: str) -> str | None:
        return self._restart_notices.pop((subject, chat_id), None)

    def adapter(self, client: TelegramBotClient) -> TelegramGuidedAdapter:
        """Build the wire adapter with this exact identity and grant composition."""
        return TelegramGuidedAdapter(
            client,
            self.chats,
            self.session_for,
            session_notice_for=self.pop_restart_notice,
            bindings=self.bindings,
        )

    def report_delivery(
        self,
        client: TelegramBotClient,
        receipts: DeliveryReceipts,
        subject: str,
        chat_id: str,
        *,
        max_file_bytes: int = 50 * 1024 * 1024,
    ) -> AuthorizedReportDelivery:
        """Build a live-grant checked retained-report handoff for one subject/chat."""
        projects = self._authorize(subject, chat_id)
        return AuthorizedReportDelivery(
            self.backend,
            projects,
            {telegram_destination(chat_id)},
            receipts,
            TelegramDocumentTransport(client, self.chats, subject).send,
            max_file_bytes=max_file_bytes,
            subject=subject,
            ownership=self.ownership,
            authorization=self.projects,
            scope=chat_id,
            authorize=lambda: self._authorize(subject, chat_id),
        )


@dataclass
class TelegramDocumentTransport:
    """`AuthorizedReportDelivery.send` implementation for one authorized subject/chat."""

    client: TelegramBotClient
    chats: TelegramChatAuthorizationStore
    subject: str

    def __post_init__(self) -> None:
        _validate_telegram_subject(self.subject)

    def send(self, destination: str, opened: OpenedArtifact, receipt: str) -> None:
        if not _DESTINATION.fullmatch(destination):
            raise DeliveryUnavailable("Telegram delivery destination is invalid")
        if not receipt or len(receipt) > 128:
            raise DeliveryUnavailable("delivery receipt is invalid")
        chat_id = destination.split(":", 1)[1]
        if not self.chats.allows(self.subject, chat_id):
            raise DeliveryUnavailable("Telegram chat is not authorized for this actor")
        try:
            body = self.client.call(
                "sendDocument",
                data={
                    "chat_id": chat_id,
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
            raise DeliveryAmbiguous("Telegram Bot API did not confirm a document message")
