"""Settings → Обновления (sheet SetUpdates)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "updates", "system_update", "Обновления"
HINT = "Версия и проверка обновлений"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
