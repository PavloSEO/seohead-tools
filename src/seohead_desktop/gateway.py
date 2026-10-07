"""Bounded local CLI adapter; the desktop never reimplements SEOHEAD core."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal


# These are the only commands the presentation adapter may invoke. All reads
# are bounded by the core contracts; the one writer records an explicit note.
READ_COMMANDS = frozenset(
    {
        "project-open",
        "project-status",
        "project-progress",
        "project-observe",
        "project-checklist-page",
        "project-task-detail",
        "project-scans",
        "project-activity",
        "project-inbox-list",
        "project-inbox-unread",
        "scan-inspect",
        "scan-navigation",
    }
)
WRITE_COMMANDS = frozenset({"project-inbox-submit"})
MAX_RESPONSE_BYTES = 3 * 1024 * 1024


class Signals(QObject):
    """Thread-safe result transport back to the Qt owner thread."""

    loaded = pyqtSignal(str, dict, int)
    failed = pyqtSignal(str, str, int)
    cancelled = pyqtSignal(str, int)


def command_argv(executable: str, command: str, arguments: Iterable[str]) -> list[str]:
    """Return a declared core invocation, never a shell command."""
    if command not in READ_COMMANDS | WRITE_COMMANDS:
        raise ValueError(f"unsupported core command: {command}")
    if not executable:
        raise ValueError("core CLI executable is required")
    values = list(arguments)
    if any(not isinstance(value, str) or "\x00" in value for value in values):
        raise ValueError("core arguments must be text without NUL bytes")
    return [executable, command, *values]


class CoreCommand(QRunnable):
    """Run one declared local-core request outside the GUI event loop.

    Cancellation terminates only this child process. It never kills a scan:
    scan dispatch is deliberately absent from this adapter.
    """

    def __init__(
        self,
        executable: str,
        command: str,
        arguments: Iterable[str],
        *,
        request_id: str,
        generation: int,
        timeout_seconds: float = 15,
    ):
        super().__init__()
        self.argv = command_argv(executable, command, arguments)
        self.request_id = request_id
        self.generation = generation
        self.timeout_seconds = timeout_seconds
        self.signals = Signals()
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self) -> None:
        try:
            with self._lock:
                if self._cancelled.is_set():
                    self.signals.cancelled.emit(self.request_id, self.generation)
                    return
                self._process = subprocess.Popen(
                    self.argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                process = self._process
            deadline = time.monotonic() + self.timeout_seconds
            while process.poll() is None:
                if self._cancelled.is_set():
                    process.terminate()
                    process.wait(timeout=2)
                    self.signals.cancelled.emit(self.request_id, self.generation)
                    return
                if time.monotonic() >= deadline:
                    process.terminate()
                    process.wait(timeout=2)
                    raise TimeoutError("local core request exceeded 15 seconds")
                time.sleep(0.03)
            stdout, stderr = process.communicate()
            if self._cancelled.is_set():
                self.signals.cancelled.emit(self.request_id, self.generation)
                return
            if process.returncode:
                raise RuntimeError((stderr or stdout or "core request failed").strip())
            if len(stdout.encode()) > MAX_RESPONSE_BYTES:
                raise ValueError("core response exceeds the 3 MiB presentation limit")
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise TypeError("unexpected core response format")
            self.signals.loaded.emit(self.request_id, result, self.generation)
        except (OSError, TypeError, ValueError, subprocess.SubprocessError, TimeoutError) as exc:
            if self._cancelled.is_set():
                self.signals.cancelled.emit(self.request_id, self.generation)
            else:
                self.signals.failed.emit(self.request_id, str(exc), self.generation)
        finally:
            with self._lock:
                self._process = None


class ProjectRead(CoreCommand):
    """Compatibility helper for the initial project-open action."""

    def __init__(self, executable: str, directory: str, generation: int = 0):
        if not (Path(directory) / "project.json").is_file():
            raise ValueError("В папке нет project.json SEOHEAD")
        super().__init__(
            executable,
            "project-open",
            ("--directory", directory),
            request_id="project-open",
            generation=generation,
        )
