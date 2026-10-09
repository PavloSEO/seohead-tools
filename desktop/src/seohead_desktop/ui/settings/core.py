"""Settings → Ядро (sheet SetCore)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "core", "memory", "Ядро"
HINT = "Приложение — оболочка над ядром seohead; CLI той же версии"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
