"""Shared local MCP state and permissioned, reversible client registration.

Only SEOHEAD's entry is returned to callers. Foreign configuration is opaque:
parse it to preserve it, never include values or parser diagnostics in errors.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from seohead.core.filesystem import fsync_directory, lock_exclusive, open_lock, unlock
from seohead.mcp.mcp_profiles import PROFILE_LABELS, PROFILES, profile_tools

SERVER = "seohead"
CLIENTS = ("claude-code", "claude-desktop", "codex", "cursor")
DISABLED = "MCP выключен пользователем (MCP disabled by the user)"


def state_path() -> Path:
    root = os.environ.get("SEOHEAD_CONFIG_DIR")
    return (
        Path(root) / "mcp-state.json"
        if root
        else Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "seohead"
        / "mcp-state.json"
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError("configuration symlinks are not supported")
    if not path.exists():
        return b""
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024:
        raise ValueError("configuration must be a bounded, unaliased regular file")
    return path.read_bytes()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) & 0o600 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".seohead-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.chmod(temporary, mode or 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open_lock(path.with_name(path.name + ".seohead.lock"))
    try:
        lock_exclusive(fd)
        yield
    finally:
        unlock(fd)
        os.close(fd)


def read_state() -> dict:
    raw = _read(state_path())
    if not raw:
        return {"enabled": True, "profile": "full", "by": None, "changed_at": None, "history": []}
    try:
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or type(state["enabled"]) is not bool
            or state["profile"] not in PROFILES
        ):
            raise ValueError
        if not isinstance(state.get("history", []), list):
            raise ValueError
        return state
    except (ValueError, KeyError, TypeError):
        raise ValueError("invalid SEOHEAD MCP state; repair or restore its backup") from None


def set_state(enabled: bool, *, profile: str | None = None, actor: str = "CLI") -> dict:
    if profile is not None and profile not in PROFILES:
        raise ValueError("unknown MCP profile")
    if actor not in {"CLI", "SEOHEAD Desktop"}:
        raise ValueError("unknown MCP state actor")
    path = state_path()
    with _locked(path):
        state = read_state()
        state.update(
            enabled=enabled, profile=profile or state["profile"], by=actor, changed_at=_stamp()
        )
        state.setdefault("history", []).append(
            {k: state[k] for k in ("enabled", "profile", "by", "changed_at")}
        )
        _atomic(path, json.dumps(state, indent=2).encode())
    return state


def require_enabled() -> None:
    if not read_state()["enabled"]:
        raise ValueError(DISABLED)


def client_path(client: str) -> Path:
    home = Path.home()
    if client == "claude-code":
        return home / ".claude.json"
    if client == "claude-desktop":
        if sys.platform == "darwin":
            return home / "Library/Application Support/Claude/claude_desktop_config.json"
        if os.name == "nt":
            return (
                Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
                / "Claude/claude_desktop_config.json"
            )
        return home / ".config/Claude/claude_desktop_config.json"
    if client == "codex":
        return Path(os.environ.get("CODEX_HOME", str(home / ".codex"))) / "config.toml"
    if client == "cursor":
        return home / ".cursor/mcp.json"
    raise ValueError("unsupported MCP client")


def _parse(client: str, raw: bytes):
    try:
        if client == "codex":
            import tomlkit

            doc = tomlkit.parse(raw.decode())
        else:
            doc = json.loads(raw) if raw else {}
        if not isinstance(doc, dict):
            raise ValueError
        key = "mcp_servers" if client == "codex" else "mcpServers"
        if key in doc and not isinstance(doc[key], dict):
            raise ValueError
        return doc, key
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("client configuration is invalid; no changes made") from None


def _serialize(client: str, doc) -> bytes:
    if client == "codex":
        import tomlkit

        return tomlkit.dumps(doc).encode()
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()


def _entry(command: str | None = None) -> dict:
    if command is None:
        if getattr(sys, "frozen", False):
            return {"command": sys.executable, "args": ["mcp"]}
        return {"command": sys.executable, "args": ["-m", "seohead", "mcp"]}
    if not Path(command).is_absolute():
        raise ValueError("MCP command must be an absolute executable path")
    return {"command": command, "args": ["mcp"]}


def changed_label(by: str | None, changed_at: str | None, *, now: datetime | None = None) -> str:
    """Who switched MCP last and when (Russian caption); the default caption before any switch."""
    if not by or not changed_at:
        return "по умолчанию"
    try:
        moment = datetime.fromisoformat(changed_at).astimezone()
    except ValueError:
        return by
    today = (now or datetime.now().astimezone()).date()
    day = "сегодня" if moment.date() == today else moment.strftime("%d.%m")
    return f"{by} · {day} {moment:%H:%M}"


def status(*, inspect_clients: bool = True) -> dict:
    from seohead.mcp.tool_reference import load_seo_tools, load_sf_tools

    state = read_state()
    names = {t.name for t in [*load_seo_tools(), *load_sf_tools()]}

    def count(profile: str) -> int:
        allowed = profile_tools(profile)
        return len(names if allowed is None else names & allowed)

    result = {k: state.get(k) for k in ("enabled", "profile", "by", "changed_at")}
    result.update(
        ok=True,
        file=str(state_path()),
        tools=count(state["profile"]),
        by_label=changed_label(state.get("by"), state.get("changed_at")),
        profiles=[
            {"id": name, "label": PROFILE_LABELS[name], "tools": count(name)}
            for name in PROFILE_LABELS
        ],
        clients={},
    )
    if inspect_clients:
        for client in CLIENTS:
            path = client_path(client)
            try:
                doc, key = _parse(client, _read(path))
                info = {"registered": SERVER in doc.get(key, {}), "file": str(path)}
            except (OSError, ValueError):
                info = {
                    "registered": None,
                    "file": str(path),
                    "error": "configuration unavailable or invalid",
                }
            result["clients"][client] = info
    return result


def install(
    client: str,
    *,
    dry_run: bool = False,
    yes: bool = False,
    command: str | None = None,
    expected_sha256: str | None = None,
    backup_path: str | None = None,
) -> dict:
    if not dry_run and not yes:
        raise ValueError("writing a client configuration requires --yes; preview with --dry-run")
    path = client_path(client)
    # Preview does not create directories, lock files or backups.
    if dry_run:
        return _install(
            client,
            path,
            dry_run=True,
            command=command,
            expected_sha256=expected_sha256,
            backup_path=backup_path,
        )
    with _locked(path):
        return _install(
            client,
            path,
            dry_run=False,
            command=command,
            expected_sha256=expected_sha256,
            backup_path=backup_path,
        )


def _install(client, path, *, dry_run, command, expected_sha256, backup_path):
    raw = _read(path)
    before = _digest(raw)
    if expected_sha256 is not None and expected_sha256 != before:
        raise ValueError("configuration changed since preview; preview again before approving")
    doc, key = _parse(client, raw)
    own = _entry(command)
    existing = doc.get(key, {}).get(SERVER)
    if existing is not None and not isinstance(existing, dict):
        raise ValueError("existing SEOHEAD entry must be an object; configuration unchanged")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.seohead-{timestamp}-{before[:12]}.bak")
    if backup_path is not None:
        import re

        candidate = Path(backup_path)
        if candidate.parent != path.parent or not re.fullmatch(
            re.escape(path.name) + r"\.seohead-\d{8}T\d{12}Z-" + before[:12] + r"\.bak",
            candidate.name,
        ):
            raise ValueError("backup must match the reviewed adjacent timestamp/hash path")
        backup = candidate
    fragment = {key: {SERVER: own}}
    if client == "codex":
        import tomlkit

        fragment = tomlkit.dumps(fragment)
    else:
        fragment = json.dumps(fragment, indent=2)
    result = {
        "ok": True,
        "client": client,
        "file": str(path),
        "block": fragment,
        "backup": str(backup),
        "sha256": before,
        "changed": existing != own,
        "operation": "add" if existing is None else "none" if existing == own else "replace",
        "dry_run": dry_run,
    }
    if dry_run or existing == own:
        return result
    existed = path.exists()
    changed = copy.deepcopy(doc)
    changed.setdefault(key, {})[SERVER] = own
    after = _serialize(client, changed)
    _parse(client, after)
    if backup.exists() or backup.is_symlink():
        raise ValueError("backup path already exists; preview again")
    _atomic(backup, raw)
    if _digest(_read(backup)) != before:
        raise ValueError("backup verification failed; configuration unchanged")
    receipt = backup.with_suffix(backup.suffix + ".json")
    _atomic(
        receipt,
        json.dumps(
            {
                "client": client,
                "reason": "install",
                "sha256": before,
                "installed_sha256": _digest(after),
                "existed": existed,
                "entry": own,
            }
        ).encode(),
    )
    if _digest(_read(path)) != before:
        raise ValueError("configuration changed during installation; configuration unchanged")
    _atomic(path, after)
    _parse(client, _read(path))
    return result


def backups(client: str | None = None) -> dict:
    """Adjacent configuration backups made before SEOHEAD registration, newest first."""
    if client is not None and client not in CLIENTS:
        raise ValueError("unsupported MCP client")
    items = []
    for name in [client] if client else CLIENTS:
        path = client_path(name)
        for backup in path.parent.glob(path.name + ".seohead-*.bak"):
            stamp = backup.name[len(path.name) + len(".seohead-") :].split("-", 1)[0]
            try:
                created = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
                receipt = json.loads(_read(backup.with_suffix(backup.suffix + ".json")))
                digest = _digest(_read(backup))
            except (OSError, ValueError):
                continue  # Not ours or unreadable: never list foreign or damaged files.
            items.append(
                {
                    "client": name,
                    "path": str(backup),
                    "created_at": created.isoformat(),
                    "reason": receipt.get("reason", "install"),
                    "sha256": digest,
                    "verified": receipt.get("sha256") == digest,
                }
            )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return {"ok": True, "backups": items}


def uninstall(client: str, *, yes: bool = False) -> dict:
    if not yes:
        raise ValueError("restoring a client configuration requires --yes")
    path = client_path(client)
    with _locked(path):
        backups = sorted(path.parent.glob(path.name + ".seohead-*.bak"), reverse=True)
        if not backups:
            raise ValueError("no SEOHEAD backup exists for this client")
        backup = backups[0]
        raw = _read(backup)
        try:
            receipt = json.loads(_read(backup.with_suffix(backup.suffix + ".json")))
        except ValueError:
            raise ValueError("backup receipt is invalid; configuration unchanged") from None
        if receipt.get("client") != client or receipt.get("sha256") != _digest(raw):
            raise ValueError("backup hash verification failed; configuration unchanged")
        current_raw = _read(path)
        current, key = _parse(client, current_raw)
        previous, _ = _parse(client, raw)
        if current.get(key, {}).get(SERVER) != receipt.get("entry"):
            raise ValueError("SEOHEAD entry changed after installation; configuration unchanged")
        if _digest(current_raw) == receipt.get("installed_sha256"):
            restored = raw
        else:
            # Restore only our server. Preserve later changes to foreign entries and keys.
            prior = previous.get(key, {}).get(SERVER)
            if prior is None:
                current[key].pop(SERVER)
            else:
                current[key][SERVER] = prior
            restored = _serialize(client, current)
        _parse(client, restored)
        if receipt.get("existed") is False and _digest(current_raw) == receipt.get(
            "installed_sha256"
        ):
            # Restore absence, rather than leave an invalid empty JSON configuration.
            path.unlink()
            fsync_directory(path.parent)
        else:
            _atomic(path, restored)
            _parse(client, _read(path))
    return {
        "ok": True,
        "client": client,
        "file": str(path),
        "backup": str(backup),
        "restored": True,
    }
