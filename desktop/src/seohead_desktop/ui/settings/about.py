"""Settings → О программе (sheet SetAbout)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "about", "info", "О программе"
HINT = "Версия, лицензии, диагностика"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
