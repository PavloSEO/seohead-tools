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
from PyQt5.QtGui import QFontDatabase, QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgGenerator, QSvgRenderer
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .mcp_gateway import PersistentMcpGateway
from .models import RecordModel, UrlModel
from .scan_manager import LocalScanManager
from .crawl_configuration import preview_configuration, validate_overrides
from .ui.panels import AuditWorkspace, ProjectPanels, component_stylesheet
from .ui.crawl_configuration_dialog import CrawlConfigurationDialog

ROOT = Path(__file__).resolve().parent
CONSUMER_ID = "desktop/gui"
PAGE_LIMIT = 50


def load_theme(app):
    tokens = json.loads((ROOT / "theme/tokens.json").read_text())
    font = ROOT / "assets/fonts/Roboto.ttf"
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    values = {**tokens["colors"], "font_family": tokens["font_family"]}
    app.setStyleSheet(Template((ROOT / "theme/theme.qss").read_text()).substitute(values))
    return tokens


def icon(name, color="#49454F"):
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
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(True)


class MainWindow(QMainWindow):
    """Desktop presentation adapter. All scanning remains owned by core CLI."""

    def __init__(self, *, persistent=True, core_executable=None):
        super().__init__()
        self.setWindowTitle("SEOHEAD Desktop — подготовительный каркас")
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
        self.mcp_gateway = None
        self.mcp_ready = False
        self.crawl_descriptor = None
        self.scan_manager = None
        self.current_project_uuid = None
        self.selected_managed_run_id = None
        self.selected_scan_path = None
        self.selected_scan_uuid = None
        self.last_observer_signature = None
        self.poll_backoff_ms = 500
        self._close_waiting = False
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

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(192)
        self.navigation.addItems(["Работа", "URL", "Аудит", "Проект", "Задачи", "Сканы", "Входящие", "Отчёты", "Журнал"])
        body.addWidget(self.navigation)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        shell.addLayout(body, 1)

        self.pages.addWidget(self.work_page())
        self.pages.addWidget(self.url_page())
        self.pages.addWidget(self.audit_page())
        self.pages.addWidget(self.project_page())
        self.pages.addWidget(self.tasks_page())
        self.pages.addWidget(self.scans_page())
        self.pages.addWidget(self.inbox_page())
        for title in ("Отчёты", "Журнал"):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(20, 16, 20, 16)
            layout.addWidget(QLabel(title))
            layout.addWidget(plain("Контейнер интерфейса подготовлен. Экспорт и журнал остаются источниками ядра."))
            self.pages.addWidget(page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(1)
        self.statusBar().showMessage("Демо · сеть и сканирование не запускаются")

        view_menu = self.menuBar().addMenu("Вид")
        for name, widget in [("Навигация", self.navigation), ("Сводка", self.overview), ("Инспектор URL", self.inspector)]:
            action = view_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(widget.setVisible)
        restore = view_menu.addAction("Восстановить панели")
        restore.triggered.connect(self.restore_panels)
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
        layout.setContentsMargins(16, 10, 16, 10)
        self.nav_toggle = QPushButton()
        self.nav_toggle.setIcon(icon("menu"))
        self.nav_toggle.setAccessibleName("Скрыть или показать навигацию")
        self.nav_toggle.clicked.connect(self.toggle_navigation)
        layout.addWidget(self.nav_toggle)
        brand = QLabel("SEOHEAD")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        self.project_picker = QComboBox()
        self.project_picker.addItem(self.demo["label"])
        self.project_picker.setMinimumWidth(180)
        layout.addWidget(self.project_picker)
        open_project = QPushButton("Открыть проект")
        open_project.setIcon(icon("folder_open"))
        open_project.clicked.connect(self.choose_project)
        layout.addWidget(open_project)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.refresh_project)
        self.refresh_button.setEnabled(False)
        layout.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Отменить чтение")
        self.cancel_button.clicked.connect(self.cancel_active_work)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        layout.addStretch()
        self.source_badge = QLabel("Демо · синтетические данные")
        self.source_badge.setObjectName("sourceBadge")
        layout.addWidget(self.source_badge)
        self.new_scan = QPushButton("Новый скан")
        self.new_scan.setProperty("role", "primary")
        self.new_scan.setIcon(icon("play_arrow", "#FFFFFF"))
        self.new_scan.clicked.connect(self.scan_preview)
        layout.addWidget(self.new_scan)
        return top

    def work_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(QLabel("Работа с агентом"))
        self.progress_text = plain(
            "Демо структуры\n\nПланирование → конфигурация → сбор → анализ → сравнение → задачи → перепроверка → отчёт.\n\nОткройте локальный проект, чтобы увидеть сохранённый прогресс, задачи, сканы и входящие."
        )
        layout.addWidget(self.progress_text)
        self.activity_text = plain("Текущая активность будет показана только из локального project-activity.")
        layout.addWidget(self.activity_text)
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
        toolbar.addWidget(self.url_caption)
        toolbar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск URL в демо-наборе")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        toolbar.addWidget(self.search)
        settings = QPushButton("Панели")
        settings.setIcon(icon("view_sidebar"))
        settings.clicked.connect(lambda: self.overview.setVisible(not self.overview.isVisible()))
        toolbar.addWidget(settings)
        area.addLayout(toolbar)
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
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 115)
        self.table.setColumnWidth(4, 190)
        self.table.setColumnWidth(5, 85)
        self.table.selectionModel().currentRowChanged.connect(self.show_url)
        area.addWidget(self.table)
        self.vertical.addWidget(table_area)
        self.inspector = QTabWidget()
        self.detail = plain()
        self.inspector.addTab(self.detail, "Сведения")
        self.debug_detail = plain()
        self.inspector.addTab(self.debug_detail, "Диагностика")
        self.link_detail = plain("Внутренние ссылки появятся только из сохранённого скана.")
        self.inspector.addTab(self.link_detail, "Ссылки")
        self.evidence_detail = plain("Состояние скана появится после выбора сохранённого скана.")
        self.inspector.addTab(self.evidence_detail, "Снимки")
        for title in ("HTTP headers", "HTML", "Извлечение"):
            self.inspector.addTab(plain("Данные появятся только из сохранённых измерений ядра."), title)
        self.vertical.addWidget(self.inspector)
        self.vertical.setSizes([480, 230])
        self.horizontal.addWidget(self.vertical)
        self.overview = QWidget()
        summary = QVBoxLayout(self.overview)
        summary.setContentsMargins(16, 8, 4, 8)
        box = QGroupBox("Сводка")
        facts = QFormLayout(box)
        self.summary_source = QLabel("Synthetic fixture")
        self.summary_records = QLabel("5")
        self.summary_scan = QLabel("Не запускался")
        self.summary_coverage = QLabel("Нет измерений")
        for label, value in [("Источник", self.summary_source), ("Записей", self.summary_records), ("Краул", self.summary_scan), ("Покрытие", self.summary_coverage)]:
            facts.addRow(label, value)
        summary.addWidget(box)
        self.summary_note = plain("Выберите сохранённый скан на вкладке «Сканы», чтобы открыть первую ограниченную страницу URL.")
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
        layout.addWidget(self.scan_caption)
        self.scan_model = RecordModel((("Начальный URL", "start_url"), ("Состояние", "lifecycle"), ("Источник", "source_kind"), ("Завершён", "finished_at"), ("Частичный", "partial")), parent=self)
        self.scan_table = QTableView()
        self.scan_table.setModel(self.scan_model)
        configure_table(self.scan_table)
        self.scan_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.scan_table.selectionModel().currentRowChanged.connect(self.show_scan)
        layout.addWidget(self.scan_table, 3)
        controls = QHBoxLayout()
        self.owned_run_picker = QComboBox()
        self.owned_run_picker.setMinimumWidth(280)
        self.owned_run_picker.addItem("Запуски этого окна: нет", None)
        self.owned_run_picker.currentIndexChanged.connect(self.select_owned_run)
        controls.addWidget(self.owned_run_picker)
        self.stop_run_button = QPushButton("Остановить выбранный запуск")
        self.stop_run_button.setEnabled(False)
        self.stop_run_button.clicked.connect(self.stop_selected_run)
        controls.addWidget(self.stop_run_button)
        self.resume_scan_button = QPushButton("Продолжить выбранный скан")
        self.resume_scan_button.setEnabled(False)
        self.resume_scan_button.clicked.connect(self.resume_selected_scan)
        controls.addWidget(self.resume_scan_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.scan_detail = plain("Выберите сохранённый скан: URL загружаются только из его локальной SQLite-копии.")
        layout.addWidget(self.scan_detail, 2)
        return page

    def inbox_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        self.inbox_caption = QLabel("Входящие · откройте локальный проект")
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
        self.requests[request_id] = self.read_generation
        self.request_handlers[request_id] = handler
        try:
            gateway.submit(request_id, tool, arguments, self.read_generation)
        except (RuntimeError, ValueError) as exc:
            self.requests.pop(request_id, None)
            self.request_handlers.pop(request_id, None)
            self.statusBar().showMessage(str(exc))
            return
        self.cancel_button.setEnabled(True)

    def command_loaded(self, request_id, result, generation):
        if generation != self.read_generation:
            return
        self.requests.pop(request_id, None)
        handler = self.request_handlers.pop(request_id, None)
        if handler:
            handler(result)
        self.cancel_button.setEnabled(bool(self.requests))

    def command_failed(self, request_id, text, generation):
        if generation != self.read_generation:
            return
        self.requests.pop(request_id, None)
        self.request_handlers.pop(request_id, None)
        self.cancel_button.setEnabled(bool(self.requests))
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
        self.read_generation += 1
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
        self.owned_run_picker.addItem("Запуски этого окна: выберите проект", None)
        self.owned_run_picker.blockSignals(False)
        self.stop_run_button.setEnabled(False)
        label = site.get("label") or site.get("host") or "Подключённый проект"
        self.project_picker.clear()
        self.project_picker.addItem(label)
        self.source_badge.setText("Локальный проект · сохранённые данные")
        self.refresh_button.setEnabled(True)
        self.note_submit.setEnabled(True)
        self.detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.debug_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.summary_source.setText("Retained local core")
        self.summary_records.setText("—")
        self.summary_scan.setText("Выберите скан")
        self.summary_coverage.setText("См. прогресс проекта")
        self.refresh_project()
        self.load_crawl_descriptor()

    def load_crawl_descriptor(self):
        if self.crawl_descriptor is None:
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
        self.read_generation += 1
        directory = self.project_directory
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
        completion_text = "не измерено"
        if completion.get("state") == "measured":
            completion_text = f"{completion.get('numerator')}/{completion.get('denominator')} ({completion.get('percent')}%)"
        else:
            completion_text = completion.get("reason") or "не измерено"
        actions = result.get("next_actions") or []
        action_lines = [f"• {item.get('title') or item.get('id')}: {item.get('action')}" for item in actions]
        self.progress_text.setPlainText(
            "Сохранённый прогресс проекта\n\n"
            f"Состояние: {result.get('state', 'unknown')}\n"
            f"Задачи: {counts.get('complete', 0)} завершено, {counts.get('remaining', 0)} осталось, {counts.get('stale', 0)} устарело\n"
            f"Покрытие задач аудита: {completion_text}\n\n"
            + ("Следующие действия:\n" + "\n".join(action_lines) if action_lines else "Следующих действий ядро не объявило.")
        )

    def load_activity(self, result):
        sites = (result.get("sites") or {}).get("items") or []
        self.activity_text.setPlainText(json.dumps({"observed_at": result.get("observed_at"), "sites": sites}, ensure_ascii=False, indent=2))

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
            source="Project checklist · retained local state",
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
        self.task_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

    def load_scans(self, result):
        rows = []
        for item in result.get("items") or []:
            rows.append({**item, "partial": "да" if item.get("crawl_partial") or item.get("corpus_partial") else "нет"})
        self.scan_model.replace(rows)
        self.scan_caption.setText(f"Сканы · {result.get('total', len(rows))} сохранено · показано {len(rows)}")
        self.project_panels.set_page(
            "scans",
            rows,
            total=result.get("total"),
            offset=(result.get("pagination") or {}).get("offset", 0),
            has_more=(result.get("pagination") or {}).get("next_offset") is not None,
            source="Project retained scans",
        )
        if rows:
            self.scan_table.selectRow(0)

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
        self.selected_scan_path = path
        self.selected_scan_uuid = scan.get("uuid") if isinstance(scan.get("uuid"), str) else None
        self.model.replace([])
        self.search.clear()
        self.search.setEnabled(False)
        self.audit_workspace.set_page(
            "internal", [], state="loading", source="Loading retained scan", reason="Selected scan changed"
        )
        self.audit_workspace.set_page(
            "url_details", [], state="unavailable", reason="Selected scan changed"
        )
        self.resume_scan_button.setEnabled(False)
        self.scan_detail.setPlainText(json.dumps(scan, ensure_ascii=False, indent=2))
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
        self.evidence_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        source = result.get("source") if isinstance(result.get("source"), dict) else {}
        self.resume_scan_button.setEnabled(
            bool(scan_path)
            and source.get("lifecycle") == "interrupted"
            and Path(scan_path).is_file()
        )

    def load_urls(self, result, scan_path=None):
        if scan_path is not None and scan_path != self.selected_scan_path:
            return
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
        self.url_caption.setText(f"URL · сохранённая страница {len(rows)} записей")
        self.summary_records.setText(str(len(rows)))
        self.summary_scan.setText("Сохранённая SQLite-проекция")
        self.summary_coverage.setText("Частичная страница; см. метаданные скана")
        self.summary_note.setPlainText(json.dumps({key: result.get(key) for key in ("offset", "has_more", "next_offset", "truncated", "bytes")}, ensure_ascii=False, indent=2))
        self.audit_workspace.set_page(
            "internal",
            rows,
            total=None,
            offset=result.get("offset", 0),
            has_more=bool(result.get("has_more")),
            source=f"Retained scan {self.selected_scan_uuid or 'unknown'} · bounded page",
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
            "inbox", rows, total=total, source="Project durable inbox"
        )
        inbox_panel.set_submission_enabled(
            bool(self.project_directory), "Сохранение явным действием; скан не запускается"
        )
        if rows:
            self.inbox_table.selectRow(0)

    def load_unread(self, result):
        count = result.get("count", 0)
        label = "сообщений" if count != 1 else "сообщение"
        self.source_badge.setText(f"Локальный проект · {count} непрочит. {label}")

    def show_inbox_entry(self, current, _previous):
        if not current.isValid():
            return
        self.inbox_detail.setPlainText(json.dumps(self.inbox_model.rows[current.row()], ensure_ascii=False, indent=2))

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
            self.detail.setPlainText("Сохранённая ограниченная проекция страницы\n\n" + json.dumps(retained, ensure_ascii=False, indent=2))
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
        if scan_path != self.selected_scan_path:
            return
        self.link_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.audit_workspace.set_page(
            "inlinks",
            result.get("items") or [],
            total=result.get("total"),
            offset=result.get("offset", 0),
            has_more=bool(result.get("has_more")),
            source="Retained link graph",
        )

    def load_url_detail(self, result, scan_path=None, url=None):
        if scan_path != self.selected_scan_path:
            return
        self.detail.setPlainText(
            "Сохранённая деталь URL (без HTML-тела и секретных значений)\n\n"
            + json.dumps(result, ensure_ascii=False, indent=2)
        )
        page = result.get("page") or {}
        rows = [{"name": key, "value": value} for key, value in page.items()]
        self.audit_workspace.set_page(
            "url_details",
            rows,
            total=len(rows),
            source="Retained URL detail · redacted by core",
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

    def toggle_navigation(self):
        self.navigation.setVisible(not self.navigation.isVisible())

    def restore_panels(self):
        for widget in (self.navigation, self.overview, self.inspector):
            widget.show()
        self.horizontal.setSizes([1000, 280])
        self.vertical.setSizes([480, 230])

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
        def edit_advanced():
            editor = CrawlConfigurationDialog(self.crawl_descriptor, dialog, project_directory=self.project_directory, overrides=advanced_overrides)
            if editor.exec_() == QDialog.Accepted:
                advanced_overrides.clear()
                advanced_overrides.update(editor.get_overrides())
        advanced.clicked.connect(edit_advanced)
        layout.addWidget(advanced)

        def update_approval():
            elevated = limit.value() > 5_000 or requests.value() > 10_000 or duration.value() > 300
            approval.setVisible(elevated)
            if not elevated:
                approval.setChecked(False)
            buttons.button(QDialogButtonBox.Ok).setEnabled(not elevated or approval.isChecked())

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
                max_urls_per_second=0.5,
            )
            self.selected_managed_run_id = run_id
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
            label = f"{item['id'][:8]} · {item['kind']} · {item['state']}"
            self.owned_run_picker.addItem(label, item["id"])
        index = self.owned_run_picker.findData(selected)
        self.owned_run_picker.setCurrentIndex(index if index >= 0 else 0)
        self.owned_run_picker.blockSignals(False)
        self.select_owned_run()
        if run.get("state") in {"starting", "running"}:
            self.statusBar().showMessage("Локальный native crawl запущен; наблюдение обновляется каждые 0,5 с")
        elif run.get("state") in {"finished", "failed", "interrupted"}:
            self.refresh_project()
        if self._close_waiting:
            self._finish_owned_shutdown()

    def select_owned_run(self):
        value = self.owned_run_picker.currentData()
        self.selected_managed_run_id = value if isinstance(value, str) else None
        active = False
        if self.scan_manager and self.selected_managed_run_id:
            detail = self.scan_manager.detail(self.selected_managed_run_id)
            if detail is not None:
                self.scan_detail.setPlainText(json.dumps(detail, ensure_ascii=False, indent=2))
            active = any(
                item["id"] == self.selected_managed_run_id
                and item["state"] in {"starting", "running", "stop_requested"}
                for item in self.scan_manager.snapshot(self.current_project_uuid)
            )
        self.stop_run_button.setEnabled(active)
        self.cancel_button.setText("Остановить скан" if active else "Отменить чтение")

    def stop_selected_run(self):
        self.cancel_active_work()

    def managed_scan_output(self, run_id, text):
        if run_id != self.selected_managed_run_id:
            return
        current = self.scan_detail.toPlainText()
        self.scan_detail.setPlainText((current + "\n" + text).strip()[-20_000:])

    def poll_active_scan(self):
        if self.scan_manager is None or self.scan_manager.active_count == 0:
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
        if not self._close_waiting and self.scan_manager is not None and self.scan_manager.active_count:
            self._close_waiting = True
            self.scan_manager.stop_all_owned()
            event.ignore()
            QTimer.singleShot(50, self._finish_owned_shutdown)
            return
        self.cancel_requests()
        self.scan_poll_timer.stop()
        if self.mcp_gateway is not None:
            self.mcp_gateway.stop()
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
