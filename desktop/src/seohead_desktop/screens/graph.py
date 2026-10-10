"""Граф ссылок (canvas LinkGraphStates.dc.html), states only: the graph itself waits for the core (#975).

Without a project it shows the project gate, without a saved scan the «Графа пока нет» state with «Новый скан», and with a
selected scan an honest waiting state: the core does not give the links of a scan as a paged graph yet, so nothing is drawn
and no edge, node or count is invented.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QLabel, QVBoxLayout

from .. import i18n
from ..i18n import tr
from ..ui.kit import StatePanel, no_project_panel
from .base import Screen
from .issues import selected_scan


class GraphScreen(Screen):
    slot = "graph"
    watches = ("project", "scans", "scan_status")

    def __init__(self, host):
        super().__init__(host)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        self.title = QLabel(tr("Граф ссылок"))
        self.title.setProperty("text_style", "section")
        root.addWidget(self.title)
        self.state_layout = QVBoxLayout()
        root.addLayout(self.state_layout, 1)
        i18n.signals.changed.connect(self._language_changed)
        self.refresh()

    def _set_panel(self, panel):
        while self.state_layout.count():
            item = self.state_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.state_layout.addWidget(panel)

    def refresh(self):
        host = self.host
        if not host.project_directory:
            return self._set_panel(no_project_panel(host, tr("Откройте проект, чтобы увидеть граф ссылок его сканов.")))
        if selected_scan(host) is None:
            return self._set_panel(StatePanel(
                "empty", "Графа пока нет",
                "Граф строится из таблицы ссылок сохранённого скана. В проекте ещё нет скана: запустите краул или импортируйте выгрузку Screaming Frog.",
                action=("Новый скан", host.scan_preview)))
        self._set_panel(StatePanel(
            "waiting", "Граф по скану пока не отдаётся",
            "Ядро ещё не отдаёт связи скана в виде графа. Это не «нет ссылок»: граф появится, когда ядро начнёт его отдавать.",
            issue=975, hint="Граф ссылок по выбранному скану"))

    def _language_changed(self, _language):
        try:
            self.title.setText(tr("Граф ссылок"))
            self.refresh()
        except RuntimeError:  # the screen was deleted (tests, shutdown)
            pass
