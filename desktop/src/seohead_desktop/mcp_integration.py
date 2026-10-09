"""Permissioned MCP setup through the core CLI, always in a worker thread."""
from __future__ import annotations

import json
import subprocess

from PyQt5.QtCore import Qt, QThreadPool, QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr, trf
from .integration_worker import run_background
from .ui.controls import Switch
from .ui.icons import MaterialIconLabel

CLIENTS = (("claude-code", "Claude Code"), ("claude-desktop", "Claude Desktop"), ("codex", "Codex"), ("cursor", "Cursor"))


def call_core(executable, arguments):
    """Capture only the core's sanitized JSON; never expose arbitrary process stderr."""
    if not executable:
        raise ValueError(tr("CLI ядра seohead не найден"))
    try:
        completed = subprocess.run([executable, "mcp", *arguments, "--json"], capture_output=True, text=True, timeout=30, check=False)
        result = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError(tr("Ядро не отвечает или не поддерживает настройку MCP (#929).")) from None
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError((result.get("error") if isinstance(result, dict) else None) or tr("Ошибка настройки MCP"))
    return result


class PermissionDialog(QDialog):
    """Display the exact core plan before the user grants one configuration write."""
    def __init__(self, executable, client, parent=None, runner=call_core):
        super().__init__(parent)
        self.executable, self.client, self.runner = executable, client, runner
        self.plan = None
        self.busy = False
        self.setWindowTitle(tr("Разрешить запись MCP"))
        self.resize(640, 500)
        self.setMinimumSize(520, 400)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        heading = QHBoxLayout()
        heading.addWidget(MaterialIconLabel("hub", 24, color="role:primary"))
        title = QLabel(tr("Разрешить запись MCP"))
        title.setProperty("text_style", "dialog")
        heading.addWidget(title, 1)
        root.addLayout(heading)
        intro = QLabel(tr("Приложение добавит только сервер SEOHEAD. Остальные записи сохранятся. Перед записью ядро создаст бэкап с SHA-256."))
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName(tr("Файл, строки и бэкап MCP"))
        self.preview.setProperty("mono", True)
        root.addWidget(self.preview, 1)
        self.result_label = QLabel(tr("Подготовка плана…"))
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.PlainText)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.result_label)
        foot = QHBoxLayout()
        self.cancel = QPushButton(tr("Отмена"))
        self.cancel.setProperty("role", "text")
        self.cancel.clicked.connect(self.reject)
        self.allow = QPushButton(tr("Разрешить"))
        self.allow.setProperty("role", "primary")
        self.allow.setEnabled(False)
        self.allow.clicked.connect(self.approve)
        foot.addStretch(1)
        foot.addWidget(self.cancel)
        foot.addWidget(self.allow)
        root.addLayout(foot)
        self._run(["install", "--client", client, "--dry-run", "--command", executable], self._planned)

    def _run(self, arguments, callback):
        self.busy = True
        self.allow.setEnabled(False)
        run_background(QThreadPool.globalInstance(), lambda: self.runner(self.executable, arguments), lambda value: self._delivered(value, callback), self)

    def _delivered(self, value, callback):
        self.busy = False
        if isinstance(value, Exception):
            self.cancel.setEnabled(True)
            self.result_label.setText(str(value))
        else:
            callback(value)

    def _planned(self, plan):
        self.plan = plan
        self.preview.setPlainText(trf("Файл: {file}\n\n{block}\n\nБэкап: {backup}", **{k: plan[k] for k in ("file", "block", "backup")}))
        self.result_label.setText(tr("Запись произойдёт только после «Разрешить»."))
        self.allow.setEnabled(bool(plan.get("changed", True)))
        if not plan.get("changed", True):
            self.result_label.setText(tr("Сервер уже прописан. Изменений нет."))

    def approve(self):
        if self.plan is None or self.busy:
            return
        self.cancel.setEnabled(False)
        self._run(["install", "--client", self.client, "--yes", "--command", self.executable,
                   "--expected-sha256", self.plan["sha256"], "--backup", self.plan["backup"]], self._installed)

    def _installed(self, result):
        self.preview.setPlainText(trf("Файл: {file}\n\n{block}\n\nБэкап: {backup}", **{k: result[k] for k in ("file", "block", "backup")}))
        self.result_label.setText(trf("MCP прописан. Бэкап: {path}", path=result["backup"]))
        self.cancel.setEnabled(True)
        self.cancel.setText(tr("Готово"))
        self.cancel.clicked.disconnect()
        self.cancel.clicked.connect(self.accept)
        self.allow.hide()

    def reject(self):
        if not self.busy or self.plan is None:
            super().reject()

    def closeEvent(self, event):
        if self.busy and self.plan is not None:
            event.ignore()
        else:
            super().closeEvent(event)


