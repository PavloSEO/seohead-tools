"""Settings → Горячие клавиши (sheet SetKeys)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "keys", "keyboard", "Горячие клавиши"
HINT = "Сочетания клавиш"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
