"""Native Qt Widgets shell over declared, local SEOHEAD CLI projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from string import Template

from PyQt5.QtCore import QSettings, QSortFilterProxyModel, Qt, QThreadPool, QTimer
from PyQt5.QtGui import QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap
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
    QSpinBox,
    QSplitter,
    QTableView,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .crawl_configuration import preview_configuration, validate_overrides
from .mcp_gateway import PersistentMcpGateway
from .models import RecordModel, UrlModel
from .scan_manager import LocalScanManager
from .ui.crawl_configuration_dialog import CrawlConfigurationDialog
from .ui.panels import AuditWorkspace, ProjectPanels, component_stylesheet
from .ui.presentation import (
    FIELDS,
    ElidedLabel,
    StateBadge,
    field_text,
    readable_record,
    run_projection,
    state_text,
    theme_tokens,
    value_text,
)

ROOT = Path(__file__).resolve().parent
CONSUMER_ID = "desktop/gui"
PAGE_LIMIT = 50


def load_theme(app):
    tokens = theme_tokens()
    font = ROOT / "assets/fonts/Roboto.ttf"
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    values = {**tokens["colors"], **{key: value for key, value in tokens.items() if isinstance(value, (str, int))}}
    app.setStyleSheet(Template((ROOT / "theme/theme.qss").read_text()).substitute(values))
    return tokens


def icon(name, color=None):
    color = color or theme_tokens()["colors"]["on_surface_variant"]
    path = ROOT / "assets/icons" / f"{name}.svg"
    if not path.exists():
        return QIcon()
    raw = path.read_text().replace("<svg ", f'<svg fill="{color}" ', 1)
    renderer = QSvgRenderer(raw.encode())
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
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
        self.requests = {}
        self.request_handlers = {}
        self.active_commands = {}
        self.pending_commands = {}
        self.mcp_gateway = None
        self.mcp_ready = False
        self.crawl_descriptor = None
        self.scan_manager = None
        self.current_project_uuid = None
        self.selected_managed_run_id = None
        self.selected_scan_path = None
        self.selected_scan_uuid = None
        self.selected_url = None
        self.last_observer_signature = None
        self._reload_selected_scan = False
        self.poll_backoff_ms = 500
        self._close_waiting = False
        self._compact = None
        self._focus_mode = False
        self._density = "standard"
        self.observed_runs = []
        self.observed_at = None
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
        shell.addWidget(self.topbar())
        shell.addWidget(self.contextbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(theme_tokens()["layout"]["navigation_width"])
        self.navigation.setAccessibleName("Разделы проекта")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation.setUniformItemSizes(True)
        self.navigation_labels = ["Работа", "URL", "Аудит", "Проект", "Задачи", "Сканы", "Входящие", "Отчёты", "Журнал"]
        self.navigation.addItems(self.navigation_labels)
        body.addWidget(self.navigation)
        from .ui.components import PanelStack
        self.pages = PanelStack()
        body.addWidget(self.pages, 1)
        shell.addLayout(body, 1)

        self.pages.addWidget(self.work_page())
        self.pages.addWidget(self.url_page())
        self.pages.addWidget(self.audit_page())
        self.pages.addWidget(self.project_page())
        self.pages.addWidget(self.tasks_page())
        self.pages.addWidget(self.scans_page())
        self.pages.addWidget(self.inbox_page())
        self.pages.addWidget(self.reports_page())
        self.pages.addWidget(self.journal_page())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
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
            action.toggled.connect(widget.setVisible)
            self.panel_actions[name] = action
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

    def topbar(self):
        top = QWidget()
        top.setObjectName("topbar")
        layout = QHBoxLayout(top)
        layout.setContentsMargins(12, 10, 16, 10)
        layout.setSpacing(8)
        self.nav_toggle = QToolButton()
        self.nav_toggle.setIcon(icon("menu"))
        self.nav_toggle.setAccessibleName("Свернуть или развернуть навигацию")
        self.nav_toggle.setToolTip("Свернуть или развернуть навигацию")
        self.nav_toggle.clicked.connect(self.toggle_navigation)
        layout.addWidget(self.nav_toggle)
        self.brand = QLabel("SEOHEAD")
        self.brand.setObjectName("brand")
        layout.addWidget(self.brand)
        self.project_picker = QComboBox()
        self.project_picker.addItem(self.demo["label"])
        self.project_picker.setAccessibleName("Текущий проект")
        self.project_picker.setMinimumContentsLength(16)
        self.project_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.project_picker.setMaximumWidth(300)
        layout.addWidget(self.project_picker, 1)
        self.open_project_button = QToolButton()
        self.open_project_button.setIcon(icon("folder_open"))
        self.open_project_button.setAccessibleName("Открыть проект")
        self.open_project_button.setToolTip("Открыть проект · Cmd/Ctrl+O")
        self.open_project_button.clicked.connect(self.choose_project)
        layout.addWidget(self.open_project_button)
        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(icon("chevron_right"))
        self.refresh_button.setText("Обновить")
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.refresh_button.setAccessibleName("Обновить сохранённые данные")
        self.refresh_button.setToolTip("Перечитать сохранённые данные проекта")
        self.refresh_button.clicked.connect(self.refresh_project)
        self.refresh_button.setEnabled(False)
        layout.addWidget(self.refresh_button)
        layout.addStretch()
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
        self.work_splitter = QSplitter(Qt.Vertical)
        self.progress_text = plain("Откройте проект, чтобы увидеть сохранённые задачи и согласованный план.\n\nДемо не содержит измеренного прогресса проекта.")
        self.progress_text.setAccessibleName("Прогресс задач проекта")
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
        self.horizontal = QSplitter(Qt.Horizontal)
        self.vertical = QSplitter(Qt.Vertical)
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
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
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
        self.vertical.addWidget(table_area)
        self.inspector = QTabWidget()
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
        self.stop_run_button.setEnabled(False)
        self.stop_run_button.clicked.connect(self.stop_selected_run)
        self.stop_run_button.setProperty("role", "danger")
        controls.addWidget(self.stop_run_button, 1, 0)
        self.resume_scan_button = QPushButton("Продолжить скан")
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
        self.scan_detail = plain("Выберите сохранённый скан: URL загружаются только из его локальной SQLite-копии.")
        layout.addWidget(self.scan_detail, 2)
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
        self.note_input.setPlaceholderText("Сохранить заметку для агента в этом локальном проекте")
        self.note_kind = QComboBox()
        self.note_kind.addItem("Заметка", "note")
        self.note_kind.addItem("Предложенная цель", "proposed_goal")
        self.note_submit = QPushButton("Сохранить")
        self.note_submit.clicked.connect(self.submit_note)
        self.note_submit.setEnabled(False)
        composer.addWidget(self.note_input, 1)
        composer.addWidget(self.note_kind)
        composer.addWidget(self.note_submit)
        layout.addLayout(composer)
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
            self.note_submit.setEnabled(True)
            self.project_panels.panel("inbox").set_submission_enabled(
                True, "Ядро отклонило запись; черновик сохранён в форме"
            )
        elif request_id.startswith("url-page:"):
            self.audit_workspace.set_page(
                "internal", [], state="unavailable", reason=text, source="Retained scan unavailable"
            )
        elif request_id.startswith("url-detail:"):
            self.audit_workspace.set_page(
                "url_details", [], state="unavailable", reason=text, source="Retained URL detail unavailable"
            )
        self.statusBar().showMessage(f"{request_id}: {text}")

    def cancel_requests(self):
        if self.mcp_gateway is not None:
            self.mcp_gateway.cancel_generation(self.read_generation)
        self.requests.clear()
        self.request_handlers.clear()
        self.active_commands.clear()
        self.pending_commands.clear()
        self.read_generation += 1
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
        if not (Path(directory) / "project.json").is_file():
            self.statusBar().showMessage("В папке нет project.json SEOHEAD")
            return
        self.cancel_requests()
        self.statusBar().showMessage("Чтение локального проекта в фоне…")
        gateway = self.ensure_mcp_gateway()
        if gateway is None:
            return
        try:
            gateway.set_project_scope(directory)
        except ValueError as exc:
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
        self.clear_scan_selection("Выбран другой проект. Загрузка сохранённого контекста…")
        self.last_observer_signature = None
        self.scan_model.replace([])
        self.task_model.replace([])
        self.inbox_model.replace([])
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
        self.owned_run_picker.blockSignals(True)
        self.owned_run_picker.clear()
        self.owned_run_picker.addItem("Запуски этого окна: нет", None)
        self.owned_run_picker.blockSignals(False)
        self.stop_run_button.setEnabled(False)
        label = site.get("label") or site.get("host") or "Подключённый проект"
        self.project_picker.clear()
        self.project_picker.addItem(label)
        self.setWindowTitle(f"SEOHEAD · {label}")
        self.source_badge.setText("Локальный проект · сохранённые данные")
        self.refresh_button.setEnabled(True)
        self.note_submit.setEnabled(True)
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
            self.start_command("crawl-settings", "seo_crawl_describe_settings", {}, self.crawl_descriptor_loaded)

    def crawl_descriptor_loaded(self, result):
        try:
            validate_overrides(result, {})
        except (TypeError, ValueError) as exc:
            self.statusBar().showMessage(f"Конфигурация ядра недоступна: {exc}")
            return
        self.crawl_descriptor = result

    def refresh_project(self):
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
        selected = self.activity_model.rows[current.row()].get("id") if current.isValid() else None
        self.activity_table.selectionModel().blockSignals(True)
        self.activity_model.replace([run_projection(run) for run in self.observed_runs])
        row = next((index for index, run in enumerate(self.observed_runs) if run.get("id") == selected), 0)
        if self.observed_runs:
            self.activity_table.selectRow(row)
        self.activity_table.selectionModel().blockSignals(False)
        if self.observed_runs:
            self.show_observed_run(self.activity_model.index(row, 0), None)
        else:
            self.activity_text.setPlainText("В текущем наблюдении ядро не вернуло запусков. Это не измерение скорости или покрытия сайта.")
        events = [{"run_id": run.get("id"), **event} for run in self.observed_runs for event in (run.get("events") or [])[-20:]][-200:]
        self.journal_model.replace(events)
        self.journal_caption.setText(f"Сохранённых событий в выборке: {len(events)} · последние 20 на запуск, до 200 строк")

    def show_observed_run(self, current, _previous):
        if not current.isValid():
            return
        row = self.activity_model.rows[current.row()]
        run = row["_run"]
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
        elif not self.selected_scan_path:
            self.clear_scan_selection("В этом проекте нет сохранённых сканов")

    def clear_scan_selection(self, reason):
        self.selected_scan_path = None
        self.selected_scan_uuid = None
        self.selected_url = None
        self.model.replace([])
        self.search.clear()
        self.search.setEnabled(False)
        self.audit_workspace.clear(reason)
        for view in (self.detail, self.debug_detail, self.link_detail, self.evidence_detail, self.scan_detail, self.headers_detail):
            view.setPlainText(reason)
        self.resume_scan_button.setEnabled(False)
        self.scan_progress.hide()
        self.scan_progress_label.setText("Прогресс выбранного скана не измерен")
        self.scan_state_badge.set_state("unknown")

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
            self.scan_progress_label.setText(f"Обработано известных URL: {done} из {total} · в очереди {queued} · в работе {inflight}. Размер сайта не измерен.")
        else:
            self.scan_progress_label.setText("Прогресс известных URL не измерен: нет полного набора счётчиков.")
        self.summary_scan.setText(state_text(source.get("lifecycle")))
        outcomes = result.get("committed_page_outcomes")
        if isinstance(outcomes, dict):
            aggregate_rows = [{"name": "Без ответа" if key == "no_response" else "Другие" if key == "other" else key, "urls": value} for key, value in outcomes.items()]
            self.audit_workspace.set_page("overview", aggregate_rows, total=len(aggregate_rows), source="Сохранённые HTTP-ответы · доля по всему сайту не измерена")
        self.resume_scan_button.setEnabled(
            bool(scan_path)
            and source.get("lifecycle") == "interrupted"
            and Path(scan_path).is_file()
        )

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
        self.model.replace(rows)
        self.search.setEnabled(True)
        self.search.setPlaceholderText("Поиск в загруженной странице сохранённого скана")
        self.url_caption.setText(f"URL · {len(rows)} записей")
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
        if rows:
            self.table.selectRow(0)

    def load_inbox(self, result):
        self.inbox_revision = result.get("revision")
        rows = list(result.get("entries") or [])
        self.inbox_model.replace(rows)
        total = (result.get("pagination") or {}).get("total", len(rows))
        self.inbox_caption.setText(f"Входящие · {total} сохранённых записей")
        inbox_panel = self.project_panels.panel("inbox")
        self.project_panels.set_page(
            "inbox", rows, total=total, source="Сохранённые входящие проекта"
        )
        inbox_panel.set_submission_enabled(
            bool(self.project_directory), "Сохранение явным действием; скан не запускается"
        )
        if rows:
            self.inbox_table.selectRow(0)

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

    def submit_note(self, supplied_text=None, supplied_kind=None):
        if not self.project_directory:
            return
        if isinstance(supplied_text, bool):
            supplied_text = None
        text = (supplied_text if supplied_text is not None else self.note_input.text()).strip()
        if not text:
            self.statusBar().showMessage("Введите текст заметки")
            return
        kind = supplied_kind or self.note_kind.currentData()
        if kind not in {"note", "proposed_goal"}:
            kind = "note"
        arguments = {
            "directory": self.project_directory,
            "text": text,
            "kind": kind,
            "author_role": "specialist",
        }
        if isinstance(self.inbox_revision, int):
            arguments["expected_revision"] = self.inbox_revision
        self.note_submit.setEnabled(False)
        self.start_command("inbox-submit", "seo_project_inbox_submit", arguments, self.note_saved)

    def note_saved(self, _result):
        self.note_input.clear()
        self.note_submit.setEnabled(True)
        self.project_panels.panel("inbox").submission_succeeded()
        self.statusBar().showMessage("Заметка сохранена локально; скан и агент не запускались")
        self.refresh_project()

    def show_url(self, current, _previous):
        source = self.proxy.mapToSource(current)
        if not source.isValid():
            self.detail.setPlainText("Выберите сохранённую запись")
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

    def load_url_links(self, result, scan_path=None, url=None):
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

    def load_url_detail(self, result, scan_path=None, url=None):
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
        self.selected_url = row["url"]
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
            lambda result, path=self.selected_scan_path, url=row["url"]: self.load_url_detail(result, path, url),
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
            lambda result, path=self.selected_scan_path, url=row["url"]: self.load_url_links(result, path, url),
        )

    def handle_project_intent(self, intent, payload):
        if intent != "query" or not isinstance(payload, dict) or not self.project_directory:
            return
        tab_id = payload.get("tab_id")
        limit = min(100, max(1, int(payload.get("limit", PAGE_LIMIT))))
        offset = max(0, int(payload.get("offset", 0)))
        if tab_id == "tasks":
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

    def set_navigation_compact(self, compact):
        width = theme_tokens()["layout"]["navigation_rail" if compact else "navigation_width"]
        self.navigation.setFixedWidth(width)
        self.navigation.setProperty("compact", compact)
        short = ["Обзор", "URL", "Аудит", "Проект", "Задачи", "Сканы", "Вход.", "Отчёт", "Лог"]
        for index, title in enumerate(self.navigation_labels):
            item = self.navigation.item(index)
            item.setText(short[index] if compact else title)
            item.setToolTip(title)
            item.setTextAlignment(Qt.AlignCenter if compact else Qt.AlignLeft | Qt.AlignVCenter)
        self.navigation.style().unpolish(self.navigation)
        self.navigation.style().polish(self.navigation)

    def toggle_navigation(self):
        self.navigation.show()
        self.set_navigation_compact(not bool(self.navigation.property("compact")))

    def set_panel_visible(self, name, visible):
        action = self.panel_actions.get(name)
        if action is not None:
            action.setChecked(visible)

    def resizeEvent(self, event):
        if hasattr(self, "panel_actions"):
            compact = self.width() < theme_tokens()["layout"]["compact_breakpoint"]
            if compact != self._compact:
                self._compact = compact
                self.set_navigation_compact(compact)
                self.set_panel_visible("Сводка", not compact and not self._focus_mode)
                self.audit_workspace.right.setVisible(not compact and not self._focus_mode)
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
                self.set_panel_visible(name, False)
            self._audit_visibility = (not self.audit_workspace.detail.isHidden(), not self.audit_workspace.right.isHidden())
            self.audit_workspace.detail.hide()
            self.audit_workspace.right.hide()
        else:
            for name, visible in self._panel_visibility.items():
                self.set_panel_visible(name, visible)
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
        if not self.project_directory:
            self.statusBar().showMessage("Сначала откройте локальный проект SEOHEAD")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Новый скан · явный план")
        dialog.resize(540, 410)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        target = ((self.project_result or {}).get("project") or {}).get("site", {}).get("target") or "Не измерено"
        target_input = QLineEdit(target)
        target_input.setReadOnly(True)
        form.addRow("Проектный URL", target_input)
        mode = QComboBox()
        mode.addItem("Native raw HTML", "raw")
        mode.addItem("Native JavaScript", "js")
        form.addRow("Режим", mode)
        limit = QSpinBox()
        limit.setRange(1, 50000)
        limit.setValue(40)
        limit.setObjectName("scanUrlLimit")
        form.addRow("Лимит URL", limit)
        requests = QSpinBox()
        requests.setRange(1, 2_000_000)
        requests.setValue(100)
        requests.setObjectName("scanRequestBudget")
        form.addRow("Лимит HTTP-запросов", requests)
        duration = QSpinBox()
        duration.setRange(1, 86_400)
        duration.setValue(60)
        duration.setSuffix(" с")
        duration.setObjectName("scanDurationBudget")
        form.addRow("Лимит времени", duration)
        layout.addLayout(form)
        approval = QCheckBox("Подтверждаю запуск с повышенным бюджетом")
        approval.setObjectName("scanLargeApproval")
        approval.setVisible(False)
        layout.addWidget(approval)
        advanced_overrides = {}
        advanced = QPushButton("Расширенные настройки…")
        advanced.setEnabled(self.crawl_descriptor is not None)
        if self.crawl_descriptor is None:
            self.load_crawl_descriptor()
        def edit_advanced():
            current = {
                **advanced_overrides,
                "limits.max_urls": limit.value(),
                "limits.max_requests": requests.value(),
                "limits.max_crawl_seconds": duration.value(),
                "rendering.mode": mode.currentData(),
            }
            editor = CrawlConfigurationDialog(self.crawl_descriptor, dialog, project_directory=self.project_directory, overrides=current)
            if editor.exec_() == QDialog.Accepted:
                advanced_overrides.clear()
                advanced_overrides.update(editor.get_overrides())
                if "limits.max_urls" in advanced_overrides:
                    limit.setValue(advanced_overrides["limits.max_urls"])
                if "limits.max_requests" in advanced_overrides:
                    requests.setValue(advanced_overrides["limits.max_requests"])
                if "limits.max_crawl_seconds" in advanced_overrides:
                    seconds = advanced_overrides["limits.max_crawl_seconds"]
                    duration.setMaximum(max(duration.maximum(), seconds))
                    duration.setValue(seconds)
                if "rendering.mode" in advanced_overrides:
                    mode.setCurrentIndex(mode.findData(advanced_overrides["rendering.mode"]))
        advanced.clicked.connect(edit_advanced)
        layout.addWidget(advanced)

        def update_approval():
            elevated = limit.value() > 5_000 or requests.value() > 10_000 or duration.value() > 300
            approval.setVisible(elevated)
            if not elevated:
                approval.setChecked(False)
            buttons.button(QDialogButtonBox.Ok).setEnabled(
                self.crawl_descriptor is not None and (not elevated or approval.isChecked())
            )

        limit.valueChanged.connect(update_approval)
        requests.valueChanged.connect(update_approval)
        duration.valueChanged.connect(update_approval)
        approval.toggled.connect(update_approval)
        message = QLabel(
            "После явного «Запустить» приложение создаст только локальный native crawl этого проекта. "
            "Никакой скан не начинается при открытии проекта или обновлении экрана."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Запустить")
        buttons.button(QDialogButtonBox.Ok).setObjectName("scanStartButton")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        update_approval()
        if dialog.exec_() == QDialog.Accepted:
            self.launch_scan(
                limit.value(),
                mode.currentData(),
                requests.value(),
                duration.value(),
                approval.isChecked(),
                advanced_overrides,
            )

    def ensure_scan_manager(self):
        if self.scan_manager is None:
            self.scan_manager = LocalScanManager(self.core_executable, max_parallel=3, parent=self)
            self.scan_manager.changed.connect(self.managed_scan_changed)
            self.scan_manager.output.connect(self.managed_scan_output)
            self.scan_manager.failed.connect(self.managed_scan_failed)
        return self.scan_manager

    def launch_scan(
        self, max_urls, rendering_mode, max_requests=100, max_seconds=60, approve_large_crawl=False, configuration_overrides=None
    ):
        if not self.project_directory or not self.core_executable:
            return
        if self.crawl_descriptor is None or not isinstance(self.current_project_uuid, str):
            self.statusBar().showMessage("Ядро не вернуло устойчивый ID проекта")
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
            )
            self.selected_managed_run_id = run_id
            self.owned_run_picker.setCurrentIndex(self.owned_run_picker.findData(run_id))
            self.select_owned_run()
            self.scan_poll_timer.start()
        except (RuntimeError, ValueError) as exc:
            self.statusBar().showMessage(str(exc))

    def resume_selected_scan(self):
        if not self.selected_scan_path or not self.project_directory:
            return
        if not isinstance(self.current_project_uuid, str):
            return
        manager = self.ensure_scan_manager()
        try:
            self.selected_managed_run_id = manager.resume(
                project=self.project_directory,
                project_uuid=self.current_project_uuid,
                artifact=self.selected_scan_path,
            )
            self.scan_poll_timer.start()
        except (RuntimeError, ValueError) as exc:
            self.statusBar().showMessage(str(exc))

    def managed_scan_changed(self, run):
        if run.get("project_uuid") != self.current_project_uuid:
            return
        rows = self.scan_manager.snapshot(self.current_project_uuid) if self.scan_manager else []
        selected = self.selected_managed_run_id
        self.owned_run_picker.blockSignals(True)
        self.owned_run_picker.clear()
        if not rows:
            self.owned_run_picker.addItem("Запуски этого окна: нет", None)
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
        self.selected_managed_run_id = value if isinstance(value, str) else None
        active = False
        if self.scan_manager and self.selected_managed_run_id:
            detail = self.scan_manager.detail(self.selected_managed_run_id)
            if detail is not None:
                self.scan_detail.setPlainText(readable_record({key: value for key, value in detail.items() if key != "output"}, heading="Выбранный запуск этого окна"))
            active = any(
                item["id"] == self.selected_managed_run_id
                and item["state"] in {"queued", "starting", "running", "stop_requested"}
                for item in self.scan_manager.snapshot(self.current_project_uuid)
            )
        self.stop_run_button.setEnabled(active)
        self.cancel_button.setText("Остановить скан" if active else "Отменить чтение")
        self.cancel_button.setEnabled(active or bool(self.requests))

    def stop_selected_run(self):
        self.cancel_active_work()

    def managed_scan_output(self, run_id, text):
        if run_id != self.selected_managed_run_id:
            return
        current = self.scan_detail.toPlainText()
        self.scan_detail.setPlainText((current + "\n" + text).strip()[-20_000:])

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

    def closeEvent(self, event):
        if self.scan_manager is not None and self.scan_manager.active_count:
            event.ignore()
            if not self._close_waiting:
                self._close_waiting = True
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
        super().closeEvent(event)

    def _finish_owned_shutdown(self):
        if not self._close_waiting:
            return
        if self.scan_manager is not None and self.scan_manager.active_count:
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
