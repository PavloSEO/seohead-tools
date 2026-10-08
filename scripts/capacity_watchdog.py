"""Supervise one owned capacity stage without deleting its retained evidence."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


def _rss_bytes(pid: int) -> int:
    """Read the stage worker's RSS; unavailable telemetry fails the gate closed."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1010, False, pid)
        if not handle:
            raise OSError("cannot sample stage process")
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise OSError("cannot sample stage RSS")
            return int(counters.WorkingSetSize)
        finally:
            kernel.CloseHandle(handle)
    sampled = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if not sampled.stdout.strip():
        raise OSError("cannot sample stage RSS")
    return int(sampled.stdout.strip()) * 1024


def _directory_bytes(directory: Path) -> int:
    total = 0
    for parent, _, files in os.walk(directory):
        for name in files:
            path = Path(parent) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # Atomic report publication can rename a file during a sample.
    return total


def supervise(
    command: list[str],
    *,
    cwd: Path,
    output: Path,
    disk_dir: Path,
    env: dict[str, str] | None = None,
    max_seconds: float = 1800,
    max_rss_mib: float = 4096,
    max_disk_mib: float = 32768,
    min_free_mib: float = 12288,
    poll_seconds: float = 1,
    measured_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Run one child with declared limits; SIGINT first preserves native checkpoints.

    ``output`` holds watchdog.json and worker.log. ``disk_dir`` is the complete
    stage tree, including temporary report spools; no file is removed on failure.
    RSS covers this worker process, not unrelated work or external browser trees.
    The worker also records its operating-system peak RSS for the final gate.
    """
    import math

    budgets = dict(
        max_seconds=max_seconds,
        max_rss_mib=max_rss_mib,
        max_disk_mib=max_disk_mib,
        min_free_mib=min_free_mib,
    )
    if any(not math.isfinite(value) or value <= 0 for value in budgets.values()):
        raise ValueError("capacity budgets must be finite and positive")
    if not 0 < poll_seconds <= 5:
        raise ValueError("capacity sample interval must be in (0, 5]")
    if output.exists() and any(output.iterdir()):
        raise ValueError("watchdog output must be empty or new; preserve the previous receipt")
    output.mkdir(parents=True, exist_ok=True)
    disk_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    receipt: dict[str, Any] = {
        "schema": "seohead.capacity-watchdog.v1",
        "status": "running",
        "command": command,
        "cwd": str(cwd),
        "budgets": budgets,
        "rss_scope": "stage worker process",
        "sample_interval_seconds": poll_seconds,
        "peak_sampled_rss_bytes": 0,
        "peak_stage_disk_bytes": 0,
        "minimum_free_bytes": shutil.disk_usage(disk_dir).free,
        "samples": 0,
        "measured_paths": {name: str(path) for name, path in (measured_paths or {}).items()},
        "peak_measured_path_bytes": {name: 0 for name in (measured_paths or {})},
    }

    def persist() -> None:
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        temporary = output / ".watchdog.tmp"
        temporary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "watchdog.json")

    def sample(pid: int | None = None) -> str | None:
        disk = _directory_bytes(disk_dir)
        free = shutil.disk_usage(disk_dir).free
        rss = _rss_bytes(pid) if pid is not None else 0
        receipt["samples"] += 1
        receipt["peak_sampled_rss_bytes"] = max(receipt["peak_sampled_rss_bytes"], rss)
        receipt["peak_stage_disk_bytes"] = max(receipt["peak_stage_disk_bytes"], disk)
        receipt["minimum_free_bytes"] = min(receipt["minimum_free_bytes"], free)
        for name, path in (measured_paths or {}).items():
            try:
                size = _directory_bytes(path) if path.is_dir() else path.stat().st_size
            except FileNotFoundError:
                size = 0
            receipt["peak_measured_path_bytes"][name] = max(
                receipt["peak_measured_path_bytes"][name], size
            )
        for exceeded, reason in (
            (free < min_free_mib * 2**20, "free_disk_reserve"),
            (disk > max_disk_mib * 2**20, "stage_disk_budget"),
            (rss > max_rss_mib * 2**20, "rss_budget"),
            (time.monotonic() - started > max_seconds, "wall_time_budget"),
        ):
            if exceeded:
                return reason
        return None

    reason = sample()
    persist()
    if reason is None:
        with (output / "worker.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log)
            receipt["pid"] = process.pid
            try:
                while process.poll() is None:
                    try:
                        reason = sample(process.pid)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        if process.poll() is not None:
                            break
                        reason = "resource_telemetry_unavailable"
                    persist()
                    if reason:
                        break
                    time.sleep(poll_seconds)
            except BaseException:
                reason = "supervisor_interrupted"
                raise
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                receipt["returncode"] = process.returncode
                reason = reason or sample()
                receipt["status"] = (
                    "blocked" if reason else ("passed" if process.returncode == 0 else "failed")
                )
                receipt["reason"] = reason or "worker_exit"
                persist()
    else:
        receipt.update(status="blocked", reason=reason, returncode=None)
        persist()
    return receipt
