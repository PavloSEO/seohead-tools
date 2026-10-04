"""Durable, local notes and proposed goals for an agent-owned project.

The inbox is deliberately a small project sidecar instead of another task or
scan registry.  It only records a specialist's intent and the explicit receipt
and goal decisions made by an agent.  It never starts a scan, calls a provider,
or injects text into a model conversation.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import _load

FORMAT = "seohead.project-inbox.v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT = 8_000
MAX_ENTRIES = 10_000
MAX_PAGE = 100
_CONSUMER = re.compile(r"[a-z][a-z0-9._/-]{0,127}\Z")
_REFERENCE = re.compile(r"(?:goal|task|scan|finding|section):[A-Za-z0-9._/-]{1,128}\Z")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, name: str, maximum: int = MAX_TEXT) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _consumer(value: Any) -> str:
    if type(value) is not str or not _CONSUMER.fullmatch(value) or ".." in value:
        raise ValueError("consumer must be a bounded stable local identifier")
    return value


def _references(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("references must be a list of at most 20 stable project references")
    if any(type(item) is not str or not _REFERENCE.fullmatch(item) for item in value):
        raise ValueError("references contain an invalid project reference")
    return list(dict.fromkeys(value))


def _entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "id", "kind", "text", "references", "author_role", "created_at", "delivery", "goal_state"
    }:
        raise ValueError("project inbox entry has an unsupported shape")
    if type(value["id"]) is not str or not value["id"].startswith("inbox:"):
        raise ValueError("project inbox entry has an invalid id")
    if value["kind"] not in {"note", "proposed_goal"}:
        raise ValueError("project inbox entry has an invalid kind")
    _text(value["text"], "entry text")
    _references(value["references"])
    if value["author_role"] not in {"specialist", "agent"}:
        raise ValueError("project inbox entry has an invalid author role")
    if type(value["created_at"]) is not str:
        raise ValueError("project inbox entry has an invalid timestamp")
    try:
        timestamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("project inbox entry has an invalid timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise ValueError("project inbox entry has an invalid timestamp")
    if value["goal_state"] not in {None, "proposed", "accepted", "completed"}:
        raise ValueError("project inbox entry has an invalid goal state")
    if value["kind"] == "note" and value["goal_state"] is not None:
        raise ValueError("notes cannot have a goal state")
    if value["kind"] == "proposed_goal" and value["goal_state"] is None:
        raise ValueError("proposed goals need a goal state")
    delivery = value["delivery"]
    if not isinstance(delivery, dict) or len(delivery) > 100:
        raise ValueError("project inbox delivery state is invalid")
    for consumer, receipt in delivery.items():
        _consumer(consumer)
        if not isinstance(receipt, dict) or set(receipt) - {"read_at", "acknowledged_at"}:
            raise ValueError("project inbox delivery receipt is invalid")
        for stamp in receipt.values():
            if type(stamp) is not str:
                raise ValueError("project inbox delivery receipt is invalid")
    return copy.deepcopy(value)


def _document(root: Path, project: dict[str, Any]) -> dict[str, Any]:
    path = root / "inbox.json"
    if not path.exists():
        return {"format": FORMAT, "revision": 0, "project_uuid": project["project_uuid"], "entries": []}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("project inbox is missing, unsafe, or exceeds its byte limit")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("project inbox is not valid JSON") from exc
    if not isinstance(document, dict) or set(document) != {"format", "revision", "project_uuid", "entries"}:
        raise ValueError("project inbox has an unsupported shape")
    if document["format"] != FORMAT or document["project_uuid"] != project["project_uuid"]:
        raise ValueError("project inbox belongs to a different project or format")
    if type(document["revision"]) is not int or document["revision"] < 0:
        raise ValueError("project inbox has an invalid revision")
    if not isinstance(document["entries"], list) or len(document["entries"]) > MAX_ENTRIES:
        raise ValueError("project inbox has too many entries")
    ids = set()
    for item in document["entries"]:
        entry = _entry(item)
        if entry["id"] in ids:
            raise ValueError("project inbox has duplicate entry ids")
        ids.add(entry["id"])
    return document


def _write(root: Path, document: dict[str, Any]) -> None:
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode()) > MAX_BYTES:
        raise ValueError("project inbox exceeds its byte limit")
    fd, stage = tempfile.mkstemp(prefix=".inbox-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, root / "inbox.json")
        from seohead.filesystem import fsync_directory

        fsync_directory(root)
    finally:
        Path(stage).unlink(missing_ok=True)


@contextmanager
def _transaction(
    directory: str | Path, expected_revision: int | None = None
) -> Iterator[tuple[Path, dict[str, Any]]]:
    root, project = _load(directory)
    lock_path = root / ".inbox.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        document = _document(root, project)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision != document["revision"]
        ):
            raise ValueError(f"inbox revision conflict: current revision is {document['revision']}")
        before = copy.deepcopy(document)
        yield root, document
        if document != before:
            document["revision"] += 1
            _write(root, document)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _public(entry: dict[str, Any], consumer: str | None = None) -> dict[str, Any]:
    result = {key: entry[key] for key in ("id", "kind", "text", "references", "author_role", "created_at", "goal_state")}
    if consumer is not None:
        receipt = entry["delivery"].get(consumer, {})
        result["read_at"] = receipt.get("read_at")
        result["acknowledged_at"] = receipt.get("acknowledged_at")
    return result


def submit(
    directory: str | Path, *, text: str, kind: str = "note", references: list[str] | None = None,
    author_role: str = "specialist", expected_revision: int | None = None,
) -> dict[str, Any]:
    """Store a note or proposed goal without executing anything."""
    if kind not in {"note", "proposed_goal"}:
        raise ValueError("kind must be note or proposed_goal")
    if author_role not in {"specialist", "agent"}:
        raise ValueError("author_role must be specialist or agent")
    with _transaction(directory, expected_revision) as (_, document):
        entry: dict[str, Any] = {
            "id": f"inbox:{uuid.uuid4()}", "kind": kind, "text": _text(text, "text"),
            "references": _references(references), "author_role": author_role, "created_at": _now(),
            "delivery": {}, "goal_state": "proposed" if kind == "proposed_goal" else None,
        }
        document["entries"].append(entry)
        return {"ok": True, "revision": document["revision"] + 1, "entry": _public(entry)}


def list_entries(
    directory: str | Path, *, consumer: str, offset: int = 0, limit: int = 20,
    include_acknowledged: bool = True,
) -> dict[str, Any]:
    """Read a bounded page without changing delivery state."""
    consumer = _consumer(consumer)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError("offset must be nonnegative and limit must be from 1 to 100")
    with _transaction(directory) as (_, document):
        entries = [entry for entry in document["entries"] if include_acknowledged or not entry["delivery"].get(consumer, {}).get("acknowledged_at")]
        page = entries[offset : offset + limit]
        return {"ok": True, "revision": document["revision"], "entries": [_public(item, consumer) for item in page], "pagination": {"offset": offset, "limit": limit, "total": len(entries), "next_offset": offset + len(page) if offset + len(page) < len(entries) else None}}


def mark_read(directory: str | Path, *, consumer: str, entry_ids: list[str], expected_revision: int | None = None) -> dict[str, Any]:
    """Record inspection.  A read message remains unread until acknowledgment."""
    consumer = _consumer(consumer)
    if not isinstance(entry_ids, list) or not entry_ids or len(entry_ids) > MAX_PAGE or len(entry_ids) != len(set(entry_ids)):
        raise ValueError("entry_ids must be a unique bounded nonempty list")
    with _transaction(directory, expected_revision) as (_, document):
        entries = {entry["id"]: entry for entry in document["entries"]}
        if any(item not in entries for item in entry_ids):
            raise ValueError("unknown inbox entry")
        stamp = _now()
        changed = False
        for item in entry_ids:
            receipt = entries[item]["delivery"].setdefault(consumer, {})
            if "read_at" not in receipt:
                receipt["read_at"] = stamp
                changed = True
        return {"ok": True, "revision": document["revision"] + int(changed), "entries": [_public(entries[item], consumer) for item in entry_ids]}


def acknowledge(directory: str | Path, *, consumer: str, entry_ids: list[str], expected_revision: int | None = None) -> dict[str, Any]:
    """Explicitly acknowledge entries; idempotent retries retain the original receipt."""
    consumer = _consumer(consumer)
    with _transaction(directory, expected_revision) as (_, document):
        entries = {entry["id"]: entry for entry in document["entries"]}
        if not isinstance(entry_ids, list) or not entry_ids or len(entry_ids) > MAX_PAGE or len(entry_ids) != len(set(entry_ids)) or any(item not in entries for item in entry_ids):
            raise ValueError("entry_ids must name unique existing inbox entries")
        stamp, changed = _now(), False
        for item in entry_ids:
            receipt = entries[item]["delivery"].setdefault(consumer, {})
            if "read_at" not in receipt:
                receipt["read_at"] = stamp
            if "acknowledged_at" not in receipt:
                receipt["acknowledged_at"] = stamp
                changed = True
        return {"ok": True, "revision": document["revision"] + int(changed), "entries": [_public(entries[item], consumer) for item in entry_ids]}


def set_goal_state(directory: str | Path, *, entry_id: str, state: str, expected_revision: int | None = None) -> dict[str, Any]:
    """Accept or complete a proposed goal without treating delivery as execution."""
    if state not in {"accepted", "completed"}:
        raise ValueError("goal state must be accepted or completed")
    with _transaction(directory, expected_revision) as (_, document):
        entry: dict[str, Any] | None = next(
            (item for item in document["entries"] if item["id"] == entry_id), None
        )
        if entry is None or entry["kind"] != "proposed_goal":
            raise ValueError("entry is not a proposed goal")
        if state == "completed" and entry["goal_state"] != "accepted":
            raise ValueError("a proposed goal must be accepted before completion")
        changed = entry["goal_state"] != state
        entry["goal_state"] = state
        return {"ok": True, "revision": document["revision"] + int(changed), "entry": _public(entry)}


def unread_summary(directory: str | Path, *, consumer: str, limit: int = 10) -> dict[str, Any]:
    """Return bounded references for a tool response; it never consumes messages."""
    consumer = _consumer(consumer)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError("limit must be from 1 to 100")
    with _transaction(directory) as (_, document):
        unread = [entry for entry in document["entries"] if not entry["delivery"].get(consumer, {}).get("acknowledged_at")]
        return {"count": len(unread), "entries": [{"id": entry["id"], "kind": entry["kind"], "references": entry["references"]} for entry in unread[:limit]], "truncated": len(unread) > limit}


def fingerprint(directory: str | Path) -> str:
    """A test-only observer aid: hash raw inbox bytes without modifying source artifacts."""
    root, _ = _load(directory)
    path = root / "inbox.json"
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()
