"""Contract tests for the shared atomic publish helper in ``seohead.core.filesystem``."""

from __future__ import annotations

import os
import stat

import pytest

from seohead.core import filesystem
from seohead.core.filesystem import atomic_write_bytes


def test_publishes_bytes_with_requested_mode_and_no_leftover_temp(tmp_path):
    target = tmp_path / "state.json"

    atomic_write_bytes(target, b"one")
    atomic_write_bytes(target, b"two", mode=0o640)

    assert target.read_bytes() == b"two"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.json"]


def test_failed_replace_keeps_previous_content_and_removes_temp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_bytes(b"old")

    def refuse(source, destination):
        raise OSError("replace refused")

    monkeypatch.setattr(filesystem.os, "replace", refuse)

    with pytest.raises(OSError, match="replace refused"):
        atomic_write_bytes(target, b"new")

    assert target.read_bytes() == b"old"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.json"]
