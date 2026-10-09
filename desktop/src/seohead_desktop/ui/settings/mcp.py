"""Settings → MCP-сервер (sheet SetMcp). State is shown only from ``context.request("mcp_status")``; no config is written here."""

from __future__ import annotations

from PyQt5.QtWidgets import QHBoxLayout, QWidget

from ...settings_store import Setting
from ..controls import Note, SettingRow, Switch
from .helpers import group_label, keyed, page, segmented_row, switch_row
from .listing import Columns, action_button, badge, hint, list_item, mono_html, no_data, terminal

ID, ICON, TITLE = "mcp", "hub", "MCP-сервер"
HINT = "Доступ агентов к проектам через локальный MCP"

PROFILES = ("full", "audit", "quick")

SCHEMA = (
    Setting("mcp.enabled", True, bool),
    Setting("mcp.profile", "full", str, choices=PROFILES),
    Setting("mcp.journal", True, bool),
)

CLIENTS = (
    ("claude-code", "Claude Code", "~/.claude.json"),
    ("claude-desktop", "Claude Desktop", "claude_desktop_config.json"),
    ("codex", "Codex", "~/.codex/config.toml"),
    ("cursor", "Cursor", "~/.cursor/mcp.json"),
)


def _status(context):
    status = context.request("mcp_status")
    return status if isinstance(status, dict) else None


def _server_row(store, context, status):
    switch = Switch("MCP-сервер", bool(store.get("mcp.enabled")))

    def toggled(checked):
        store.set("mcp.enabled", checked)
        context.request("mcp_set_enabled", enabled=checked)

    switch.toggled.connect(toggled)
    if status is None or "enabled" not in status:
        state = badge("mut", "нет данных", "help")
    elif status["enabled"]:
        state = badge("ok", "включён", "check_circle")
    else:
        state = badge("mut", "выключен", "pause_circle")
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(state)
    layout.addWidget(switch)
    tools = status.get("tools") if status else None
    return keyed(SettingRow("Локальный MCP-сервер", "stdio · без сети" + (f" · {tools} инструментов" if tools else ""), box), "mcp.enabled")


def _console(status):
    if status is None:
        return terminal("seohead mcp status", ["Нет данных"])
    enabled = status.get("enabled")
    head = "● " + {True: "включён", False: "выключен"}.get(enabled, "нет данных")
    lines = [head + (f" · профиль {status['profile']} · кем: {status['by']}" if status.get("profile") and status.get("by") else "")]
    registered = [name for cid, name, _path in CLIENTS if (status.get("clients") or {}).get(cid, {}).get("registered")]
    if registered:
        lines.append("клиенты: " + ", ".join(registered))
    return terminal("seohead mcp status", lines)


def _client_row(client, status, context):
    cid, name, path = client
    clients = (status or {}).get("clients")
    info = clients.get(cid) if isinstance(clients, dict) else None
    if info is None:
        state, button = badge("mut", "нет данных", "help"), action_button("Прописать…", role="tonal", size="pill", enabled=False)
        sub = ""
    elif info.get("registered"):
        state = badge("ok", "прописано", "check_circle")
        button = action_button("Изменить…", role="text", size="pill", enabled=False)
        sub = f"был {info['last_seen']}" if info.get("last_seen") else ""
    else:
        state = badge("mut", "не прописано", "remove")
        button = action_button("Прописать…", role="tonal", size="pill", enabled=False)
        sub = ""
    return list_item("terminal", name, sub, state, button, sub_mono=path)


def _backups(context):
    backups = context.request("mcp_backups")
    if not isinstance(backups, list):
        return [no_data()]
    if not backups:
        return [no_data("Бэкапов нет")]
    return [list_item("history", b.get("name", ""), b.get("when", ""), action_button("Восстановить", role="text", size="pill", enabled=False), title_mono=True)
            for b in backups]


def build_page(store, context):
    status = _status(context)
    left = [
        _server_row(store, context, status),
        segmented_row(store, "mcp.profile", "Профиль инструментов", "full — все; audit — чтение и аудит; quick — быстрые проверки",
                      [(p, p) for p in PROFILES]),
        switch_row(store, "mcp.journal", "Писать журнал вызовов", "Каждый вызов инструмента — в журнал агента"),
        _console(status),
        hint(f"Одно состояние для приложения и CLI: {mono_html('seohead mcp disable')} выключит переключатель здесь.", rich=True),
        Note("info", "Что это меняет.", "Что видят агенты. Выключение отключает инструменты у всех клиентов сразу; конфиги клиентов не трогаются."),
    ]
    right = [
        group_label("Клиенты"),
        *(_client_row(client, status, context) for client in CLIENTS),
        group_label("Бэкапы конфигов"),
        *_backups(context),
        hint(f"Перед каждой пропиской конфиг клиента копируется в {mono_html('~/Work/backups/mcp/')}", rich=True),
    ]
    return page(Columns(left, right))
