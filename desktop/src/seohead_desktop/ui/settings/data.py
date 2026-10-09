"""Settings → Хранилище и данные (sheet SetData)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "data", "database", "Хранилище и данные"
HINT = "Где лежат данные и сколько места они занимают"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
