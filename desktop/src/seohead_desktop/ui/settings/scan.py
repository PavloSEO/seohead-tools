"""Settings → Сканы по умолчанию (sheet SetScan)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "scan", "manage_search", "Сканы по умолчанию"
HINT = "Подставляются в «Новый скан»; профиль проекта важнее"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
