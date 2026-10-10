"""Pending browser-consent flow record for GSC grants.

The record holds lifecycle metadata only: no grant material, no client secret, no
authorization code. The raw ``state`` is returned to the caller once and stored as a
SHA-256 digest, so the file alone cannot complete a callback.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from seohead.data_sources.credentials import is_private_mode

FLOW_TTL = timedelta(seconds=600)
_KEYS = {"flow_id", "state_sha256", "status", "created_at", "expires_at"}
_STATES = {"waiting", "connected", "expired", "cancelled", "revoked"}


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _check_path(path: Path) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("OAuth storage must not use symlinks")


def _write(path: Path, record: dict[str, Any]) -> None:
    descriptor, staged = tempfile.mkstemp(prefix=".oauth-flow-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, 0o600)
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def create_flow(path: Path, now: datetime) -> tuple[dict[str, Any], str]:
    """Start a waiting flow, replacing any earlier pending one.

    Returns the stored record and the raw state, which is never written to disk.
    """
    _check_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = secrets.token_urlsafe(24)
    record = {
        "flow_id": secrets.token_urlsafe(16),
        "state_sha256": hash_state(state),
        "status": "waiting",
        "created_at": _iso(now),
        "expires_at": _iso(now + FLOW_TTL),
    }
    _write(path, record)
    return record, state


def load_flow(path: Path) -> dict[str, Any] | None:
    _check_path(path)
    if not path.exists():
        return None
    if not path.is_file() or not is_private_mode(path.stat().st_mode) or path.stat().st_size > 4096:
        raise ValueError("OAuth flow record must be a private bounded regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("OAuth flow record is unreadable") from exc
    if not isinstance(value, dict) or set(value) != _KEYS or value["status"] not in _STATES:
        raise ValueError("OAuth flow record is invalid")
    return value


def effective_status(flow: dict[str, Any], now: datetime) -> str:
    if flow["status"] == "waiting" and now >= datetime.fromisoformat(flow["expires_at"]):
        return "expired"
    return flow["status"]


def cancel_flow(path: Path, now: datetime) -> dict[str, Any] | None:
    """Mark a waiting flow cancelled. Repeated cancels are no-ops returning the same status."""
    flow = load_flow(path)
    if flow is None:
        return None
    status = effective_status(flow, now)
    if status == "waiting":
        flow["status"] = status = "cancelled"
        _write(path, flow)
    return {**flow, "status": status}
