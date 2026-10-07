"""Locate the bundled, version-pinned SEOHEAD core without absolute paths."""

from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from typing import Iterable


def _resource_roots(executable: Path | None = None) -> Iterable[Path]:
    """Yield possible PyInstaller resource locations in deterministic order."""
    executable = executable or Path(sys.executable).resolve()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        yield Path(meipass)
    yield executable.parent / "Resources"
    yield executable.parent.parent / "Resources"
    yield executable.parent / "resources"


def bundled_core_manifest(executable: Path | None = None) -> tuple[Path, dict] | None:
    """Return the manifest next to a frozen desktop bundle, if present and valid."""
    for root in _resource_roots(executable):
        manifest = root / "core-manifest.json"
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema") != "seohead.desktop.core-manifest.v1":
            continue
        if not isinstance(payload.get("core"), dict):
            continue
        return manifest, payload
    return None


def bundled_core_cli(executable: Path | None = None) -> Path | None:
    """Return a bundled CLI only when its manifest identity verifies."""
    identity = verified_bundled_core_identity(executable)
    return None if identity is None else identity["cli"]


def verified_bundled_core_identity(executable: Path | None = None) -> dict | None:
    """Verify the co-shipped core path, recorded commit, and executable digest."""
    located = bundled_core_manifest(executable)
    if located is None:
        return None
    manifest_path, payload = located
    relative = payload["core"].get("cli_relpath")
    if not isinstance(relative, str) or not relative:
        return None
    candidate = (manifest_path.parent / relative).resolve()
    try:
        candidate.relative_to(manifest_path.parent.resolve())
    except ValueError:
        return None
    commit = payload["core"].get("commit")
    expected_hash = payload["core"].get("cli_sha256")
    if not candidate.is_file() or not isinstance(commit, str) or len(commit) != 40:
        return None
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        return None
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if digest != expected_hash:
        return None
    return {"cli": candidate, "commit": commit, "manifest": manifest_path, "payload": payload}


def package_arguments(arguments: list[str], core_cli: Path | None = None) -> list[str]:
    """Add the bundled core only when callers did not choose another executable."""
    if "--core-cli" in arguments:
        return arguments
    core_cli = bundled_core_cli() if core_cli is None else core_cli
    return arguments if core_cli is None else ["--core-cli", str(core_cli), *arguments]
