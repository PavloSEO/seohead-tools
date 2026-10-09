"""Helpers shared by the settings section tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

from seohead_desktop import theming
from seohead_desktop.settings_store import AppSettings
from seohead_desktop.ui.controls import SettingRow
from seohead_desktop.ui.settings import full_schema
from seohead_desktop.ui.settings.context import SettingsContext
from seohead_desktop.ui.settings.dialog import SettingsDialog


def app():
    qapp = QApplication.instance() or QApplication([])
    theming.set_active_theme("light")
    return qapp


def all_text(widget):
    texts = [label.text() for label in widget.findChildren(QLabel)]
    texts += [button.text() for button in widget.findChildren(QPushButton)]
    return "\n".join(texts)


def make(section, context=None):
    store = AppSettings(schema=full_schema())
    dialog = SettingsDialog(store, context or SettingsContext(), section=section)
    return store, dialog, dialog._pages[section][1]


def row(page, title):
    return next(r for r in page.findChildren(SettingRow) if r.title.text() == title)
