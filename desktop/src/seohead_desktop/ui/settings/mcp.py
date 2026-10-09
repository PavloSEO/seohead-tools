"""Settings → MCP-сервер (sheet SetMcp)."""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel

ID, ICON, TITLE = "mcp", "hub", "MCP-сервер"
HINT = "Локальный сервер для агентов"
SCHEMA = ()


def build_page(store, context):
    return QLabel("В разработке")
