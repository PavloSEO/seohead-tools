"""Main-window methods: chrome."""

from __future__ import annotations

from PyQt5.QtCore import (
    Qt,
)
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QWidget,
)

from .common import (  # noqa: F401
    CONSUMER_ID,
    PAGE_LIMIT,
    ROOT,
    configure_table,
    plain,
    scan_request_key,
)
from .ui.icons import material_icon as icon
from .ui.presentation import (
    ElidedLabel,
    StateBadge,
    theme_tokens,
)


class ChromeMixin:
    def add_workspace_toolbar(self, name, content):
        toolbar = QToolBar(self)
        toolbar.setObjectName(name)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(content)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

    def topbar(self):
        top = QWidget()
        top.setObjectName("topbar")
        layout = QHBoxLayout(top)
        layout.setContentsMargins(12, 10, 16, 10)
        layout.setSpacing(8)
        self.nav_toggle = QToolButton()
        self.nav_toggle.setIcon(icon("menu"))
        self.nav_toggle.setProperty("role", "quiet")
        self.nav_toggle.setAccessibleName("Свернуть или развернуть навигацию")
        self.nav_toggle.setToolTip("Свернуть или развернуть навигацию")
        self.nav_toggle.clicked.connect(self.toggle_navigation)
        layout.addWidget(self.nav_toggle)
        self.brand = QLabel("SEOHEAD")
        self.brand.setObjectName("brand")
        layout.addWidget(self.brand)
        self.project_picker = QComboBox()
        self.project_picker.addItem(self.demo["label"], None)
        self.project_picker.addItem("Открыть другой проект…", {"action": "open"})
        self.project_picker.activated.connect(self.activate_project_picker)
        self.project_picker.setAccessibleName("Текущий проект")
        self.project_picker.setMinimumContentsLength(16)
        self.project_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.project_picker.setMaximumWidth(300)
        layout.addWidget(self.project_picker, 1)
        self.open_project_button = QToolButton()
        self.open_project_button.setIcon(icon("folder_open"))
        self.open_project_button.setProperty("role", "quiet")
        self.open_project_button.setAccessibleName("Открыть проект")
        self.open_project_button.setToolTip("Открыть проект · Cmd/Ctrl+O")
        self.open_project_button.clicked.connect(self.choose_project)
        layout.addWidget(self.open_project_button)
        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(icon("sync"))
        self.refresh_button.setText("Обновить")
        self.refresh_button.setProperty("role", "quiet")
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.refresh_button.setAccessibleName("Обновить сохранённые данные")
        self.refresh_button.setToolTip("Перечитать сохранённые данные проекта")
        self.refresh_button.clicked.connect(self.refresh_project)
        self.refresh_button.setEnabled(False)
        layout.addWidget(self.refresh_button)
        layout.addStretch()
        self.action_finder_button = QToolButton()
        self.action_finder_button.setText("Действия")
        self.action_finder_button.setIcon(icon("search"))
        self.action_finder_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.action_finder_button.setProperty("role", "quiet")
        self.action_finder_button.setToolTip("Найти действие · Cmd/Ctrl+K")
        self.action_finder_button.setAccessibleName("Найти действие или раскладку")
        self.action_finder_button.clicked.connect(self.show_action_finder)
        layout.addWidget(self.action_finder_button)
        self.cancel_button = QPushButton("Отменить чтение")
        self.cancel_button.setIcon(icon("stop"))
        self.cancel_button.setAccessibleName("Отменить чтение или остановить выбранный собственный запуск")
        self.cancel_button.clicked.connect(self.cancel_active_work)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        self.new_scan = QPushButton("Новый скан")
        self.new_scan.setProperty("role", "primary")
        self.new_scan.setAccessibleName("Открыть план нового скана")
        self.new_scan.setToolTip("Открыть настройки и проверить план; запуск — отдельной кнопкой")
        self.new_scan.setIcon(icon("play_arrow", theme_tokens()["colors"]["on_primary"]))
        self.new_scan.clicked.connect(self.scan_preview)
        layout.addWidget(self.new_scan)
        return top

    def contextbar(self):
        bar = QWidget()
        bar.setObjectName("contextbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(12)
        self.scan_context_label = QLabel("Сохранённый скан")
        self.scan_context_label.setObjectName("metadata")
        layout.addWidget(self.scan_context_label)
        self.scan_picker = QComboBox()
        self.scan_picker.setAccessibleName("Выбрать сохранённый скан")
        self.scan_picker.setMinimumContentsLength(22)
        self.scan_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.scan_picker.addItem("Демо · 5 синтетических URL", None)
        self.scan_picker.setEnabled(False)
        self.scan_picker.currentIndexChanged.connect(self.select_scan_from_picker)
        layout.addWidget(self.scan_picker, 1)
        self.scan_state_badge = StateBadge("Демо")
        layout.addWidget(self.scan_state_badge)
        self.compare_shortcut_button = QToolButton()
        self.compare_shortcut_button.setText("Сравнить")
        self.compare_shortcut_button.setIcon(icon("compare_arrows"))
        self.compare_shortcut_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.compare_shortcut_button.setProperty("role", "panelToggle")
        self.compare_shortcut_button.setAccessibleName("Открыть сравнение сохранённых сканов")
        self.compare_shortcut_button.clicked.connect(self.open_comparison)
        layout.addWidget(self.compare_shortcut_button)
        self.source_badge = ElidedLabel("Демо · синтетические данные")
        self.source_badge.setObjectName("sourceBadge")
        layout.addWidget(self.source_badge, 1)
        return bar
