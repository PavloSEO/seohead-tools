"""The one place that creates the QApplication."""

from PyQt5.QtWidgets import QApplication

_app = None


def app(argv=None):
    """The process-wide QApplication: the existing instance or a single new one (kept referenced for the whole run)."""
    global _app
    instance = QApplication.instance()
    if instance is not None:
        return instance
    _app = QApplication(list(argv) if argv is not None else [])
    return _app
