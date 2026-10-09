"""Main-window methods: top bar (52 px) with project and scan pickers."""

from __future__ import annotations

import sys

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from . import theming
from .common import ROOT
from .i18n import tr
from .ui.icons import material_icon as icon
from .ui.presentation import StateBadge
from .ui.shell import PickerButton


class _HideWhenDisabled(QPushButton):
    """Cancel control: present only while there is something to cancel."""

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self.setVisible(self.isEnabled())


def icon_button(icon_name, accessible_name, tooltip=None):
    button = QToolButton()
    button.setIcon(icon(icon_name))
    button.setProperty("role", "icon")
    button.setAccessibleName(accessible_name)
    button.setToolTip(tooltip or accessible_name)
    return button


class ChromeMixin:
    def topbar(self):
        """One 52 px row: navigation toggle, brand, project and scan pickers, agent state, ⌘K, refresh, new scan."""
        top = QWidget()
        top.setObjectName("topbar")
        top.setFixedHeight(theming.metrics()["layout"]["topbar"])
        layout = QHBoxLayout(top)
        layout.setContentsMargins(8, 0, 12, 0)
        layout.setSpacing(6)
        self.nav_toggle = icon_button("side_navigation", "Свернуть или развернуть навигацию")
        self.nav_toggle.clicked.connect(self.toggle_navigation)
        layout.addWidget(self.nav_toggle)
        mark = QLabel()
        mark.setPixmap(QIcon(str(ROOT / "assets/app/seohead-small.svg")).pixmap(24, 24))
        mark.setAccessibleName("SEOHEAD")
        layout.addWidget(mark)
        self.brand = QLabel("SEOHEAD")
        self.brand.setProperty("brand", "word")
        layout.addWidget(self.brand)
        layout.addSpacing(4)
        divider = QFrame()
        divider.setProperty("divider", "v")
        divider.setFixedHeight(24)
        layout.addWidget(divider)

        # The combo boxes hold the project/scan choices (and signals the rest of the app uses);
        # the picker buttons show them and open a menu. The combos themselves are never shown.
        self.project_picker = QComboBox(self)
        self.project_picker.hide()
        self.project_picker.addItem("Проект не открыт", None)
        self.project_picker.addItem("Открыть другой проект…", {"action": "open"})
        self.project_picker.activated.connect(self.activate_project_picker)
        self.scan_picker = QComboBox(self)
        self.scan_picker.hide()
        self.scan_picker.addItem("Нет сохранённых сканов", None)
        self.scan_picker.setEnabled(False)
        self.scan_picker.currentIndexChanged.connect(self.select_scan_from_picker)
        self.project_button = PickerButton("workspaces", "Выбрать проект")
        self.project_button.setMinimumWidth(180)
        self.project_button.setMaximumWidth(320)
        self.scan_button = PickerButton("manage_search", "Выбрать сохранённый запуск")
        self.scan_button.setMinimumWidth(250)
        self.scan_button.setMaximumWidth(340)
        self.project_button.clicked.connect(lambda: self.show_picker_menu(self.project_picker, self.project_button))
        self.scan_button.clicked.connect(lambda: self.show_picker_menu(self.scan_picker, self.scan_button))
        for combo, button in ((self.project_picker, self.project_button), (self.scan_picker, self.scan_button)):
            combo.currentIndexChanged.connect(lambda _i, c=combo, b=button: self.sync_picker(c, b))
            combo.model().rowsInserted.connect(lambda *_a, c=combo, b=button: self.sync_picker(c, b))
            combo.model().rowsRemoved.connect(lambda *_a, c=combo, b=button: self.sync_picker(c, b))
            combo.model().modelReset.connect(lambda c=combo, b=button: self.sync_picker(c, b))
            combo.model().dataChanged.connect(lambda *_a, c=combo, b=button: self.sync_picker(c, b))
            combo.installEventFilter(self)
            self.sync_picker(combo, button)
        layout.addWidget(self.project_button)
        chevron = QLabel()
        chevron.setPixmap(icon("chevron_right", theming.roles()["text_muted"]).pixmap(18, 18))
        self.project_chevron = chevron
        layout.addWidget(chevron)
        layout.addWidget(self.scan_button)
        self.scan_state_badge = StateBadge("unknown")
        self.scan_state_badge.hide()
        layout.addWidget(self.scan_state_badge)
        layout.addStretch(1)

        self.agent_pill = QLabel("Агент подключён")
        self.agent_pill.setProperty("status_pill", "agent")
        self.agent_pill.hide()
        self.simple_pill = QPushButton("Простой режим")
        self.simple_pill.setProperty("status_pill", "simple")
        self.simple_pill.setIcon(icon("expand_more"))
        self.simple_pill.setLayoutDirection(Qt.RightToLeft)
        self.simple_pill.setAccessibleName("Простой режим: сменить отображение")
        self.simple_pill.clicked.connect(self.show_display_menu)
        self.simple_pill.hide()
        layout.addWidget(self.agent_pill)
        layout.addWidget(self.simple_pill)
        self.cancel_button = _HideWhenDisabled("Отменить чтение")
        self.cancel_button.setProperty("role", "danger")
        self.cancel_button.setIcon(icon("stop", theming.roles()["error"]))
        self.cancel_button.setAccessibleName("Отменить чтение или остановить выбранный собственный запуск")
        self.cancel_button.clicked.connect(self.cancel_active_work)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.action_finder_button = QPushButton("Действия и переходы")
        self.action_finder_button.setProperty("search_field", True)
        self.action_finder_button.setIcon(icon("search"))
        self.action_finder_button.setFixedWidth(220)
        self.action_finder_button.setLayout(QHBoxLayout())
        self.action_finder_button.layout().setContentsMargins(0, 0, 8, 0)
        self.action_finder_button.layout().addStretch(1)
        self.finder_hint = QLabel("Ctrl+K" if sys.platform != "darwin" else "⌘K")
        self.finder_hint.setProperty("kbd", True)
        self.finder_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.action_finder_button.layout().addWidget(self.finder_hint)
        self.action_finder_button.setToolTip("Найти действие · Ctrl/⌘+K")
        self.action_finder_button.setAccessibleName("Найти действие или раздел")
        self.action_finder_button.clicked.connect(self.show_action_finder)
        layout.addWidget(self.action_finder_button)
        self.refresh_button = icon_button("refresh", "Обновить данные проекта", "Перечитать сохранённые данные проекта")
        self.refresh_button.clicked.connect(self.refresh_project)
        self.refresh_button.setEnabled(False)
        layout.addWidget(self.refresh_button)
        self.new_scan = QPushButton("Новый скан")
        self.new_scan.setProperty("role", "primary")
        self.new_scan.setAccessibleName("Открыть план нового скана")
        self.new_scan.setToolTip("Открыть настройки и проверить план; запуск — отдельной кнопкой")
        self.new_scan.setIcon(icon("play_arrow", theming.roles()["on_primary"]))
        self.new_scan.clicked.connect(self.scan_preview)
        layout.addWidget(self.new_scan)
        return top

    def sync_picker(self, combo, button):
        """Mirror the hidden combo (current text, optional subtitle in the tooltip role, enabled) on its button."""
        index = combo.currentIndex()
        title = tr(combo.itemText(index)) if index >= 0 else ""
        subtitle = combo.itemData(index, Qt.ToolTipRole) if index >= 0 else ""
        button.set_texts(title, tr(subtitle) if isinstance(subtitle, str) else "")
        button.setEnabled(combo.isEnabled() and combo.count() > 0)
        if combo is self.scan_picker and hasattr(self, "scan_state_badge"):
            self.scan_state_badge.setVisible(combo.currentData() is not None)

    def show_picker_menu(self, combo, button):
        menu = QMenu(button)
        for index in range(combo.count()):
            action = menu.addAction(combo.itemText(index))
            action.setCheckable(index != combo.count() - 1 or combo.itemData(index) != {"action": "open"})
            action.setChecked(index == combo.currentIndex())
            action.triggered.connect(lambda _checked, i=index: self._pick(combo, i))
        menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))

    @staticmethod
    def _pick(combo, index):
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