class IntegrationPanel(QWidget):
    """One shared panel for SetMcp and the onboarding MCP step."""
    def __init__(self, executable, store=None, parent=None, runner=call_core, polling=True, compact=False):
        super().__init__(parent)
        self.executable, self.store, self.runner = executable, store, runner
        self.state = None
        self.busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        head = QHBoxLayout()
        self.switch = Switch(tr("Локальный MCP-сервер"), False)
        self.switch.setEnabled(False)
        self.switch.toggled.connect(self.toggle)
        head.addWidget(self.switch)
        self.state_label = QLabel(tr("Чтение состояния ядра…"))
        self.state_label.setProperty("text_style", "meta")
        self.state_label.setTextFormat(Qt.PlainText)
        head.addWidget(self.state_label, 1)
        root.addLayout(head)
        profiles = QHBoxLayout()
        profiles.addWidget(QLabel(tr("Профиль инструментов")))
        self.profile = QComboBox()
        self.profile.addItems(["full", "audit", "quick-check", "router", "infra"])
        self.profile.setEnabled(False)
        self.profile.currentTextChanged.connect(self.change_profile)
        profiles.addWidget(self.profile)
        profiles.addStretch(1)
        root.addLayout(profiles)
        explanation = QLabel(tr("stdio · без сети. Одно состояние для приложения и CLI. Выключение блокирует инструменты во всех клиентах. Запись в конфиги — только с разрешения."))
        explanation.setWordWrap(True)
        root.addWidget(explanation)
        self.clients = {}
        self.client_frames = {}
        if compact:
            self.client_choice = QComboBox()
            for cid, name in CLIENTS:
                self.client_choice.addItem(name, cid)
            self.client_choice.setAccessibleName(tr("Подключить к"))
            root.addWidget(self.client_choice)
            self.client_choice.currentIndexChanged.connect(self.choose_client)
        for cid, name in CLIENTS:
            frame = QFrame()
            frame.setProperty("card", "panel")
            row = QHBoxLayout(frame)
            row.setContentsMargins(12, 8, 12, 8)
            row.addWidget(QLabel(name))
            label = QLabel(tr("Нет данных"))
            label.setProperty("text_style", "meta")
            label.setTextFormat(Qt.PlainText)
            row.addWidget(label, 1)
            button = QPushButton(tr("Прописать…"))
            button.setProperty("role", "tonal")
            button.setEnabled(False)
            button.clicked.connect(lambda _checked, client=cid: self.connect_client(client))
            row.addWidget(button)
            restore = QPushButton(tr("Восстановить"))
            restore.setProperty("role", "text")
            restore.setEnabled(False)
            restore.clicked.connect(lambda _checked, client=cid: self.restore(client))
            row.addWidget(restore)
            root.addWidget(frame)
            self.clients[cid] = label, button, restore
            self.client_frames[cid] = frame
            if compact:
                restore.hide()
                frame.setVisible(cid == CLIENTS[0][0])
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.feedback)
        self.feedback.setVisible(not compact)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        if polling:
            self.timer.start()
    def choose_client(self):
        chosen = self.client_choice.currentData()
        for cid, frame in self.client_frames.items():
            frame.setVisible(cid == chosen)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _request(self, arguments, callback):
        if self.busy:
            return
        self.busy = True
        if arguments[0] != "status":
            self.switch.setEnabled(False)
            self.profile.setEnabled(False)
        run_background(QThreadPool.globalInstance(), lambda: self.runner(self.executable, arguments), lambda value: self._received(value, callback), self)

    def _received(self, value, callback):
        self.busy = False
        if isinstance(value, Exception):
            self.feedback.setText(str(value))
            self.switch.setEnabled(self.state is not None)
            self.profile.setEnabled(self.state is not None)
        else:
            callback(value)

    def refresh(self):
        if self.isVisible() or self.state is None:
            self._request(["status"], self.loaded)

    def loaded(self, state):
        self.state = state
        self.switch.blockSignals(True)
        self.switch.setChecked(bool(state["enabled"]))
        self.switch.blockSignals(False)
        self.profile.blockSignals(True)
        self.profile.setCurrentText(state["profile"])
        self.profile.blockSignals(False)
        self.switch.setEnabled(True)
        self.profile.setEnabled(True)
        self.state_label.setText(trf("{state} · {n} инструментов", state=tr("включён" if state["enabled"] else "выключен"), n=state["tools"]))
        if self.store:
            self.store.set("mcp.enabled", state["enabled"])
            if state["profile"] in self.store.definition("mcp.profile").choices:
                self.store.set("mcp.profile", state["profile"])
        for cid, (label, button, restore) in self.clients.items():
            info = state.get("clients", {}).get(cid, {})
            registered = info.get("registered")
            label.setText(tr("прописано" if registered else "не прописано" if registered is False else "Нет данных"))
            label.setToolTip(info.get("file", ""))
            button.setText(tr("Изменить…" if registered else "Прописать…"))
            button.setEnabled(registered is not None)
            restore.setEnabled(bool(registered))
        actor = state.get("by") or "CLI"
        self.feedback.setText(trf("Кем: {actor} · {when}\nСостояние: {file}", actor=actor, when=state.get("changed_at") or "—", file=state["file"]))

    def toggle(self, enabled):
        self._request(["enable" if enabled else "disable", "--actor", "SEOHEAD Desktop"], self.loaded)

    def change_profile(self, profile):
        if self.state:
            self._request(["enable" if self.state["enabled"] else "disable", "--profile", profile, "--actor", "SEOHEAD Desktop"], self.loaded)

    def connect_client(self, client):
        dialog = PermissionDialog(self.executable, client, self, self.runner)
        dialog.exec_()
        self.refresh()

    def restore(self, client):
        from PyQt5.QtWidgets import QMessageBox
        choice = QMessageBox.question(self, tr("Восстановить конфиг MCP"), tr("Восстановить запись SEOHEAD из проверенного бэкапа? Остальные изменения сохранятся."), QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if choice == QMessageBox.Yes:
            self._request(["uninstall", "--client", client, "--yes"], self.restored)

    def restored(self, result):
        self.feedback.setText(trf("Восстановлено из: {path}", path=result["backup"]))
        self.refresh()
