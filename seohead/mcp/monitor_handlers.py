"""Isolated adapters for explicit one-shot project monitoring."""

from __future__ import annotations

from typing import Any


def monitor_collect(directory: str, expected_revision: int, apply: bool = False) -> dict[str, Any]:
    """Preview or explicitly run one claimed bounded monitor collection pass."""
    from seohead.projects.monitoring import collect_once

    return collect_once(directory, expected_revision=expected_revision, apply=apply)


def monitor_local_deliver(directory: str, scan_id: str, expected_revision: int) -> dict[str, Any]:
    """Record a local monitor delivery receipt; no external transport is used."""
    from seohead.projects.monitoring import local_deliver

    return local_deliver(directory, scan_id=scan_id, expected_revision=expected_revision)
