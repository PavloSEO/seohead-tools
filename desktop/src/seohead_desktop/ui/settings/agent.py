"""Settings → Агент (sheet SetAgent)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "agent", "smart_toy", "Агент"
HINT = "Подключение и права агента"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
