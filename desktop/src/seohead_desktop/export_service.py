"""Scan export through the core CLI (``seohead scan-export``) on a QThreadPool worker, shaped like source_service.

Only fixed flags are built here: the scan path, one output file in the project's ``exports`` folder, one format and one
record type. The core validates everything else and refuses bad input with ``{"ok": false, "error": ...}``; that text
is passed on as it is. Core stderr is never forwarded, and no field selection is sent (the column list is not wired).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

FORMATS = ("csv", "xlsx", "json", "xml")
RECORDS = {"url": "pages", "issues": "findings"}  # screen dataset id -> core record type
MISSING_CORE = "CLI ядра seohead не найден"
UNREADABLE = "Ядро не вернуло результат экспорта. Повторите попытку."
NO_FOLDER = "Не удалось создать папку экспорта"
TIMEOUT = 600


def output_name(dataset, run_label, fmt):
    return f"{dataset}_{run_label}.{fmt}"


def build_argv(executable, scan, out, fmt, records):
    if fmt not in FORMATS or records not in RECORDS.values():
        raise ValueError(UNREADABLE)
    return [
        executable,
        "scan-export",
        "--scan",
        scan,
        "--out",
        out,
        "--format",
        fmt,
        "--records",
        records,
    ]


def run_core(argv):
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    return completed.stdout


def export_scan(executable, scan, folder, name, fmt, records, *, run=run_core):
    """Return ``{"files", "fmt"}`` on success; raise ValueError with the message the screen shows otherwise."""
    if not executable:
        raise ValueError(MISSING_CORE)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise ValueError(NO_FOLDER) from exc
    stdout = run(build_argv(executable, scan, str(Path(folder) / name), fmt, records))
    try:
        value = json.loads(stdout)
    except ValueError as exc:
        raise ValueError(UNREADABLE) from exc
    if not isinstance(value, dict):
        raise ValueError(UNREADABLE)
    if value.get("ok") is not True:
        error = value.get("error")
        raise ValueError(error if isinstance(error, str) and error else UNREADABLE)
    files = value.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(path, str) for path in files):
        raise ValueError(UNREADABLE)
    return {"files": files, "fmt": fmt}


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class _Export(QRunnable):
    def __init__(self, args, run):
        super().__init__()
        self.signals = _Signals()
        self.args = args
        self.run_core = run

    def run(self):
        try:
            self.signals.done.emit(export_scan(*self.args, run=self.run_core))
        except ValueError as exc:
            self.signals.failed.emit(str(exc))
        except (OSError, subprocess.SubprocessError):
            self.signals.failed.emit(UNREADABLE)


class ExportService(QObject):
    """Owns the export workers and drops answers addressed to a screen that is already gone."""

    def __init__(self, executable, parent=None, run=run_core):
        super().__init__(parent)
        self.executable = executable
        self.run_core = run
        self._pending = {}
        self._serial = 0

    def request(self, scan, folder, name, fmt, records, on_done, on_failed, owner):
        if not self.executable:
            on_failed(MISSING_CORE)
            return
        self._serial += 1
        serial = self._serial
        worker = self._pending[serial] = _Export(
            (self.executable, scan, folder, name, fmt, records), self.run_core
        )

        def deliver(handler, value):
            self._pending.pop(serial, None)
            if not sip.isdeleted(owner):
                handler(value)

        worker.signals.done.connect(lambda value: deliver(on_done, value))
        worker.signals.failed.connect(lambda value: deliver(on_failed, value))
        QThreadPool.globalInstance().start(worker)
