"""Native Qt Widgets shell over declared, local SEOHEAD CLI projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from string import Template

from PyQt5.QtCore import (
    QEasingCurve,
    QEvent,
    QModelIndex,
    QSettings,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap
from PyQt5.QtSvg import QSvgGenerator, QSvgRenderer
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QActionGroup,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QTableView,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .comparison import ComparisonController
from .content_search import ContentSearchController, SEARCH_PRESETS
from .ui.content_search_panel import ContentSearchPanel
from .crawl_configuration import preview_configuration, validate_overrides
from .mcp_gateway import PersistentMcpGateway
from .models import RecordModel, UrlModel
from .scan_manager import LocalScanManager
from .scan_runner import crawl_arguments
from .ui.crawl_configuration_dialog import CrawlConfigurationDialog
from .ui.panels import AuditWorkspace, ProjectPanels, component_stylesheet
from .ui.presentation import (
    FIELDS,
    ElidedLabel,
    InlineNotice,
    StateBadge,
    WorkspaceSplitter,
    field_text,
    readable_record,
    run_projection,
    state_text,
    theme_tokens,
    value_text,
)
from .ui.workspace import (
    LAYOUT_SCHEMA,
    LAYOUTS,
    PANEL_IDS,
    VIEW_IDS,
    ActionFinder,
    ProjectMonitor,
    keep_on_screen,
    system_reduced_motion,
)

ROOT = Path(__file__).resolve().parent
CONSUMER_ID = "desktop/gui"
PAGE_LIMIT = 50


def load_theme(app):
    tokens = theme_tokens()
    font = ROOT / "assets/fonts/Roboto.ttf"
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    values = {**tokens["colors"], **{key: value for key, value in tokens.items() if isinstance(value, (str, int))}, "icon_root": (ROOT / "assets/icons").as_posix()}
    app.setStyleSheet(Template((ROOT / "theme/theme.qss").read_text()).substitute(values))
    return tokens


@lru_cache(maxsize=128)
def icon(name, color=None):
    color = color or theme_tokens()["colors"]["on_surface_variant"]
    path = ROOT / "assets/icons" / f"{name}.svg"
    if not path.exists():
        return QIcon()
    raw = path.read_text()
    renderer = QSvgRenderer(raw.encode())
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def plain(text=""):
    view = QPlainTextEdit(text)
    view.setReadOnly(True)
    view.setMaximumBlockCount(1000)
    return view


def scan_request_key(project_uuid, scan_path, operation):
    digest = hashlib.sha256(str(scan_path).encode()).hexdigest()[:12]
    return f"{operation}:{project_uuid or 'unknown'}:{digest}"


def configure_table(table):
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(theme_tokens()["table_row_height"])
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(True)


class MainWindow(QMainWindow):
    """Desktop presentation adapter. All scanning remains owned by core CLI."""

    crawl_descriptor_changed = pyqtSignal()

    def __init__(self, *, persistent=True, core_executable=None):
        super().__init__()
        self.setWindowTitle("SEOHEAD · Демо")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.persistent = persistent
        self.settings = QSettings("SEOHEAD", "DesktopPreparation") if persistent else None
        self.core_executable = core_executable or shutil.which("seohead")
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.read_generation = 0
        self.project_directory = None
        self.project_result = None
        self.inbox_revision = None
        self._note_drafts = {}
        self._pending_note = None
        self._project_loading = False
        self.requests = {}
        self.request_handlers = {}
        self.active_commands = {}
        self.pending_commands = {}
        self.mcp_gateway = None
        self.mcp_ready = False
        self.content_search = ContentSearchController(self)
        self.content_search.idle.connect(self._finish_owned_shutdown)
        self.crawl_descriptor = None
        self._crawl_descriptor_error = None
        self._scan_drafts = {}
        self.scan_manager = None
        self.current_project_uuid = None
        self.selected_managed_run_id = None
        self.selected_observed_run_id = None
        self._resume_eligible_path = None
        self._pending_resume_paths = set()
        self._shown_run_error_key = None
        self.selected_scan_path = None
        self.selected_scan_uuid = None
        self.selected_url = None
        self.url_selection_generation = 0
        self.last_observer_signature = None
        self._reload_selected_scan = False
        self.poll_backoff_ms = 500
        self._close_waiting = False
        self._compact = None
        self._focus_mode = False
        self._panel_intent = {"Навигация": True, "Сводка": None, "Инспектор URL": True}
        self._navigation_compact_intent = None
        self._syncing_panel = False
        self._density = "standard"
        self.recent_projects = self.settings.value("recent_projects", []) if self.settings else []
        if not isinstance(self.recent_projects, list):
            self.recent_projects = []
        self.recent_projects = [item for item in self.recent_projects[:20] if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("label"), str)]
        system_motion = system_reduced_motion()
        self.system_reduced_motion = system_motion
        self.reduced_motion = system_motion or (self.settings.value("reduced_motion", False, type=bool) if self.settings else False)
        self._navigation_animation = QVariantAnimation(self)
        self._navigation_animation.setDuration(theme_tokens()["motion"]["duration_ms"])
        self._navigation_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._navigation_animation.valueChanged.connect(lambda value: self.navigation.setFixedWidth(int(value)))
        self.observed_runs = []
        self.observed_at = None
        self.monitor = None
        self.current_layout = "url"
        self._single_window_geometry = None
        self.scan_poll_timer = QTimer(self)
        self.scan_poll_timer.setInterval(self.poll_backoff_ms)
        self.scan_poll_timer.timeout.connect(self.poll_active_scan)
        self.demo = json.loads((ROOT / "fixtures/demo.json").read_text())

        workspace = QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        shell = QVBoxLayout(workspace)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.add_workspace_toolbar("projectControls", self.topbar())
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.add_workspace_toolbar("scanContext", self.contextbar())
        workspace.installEventFilter(self)
        self.notice = InlineNotice()
        shell.addWidget(self.notice)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(theme_tokens()["layout"]["navigation_width"])
        self.navigation.setAccessibleName("Разделы проекта")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation.setUniformItemSizes(True)
        self.navigation_labels = ["Работа", "URL", "Аудит", "Проект", "Задачи", "Сканы", "Входящие", "Отчёты", "Журнал", "Сравнение", "Поиск HTML"]
        self.navigation.addItems(self.navigation_labels)
        self.navigation.setIconSize(QSize(20, 20))
        for index, name in enumerate(("dashboard", "table_chart", "fact_check", "folder_open", "checklist", "manage_search", "notes", "description", "history", "compare_arrows", "search")):
            item = self.navigation.item(index)
            item.setIcon(icon(name))
            item.setData(Qt.AccessibleTextRole, self.navigation_labels[index])
            item.setToolTip(self.navigation_labels[index])
        body.addWidget(self.navigation)
        from .ui.components import PanelStack
        self.pages = PanelStack()
        body.addWidget(self.pages, 1)
        shell.addLayout(body, 1)

        self.pages.addWidget(self.work_page())
        self.pages.addWidget(self.url_page())
        self.pages.addWidget(self.audit_page())
        self.pages.addWidget(self.project_page())
        self.comparison = ComparisonController(self)
        self.comparison.changed.connect(self.load_comparison)
        self.project_panels.panel("compare").pair_changed.connect(lambda: self.comparison.clear("Выбрана другая пара; нажмите «Сравнить»"))
        self.pages.addWidget(self.tasks_page())
        self.pages.addWidget(self.scans_page())
        self.pages.addWidget(self.inbox_page())
        self.pages.addWidget(self.reports_page())
        self.pages.addWidget(self.journal_page())
        self.content_search_panel = ContentSearchPanel()
        self.pages.addWidget(self.content_search_panel)
        self.content_search_panel.searchRequested.connect(lambda values: self.content_search.start(**values))
        self.content_search_panel.pageRequested.connect(self.content_search.page)
        self.content_search_panel.cancelRequested.connect(self.content_search.cancel)
        self.content_search.changed.connect(self.load_content_search)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(1)
        self.statusBar().showMessage("Демо · сеть и сканирование не запускаются")

        file_menu = self.menuBar().addMenu("Проект")
        file_menu.addAction("Открыть проект…", self.choose_project, QKeySequence.Open)
        view_menu = self.menuBar().addMenu("Вид")
        self.panel_actions = {}
        for name, widget in [("Навигация", self.navigation), ("Сводка", self.overview), ("Инспектор URL", self.inspector)]:
            action = view_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda shown, name=name, widget=widget: self.panel_action_changed(name, widget, shown))
            self.panel_actions[name] = action
        self.panel_actions["Инспектор URL"].toggled.connect(self.inspector_toggle.setChecked)
        view_menu.addSeparator()
        view_menu.addAction("Развернуть таблицу / вернуть панели", self.toggle_focus_mode, "Ctrl+Shift+F")
        view_menu.addAction("Восстановить панели", self.restore_panels)
        density_menu = view_menu.addMenu("Плотность таблиц")
        density_group = QActionGroup(self)
        for label, density in [("Компактная · 28 px", "compact"), ("Обычная · 32 px", "standard"), ("Свободная · 40 px", "comfortable")]:
            action = density_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(density == "standard")
            density_group.addAction(action)
            action.triggered.connect(lambda checked, density=density: self.set_density(density))
        layout_menu = view_menu.addMenu("Раскладка")
        for identifier, title in LAYOUTS.items():
            layout_menu.addAction(title, lambda checked=False, identifier=identifier: self.apply_layout(identifier))
        layout_menu.addSeparator()
        layout_menu.addAction("Сохранить расположение", self.save_workspace_layout)
        layout_menu.addAction("Восстановить сохранённое", self.restore_workspace_layout)
        view_menu.addAction("Монитор в отдельном окне", self.open_monitor_window)
        self.action_finder_action = view_menu.addAction("Найти действие…", self.show_action_finder, "Ctrl+K")
        self.action_finder_action.setShortcutContext(Qt.ApplicationShortcut)
        motion_action = view_menu.addAction("Уменьшить движение")
        motion_action.setCheckable(True)
        motion_action.setChecked(self.reduced_motion)
        motion_action.setEnabled(not self.system_reduced_motion)
        motion_action.setToolTip("Системное уменьшение движения имеет приоритет" if self.system_reduced_motion else "Отключить плавное сворачивание навигации")
        motion_action.toggled.connect(self.set_reduced_motion)
        self.find_shortcut = QShortcut(QKeySequence.Find, self)
        self.find_shortcut.activated.connect(self.focus_search)
        self.region_shortcut = QShortcut("F6", self)
        self.region_shortcut.activated.connect(self.focus_next_region)
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self.copy_shortcut.setContext(Qt.WidgetShortcut)
        self.copy_shortcut.activated.connect(self.copy_url_selection)
        if self.settings:
            for key, widget in [("geometry", self), ("horizontal", self.horizontal), ("vertical", self.vertical)]:
                value = self.settings.value(key)
                if value:
                    (widget.restoreGeometry if widget is self else widget.restoreState)(value)
        self.table.selectRow(0)
        if self.settings:
            QTimer.singleShot(0, self.restore_workspace_layout)

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
        self.cancel_button.setAccessibleName("Отменить чтение или остановить выбранный собственный запуск")
        self.cancel_button.clicked.connect(self.cancel_active_work)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        self.new_scan = QPushButton("Новый скан")
        self.new_scan.setProperty("role", "primary")
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
        label = QLabel("Сохранённый скан")
        label.setObjectName("metadata")
        layout.addWidget(label)
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

    def work_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = QLabel("Работа с агентом")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        caption = QLabel("Согласованный объём, следующие действия и отдельные запуски проекта")
        caption.setObjectName("sectionCaption")
        layout.addWidget(caption)
        self.work_splitter = WorkspaceSplitter(Qt.Vertical)
        self.progress_text = plain("Откройте проект, чтобы увидеть сохранённые задачи и согласованный план.\n\nДемо не содержит измеренного прогресса проекта.")
        self.progress_text.setAccessibleName("Прогресс задач проекта")
        self.progress_text.setProperty("role", "summary")
        self.work_splitter.addWidget(self.progress_text)
        activity = QWidget()
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(0, 10, 0, 0)
        self.activity_caption = QLabel("Запуски · источник не подключён")
        self.activity_caption.setObjectName("sectionCaption")
        activity_layout.addWidget(self.activity_caption)
        self.activity_model = RecordModel((("Запуск", "id"), ("Источник", "kind"), ("Состояние", "state"), ("Этап", "phase"), ("Получено", "fetched"), ("Очередь", "queued"), ("В работе", "inflight"), ("Сейчас", "rate"), ("Лимит, запр./с", "rate_limit")), parent=self)
        self.activity_table = QTableView()
        self.activity_table.setAccessibleName("Отдельные запуски проекта")
        self.activity_table.setModel(self.activity_model)
        configure_table(self.activity_table)
        for column, width in enumerate((140, 85, 160, 140, 80, 80, 80, 140, 110)):
            self.activity_table.setColumnWidth(column, width)
        self.activity_table.selectionModel().currentRowChanged.connect(self.show_observed_run)
        self.activity_table.clicked.connect(lambda index: self.show_observed_run(index, None))
        activity_layout.addWidget(self.activity_table, 1)
        self.activity_text = plain("Нет измерений активности. История появится из сохранённых запусков проекта.")
        self.activity_text.setAccessibleName("Измерения выбранного запуска")
        activity_layout.addWidget(self.activity_text, 1)
        self.work_splitter.addWidget(activity)
        self.work_splitter.setSizes([220, 480])
        layout.addWidget(self.work_splitter, 1)
        return page

    def reports_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Отчёты")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        message = QLabel("Экспорт отчётов из этого окна пока не подключён.\n\nСохранённые URL и результаты сканов доступны в разделах «URL» и «Сканы».")
        message.setObjectName("panelMessage")
        message.setWordWrap(True)
        layout.addWidget(message)
        layout.addStretch()
        return page

    def journal_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        title = QLabel("Журнал запусков")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.journal_caption = QLabel("События появляются из сохранённого наблюдения ядра")
        self.journal_caption.setObjectName("sectionCaption")
        layout.addWidget(self.journal_caption)
        self.journal_model = RecordModel((("Время", "at"), ("Запуск", "run_id"), ("Этап", "phase"), ("Событие", "code")), parent=self)
        self.journal_table = QTableView()
        self.journal_table.setModel(self.journal_model)
        self.journal_table.setAccessibleName("Сохранённые события запусков")
        configure_table(self.journal_table)
        for column, width in enumerate((220, 300, 160)):
            self.journal_table.setColumnWidth(column, width)
        layout.addWidget(self.journal_table, 1)
        return page

    def audit_page(self):
        self.audit_workspace = AuditWorkspace()
        self.audit_workspace.intent_requested.connect(self.handle_audit_intent)
        return self.audit_workspace

    def project_page(self):
        self.project_panels = ProjectPanels()
        self.project_panels.refresh.connect(self.refresh_project)
        self.project_panels.select_task.connect(self.select_project_task)
        self.project_panels.select_scan.connect(self.select_project_scan)
        self.project_panels.submit_note.connect(self.submit_note)
        self.project_panels.intent_requested.connect(self.handle_project_intent)
        return self.project_panels

    def url_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        self.horizontal = WorkspaceSplitter(Qt.Horizontal)
        self.vertical = WorkspaceSplitter(Qt.Vertical)
        table_area = QWidget()
        area = QVBoxLayout(table_area)
        area.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.url_caption = QLabel("URL · 5 демо-записей")
        self.url_caption.setObjectName("sectionTitle")
        toolbar.addWidget(self.url_caption)
        toolbar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск URL в демо-наборе")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(420)
        self.search.setAccessibleName("Поиск URL в загруженной странице")
        toolbar.addWidget(self.search)
        self.inspector_toggle = QToolButton()
        self.inspector_toggle.setText("Детали")
        self.inspector_toggle.setProperty("role", "panelToggle")
        self.inspector_toggle.setCheckable(True)
        self.inspector_toggle.setChecked(True)
        self.inspector_toggle.setAccessibleName("Показать или скрыть детали URL")
        self.inspector_toggle.clicked.connect(lambda shown: self.set_panel_visible("Инспектор URL", shown))
        toolbar.addWidget(self.inspector_toggle)
        settings = QToolButton()
        settings.setToolTip("Показать или скрыть сводку")
        settings.setAccessibleName("Показать или скрыть сводку")
        settings.setIcon(icon("view_sidebar"))
        settings.clicked.connect(lambda: self.set_panel_visible("Сводка", not self.overview.isVisible()))
        toolbar.addWidget(settings)
        area.addLayout(toolbar)
        self.url_scope_caption = ElidedLabel("Демо · поиск и сортировка по 5 синтетическим строкам")
        self.url_scope_caption.setObjectName("metadata")
        area.addWidget(self.url_scope_caption)
        self.model = UrlModel(self.demo["rows"], self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.search.textChanged.connect(self.filter_urls)
        self.search.returnPressed.connect(lambda: self.table.selectRow(0) if self.proxy.rowCount() else None)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        configure_table(self.table)
        self.table.setAccessibleName("URL сохранённого скана")
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 310)
        self.table.setColumnWidth(1, 64)
        self.table.setColumnWidth(2, 132)
        self.table.setColumnWidth(3, 112)
        self.table.setColumnWidth(4, 176)
        self.table.setColumnWidth(5, 104)
        self.table.selectionModel().currentRowChanged.connect(self.show_url)
        area.addWidget(self.table)
        self.url_empty = QLabel("Выберите сохранённый скан")
        self.url_empty.setObjectName("panelMessage")
        self.url_empty.setAlignment(Qt.AlignCenter)
        self.url_empty.hide()
        area.addWidget(self.url_empty, 1)
        self.vertical.addWidget(table_area)
        self.inspector = QTabWidget()
        hide_details = QToolButton()
        hide_details.setText("Скрыть")
        hide_details.setProperty("role", "quiet")
        hide_details.setAccessibleName("Скрыть детали URL")
        hide_details.clicked.connect(lambda: self.set_panel_visible("Инспектор URL", False))
        self.inspector.setCornerWidget(hide_details, Qt.TopRightCorner)
        self.detail = plain()
        self.inspector.addTab(self.detail, "Сведения")
        self.debug_detail = plain()
        self.debug_detail.setObjectName("diagnostics")
        self.inspector.addTab(self.debug_detail, "Диагностика")
        self.link_detail = plain("Внутренние ссылки появятся только из сохранённого скана.")
        self.inspector.addTab(self.link_detail, "Ссылки")
        self.evidence_detail = plain("Состояние скана появится после выбора сохранённого скана.")
        self.inspector.addTab(self.evidence_detail, "Снимки")
        self.headers_detail = plain("HTTP headers этого URL не загружены.")
        self.inspector.addTab(self.headers_detail, "HTTP headers")
        for title in ("HTML", "Извлечение"):
            self.inspector.addTab(plain("Данные появятся только из сохранённых измерений ядра."), title)
        self.vertical.addWidget(self.inspector)
        self.vertical.setSizes([440, 230])
        self.horizontal.addWidget(self.vertical)
        self.overview = QWidget()
        summary = QVBoxLayout(self.overview)
        summary.setContentsMargins(12, 0, 0, 0)
        self.overview.setMinimumWidth(230)
        self.overview.setMaximumWidth(380)
        box = QGroupBox("Сводка")
        facts = QFormLayout(box)
        self.summary_source = QLabel("Синтетическое демо")
        self.summary_records = QLabel("5")
        self.summary_scan = QLabel("Не запускался")
        self.summary_coverage = QLabel("Нет измерений")
        for label, value in [("Источник", self.summary_source), ("Записей", self.summary_records), ("Краул", self.summary_scan), ("Покрытие", self.summary_coverage)]:
            value.setWordWrap(True)
            facts.addRow(label, value)
        summary.addWidget(box)
        self.summary_note = plain("Выберите сохранённый скан на вкладке «Сканы», чтобы открыть первую ограниченную страницу URL.")
        self.summary_note.setAccessibleName("Происхождение и границы страницы URL")
        self.summary_note.setProperty("role", "summary")
        summary.addWidget(self.summary_note)
        self.horizontal.addWidget(self.overview)
        self.horizontal.setSizes([1000, 280])
        layout.addWidget(self.horizontal)
        return page

    def tasks_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        self.task_caption = QLabel("Задачи · откройте локальный проект")
        self.task_caption.setObjectName("sectionTitle")
        layout.addWidget(self.task_caption)
        self.task_model = RecordModel((("Задача", "title"), ("Тип", "kind"), ("Состояние", "state"), ("Причина", "reason")), parent=self)
        self.task_table = QTableView()
        self.task_table.setModel(self.task_model)
        configure_table(self.task_table)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_table.selectionModel().currentRowChanged.connect(self.show_task)
        layout.addWidget(self.task_table, 3)
        self.task_detail = plain("Детали появляются для выбранной задачи и остаются ограниченной проекцией ядра.")
        layout.addWidget(self.task_detail, 2)
        return page

    def scans_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        self.scan_caption = QLabel("Сканы · откройте локальный проект")
        self.scan_caption.setObjectName("sectionTitle")
        layout.addWidget(self.scan_caption)
        self.scan_model = RecordModel((("Начальный URL", "start_url"), ("Состояние", "lifecycle"), ("Источник", "source_kind"), ("Завершён", "finished_at"), ("Частичный", "partial")), parent=self)
        self.scan_table = QTableView()
        self.scan_table.setModel(self.scan_model)
        configure_table(self.scan_table)
        self.scan_table.horizontalHeader().setStretchLastSection(False)
        self.scan_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.scan_table.setColumnWidth(1, 150)
        self.scan_table.setColumnWidth(2, 110)
        self.scan_table.setColumnWidth(3, 220)
        self.scan_table.setColumnWidth(4, 100)
        self.scan_table.selectionModel().currentRowChanged.connect(self.show_scan)
        layout.addWidget(self.scan_table, 3)
        controls = QGridLayout()
        controls.setColumnStretch(2, 1)
        self.owned_run_picker = QComboBox()
        self.owned_run_picker.setMinimumContentsLength(22)
        self.owned_run_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.owned_run_picker.setAccessibleName("Запуски, созданные этим окном")
        self.owned_run_picker.addItem("Запуски этого окна: нет", None)
        self.owned_run_picker.currentIndexChanged.connect(self.select_owned_run)
        controls.addWidget(self.owned_run_picker, 0, 0, 1, 3)
        self.stop_run_button = QPushButton("Остановить запуск")
        self.stop_run_button.setIcon(icon("stop", theme_tokens()["colors"]["error"]))
        self.stop_run_button.setEnabled(False)
        self.stop_run_button.clicked.connect(self.stop_selected_run)
        self.stop_run_button.setProperty("role", "danger")
        controls.addWidget(self.stop_run_button, 1, 0)
        self.resume_scan_button = QPushButton("Продолжить скан")
        self.resume_scan_button.setIcon(icon("replay"))
        self.resume_scan_button.setEnabled(False)
        self.resume_scan_button.clicked.connect(self.resume_selected_scan)
        controls.addWidget(self.resume_scan_button, 1, 1)
        layout.addLayout(controls)
        self.scan_progress_label = QLabel("Прогресс выбранного скана не измерен")
        self.scan_progress_label.setObjectName("metadata")
        self.scan_progress_label.setWordWrap(True)
        layout.addWidget(self.scan_progress_label)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1000)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setAccessibleName("Обработано известных URL; размер сайта не измеряется")
        self.scan_progress.hide()
        layout.addWidget(self.scan_progress)
        self.run_details = QTabWidget()
        self.scan_detail = plain("Выберите сохранённый скан, чтобы открыть его данные.")
        self.run_details.addTab(self.scan_detail, "Сохранённый скан")
        self.owned_run_detail = plain("Выберите запуск, созданный этим окном")
        self.run_details.addTab(self.owned_run_detail, "Запуск этого окна")
        self.owned_run_output = plain("Вывод выбранного запуска появится здесь")
        self.owned_run_output.setObjectName("diagnostics")
        self.run_details.addTab(self.owned_run_output, "Журнал запуска")
        self.show_run_result = QPushButton("Показать результат запуска")
        self.show_run_result.setIcon(icon("open_in_new"))
        self.show_run_result.setEnabled(False)
        self.show_run_result.clicked.connect(self.open_owned_run_result)
        controls.addWidget(self.show_run_result, 1, 2)
        self.owned_target_caption = ElidedLabel("Управление запуском: ничего не выбрано")
        self.owned_target_caption.setObjectName("metadata")
        layout.addWidget(self.owned_target_caption)
        layout.addWidget(self.run_details, 2)
        return page

    def inbox_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        self.inbox_caption = QLabel("Входящие · откройте локальный проект")
        self.inbox_caption.setObjectName("sectionTitle")
        layout.addWidget(self.inbox_caption)
        self.inbox_model = RecordModel((("Тип", "kind"), ("Текст", "text"), ("Автор", "author_role"), ("Цель", "goal_state"), ("Создано", "created_at")), parent=self)
        self.inbox_table = QTableView()
        self.inbox_table.setModel(self.inbox_model)
        configure_table(self.inbox_table)
        self.inbox_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.inbox_table.selectionModel().currentRowChanged.connect(self.show_inbox_entry)
        layout.addWidget(self.inbox_table, 3)
        composer = QHBoxLayout()
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Заметка для текущего проекта")
        self.note_input.setAccessibleName("Текст заметки текущего проекта")
        self.note_input.textChanged.connect(self.update_note_controls)
        self.note_kind = QComboBox()
        self.note_kind.addItem("Заметка", "note")
        self.note_kind.addItem("Вопрос · недоступно", "question")
        self.note_kind.model().item(1).setEnabled(False)
        self.note_kind.model().item(1).setToolTip("Ядро поддерживает заметку и предложенную цель. Вопрос можно записать текстом заметки.")
        self.note_kind.addItem("Предложенная цель", "proposed_goal")
        self.note_kind.currentIndexChanged.connect(self.update_note_controls)
        self.note_submit = QPushButton("Сохранить заметку")
        self.note_submit.setIcon(icon("edit_note"))
        self.note_submit.clicked.connect(self.submit_note)
        self.note_submit.setEnabled(False)
        composer.addWidget(self.note_input, 1)
        composer.addWidget(self.note_kind)
        composer.addWidget(self.note_submit)
        layout.addLayout(composer)
        self.note_destination = ElidedLabel("Откройте проект для сохранения заметки")
        self.note_destination.setObjectName("metadata")
        layout.addWidget(self.note_destination)
        self.inbox_detail = plain("Заметка записывается только после явного нажатия «Сохранить». Она не запускает скан и не отправляет текст в чат агента.")
        layout.addWidget(self.inbox_detail, 1)
        return page

    def ensure_mcp_gateway(self):
        if not self.core_executable:
            self.statusBar().showMessage("Укажите --core-cli: CLI ядра SEOHEAD не найден")
            return None
        if self.mcp_gateway is None:
            self.mcp_gateway = PersistentMcpGateway(self.core_executable)
            self.mcp_gateway.signals.ready.connect(self.mcp_ready_state)
            self.mcp_gateway.signals.result.connect(self.command_loaded)
            self.mcp_gateway.signals.failed.connect(self.command_failed)
            self.mcp_gateway.signals.transport_failed.connect(self.mcp_transport_failed)
            self.pool.start(self.mcp_gateway)
        return self.mcp_gateway

    def mcp_ready_state(self, _tools):
        self.mcp_ready = True
        self.content_search.set_available(_tools)
        self.statusBar().showMessage("Локальный SEOHEAD MCP подключён")

    def mcp_transport_failed(self, text):
        self.mcp_ready = False
        self.statusBar().showMessage(f"Локальный SEOHEAD MCP переподключается: {text}")

    def start_command(self, request_id, tool, arguments, handler):
        gateway = self.ensure_mcp_gateway()
        if gateway is None:
            return
        operation = request_id.split(":", 1)[0]
        if operation in self.active_commands:
            # Keep one in-flight callback and only the latest pending intent per panel.
            self.pending_commands[operation] = (request_id, tool, arguments, handler)
            return
        self.active_commands[operation] = request_id
        self.requests[request_id] = self.read_generation
        self.request_handlers[request_id] = handler
        try:
            gateway.submit(request_id, tool, arguments, self.read_generation)
        except (RuntimeError, ValueError) as exc:
            self.complete_command(request_id)
            self.statusBar().showMessage(str(exc))
            return
        self.cancel_button.setEnabled(True)

    def complete_command(self, request_id):
        self.requests.pop(request_id, None)
        handler = self.request_handlers.pop(request_id, None)
        operation = request_id.split(":", 1)[0]
        self.active_commands.pop(operation, None)
        pending = self.pending_commands.pop(operation, None)
        if pending is not None:
            self.start_command(*pending)
        return handler

    def command_loaded(self, request_id, result, generation):
        if generation != self.read_generation:
            return
        handler = self.complete_command(request_id)
        if handler:
            handler(result)
        if self.notice.context == request_id:
            self.notice.hide()
        self.select_owned_run()
        if not self.requests and self.project_directory:
            self.statusBar().showMessage("Чтение завершено · локальный проект · выберите URL или запуск для подробностей")

    def command_failed(self, request_id, text, generation):
        if generation != self.read_generation:
            return
        superseded = request_id.split(":", 1)[0] in self.pending_commands
        self.complete_command(request_id)
        self.select_owned_run()
        if superseded:
            return
        if request_id == "inbox-submit":
            self._pending_note = None
            self.update_note_controls()
        elif request_id == "project-open":
            self._project_loading = False
            if self.mcp_gateway is not None and self.project_directory:
                self.mcp_gateway.set_project_scope(self.project_directory)
            self.update_note_controls()
        elif request_id == "crawl-settings":
            self._crawl_descriptor_error = text
            self.crawl_descriptor_changed.emit()
        elif request_id.startswith("url-page:"):
            self.audit_workspace.set_page(
                "internal", [], state="unavailable", reason=text, source="Retained scan unavailable"
            )
        elif request_id.startswith("url-detail:"):
            self.audit_workspace.set_page(
                "url_details", [], state="unavailable", reason=text, source="Retained URL detail unavailable"
            )
        self.statusBar().showMessage(f"{request_id}: {text}")
        self.notice.show_error(f"Не удалось получить данные. {text}", request_id)

    def cancel_requests(self):
        if self._pending_note is not None:
            self.notice.show_error("Сохранение заметки ещё не подтверждено. Черновик сохранён; дождитесь ответа ядра.", "inbox-submit")
            return
        if self.mcp_gateway is not None:
            self.mcp_gateway.cancel_generation(self.read_generation)
        self.requests.clear()
        self.request_handlers.clear()
        self.active_commands.clear()
        self.pending_commands.clear()
        self.read_generation += 1
        if hasattr(self, "comparison"):
            self.comparison.clear("Чтение сравнения отменено; выберите пару и повторите")
        self.cancel_button.setEnabled(False)
        self.statusBar().showMessage("Текущие чтения отменены; сохранённые данные проекта не изменены")

    def cancel_active_work(self):
        if self.scan_manager is not None and self.selected_managed_run_id:
            if self.scan_manager.stop(self.selected_managed_run_id):
                self.statusBar().showMessage("Остановка отправлена только выбранному запуску этого окна")
                return
        self.cancel_requests()

    def choose_project(self):
        directory = QFileDialog.getExistingDirectory(self, "Открыть существующий проект SEOHEAD")
        if directory:
            self.read_project(directory)

    def read_project(self, directory):
        if self._pending_note is not None:
            self.notice.show_error("Заметка сохраняется в текущий проект. Дождитесь подтверждения перед сменой проекта.", "inbox-submit")
            return
        if not (Path(directory) / "project.json").is_file():
            self.statusBar().showMessage("В папке нет project.json SEOHEAD")
            self.notice.show_error("В выбранной папке нет project.json SEOHEAD. Выберите сохранённый проект через меню проекта.", "project-open")
            return
        self._project_loading = True
        self.update_note_controls()
        self.cancel_requests()
        self.statusBar().showMessage("Чтение локального проекта в фоне…")
        gateway = self.ensure_mcp_gateway()
        if gateway is None:
            self._project_loading = False
            self.update_note_controls()
            return
        try:
            gateway.set_project_scope(directory)
        except ValueError as exc:
            self._project_loading = False
            self.update_note_controls()
            self.statusBar().showMessage(str(exc))
            return
        self.start_command(
            "project-open",
            "seo_project_open",
            {"directory": directory},
            lambda result: self.project_loaded(result, self.read_generation),
        )

    def project_loaded(self, result, generation):
        if generation != self.read_generation:
            return
        self.stash_note_drafts()
        self._project_loading = False
        self.inbox_revision = None
        self.clear_scan_selection("Выбран другой проект. Загрузка сохранённого контекста…")
        self.comparison.clear("Выбран другой проект. Выберите два его сохранённых скана.")
        self.project_panels.panel("compare").set_scans([])
        self.last_observer_signature = None
        self.observed_runs = []
        self.observed_at = None
        self.activity_model.replace([])
        self.journal_model.replace([])
        self.progress_text.setPlainText("Загрузка согласованного плана выбранного проекта…")
        self.activity_text.setPlainText("Загрузка запусков выбранного проекта…")
        self.activity_caption.setText("Запуски · загрузка")
        self.journal_caption.setText("События проекта · загрузка")
        self.scan_model.replace([])
        self.task_model.replace([])
        self.inbox_model.replace([])
        self.inbox_detail.setPlainText("Выберите запись текущего проекта")
        self.task_detail.setPlainText("Выберите задачу текущего проекта")
        self.project_result = result
        self.project_directory = str(result.get("path") or "")
        self.model.replace([])
        self.search.clear()
        self.search.setEnabled(False)
        self.url_caption.setText("URL · выберите сохранённый скан")
        if not self.project_directory:
            self.statusBar().showMessage("Ядро не вернуло путь открытого проекта")
            return
        project = result.get("project", {})
        site = project.get("site", {}) if isinstance(project, dict) else {}
        self.current_project_uuid = project.get("project_uuid") if isinstance(project, dict) else None
        self.selected_managed_run_id = None
        self.selected_observed_run_id = None
        self.owned_run_detail.setPlainText("Выберите запуск текущего проекта")
        self.owned_run_output.clear()
        self.restore_note_drafts()
        self.owned_run_picker.blockSignals(True)
        self.owned_run_picker.clear()
        self.owned_run_picker.addItem("Запуски этого окна: нет", None)
        self.owned_run_picker.blockSignals(False)
        self.stop_run_button.setEnabled(False)
        label = site.get("label") or site.get("host") or "Подключённый проект"
        self.remember_project(label, self.project_directory)
        self.fill_project_picker(label)
        self.setWindowTitle(f"SEOHEAD · {label}")
        if self.monitor is not None:
            self.monitor.sync_context()
        self.source_badge.setText("Локальный проект · сохранённые данные")
        self.refresh_button.setEnabled(True)
        self.update_note_controls()
        self.detail.setPlainText(readable_record(project, heading="Локальный проект"))
        self.debug_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.summary_source.setText("Сохранённые данные ядра")
        self.summary_records.setText("—")
        self.summary_scan.setText("Выберите скан")
        self.summary_coverage.setText("См. прогресс проекта")
        self.refresh_project()
        self.load_crawl_descriptor()

    def load_crawl_descriptor(self):
        if self.crawl_descriptor is None and "crawl-settings" not in self.requests:
            self._crawl_descriptor_error = None
            self.start_command("crawl-settings", "seo_crawl_describe_settings", {}, self.crawl_descriptor_loaded)

    def crawl_descriptor_loaded(self, result):
        try:
            validate_overrides(result, {})
        except (TypeError, ValueError) as exc:
            self._crawl_descriptor_error = str(exc)
            self.statusBar().showMessage(f"Конфигурация ядра недоступна: {exc}")
            self.crawl_descriptor_changed.emit()
            return
        self.crawl_descriptor = result
        self._crawl_descriptor_error = None
        self.crawl_descriptor_changed.emit()

    def refresh_project(self):
        if self._pending_note is not None:
            self.statusBar().showMessage("Обновление будет доступно после подтверждения сохранения заметки")
            return
        if not self.project_directory:
            self.statusBar().showMessage("Сначала откройте локальный проект SEOHEAD")
            return
        self.cancel_requests()
        directory = self.project_directory
        self._reload_selected_scan = True
        self.statusBar().showMessage("Обновление сохранённых проекций в фоне…")
        self.start_command(
            "observer",
            "seo_project_observe",
            {"directory": directory, "consumer": CONSUMER_ID, "scan_limit": 20},
            self.load_observer,
        )
        self.start_command(
            "tasks",
            "seo_project_checklist_page",
            {"directory": directory, "limit": PAGE_LIMIT},
            self.load_tasks,
        )
        self.load_crawl_descriptor()

    def load_observer(self, result):
        """Project-observe is the core's coherent bounded snapshot."""
        progress = result.get("progress") or {}
        scans = result.get("scans") or {}
        inbox = result.get("inbox") or {}
        run_envelope = result.get("runs")
        runs = run_envelope.get("items", []) if isinstance(run_envelope, dict) else run_envelope if isinstance(run_envelope, list) else []
        signature = (
            progress.get("revision"),
            scans.get("total"),
            tuple(item.get("uuid") for item in scans.get("items") or ()),
            inbox.get("revision"),
            tuple((item.get("id"), item.get("state"), (item.get("telemetry") or {}).get("sampled_at")) for item in runs),
        )
        if self.scan_manager is not None and isinstance(self.current_project_uuid, str):
            self.scan_manager.observe(self.current_project_uuid, runs)
        if signature == self.last_observer_signature and self.scan_manager is not None and self.scan_manager.active_count:
            self.poll_backoff_ms = 500
            self.scan_poll_timer.setInterval(self.poll_backoff_ms)
            return
        self.last_observer_signature = signature
        self.present_observed_runs(runs, result.get("observed_at"))
        self.load_progress(result.get("progress") or {})
        self.load_activity({"observed_at": result.get("observed_at"), "sites": result.get("sites") or {}})
        self.load_scans(result.get("scans") or {})
        self.load_inbox(result.get("inbox") or {})
        self.load_unread(result.get("inbox_unread") or {})
        self.poll_backoff_ms = 500
        self.scan_poll_timer.setInterval(self.poll_backoff_ms)

    def load_progress(self, result):
        counts = result.get("counts") or {}
        completion = result.get("audit_task_completion") or {}
        numerator, denominator = completion.get("numerator"), completion.get("denominator")
        if completion.get("state") == "measured" and isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator > 0:
            completion_text = f"{value_text(numerator)} из {value_text(denominator)} согласованных задач"
        else:
            reason = completion.get("reason") or "Нет подтверждённого знаменателя согласованного плана."
            if reason == "record an explicit audit plan before reporting a percentage":
                reason = "Сначала согласуйте и сохраните план аудита."
            completion_text = f"Не измерено — {reason}"
        lines = ["ПЛАН ПРОЕКТА", "", f"Состояние: {state_text(result.get('state'))}",
                 f"Завершено: {value_text(counts.get('complete'))}    Осталось: {value_text(counts.get('remaining'))}    Устарело: {value_text(counts.get('stale'))}",
                 f"Покрытие задач: {completion_text}", "", "СЛЕДУЮЩИЕ ДЕЙСТВИЯ"]
        for item in (result.get("next_actions") or [])[:8]:
            if item.get("id") == "scope:initialize":
                lines.append("Согласовать объём аудита и настроить checklist проекта.")
            else:
                lines.append(f"{item.get('title') or item.get('id')}: {item.get('action') or 'Действие не задано'}")
        if not result.get("next_actions"):
            lines.append("Ядро не объявило следующих действий.")
        self.progress_text.setPlainText("\n".join(lines))

    def load_activity(self, result):
        sites = (result.get("sites") or {}).get("items") or []
        self.activity_caption.setText(f"Запуски · {len(self.observed_runs)} в текущем наблюдении · сайтов: {len(sites)}")
        self.activity_caption.setToolTip("Наблюдение: " + field_text("observed_at", result.get("observed_at")))

    def present_observed_runs(self, runs, observed_at):
        self.observed_runs = list(runs[:50])
        self.observed_at = observed_at
        current = self.activity_table.currentIndex()
        selected = self.selected_observed_run_id or (self.activity_model.rows[current.row()].get("id") if current.isValid() else None)
        self.activity_table.selectionModel().blockSignals(True)
        self.activity_model.replace([run_projection(run) for run in self.observed_runs])
        row = next((index for index, run in enumerate(self.observed_runs) if run.get("id") == selected), -1 if self.selected_observed_run_id else 0)
        if self.observed_runs and row >= 0:
            self.activity_table.selectRow(row)
        else:
            self.activity_table.clearSelection()
            self.activity_table.setCurrentIndex(QModelIndex())
        self.activity_table.selectionModel().blockSignals(False)
        if self.observed_runs and row >= 0:
            self.show_observed_run(self.activity_model.index(row, 0), None, sync_controls=False)
        else:
            self.activity_text.setPlainText("В текущем наблюдении ядро не вернуло запусков. Это не измерение скорости или покрытия сайта.")
        events = [{"run_id": run.get("id"), **event} for run in self.observed_runs for event in (run.get("events") or [])[-20:]][-200:]
        self.journal_model.replace(events)
        self.journal_caption.setText(f"Сохранённых событий в выборке: {len(events)} · последние 20 на запуск, до 200 строк")

    def show_observed_run(self, current, _previous, sync_controls=True):
        if not current.isValid():
            return
        row = self.activity_model.rows[current.row()]
        run = row["_run"]
        if sync_controls:
            self.selected_observed_run_id = run.get("id")
            owned = next((item for item in self.owned_runs_for_project() if run.get("id") in {item.get("core_run_id"), item.get("observer_run_id")}), None)
            self.selected_managed_run_id = owned.get("id") if owned else None
            self.owned_run_picker.blockSignals(True)
            self.owned_run_picker.setCurrentIndex(max(0, self.owned_run_picker.findData(self.selected_managed_run_id)))
            self.owned_run_picker.blockSignals(False)
            self.render_owned_run(self.scan_manager.detail(owned["id"]) if owned else None)
        telemetry = run.get("telemetry") or {}
        counters = run.get("counters") or {}
        collector = run.get("collector") or {}
        lines = [f"{state_text(run.get('state'))} · {state_text(run.get('kind'))} · {run.get('id')}", "",
                 f"Получено: {value_text(counters.get('fetched'))}    В очереди: {value_text(counters.get('queued'))}    В работе: {value_text(counters.get('inflight'))}    Исключено: {value_text(counters.get('excluded'))}",
                 f"Сейчас: {row['rate']}    Лимит: {value_text(collector.get('max_requests_per_second'))} запросов/с",
                 f"Измерение: {field_text('sampled_at', telemetry.get('sampled_at'))} · {state_text(telemetry.get('state'))}",
                 f"Окно измерения: {value_text(telemetry.get('rate_window_seconds'))} с · Возраст при наблюдении: {value_text(telemetry.get('age_seconds'))} с",
                 f"Начало: {field_text('started_at', run.get('started_at'))}    Завершение: {field_text('finished_at', run.get('finished_at'))}",
                 f"Результат: {value_text(run.get('artifact'))}"]
        self.activity_text.setPlainText("\n".join(lines))

    def load_tasks(self, result):
        rows = []
        for item in result.get("items") or []:
            rows.append({"id": item.get("id"), "title": item.get("title"), "kind": item.get("kind"), "state": item.get("display_state"), "reason": item.get("reason")})
        self.task_model.replace(rows)
        pagination = result.get("pagination") or {}
        self.task_caption.setText(f"Задачи · {pagination.get('total', len(rows))} всего · показано {len(rows)}")
        self.project_panels.set_page(
            "tasks",
            rows,
            total=pagination.get("total"),
            offset=pagination.get("offset", 0),
            has_more=pagination.get("next_offset") is not None,
            source="Checklist проекта · сохранённые данные",
        )
        if rows:
            self.task_table.selectRow(0)

    def show_task(self, current, _previous):
        if not current.isValid() or not self.project_directory:
            return
        item = self.task_model.rows[current.row()]
        self.select_project_task(item.get("id"))

    def select_project_task(self, item_id):
        if not self.project_directory:
            return
        if isinstance(item_id, str):
            self.start_command(
                "task-detail",
                "seo_project_task_detail",
                {"directory": self.project_directory, "item_id": item_id},
                self.load_task_detail,
            )

    def load_task_detail(self, result):
        self.task_detail.setPlainText(readable_record(result, heading="Задача · сохранённый контекст"))

    def load_scans(self, result):
        rows = [
            {**item, "partial": "да" if item.get("crawl_partial") is True or item.get("corpus_partial") is True else "нет" if item.get("crawl_partial") is False and item.get("corpus_partial") is False else None}
            for item in result.get("items") or []
        ]
        old = next((item for item in self.scan_model.rows if item.get("path") == self.selected_scan_path), None)
        selection = self.scan_table.selectionModel()
        selection.blockSignals(True)
        self.scan_model.replace(rows)
        self.project_panels.panel("compare").set_scans(rows)
        self.scan_picker.blockSignals(True)
        self.scan_picker.clear()
        for item in rows:
            stamp = field_text("finished_at", item.get("finished_at") or item.get("created_at"))
            self.scan_picker.addItem(f"{stamp} · {state_text(item.get('source_kind'))} · {str(item.get('uuid') or 'ID неизвестен')[:8]}", item)
        if not rows:
            self.scan_picker.addItem("В проекте нет сохранённых сканов", None)
        self.scan_picker.setEnabled(bool(rows))
        self.scan_picker.blockSignals(False)
        self.scan_caption.setText(f"Сканы · {result.get('total', len(rows))} сохранено · показано {len(rows)}")
        self.project_panels.set_page(
            "scans", rows, total=result.get("total"),
            offset=(result.get("pagination") or {}).get("offset", 0),
            has_more=(result.get("pagination") or {}).get("next_offset") is not None,
            source="Сохранённые сканы проекта",
        )
        index = next((index for index, item in enumerate(rows) if item.get("path") == self.selected_scan_path), 0)
        if rows:
            self.scan_table.selectRow(index)
            self.scan_picker.blockSignals(True)
            self.scan_picker.setCurrentIndex(index)
            self.scan_picker.blockSignals(False)
        selection.blockSignals(False)
        if rows:
            selected = rows[index]
            if self._reload_selected_scan or selected.get("path") != self.selected_scan_path or selected != old:
                self._reload_selected_scan = False
                self.select_project_scan(selected)
        elif result.get("total") == 0 or not self.selected_scan_path:
            self.clear_scan_selection("В этом проекте нет сохранённых сканов")

    def clear_scan_selection(self, reason):
        self.content_search.clear(reason)
        self.selected_scan_path = None
        self.selected_scan_uuid = None
        self.update_content_search_context()
        self.clear_url_selection(reason)
        self.model.replace([])
        self.search.clear()
        self.search.setEnabled(False)
        self.audit_workspace.clear(reason)
        for view in (self.detail, self.debug_detail, self.link_detail, self.evidence_detail, self.scan_detail, self.headers_detail):
            view.setPlainText(reason)
        self.resume_scan_button.setEnabled(False)
        self._resume_eligible_path = None
        self.scan_progress.hide()
        self.scan_progress_label.setText("Прогресс выбранного скана не измерен")
        self.scan_state_badge.set_state("unknown")
        self.url_caption.setText("URL · данные не загружены")
        self.url_empty.setText(reason)
        self.url_empty.show()
        self.table.hide()

    def select_scan_from_picker(self):
        scan = self.scan_picker.currentData()
        if isinstance(scan, dict) and scan.get("path") != self.selected_scan_path:
            self.select_project_scan(scan)

    def show_scan(self, current, _previous):
        if not current.isValid():
            return
        scan = self.scan_model.rows[current.row()]
        self.select_project_scan(scan)

    def select_project_scan(self, scan):
        path = scan.get("path")
        if not isinstance(path, str) or not path:
            self.scan_detail.setPlainText("Ядро не предоставило путь сохранённого скана.")
            return
        self.clear_scan_selection("Выбран другой скан. Загрузка сохранённых данных…")
        self.selected_scan_path = path
        self.selected_scan_uuid = scan.get("uuid") if isinstance(scan.get("uuid"), str) else None
        self.update_content_search_context()
        self.model.replace([])
        self.search.clear()
        self.search.setEnabled(False)
        self.audit_workspace.set_page(
            "internal", [], state="loading", source="Чтение сохранённого скана", reason="Выбран другой скан. Загрузка сохранённых данных…"
        )
        self.audit_workspace.set_page(
            "url_details", [], state="unavailable", reason="Выбран другой скан. Загрузка сохранённых данных…"
        )
        self.resume_scan_button.setEnabled(False)
        self.scan_detail.setPlainText(readable_record({key: scan.get(key) for key in ("start_url", "uuid", "source_kind", "lifecycle", "finish_reason", "created_at", "finished_at", "crawl_partial", "corpus_partial", "writer_revision", "path")}, heading="Сохранённый скан"))
        self.scan_state_badge.set_state("partial" if scan.get("crawl_partial") or scan.get("corpus_partial") else scan.get("lifecycle"))
        self.scan_picker.blockSignals(True)
        for index in range(self.scan_picker.count()):
            item = self.scan_picker.itemData(index)
            if isinstance(item, dict) and item.get("path") == path:
                self.scan_picker.setCurrentIndex(index)
                selection = self.scan_table.selectionModel()
                selection.blockSignals(True)
                self.scan_table.selectRow(index)
                selection.blockSignals(False)
                break
        self.scan_picker.blockSignals(False)
        self.start_command(
            scan_request_key(self.current_project_uuid, path, "url-page"),
            "seo_scan_inspect",
            {"input_path": path, "table": "pages", "limit": PAGE_LIMIT, "offset": 0},
            lambda result, path=path: self.load_urls(result, path),
        )
        self.start_command(
            scan_request_key(self.current_project_uuid, path, "scan-status"),
            "seo_scan_status",
            {"input_path": path},
            lambda result, path=path: self.load_scan_status(result, path),
        )

    def load_scan_status(self, result, scan_path=None):
        if scan_path is not None and scan_path != self.selected_scan_path:
            return
        self.evidence_detail.setPlainText(readable_record(result, heading="Сохранённый снимок и происхождение"))
        source = result.get("source") if isinstance(result.get("source"), dict) else {}
        self.scan_state_badge.set_state("partial" if source.get("crawl_partial") or source.get("corpus_partial") else source.get("lifecycle"))
        frontier = result.get("frontier") or {}
        counts = frontier.get("counts") or {}
        done, queued, inflight = (counts.get(key) for key in ("done", "queued", "inflight"))
        measured = frontier.get("state") == "available" and all(type(value) is int and value >= 0 for value in (done, queued, inflight))
        total = done + queued + inflight if measured else 0
        self.scan_progress.setVisible(measured and total > 0)
        if measured and total > 0:
            self.scan_progress.setValue(round(1000 * done / total))
            self.scan_progress_label.setText(f"Сохранённый скан {self.selected_scan_uuid or source.get('scan_uuid') or 'ID неизвестен'} · обработано известных URL: {done} из {total} · очередь {queued} · в работе {inflight}. Размер сайта не измерен.")
        else:
            self.scan_progress_label.setText("Прогресс известных URL не измерен: нет полного набора счётчиков.")
        self.summary_scan.setText(state_text(source.get("lifecycle")))
        outcomes = result.get("committed_page_outcomes")
        if isinstance(outcomes, dict):
            aggregate_rows = [{"name": "Без ответа" if key == "no_response" else "Другие" if key == "other" else key, "urls": value} for key, value in outcomes.items()]
            self.audit_workspace.set_page("overview", aggregate_rows, total=len(aggregate_rows), source="Сохранённые HTTP-ответы · доля по всему сайту не измерена")
        self._resume_eligible_path = scan_path if scan_path and source.get("lifecycle") == "interrupted" and Path(scan_path).is_file() else None
        self.update_resume_control()

    def load_urls(self, result, scan_path=None):
        if scan_path is not None and scan_path != self.selected_scan_path:
            return
        self.selected_url = None
        self.link_detail.setPlainText("Выберите URL в текущей странице")
        self.audit_workspace.set_page("inlinks", [], state="unavailable", reason="Выберите URL в новой странице")
        rows = []
        for page in result.get("rows") or []:
            rows.append({
                "url": page.get("url"),
                "status": page.get("status_code", page.get("status")),
                "type": page.get("content_type", page.get("type")),
                "indexability": page.get("indexability", page.get("indexable")),
                "title": page.get("title"),
                "issues": page.get("finding_count"),
                "_retained": page,
            })
        self.clear_url_selection("Выберите URL в загруженной странице")
        self.model.replace(rows)
        self.search.setEnabled(True)
        self.search.setPlaceholderText("Поиск в загруженной странице сохранённого скана")
        self.update_url_count()
        self.url_scope_caption.setText(f"Скан {self.selected_scan_uuid or 'ID неизвестен'} · поиск и сортировка только в загруженной странице")
        self.summary_records.setText(str(len(rows)))
        self.summary_source.setText("Native · сохранённый скан")
        self.summary_coverage.setText("Размер сайта не измерен")
        self.summary_note.setPlainText(readable_record({key: result.get(key) for key in ("offset", "has_more", "next_offset", "truncated", "bytes")}, heading="Текущая страница URL") + "\n\nПоиск и сортировка применяются к загруженной странице, не ко всему скану.")
        self.audit_workspace.set_page(
            "internal",
            rows,
            total=None,
            offset=result.get("offset", 0),
            has_more=bool(result.get("has_more")),
            source=f"Скан {self.selected_scan_uuid or 'ID неизвестен'} · загруженная страница",
            available_filters=("all",),
        )
        if rows and self.navigation.currentRow() == 1 and self.proxy.rowCount():
            self.table.selectRow(0)
        elif rows and self.navigation.currentRow() == 2 and self.audit_workspace.main.current_id == "internal":
            panel = self.audit_workspace.panel("internal")
            if panel.proxy.rowCount():
                panel.table.selectRow(0)

    def load_inbox(self, result):
        self.inbox_revision = result.get("revision")
        rows = list(result.get("entries") or [])
        self.inbox_model.replace(rows)
        total = (result.get("pagination") or {}).get("total", len(rows))
        self.inbox_caption.setText(f"Входящие · {total} сохранённых записей")
        self.project_panels.set_page(
            "inbox", rows, total=total, source="Сохранённые входящие проекта"
        )
        self.update_note_controls()
        if rows:
            self.inbox_table.selectRow(0)
        else:
            self.inbox_detail.setPlainText("В этом проекте пока нет сохранённых заметок")

    def load_unread(self, result):
        count = result.get("count")
        if type(count) is not int:
            self.source_badge.setText("Локальный проект · непрочитанные не измерены")
            return
        label = "сообщений" if count != 1 else "сообщение"
        self.source_badge.setText(f"Локальный проект · {count} непрочит. {label}")

    def show_inbox_entry(self, current, _previous):
        if not current.isValid():
            return
        self.inbox_detail.setPlainText(readable_record(self.inbox_model.rows[current.row()], heading="Запись входящих"))

    def note_project_key(self):
        if not self.project_directory:
            return None
        return (self.current_project_uuid or "", str(Path(self.project_directory).resolve()))

    def stash_note_drafts(self):
        key = self.note_project_key()
        if key is None or not hasattr(self, "note_input"):
            return
        panel = self.project_panels.panel("inbox")
        self._note_drafts[key] = {"main": (self.note_input.text(), self.note_kind.currentData()), "project": (panel.note.toPlainText(), panel.kind.currentData())}

    def restore_note_drafts(self):
        draft = self._note_drafts.get(self.note_project_key(), {})
        panel = self.project_panels.panel("inbox")
        for name, edit, combo in (("main", self.note_input, self.note_kind), ("project", panel.note, panel.kind)):
            text, kind = draft.get(name, ("", "note"))
            edit.blockSignals(True)
            (edit.setText if name == "main" else edit.setPlainText)(text)
            edit.blockSignals(False)
            combo.setCurrentIndex(max(0, combo.findData(kind)))
        self.update_note_controls()

    def update_note_controls(self):
        if not hasattr(self, "note_submit"):
            return
        panel = self.project_panels.panel("inbox")
        ready = bool(self.project_directory) and type(self.inbox_revision) is int and not self._project_loading and self._pending_note is None
        length = len(self.note_input.text().strip())
        self.note_submit.setEnabled(ready and 0 < length <= 4000 and self.note_kind.currentData() in {"note", "proposed_goal"})
        destination = f"Проект: {self.project_picker.currentText()} · {self.project_directory}" if self.project_directory else "Откройте проект"
        reason = "Сохранение…" if self._pending_note is not None else "Ожидание состояния входящих" if not ready else "Черновик относится только к этому проекту"
        if hasattr(self, "note_destination"):
            self.note_destination.setText(destination + " · " + reason)
        panel.set_submission_enabled(ready, destination + " · " + reason)

    def submit_note(self, supplied_text=None, supplied_kind=None):
        if not self.project_directory or self._project_loading or self._pending_note is not None:
            return
        if type(self.inbox_revision) is not int:
            self.notice.show_error("Состояние входящих ещё не получено. Обновите проект перед сохранением; черновик остаётся в форме.")
            return
        if isinstance(supplied_text, bool):
            supplied_text = None
        source = "project" if supplied_text is not None else "main"
        text = (supplied_text if supplied_text is not None else self.note_input.text()).strip()
        kind = supplied_kind or self.note_kind.currentData()
        if not 0 < len(text) <= 4000:
            self.notice.show_error("Введите заметку до 4 000 символов. Текст остаётся в форме.")
            return
        if kind not in {"note", "proposed_goal"}:
            self.notice.show_error("Выбранный тип сообщения не поддержан ядром. Выберите заметку или предложенную цель; текст не изменён.")
            return
        pending = {"key": self.note_project_key(), "source": source, "text": text, "kind": kind}
        self._pending_note = pending
        self.update_note_controls()
        self.start_command("inbox-submit", "seo_project_inbox_submit", {"directory": self.project_directory, "text": text, "kind": kind, "author_role": "specialist", "expected_revision": self.inbox_revision}, lambda result: self.note_saved(result, pending))
        if self._pending_note is pending and "inbox-submit" not in self.requests:
            self._pending_note = None
            self.update_note_controls()

    def note_saved(self, _result, pending=None):
        pending = pending or self._pending_note
        if not pending:
            return
        if self._pending_note is pending:
            self._pending_note = None
        if self.notice.context == "inbox-submit":
            self.notice.hide()
        if pending["key"] != self.note_project_key():
            saved = self._note_drafts.get(pending["key"], {})
            if saved.get(pending["source"]) == (pending["text"], pending["kind"]):
                saved[pending["source"]] = ("", pending["kind"])
            return
        panel = self.project_panels.panel("inbox")
        edit, combo = (panel.note, panel.kind) if pending["source"] == "project" else (self.note_input, self.note_kind)
        current = edit.toPlainText() if pending["source"] == "project" else edit.text()
        if current.strip() == pending["text"] and combo.currentData() == pending["kind"]:
            edit.clear()
        self.stash_note_drafts()
        self.update_note_controls()
        self.statusBar().showMessage("Заметка сохранена в выбранном проекте; остальные черновики сохранены")
        self.refresh_project()

    def clear_url_selection(self, reason="Выберите сохранённый URL"):
        self.selected_url = None
        self.url_selection_generation += 1
        self.pending_commands.pop("url-detail", None)
        self.pending_commands.pop("url-links", None)
        for view in (self.detail, self.debug_detail, self.link_detail, self.headers_detail):
            view.setPlainText(reason)
        self.audit_workspace.detail.clear(reason)

    def update_url_count(self):
        found, loaded = self.proxy.rowCount(), self.model.rowCount()
        self.url_caption.setText(f"URL · {found} из {loaded} на странице" if self.search.text() else f"URL · {loaded} записей")
        if hasattr(self, "url_empty"):
            self.url_empty.setText("По вашему поиску нет URL в этой странице" if self.search.text() else "Выберите сохранённый скан")
            self.url_empty.setVisible(found == 0)
            self.table.setVisible(found > 0)

    def filter_urls(self, text):
        if not hasattr(self, "table"):
            return
        self.clear_url_selection("Поиск изменён. Выберите URL из результатов или нажмите Enter.")
        selection = self.table.selectionModel()
        selection.blockSignals(True)
        self.proxy.setFilterFixedString(text)
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        selection.blockSignals(False)
        self.update_url_count()

    def show_url(self, current, _previous):
        source = self.proxy.mapToSource(current)
        if not source.isValid():
            self.clear_url_selection("Выберите сохранённый URL")
            return
        row = self.model.rows[source.row()]
        retained = row.get("_retained")
        if retained:
            self.detail.setPlainText(readable_record(retained, heading="Сохранённые данные URL"))
            self.debug_detail.setPlainText(json.dumps(retained, ensure_ascii=False, indent=2))
            self.show_retained_url(row)
            return
        self.detail.setPlainText(
            "Демо · синтетическая запись\n\n"
            f"Адрес: {row['url']}\nHTTP: {row.get('status', 'Не измерено')}\n"
            f"Тип: {row.get('type', 'Не измерено')}\nИндексация: {row.get('indexability', 'Не измерено')}\n"
            f"Title: {row.get('title') or 'Не измерено'}\nПроблемы: {row.get('issues', 'Не измерено')}\n\n"
            "Это демонстрационные данные. Headers, HTML и cookies не измерялись."
        )
        self.debug_detail.setPlainText(json.dumps(row, ensure_ascii=False, indent=2))

    def load_url_links(self, result, scan_path=None, url=None, selection_generation=None):
        if selection_generation is not None and selection_generation != self.url_selection_generation:
            return
        if scan_path != self.selected_scan_path or url != self.selected_url:
            return
        self.link_detail.setPlainText(readable_record(result, heading="Сохранённые входящие ссылки"))
        self.audit_workspace.set_page(
            "inlinks",
            result.get("items") or [],
            total=result.get("total"),
            offset=result.get("offset", 0),
            has_more=bool(result.get("has_more")),
            source="Сохранённые входящие ссылки",
        )

    def load_url_detail(self, result, scan_path=None, url=None, selection_generation=None):
        if selection_generation is not None and selection_generation != self.url_selection_generation:
            return
        if scan_path != self.selected_scan_path or url != self.selected_url:
            return
        page = result.get("page") or {}
        visible = {key: page.get(key) for key in ("url", "status_code", "content_type", "title", "meta_description", "h1", "canonical", "meta_robots", "x_robots", "response_time", "size_bytes", "word_count", "crawl_depth", "outlinks", "external_outlinks") if key in page}
        self.detail.setPlainText(readable_record(visible, heading="Сохранённые данные URL") + "\n\nИсточник и ревизия: " + value_text(result.get("source")))
        self.debug_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        responses = (result.get("responses") or {}).get("items") or []
        header_rows = []
        if responses:
            response = responses[0]
            for direction, key in (("Запрос", "request_headers"), ("Ответ", "response_headers")):
                for name, value in (response.get(key) or [])[:40]:
                    redacted = str(name).casefold() in {"authorization", "proxy-authorization", "cookie", "set-cookie"}
                    header_rows.append({"name": str(name), "value": "[скрыто]" if redacted else value, "direction": direction})
            self.headers_detail.setPlainText(readable_record({"response_id": response.get("response_id"), "requested_at": response.get("requested_at"), "received_at": response.get("received_at"), "headers": header_rows}, heading="HTTP headers · первый сохранённый ответ URL"))
            self.audit_workspace.set_page("http_headers", header_rows, total=len(header_rows), source="Первый сохранённый ответ URL · чувствительные значения скрыты")
        else:
            self.headers_detail.setPlainText("В сохранённой детали нет HTTP-ответа. Новые запросы при выборе URL не выполняются.")
        rows = [{"name": FIELDS.get(key, key), "value": value} for key, value in page.items()]
        self.audit_workspace.set_page(
            "url_details",
            rows,
            total=len(rows),
            source="Сохранённая деталь URL · безопасная проекция ядра",
            state="ready" if result.get("state") == "available" else "unavailable",
            reason=result.get("reason", ""),
        )

    def handle_audit_intent(self, intent, payload):
        if intent == "clear_url":
            self.clear_url_selection("Выберите URL в текущем разделе")
            return
        if intent == "refresh":
            self.refresh_project()
            return
        if intent == "query" and isinstance(payload, dict) and payload.get("tab_id") == "internal":
            if not self.selected_scan_path:
                return
            self.start_command(
                scan_request_key(self.current_project_uuid, self.selected_scan_path, "url-page"),
                "seo_scan_inspect",
                {
                    "input_path": self.selected_scan_path,
                    "table": "pages",
                    "limit": min(100, int(payload.get("limit", PAGE_LIMIT))),
                    "offset": max(0, int(payload.get("offset", 0))),
                },
                lambda result, path=self.selected_scan_path: self.load_urls(result, path),
            )
            return
        if intent != "select_url" or not isinstance(payload, dict):
            return
        row = payload.get("row")
        if not isinstance(row, dict):
            return
        self.show_retained_url(row)

    def show_retained_url(self, row):
        if not self.selected_scan_path or not isinstance(row.get("url"), str):
            return
        self.clear_url_selection("Загрузка выбранного URL…")
        self.selected_url = row["url"]
        selection_generation = self.url_selection_generation
        self.link_detail.setPlainText("Загрузка ссылок выбранного URL…")
        self.headers_detail.setPlainText("HTTP headers выбранного URL загружаются…")
        self.audit_workspace.set_page("inlinks", [], state="loading", reason="Загрузка выбранного URL…")
        self.audit_workspace.set_page("url_details", [], state="loading", reason="Загрузка выбранного URL…")
        self.start_command(
            scan_request_key(self.current_project_uuid, self.selected_scan_path, "url-detail"),
            "seo_scan_url_detail",
            {
                "input_path": self.selected_scan_path,
                "url": row["url"],
                "response_limit": 10,
                "form_limit": 20,
                "max_bytes": 262144,
            },
            lambda result, path=self.selected_scan_path, url=row["url"], token=selection_generation: self.load_url_detail(result, path, url, token),
        )
        self.start_command(
            scan_request_key(self.current_project_uuid, self.selected_scan_path, "url-links"),
            "seo_scan_link_inspect",
            {
                "input_path": self.selected_scan_path,
                "view": "inlinks",
                "target": row["url"],
                "limit": 20,
                "max_bytes": 262144,
            },
            lambda result, path=self.selected_scan_path, url=row["url"], token=selection_generation: self.load_url_links(result, path, url, token),
        )

    def load_comparison(self, payload):
        panel = self.project_panels.panel("compare")
        panel.set_page(payload.get("rows") or [], total=payload.get("total"), offset=payload.get("offset", 0), has_more=bool(payload.get("has_more")), state=payload.get("state", "unavailable"), reason=payload.get("reason", ""), source=payload.get("source", "Сравнение сохранённых данных"), available_filters=("all",))
        panel.set_summary(payload)

    def handle_project_intent(self, intent, payload):
        if intent == "preview_compare" and isinstance(payload, dict) and self.project_directory:
            self.comparison.start(payload.get("before"), payload.get("after"))
            return
        if intent != "query" or not isinstance(payload, dict) or not self.project_directory:
            return
        tab_id = payload.get("tab_id")
        limit = min(100, max(1, int(payload.get("limit", PAGE_LIMIT))))
        offset = max(0, int(payload.get("offset", 0)))
        if tab_id == "compare":
            self.comparison.page(offset, limit)
        elif tab_id == "tasks":
            self.start_command(
                "tasks",
                "seo_project_checklist_page",
                {"directory": self.project_directory, "limit": limit, "offset": offset},
                self.load_tasks,
            )
        elif tab_id == "scans":
            self.start_command(
                "scans",
                "seo_project_scans",
                {"directory": self.project_directory, "limit": limit, "offset": offset},
                self.load_scans,
            )
        elif tab_id == "inbox":
            self.start_command(
                "inbox",
                "seo_project_inbox_list",
                {
                    "directory": self.project_directory,
                    "consumer": CONSUMER_ID,
                    "limit": limit,
                    "offset": offset,
                },
                self.load_inbox,
            )

    def ensure_monitor(self):
        if self.monitor is None:
            self.monitor = ProjectMonitor(self, configure_table)
            self.addDockWidget(Qt.RightDockWidgetArea, self.monitor)
            self.monitor.hide()
        self.monitor.sync_context()
        return self.monitor

    def open_monitor_window(self):
        monitor = self.ensure_monitor()
        monitor.setFloating(True)
        monitor.show()
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        monitor.resize(min(540, available.width()), min(720, available.height()))
        monitor.move(min(self.frameGeometry().right() + 12, available.right() - monitor.width() - 12), max(available.top(), self.frameGeometry().top()))
        keep_on_screen(monitor, screen)
        monitor.raise_()
        monitor.activateWindow()
        return monitor

    def apply_layout(self, identifier):
        if identifier not in LAYOUTS:
            return
        self.current_layout = identifier
        self._focus_mode = False
        if identifier != "monitor" and self.monitor is not None:
            self.monitor.hide()
            if self._single_window_geometry is not None:
                self.restoreGeometry(self._single_window_geometry)
                self._single_window_geometry = None
        if identifier == "compare":
            self.open_comparison()
        else:
            self.navigation.setCurrentRow(1)
            self.set_panel_visible("Инспектор URL", identifier != "table")
            self._panel_intent["Сводка"] = None if identifier == "url" else False
            self.set_panel_visible("Сводка", identifier == "url" and self.width() >= theme_tokens()["layout"]["compact_breakpoint"], remember=False)
            self.horizontal.setSizes([1000, 280])
            self.vertical.setSizes([440, 230])
        if identifier == "monitor":
            if self._single_window_geometry is None:
                self._single_window_geometry = self.saveGeometry()
            if self.isFullScreen():
                self.showNormal()
            available = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            self.resize(max(self.minimumWidth(), min(self.width(), available.width() - 580)), min(self.height(), available.height()))
            self.move(available.topLeft())
            self.set_panel_visible("Сводка", False)
            self.open_monitor_window()
        keep_on_screen(self)
        self.statusBar().showMessage("Раскладка: " + LAYOUTS[identifier])

    def save_workspace_layout(self):
        if not self.settings:
            return
        self.settings.setValue("workspace/schema", LAYOUT_SCHEMA)
        self.settings.setValue("workspace/layout", self.current_layout)
        self.settings.setValue("workspace/density", self._density)
        self.settings.setValue("workspace/main_state", self.saveState(LAYOUT_SCHEMA))
        self.settings.setValue("workspace/horizontal", self.horizontal.saveState())
        self.settings.setValue("workspace/vertical", self.vertical.saveState())
        self.settings.setValue("workspace/navigation_compact", bool(self.navigation.property("compact")))
        self.settings.setValue("workspace/navigation_intent", "auto" if self._navigation_compact_intent is None else self._navigation_compact_intent)
        for identifier, name in PANEL_IDS.items():
            self.settings.setValue("workspace/panel/" + identifier, self.panel_actions[name].isChecked())
            self.settings.setValue("workspace/panel_intent/" + identifier, "auto" if self._panel_intent[name] is None else self._panel_intent[name])
        row = self.navigation.currentRow()
        self.settings.setValue("workspace/view", VIEW_IDS[row] if 0 <= row < len(VIEW_IDS) else "url")
        self.settings.setValue("workspace/monitor_exists", self.monitor is not None)
        self.settings.setValue("workspace/monitor_visible", self.monitor is not None and not self.monitor.isHidden())
        if self.monitor is not None:
            self.settings.setValue("workspace/monitor_geometry", self.monitor.saveGeometry())
        self.settings.sync()

    def restore_workspace_layout(self):
        if not self.settings or self.settings.value("workspace/schema", 0, type=int) != LAYOUT_SCHEMA:
            return
        if self.settings.value("workspace/monitor_exists", False, type=bool):
            self.ensure_monitor()
        state = self.settings.value("workspace/main_state")
        if state:
            self.restoreState(state, LAYOUT_SCHEMA)
        for key, widget in (("horizontal", self.horizontal), ("vertical", self.vertical)):
            saved = self.settings.value("workspace/" + key)
            if saved:
                widget.restoreState(saved)
        for identifier, name in PANEL_IDS.items():
            intent = self.settings.value("workspace/panel_intent/" + identifier, None)
            self._panel_intent[name] = None if intent == "auto" else self.settings.value("workspace/panel/" + identifier, True, type=bool) if intent is None else str(intent).lower() in {"true", "1"}
            self.set_panel_visible(name, self.settings.value("workspace/panel/" + identifier, True, type=bool), remember=False)
        intent = self.settings.value("workspace/navigation_intent", "auto")
        self._navigation_compact_intent = None if intent == "auto" else str(intent).lower() in {"true", "1"}
        self.set_navigation_compact(self.settings.value("workspace/navigation_compact", False, type=bool))
        density = self.settings.value("workspace/density", "standard")
        if density in theme_tokens()["density"]:
            self.set_density(density)
        identifier = self.settings.value("workspace/layout", "url")
        self.current_layout = identifier if identifier in LAYOUTS else "url"
        view = self.settings.value("workspace/view", "url")
        if view in VIEW_IDS:
            self.navigation.setCurrentRow(VIEW_IDS.index(view))
        if self.monitor is not None:
            geometry = self.settings.value("workspace/monitor_geometry")
            if geometry:
                self.monitor.restoreGeometry(geometry)
            self.monitor.setVisible(self.settings.value("workspace/monitor_visible", False, type=bool))
            keep_on_screen(self.monitor)
        keep_on_screen(self)

    def action_registry(self):
        actions = [
            {"title": "Открыть проект…", "keywords": "open folder проект папка", "callback": self.choose_project},
            {"title": "URL · найти в текущей странице", "keywords": "поиск url search", "callback": self.open_url_search},
            {"title": "Сравнить сохранённые сканы", "keywords": "compare before after до после", "callback": self.open_comparison},
            {"title": "Новый скан · открыть план", "keywords": "scan spider sitemap конфигурация настройки", "callback": self.scan_preview, "enabled": bool(self.project_directory), "reason": "Открывает план; скан запускается отдельной кнопкой" if self.project_directory else "Сначала откройте проект"},
            {"title": "Монитор в отдельном окне", "keywords": "monitor окно второе два", "callback": self.open_monitor_window},
            {"title": "Восстановить панели", "keywords": "панели restore reset", "callback": self.restore_panels},
            {"title": "Обновить сохранённые данные", "keywords": "refresh чтение", "callback": self.refresh_project, "enabled": bool(self.project_directory), "reason": "Чтение текущего проекта"},
        ]
        for identifier, title in LAYOUTS.items():
            actions.append({"title": "Раскладка · " + title, "keywords": "layout вид панели", "callback": lambda identifier=identifier: self.apply_layout(identifier)})
        for identifier, title in (("compact", "Компактные строки · 28 px"), ("standard", "Обычные строки · 32 px"), ("comfortable", "Свободные строки · 40 px")):
            actions.append({"title": title, "keywords": "density плотность таблица", "callback": lambda identifier=identifier: self.set_density(identifier)})
        for preset in (*SEARCH_PRESETS, {"id": None, "label": "Текст в сохранённом HTML"}):
            actions.append({"title": "Поиск · " + preset["label"], "keywords": "html body код теги search " + str(preset["id"]), "callback": lambda identifier=preset["id"]: self.open_content_search(identifier), "enabled": self.content_search.available, "reason": "Открывает форму без запуска поиска" if self.content_search.available else "Подключённое ядро не поддерживает поиск по сохранённым телам"})
        return actions

    def show_action_finder(self):
        dialog = ActionFinder(self.action_registry(), self)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_callback:
            QTimer.singleShot(0, dialog.selected_callback)

    def open_url_search(self):
        self.navigation.setCurrentRow(1)
        self.search.setFocus()
        self.search.selectAll()

    def navigate(self, row):
        self.pages.setCurrentIndex(3 if row == 9 else 9 if row == 10 else row)
        if row == 9:
            self.project_panels.select_tab("compare")
        elif row == 1 and not self.table.currentIndex().isValid() and self.proxy.rowCount():
            self.table.selectRow(0)

    def update_content_search_context(self):
        if hasattr(self, "content_search_panel"):
            self.content_search_panel.set_context(self.project_picker.currentText(), self.selected_scan_uuid, self.content_search.available)

    def load_content_search(self, payload):
        self.update_content_search_context()
        self.content_search_panel.set_payload(payload)

    def open_content_search(self, preset_id=None):
        self.navigation.setCurrentRow(10)
        self.update_content_search_context()
        self.content_search_panel.set_preset(preset_id)

    def open_comparison(self):
        self.navigation.setCurrentRow(9)
        self.project_panels.select_tab("compare")

    def remember_project(self, label, path):
        self.recent_projects = [{"label": label, "path": path}, *[item for item in self.recent_projects if item["path"] != path]][:20]
        if self.settings:
            self.settings.setValue("recent_projects", self.recent_projects)

    def fill_project_picker(self, label):
        self.project_picker.blockSignals(True)
        self.project_picker.clear()
        self.project_picker.addItem(label, {"path": self.project_directory})
        for item in self.recent_projects:
            if item["path"] != self.project_directory:
                self.project_picker.addItem(item["label"], dict(item))
                self.project_picker.setItemData(self.project_picker.count() - 1, item["path"], Qt.ToolTipRole)
        self.project_picker.addItem("Открыть другой проект…", {"action": "open"})
        self.project_picker.blockSignals(False)
        self.project_picker.setToolTip(self.project_directory or "Выбрать локальный проект")

    def activate_project_picker(self, index):
        item = self.project_picker.itemData(index)
        self.project_picker.blockSignals(True)
        self.project_picker.setCurrentIndex(0)
        self.project_picker.blockSignals(False)
        if isinstance(item, dict) and item.get("action") == "open":
            self.choose_project()
        elif isinstance(item, dict) and item.get("path") and item["path"] != self.project_directory:
            self.read_project(item["path"])

    def set_reduced_motion(self, enabled):
        self.reduced_motion = bool(enabled) or self.system_reduced_motion
        if self.reduced_motion:
            self._navigation_animation.stop()
            self.set_navigation_compact(bool(self.navigation.property("compact")))
        if self.settings:
            self.settings.setValue("reduced_motion", self.reduced_motion)

    def set_navigation_compact(self, compact, animate=False):
        width = theme_tokens()["layout"]["navigation_rail" if compact else "navigation_width"]
        self._navigation_animation.stop()
        if animate and not self.reduced_motion:
            self._navigation_animation.setStartValue(self.navigation.width())
            self._navigation_animation.setEndValue(width)
            self._navigation_animation.start()
        else:
            self.navigation.setFixedWidth(width)
        self.navigation.setProperty("compact", compact)
        for index, title in enumerate(self.navigation_labels):
            item = self.navigation.item(index)
            item.setText("" if compact else title)
            item.setToolTip(title)
            item.setTextAlignment(Qt.AlignCenter if compact else Qt.AlignLeft | Qt.AlignVCenter)
        self.navigation.style().unpolish(self.navigation)
        self.navigation.style().polish(self.navigation)

    def toggle_navigation(self):
        self.set_panel_visible("Навигация", True)
        self.navigation.show()
        self._navigation_compact_intent = not bool(self.navigation.property("compact"))
        self.set_navigation_compact(self._navigation_compact_intent, animate=True)

    def panel_action_changed(self, name, widget, shown):
        if not self._syncing_panel:
            self._panel_intent[name] = bool(shown)
        widget.setVisible(shown)

    def set_panel_visible(self, name, visible, remember=True):
        if remember:
            self._panel_intent[name] = bool(visible)
        action = self.panel_actions.get(name)
        if action is not None:
            self._syncing_panel = True
            action.setChecked(visible)
            {"Навигация": self.navigation, "Сводка": self.overview, "Инспектор URL": self.inspector}[name].setVisible(visible)
            self._syncing_panel = False

    def sync_workspace_width(self):
        if not hasattr(self, "panel_actions"):
            return
        compact = self.centralWidget().width() < theme_tokens()["layout"]["compact_breakpoint"]
        if compact != self._compact:
            self._compact = compact
            self.set_navigation_compact(compact if self._navigation_compact_intent is None else self._navigation_compact_intent)
            wanted = self._panel_intent["Сводка"]
            self.set_panel_visible("Сводка", (not compact if wanted is None else wanted) and not self._focus_mode, remember=False)
            self.audit_workspace.right.setVisible(not compact and not self._focus_mode)

    def eventFilter(self, watched, event):
        if watched is self.centralWidget() and event.type() == QEvent.Resize:
            self.sync_workspace_width()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        self.sync_workspace_width()
        super().resizeEvent(event)

    def restore_panels(self):
        self._focus_mode = False
        for action in self.panel_actions.values():
            action.setChecked(True)
        for widget in (self.navigation, self.overview, self.inspector):
            widget.show()
        self.horizontal.setSizes([1000, 280])
        self.vertical.setSizes([440, 230])
        self.audit_workspace.restore_panels()

    def toggle_focus_mode(self):
        self._focus_mode = not self._focus_mode
        if self._focus_mode:
            self._panel_visibility = {name: action.isChecked() for name, action in self.panel_actions.items()}
            for name in ("Сводка", "Инспектор URL"):
                self.set_panel_visible(name, False, remember=False)
            self._audit_visibility = (not self.audit_workspace.detail.isHidden(), not self.audit_workspace.right.isHidden())
            self.audit_workspace.detail.hide()
            self.audit_workspace.right.hide()
        else:
            for name, visible in self._panel_visibility.items():
                self.set_panel_visible(name, visible, remember=False)
            self._compact = None
            self.sync_workspace_width()
            for widget, visible in zip((self.audit_workspace.detail, self.audit_workspace.right), self._audit_visibility):
                widget.setVisible(visible)

    def set_density(self, density):
        self._density = density
        QApplication.instance().setProperty("seohead.density", density)
        height = theme_tokens()["density"][density]
        for table in self.findChildren(QTableView):
            table.verticalHeader().setDefaultSectionSize(height)

    def focus_search(self):
        current = self.pages.currentWidget()
        search = next((field for field in current.findChildren(QLineEdit) if field.isVisible() and field.isEnabled() and ("Поиск" in field.accessibleName() or "Поиск" in field.placeholderText())), None)
        if search:
            search.setFocus()
            search.selectAll()

    def focus_next_region(self):
        areas = [self.navigation, self.pages.currentWidget()]
        if self.pages.currentIndex() == 1:
            areas = [self.navigation, self.table, self.inspector, self.overview]
        current = QApplication.focusWidget()
        index = next((index for index, area in enumerate(areas) if current is area or area.isAncestorOf(current)), -1) if current else -1
        for step in range(1, len(areas) + 1):
            area = areas[(index + step) % len(areas)]
            target = next((widget for widget in [area, *area.findChildren(QWidget)] if widget.isVisible() and widget.isEnabled() and widget.focusPolicy() & Qt.TabFocus), None)
            if target:
                target.setFocus(Qt.ShortcutFocusReason)
                return

    def copy_url_selection(self):
        current = self.table.currentIndex()
        if current.isValid():
            QApplication.clipboard().setText("\t".join(str(self.proxy.index(current.row(), column).data()) for column in range(self.proxy.columnCount()) if not self.table.isColumnHidden(column)))

    def scan_preview(self):
        if not self.project_directory or self._project_loading:
            self.notice.show_error("Сначала откройте проект и дождитесь его данных")
            return
        project_key = self.note_project_key()
        draft = self._scan_drafts.get(project_key, {})
        dialog = QDialog(self)
        dialog.setWindowTitle("Новый скан · явный план")
        dialog.resize(620, 570)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        target = ((self.project_result or {}).get("project") or {}).get("site", {}).get("target") or "Не измерено"
        target_input = QLineEdit(target)
        target_input.setReadOnly(True)
        form.addRow("Проектный URL", target_input)
        source_mode = QComboBox()
        source_mode.setObjectName("scanSourceMode")
        source_mode.setAccessibleName("Источник URL: спайдер или sitemap")
        source_mode.addItem("Спайдер · переход по ссылкам", "spider")
        source_mode.addItem("Только URL из sitemap", "sitemap")
        source_mode.setCurrentIndex(max(0, source_mode.findData(draft.get("source", "spider"))))
        form.addRow("Источник URL", source_mode)
        sitemap_input = QLineEdit(draft.get("sitemap", ""))
        sitemap_input.setObjectName("scanSitemapUrl")
        sitemap_input.setAccessibleName("Адрес sitemap для сканирования")
        sitemap_input.setPlaceholderText("https://example.com/sitemap.xml")
        form.addRow("Sitemap", sitemap_input)
        mode = QComboBox()
        mode.addItem("Native raw HTML", "raw")
        mode.addItem("Native JavaScript", "js")
        mode.setCurrentIndex(max(0, mode.findData(draft.get("mode", "raw"))))
        form.addRow("Режим", mode)
        limit = QSpinBox(); limit.setRange(1, 50000); limit.setValue(draft.get("limit", 40)); limit.setObjectName("scanUrlLimit")
        requests = QSpinBox(); requests.setRange(1, 2_000_000); requests.setValue(draft.get("requests", 100)); requests.setObjectName("scanRequestBudget")
        duration = QSpinBox(); duration.setRange(1, 86_400); duration.setValue(draft.get("duration", 60)); duration.setSuffix(" с"); duration.setObjectName("scanDurationBudget")
        form.addRow("Лимит URL", limit); form.addRow("Лимит HTTP-запросов", requests); form.addRow("Лимит времени", duration)
        layout.addLayout(form)
        approval = QCheckBox("Подтверждаю запуск с повышенным бюджетом")
        approval.setObjectName("scanLargeApproval")
        layout.addWidget(approval)
        advanced_overrides = dict(draft.get("overrides", {}))
        advanced = QPushButton("Расширенные настройки…")
        advanced.setObjectName("scanAdvancedSettings")
        advanced.setIcon(icon("settings"))
        layout.addWidget(advanced)
        message = QLabel()
        message.setWordWrap(True)
        message.setTextFormat(Qt.PlainText)
        layout.addWidget(message)
        feedback = QLabel()
        feedback.setObjectName("scanValidationFeedback")
        feedback.setWordWrap(True)
        feedback.setTextFormat(Qt.PlainText)
        layout.addWidget(feedback)
        retry = QPushButton("Повторить загрузку настроек")
        retry.clicked.connect(self.load_crawl_descriptor)
        layout.addWidget(retry)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        start_button = buttons.button(QDialogButtonBox.Ok)
        start_button.setText("Запустить"); start_button.setObjectName("scanStartButton"); start_button.setProperty("role", "primary")
        layout.addWidget(buttons)
        accepted = []

        def prepare():
            if self.note_project_key() != project_key or self._project_loading:
                raise ValueError("Проект изменился. Закройте план и откройте его для текущего проекта.")
            if self.crawl_descriptor is None:
                raise ValueError(self._crawl_descriptor_error or "Параметры ядра загружаются…")
            sitemap = sitemap_input.text().strip() if source_mode.currentData() == "sitemap" else None
            if sitemap is not None and ((self.crawl_descriptor or {}).get("capabilities") or {}).get("sitemap_only_retained") is not True:
                raise ValueError("Подключённое ядро не поддерживает сохранённый sitemap-скан")
            values = {**advanced_overrides, "limits.max_urls": limit.value(), "limits.max_requests": requests.value(), "limits.max_crawl_seconds": duration.value(), "rendering.mode": mode.currentData()}
            preview = preview_configuration(self.crawl_descriptor, values)
            crawl_arguments(self.project_directory, limit.value(), mode.currentData(), overrides=tuple(preview["overrides"].items()), approve_large_crawl=approval.isChecked(), sitemap_url=sitemap)
            return (limit.value(), mode.currentData(), requests.value(), duration.value(), approval.isChecked(), dict(advanced_overrides), sitemap)

        def explain(error):
            text = str(error)
            if "Sitemap URL" in text:
                return "Укажите полный HTTP(S)-адрес sitemap до 4096 символов, без логина, пароля, #фрагмента и управляющих символов."
            if "port" in text.lower():
                return "В адресе sitemap некорректный порт. Исправьте адрес; остальные настройки сохранены."
            if "selected scan project" in text:
                return "Папка проекта недоступна. Настройки сохранены; проверьте расположение проекта."
            return text

        def update_plan():
            supported = ((self.crawl_descriptor or {}).get("capabilities") or {}).get("sitemap_only_retained") is True
            source_mode.model().item(1).setEnabled(supported)
            source_mode.model().item(1).setToolTip("Скан только URL из указанного XML" if supported else "Подключённое ядро не объявило эту возможность")
            advanced.setEnabled(self.crawl_descriptor is not None)
            retry.setVisible(self.crawl_descriptor is None and bool(self._crawl_descriptor_error))
            elevated = limit.value() > 5000 or requests.value() > 10000 or duration.value() > 300
            approval.setVisible(elevated)
            if not elevated and approval.isChecked():
                approval.blockSignals(True); approval.setChecked(False); approval.blockSignals(False)
            sitemap_only = source_mode.currentData() == "sitemap"
            sitemap_input.setVisible(sitemap_only); form.labelForField(sitemap_input).setVisible(sitemap_only)
            scope = f"Только URL из sitemap: {sitemap_input.text().strip() or 'укажите адрес'}" if sitemap_only else f"Спайдер: {target}"
            message.setText(f"{scope}\nЛимиты: {limit.value()} URL · {requests.value()} HTTP-запросов · {duration.value()} с\n" + ("Без перехода по ссылкам со страниц. " if sitemap_only else "") + "Запуск — отдельной кнопкой ниже.")
            try:
                prepare()
                ready = not elevated or approval.isChecked()
                feedback.setText("Подтвердите повышенный бюджет" if not ready else "План проверен. Настройки относятся только к выбранному проекту.")
            except (TypeError, ValueError, OSError) as exc:
                ready = False
                feedback.setText(explain(exc)); feedback.setToolTip(str(exc))
            start_button.setEnabled(ready)

        def edit_advanced():
            try:
                editor = CrawlConfigurationDialog(self.crawl_descriptor, dialog, project_directory=self.project_directory, overrides={**advanced_overrides, "limits.max_urls": limit.value(), "limits.max_requests": requests.value(), "limits.max_crawl_seconds": duration.value(), "rendering.mode": mode.currentData()})
                if editor.exec_() == QDialog.Accepted:
                    advanced_overrides.clear(); advanced_overrides.update(editor.get_overrides())
                    limit.setValue(advanced_overrides.get("limits.max_urls", limit.value()))
                    requests.setValue(advanced_overrides.get("limits.max_requests", requests.value()))
                    seconds = advanced_overrides.get("limits.max_crawl_seconds", duration.value()); duration.setMaximum(max(duration.maximum(), seconds)); duration.setValue(seconds)
                    mode.setCurrentIndex(mode.findData(advanced_overrides.get("rendering.mode", mode.currentData())))
                    update_plan()
            except (TypeError, ValueError) as exc:
                feedback.setText(explain(exc))

        def accept_plan():
            try:
                payload = prepare()
                elevated = payload[0] > 5000 or payload[2] > 10000 or payload[3] > 300
                if elevated and not approval.isChecked():
                    raise ValueError("Подтвердите повышенный бюджет")
            except (TypeError, ValueError, OSError) as exc:
                feedback.setText(explain(exc)); feedback.setToolTip(str(exc))
                return
            accepted.append(payload)
            dialog.accept()

        def release(*_args):
            try:
                self.crawl_descriptor_changed.disconnect(update_plan)
            except (TypeError, RuntimeError):
                pass

        advanced.clicked.connect(edit_advanced)
        for signal in (limit.valueChanged, requests.valueChanged, duration.valueChanged, approval.toggled, source_mode.currentIndexChanged, sitemap_input.textChanged, mode.currentIndexChanged):
            signal.connect(update_plan)
        self.crawl_descriptor_changed.connect(update_plan)
        dialog.finished.connect(release)
        buttons.accepted.connect(accept_plan); buttons.rejected.connect(dialog.reject)
        update_plan()
        if self.crawl_descriptor is None:
            self.load_crawl_descriptor()
        dialog.exec_()
        self._scan_drafts[project_key] = {"source": source_mode.currentData(), "sitemap": sitemap_input.text(), "mode": mode.currentData(), "limit": limit.value(), "requests": requests.value(), "duration": duration.value(), "overrides": dict(advanced_overrides)}
        release(); dialog.deleteLater()
        if accepted:
            self.launch_scan(*accepted[0])

    def ensure_scan_manager(self):
        if self.scan_manager is None:
            self.scan_manager = LocalScanManager(self.core_executable, max_parallel=3, parent=self)
            self.scan_manager.changed.connect(self.managed_scan_changed)
            self.scan_manager.output.connect(self.managed_scan_output)
            self.scan_manager.failed.connect(self.managed_scan_failed)
        return self.scan_manager

    def launch_scan(
        self, max_urls, rendering_mode, max_requests=100, max_seconds=60, approve_large_crawl=False, configuration_overrides=None, sitemap_url=None
    ):
        if not self.project_directory or not self.core_executable:
            return
        if self.crawl_descriptor is None or not isinstance(self.current_project_uuid, str):
            self.statusBar().showMessage("Ядро не вернуло устойчивый ID проекта")
            return
        if sitemap_url is not None and ((self.crawl_descriptor or {}).get("capabilities") or {}).get("sitemap_only_retained") is not True:
            self.notice.show_error("Подключённое ядро не поддерживает сохранённый sitemap-скан. Выберите совместимый комплект приложения и ядра.")
            return
        manager = self.ensure_scan_manager()
        try:
            draft = {**(configuration_overrides or {}), "limits.max_urls": max_urls, "limits.max_requests": max_requests, "limits.max_crawl_seconds": max_seconds, "rendering.mode": rendering_mode}
            preview = preview_configuration(self.crawl_descriptor, draft)
            run_id = manager.submit(
                project=self.project_directory,
                project_uuid=self.current_project_uuid,
                max_urls=max_urls,
                rendering_mode=rendering_mode,
                overrides=tuple(preview["overrides"].items()),
                approve_large_crawl=approve_large_crawl,
                max_urls_per_second=None,
                sitemap_url=sitemap_url,
            )
            self.choose_owned_run(run_id)
            self.scan_poll_timer.start()
        except (RuntimeError, ValueError) as exc:
            self.statusBar().showMessage(str(exc))

    def owned_runs_for_project(self):
        if self.scan_manager is None or not self.project_directory:
            return []
        root = Path(self.project_directory).resolve()
        return [item for item in self.scan_manager.snapshot(self.current_project_uuid) if Path(item.get("project") or "").resolve() == root]

    def update_resume_control(self):
        path = self.selected_scan_path
        active_states = {"queued", "starting", "running", "stop_requested", "awaiting_core_status"}
        busy = path in self._pending_resume_paths or any(item.get("state") in active_states and path in {item.get("resume_path"), item.get("artifact")} for item in self.owned_runs_for_project())
        self.resume_scan_button.setEnabled(bool(path) and path == self._resume_eligible_path and not busy)
        self.resume_scan_button.setText("Продолжить скан " + (self.selected_scan_uuid or "")[:8])
        self.resume_scan_button.setToolTip("Для этого снимка уже есть активная попытка" if busy else f"Сохранённый источник: {path or 'не выбран'}")

    def resume_selected_scan(self):
        if not self.selected_scan_path or not self.project_directory or not isinstance(self.current_project_uuid, str):
            return
        self.update_resume_control()
        if not self.resume_scan_button.isEnabled():
            return
        manager = self.ensure_scan_manager()
        path = self.selected_scan_path
        self._pending_resume_paths.add(path)
        self.update_resume_control()
        try:
            run_id = manager.resume(project=self.project_directory, project_uuid=self.current_project_uuid, artifact=path)
            self.choose_owned_run(run_id)
            self.scan_poll_timer.start()
        except (RuntimeError, ValueError) as exc:
            self.notice.show_error(f"Не удалось продолжить выбранный скан: {exc}")
        finally:
            self._pending_resume_paths.discard(path)
            self.update_resume_control()

    def choose_owned_run(self, run_id):
        self.selected_managed_run_id = run_id
        self.owned_run_picker.blockSignals(True)
        self.owned_run_picker.setCurrentIndex(max(0, self.owned_run_picker.findData(run_id)))
        self.owned_run_picker.blockSignals(False)
        self.select_owned_run()

    def open_owned_run_result(self):
        if not self.scan_manager or not self.selected_managed_run_id:
            return
        detail = self.scan_manager.detail(self.selected_managed_run_id) or {}
        scan = next((row for row in self.scan_model.rows if row.get("path") == detail.get("artifact")), None)
        if scan:
            self.select_project_scan(scan)
            self.navigation.setCurrentRow(1)

    def managed_scan_changed(self, run):
        if run.get("project_uuid") != self.current_project_uuid:
            return
        rows = self.owned_runs_for_project()
        selected = self.selected_managed_run_id
        self.owned_run_picker.blockSignals(True)
        self.owned_run_picker.clear()
        self.owned_run_picker.addItem("Выберите запуск этого окна" if rows else "Запуски этого окна: нет", None)
        for item in rows:
            label = f"{item['id'][:8]} · {state_text(item['kind'])} · {state_text(item['state'])}"
            self.owned_run_picker.addItem(label, item["id"])
        index = self.owned_run_picker.findData(selected)
        self.owned_run_picker.setCurrentIndex(max(index, 0))
        self.owned_run_picker.blockSignals(False)
        self.select_owned_run()
        if run.get("state") in {"starting", "running"}:
            self.statusBar().showMessage("Локальный native crawl запущен; наблюдение обновляется каждые 0,5 с")
        elif run.get("state") == "awaiting_core_status":
            self.scan_poll_timer.start()
        if self._close_waiting:
            self._finish_owned_shutdown()

    def select_owned_run(self):
        value = self.owned_run_picker.currentData()
        allowed = {item["id"] for item in self.owned_runs_for_project()}
        self.selected_managed_run_id = value if isinstance(value, str) and value in allowed else None
        detail = self.scan_manager.detail(self.selected_managed_run_id) if self.scan_manager and self.selected_managed_run_id else None
        self.render_owned_run(detail)
        if detail:
            self.selected_observed_run_id = detail.get("core_run_id") or detail.get("observer_run_id")
            selection = self.activity_table.selectionModel()
            selection.blockSignals(True)
            index = next((i for i, row in enumerate(self.activity_model.rows) if row.get("id") == self.selected_observed_run_id), None)
            if index is None:
                self.activity_table.clearSelection()
                self.activity_table.setCurrentIndex(QModelIndex())
                self.activity_text.setPlainText("Выбран запуск " + self.selected_managed_run_id + ". Измерения ожидаются от ядра.")
            else:
                self.activity_table.selectRow(index)
                self.show_observed_run(self.activity_model.index(index, 0), None, sync_controls=False)
            selection.blockSignals(False)

    def render_owned_run(self, detail):
        active = bool(detail) and detail.get("state") in {"queued", "starting", "running", "stop_requested"}
        self.stop_run_button.setEnabled(active)
        short_id = str((detail or {}).get("id") or "")[:8]
        self.stop_run_button.setText("Остановить " + short_id if active else "Остановить выбранный запуск")
        self.cancel_button.setText("Остановить " + short_id if active else "Отменить чтение")
        self.cancel_button.setEnabled(active or bool(self.requests))
        self.cancel_button.setToolTip("Цель управления: " + str((detail or {}).get("id") or "только текущие чтения"))
        self.owned_target_caption.setText(f"Запуск этого окна: {short_id} · {state_text(detail.get('state'))}" if detail else "Управление запуском: собственный запуск не выбран")
        text = readable_record({key: value for key, value in detail.items() if key != "output"}, heading="Выбранный запуск этого окна") if detail else "Выберите запуск, созданный этим окном. Чужие процессы здесь не останавливаются."
        if self.owned_run_detail.toPlainText() != text:
            self.owned_run_detail.setPlainText(text)
        output = (detail or {}).get("output") or "Вывод выбранного запуска ещё не получен"
        if self.owned_run_output.toPlainText() != output:
            self.owned_run_output.setPlainText(output[-20000:])
        self.show_run_result.setEnabled(bool(detail and detail.get("artifact") and any(row.get("path") == detail["artifact"] for row in self.scan_model.rows)))
        if detail and detail.get("state") in {"failed", "rejected", "status_unavailable"}:
            key = (detail.get("id"), detail.get("state"), detail.get("status_reason"))
            if key != self._shown_run_error_key:
                self._shown_run_error_key = key
                self.notice.show_error(f"Запуск {short_id}: {state_text(detail.get('state'))}. {detail.get('status_reason') or 'Подробности сохранены во вкладке «Журнал запуска».'}", "owned:" + str(detail.get("id")))
        self.update_resume_control()

    def stop_selected_run(self):
        self.cancel_active_work()

    def managed_scan_output(self, run_id, text):
        if run_id != self.selected_managed_run_id:
            return
        detail = self.scan_manager.detail(run_id) if self.scan_manager else None
        if detail:
            self.render_owned_run(detail)

    def poll_active_scan(self):
        if self.scan_manager is not None:
            self.scan_manager.observe(self.current_project_uuid, [])
        if self.scan_manager is None or not any(
            item["state"] in {"starting", "running", "stop_requested", "awaiting_core_status"}
            for item in self.scan_manager.snapshot(self.current_project_uuid)
        ):
            self.scan_poll_timer.stop()
            return
        if "observer" in self.requests or not self.project_directory:
            return
        self.start_command(
            "observer",
            "seo_project_observe",
            {"directory": self.project_directory, "consumer": CONSUMER_ID, "scan_limit": 20},
            self.load_observer,
        )

    def managed_scan_failed(self, run_id, text):
        if run_id == self.selected_managed_run_id:
            self.statusBar().showMessage(f"Локальный скан: {text}")
            self.notice.show_error(f"Запуск {run_id[:8]}: {text}. Полный вывод сохранён в журнале запуска.", "owned:" + run_id)

    def closeEvent(self, event):
        if self._pending_note is not None:
            event.ignore()
            self.notice.show_error("Дождитесь подтверждения сохранения заметки перед закрытием окна. Черновик остаётся в форме.", "inbox-submit")
            return
        self.content_search.shutdown()
        if self.content_search.active or (self.scan_manager is not None and self.scan_manager.active_count):
            event.ignore()
            if not self._close_waiting:
                self._close_waiting = True
                if self.scan_manager is not None:
                    self.scan_manager.stop_all_owned()
                QTimer.singleShot(50, self._finish_owned_shutdown)
            return
        self.cancel_requests()
        self.scan_poll_timer.stop()
        if self.mcp_gateway is not None:
            self.mcp_gateway.stop()
        # Qt pool destruction during Python GC can hold the GIL while its worker
        # needs it to exit. Drain with the binding (which releases it) before close.
        if not self.pool.waitForDone(200):
            self._close_waiting = True
            event.ignore()
            QTimer.singleShot(100, self._finish_owned_shutdown)
            return
        self._close_waiting = False
        if self.settings:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("horizontal", self.horizontal.saveState())
            self.settings.setValue("vertical", self.vertical.saveState())
        self.save_workspace_layout()
        if self.monitor is not None:
            self.monitor.hide()
        super().closeEvent(event)

    def _finish_owned_shutdown(self):
        if not self._close_waiting:
            return
        if self.content_search.active or (self.scan_manager is not None and self.scan_manager.active_count):
            QTimer.singleShot(100, self._finish_owned_shutdown)
            return
        self._close_waiting = False
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-cli", help="Existing seohead CLI executable; local project adapter")
    parser.add_argument("--capture", type=Path, help="Save the native widget rendering to a PNG and exit")
    parser.add_argument("--export-svg", type=Path, help="Export actual Qt painting as SVG for Figma import")
    parser.add_argument("--no-settings", action="store_true")
    args = parser.parse_args()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    tokens = load_theme(app)
    app.setStyleSheet(app.styleSheet() + component_stylesheet(tokens))
    window = MainWindow(persistent=not args.no_settings, core_executable=args.core_cli)
    window.show()
    if args.capture or args.export_svg:
        def capture():
            if args.capture:
                args.capture.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.capture))
            if args.export_svg:
                args.export_svg.parent.mkdir(parents=True, exist_ok=True)
                generator = QSvgGenerator()
                generator.setFileName(str(args.export_svg))
                generator.setSize(window.size())
                generator.setViewBox(window.rect())
                generator.setTitle("SEOHEAD native Qt preparation skeleton — demo")
                painter = QPainter(generator)
                window.render(painter)
                painter.end()
            window.close()
            app.quit()
        QTimer.singleShot(500, capture)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
