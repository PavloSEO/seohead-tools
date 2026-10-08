"""Small subprocess evidence for the manual capacity supervisor, without network."""

from __future__ import annotations

import json
import sys
from unittest.mock import Mock

import pytest

from scripts import capacity_watchdog as watchdog


def _run(tmp_path, code, **kwargs):
    return watchdog.supervise(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        output=tmp_path / "receipt",
        disk_dir=tmp_path,
        min_free_mib=1,
        poll_seconds=0.02,
        **kwargs,
    )


def test_success_keeps_command_evidence_and_file_peaks(tmp_path):
    data = tmp_path / "evidence.sqlite"
    result = _run(
        tmp_path,
        "from pathlib import Path; Path('evidence.sqlite').write_bytes(b'x' * 4096)",
        measured_paths={"database": data},
    )
    assert result["status"] == "passed"
    assert result["returncode"] == 0
    assert result["peak_measured_path_bytes"]["database"] == 4096
    assert json.loads((tmp_path / "receipt/watchdog.json").read_text()) == result


def test_wall_budget_interrupts_worker_and_preserves_checkpoint(tmp_path):
    result = _run(
        tmp_path,
        "from pathlib import Path; import time; Path('checkpoint').write_text('retained'); time.sleep(60)",
        max_seconds=0.2,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "wall_time_budget"
    assert (tmp_path / "checkpoint").read_text() == "retained"
    assert result["returncode"] != 0


def test_low_disk_refuses_before_starting_a_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog.shutil, "disk_usage", lambda _: Mock(free=0))
    result = _run(tmp_path, "raise AssertionError('must never launch')")
    assert result["status"] == "blocked"
    assert result["reason"] == "free_disk_reserve"
    assert result["returncode"] is None
    assert "pid" not in result


def test_existing_receipt_is_never_overwritten(tmp_path):
    output = tmp_path / "receipt"
    output.mkdir()
    existing = output / "watchdog.json"
    existing.write_text("preserved")
    with pytest.raises(ValueError, match="empty or new"):
        _run(tmp_path, "pass")
    assert existing.read_text() == "preserved"


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_budgets_fail_before_io(tmp_path, value):
    with pytest.raises(ValueError, match="finite and positive"):
        _run(tmp_path, "pass", max_seconds=value)
    assert not (tmp_path / "receipt").exists()
