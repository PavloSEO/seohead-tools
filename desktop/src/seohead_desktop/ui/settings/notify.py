"""Settings → Уведомления (sheet SetNotify)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "notify", "notifications", "Уведомления"
HINT = "Что и когда показывать"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
