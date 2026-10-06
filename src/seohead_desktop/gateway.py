"""Read-only CLI seam; the desktop does not duplicate the SEO engine."""

import json
import subprocess
from pathlib import Path

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal


class Signals(QObject):
    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)


class ProjectRead(QRunnable):
    def __init__(self, executable, directory):
        super().__init__()
        self.executable = executable
        self.directory = directory
        self.signals = Signals()

    def run(self):
        try:
            if not (Path(self.directory) / "project.json").is_file():
                raise ValueError("В папке нет project.json SEOHEAD")
            response = subprocess.run(
                [self.executable, "project-open", "--directory", self.directory],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            if len(response.stdout.encode()) > 1_048_576:
                raise ValueError("Ответ проекта превышает лимит 1 MiB")
            result = json.loads(response.stdout)
            if not isinstance(result, dict):
                raise TypeError("Неожиданный формат ответа проекта")
            self.signals.loaded.emit(result)
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            self.signals.failed.emit(str(exc))
