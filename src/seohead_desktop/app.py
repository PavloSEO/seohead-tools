"""Native Qt Widgets shell over declared, local SEOHEAD CLI projections."""

from __future__ import annotations

import argparse
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

from .gateway import CoreCommand
from .models import RecordModel, UrlModel

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
        self.navigation.addItems(["Работа", "URL", "Задачи", "Сканы", "Входящие", "Отчёты", "Журнал"])
        body.addWidget(self.navigation)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        shell.addLayout(body, 1)

        self.pages.addWidget(self.work_page())
        self.pages.addWidget(self.url_page())
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
        self.cancel_button.clicked.connect(self.cancel_requests)
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
        for title in ("Ссылки", "HTTP headers", "HTML", "Снимки", "Извлечение"):
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

    def start_command(self, request_id, command, arguments, handler):
        if not self.core_executable:
            self.statusBar().showMessage("Укажите --core-cli: CLI ядра SEOHEAD не найден")
            return
        previous = self.requests.pop(request_id, None)
        if previous is not None:
            previous.cancel()
        worker = CoreCommand(
            self.core_executable,
            command,
            arguments,
            request_id=request_id,
            generation=self.read_generation,
        )
        self.requests[request_id] = worker
        self.request_handlers[request_id] = handler
        worker.signals.loaded.connect(self.command_loaded)
        worker.signals.failed.connect(self.command_failed)
        worker.signals.cancelled.connect(self.command_cancelled)
        self.cancel_button.setEnabled(True)
        self.pool.start(worker)

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
        self.statusBar().showMessage(f"{request_id}: {text}")

    def command_cancelled(self, request_id, generation):
        if generation != self.read_generation:
            return
        self.requests.pop(request_id, None)
        self.request_handlers.pop(request_id, None)
        self.cancel_button.setEnabled(bool(self.requests))

    def cancel_requests(self):
        for worker in self.requests.values():
            worker.cancel()
        self.requests.clear()
        self.request_handlers.clear()
        self.cancel_button.setEnabled(False)
        self.statusBar().showMessage("Текущие чтения отменены; сохранённые данные проекта не изменены")

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
        self.start_command("project-open", "project-open", ("--directory", directory), lambda result: self.project_loaded(result, self.read_generation))

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

    def refresh_project(self):
        if not self.project_directory:
            self.statusBar().showMessage("Сначала откройте локальный проект SEOHEAD")
            return
        self.cancel_requests()
        self.read_generation += 1
        directory = self.project_directory
        self.statusBar().showMessage("Обновление сохранённых проекций в фоне…")
        self.start_command("progress", "project-progress", ("--directory", directory, "--limit", str(PAGE_LIMIT)), self.load_progress)
        self.start_command("tasks", "project-checklist-page", ("--directory", directory, "--limit", str(PAGE_LIMIT)), self.load_tasks)
        self.start_command("scans", "project-scans", ("--directory", directory, "--limit", "20"), self.load_scans)
        self.start_command("activity", "project-activity", ("--directory", directory), self.load_activity)
        self.start_command("inbox", "project-inbox-list", ("--directory", directory, "--consumer", CONSUMER_ID, "--limit", "50"), self.load_inbox)
        self.start_command("unread", "project-inbox-unread", ("--directory", directory, "--consumer", CONSUMER_ID, "--limit", "10"), self.load_unread)

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
        if rows:
            self.task_table.selectRow(0)

    def show_task(self, current, _previous):
        if not current.isValid() or not self.project_directory:
            return
        item = self.task_model.rows[current.row()]
        item_id = item.get("id")
        if isinstance(item_id, str):
            self.start_command("task-detail", "project-task-detail", ("--directory", self.project_directory, "--item-id", item_id), self.load_task_detail)

    def load_task_detail(self, result):
        self.task_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

    def load_scans(self, result):
        rows = []
        for item in result.get("items") or []:
            rows.append({**item, "partial": "да" if item.get("crawl_partial") or item.get("corpus_partial") else "нет"})
        self.scan_model.replace(rows)
        self.scan_caption.setText(f"Сканы · {result.get('total', len(rows))} сохранено · показано {len(rows)}")
        if rows:
            self.scan_table.selectRow(0)

    def show_scan(self, current, _previous):
        if not current.isValid():
            return
        scan = self.scan_model.rows[current.row()]
        path = scan.get("path")
        if not isinstance(path, str) or not path:
            self.scan_detail.setPlainText("Ядро не предоставило путь сохранённого скана.")
            return
        self.scan_detail.setPlainText(json.dumps(scan, ensure_ascii=False, indent=2))
        self.start_command("url-page", "scan-inspect", ("--scan", path, "--table", "pages", "--limit", str(PAGE_LIMIT), "--offset", "0"), self.load_urls)

    def load_urls(self, result):
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
        if rows:
            self.table.selectRow(0)

    def load_inbox(self, result):
        self.inbox_revision = result.get("revision")
        rows = list(result.get("entries") or [])
        self.inbox_model.replace(rows)
        total = (result.get("pagination") or {}).get("total", len(rows))
        self.inbox_caption.setText(f"Входящие · {total} сохранённых записей")
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

    def submit_note(self):
        if not self.project_directory:
            return
        text = self.note_input.text().strip()
        if not text:
            self.statusBar().showMessage("Введите текст заметки")
            return
        arguments = ["--directory", self.project_directory, "--text", text, "--kind", self.note_kind.currentData(), "--author-role", "specialist"]
        if isinstance(self.inbox_revision, int):
            arguments.extend(("--expected-revision", str(self.inbox_revision)))
        self.note_submit.setEnabled(False)
        self.start_command("inbox-submit", "project-inbox-submit", arguments, self.note_saved)

    def note_saved(self, _result):
        self.note_input.clear()
        self.note_submit.setEnabled(True)
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
            return
        self.detail.setPlainText(
            "Демо · синтетическая запись\n\n"
            f"Адрес: {row['url']}\nHTTP: {row.get('status', 'Не измерено')}\n"
            f"Тип: {row.get('type', 'Не измерено')}\nИндексация: {row.get('indexability', 'Не измерено')}\n"
            f"Title: {row.get('title') or 'Не измерено'}\nПроблемы: {row.get('issues', 'Не измерено')}\n\n"
            "Это демонстрационные данные. Headers, HTML и cookies не измерялись."
        )
        self.debug_detail.setPlainText(json.dumps(row, ensure_ascii=False, indent=2))

    def toggle_navigation(self):
        self.navigation.setVisible(not self.navigation.isVisible())

    def restore_panels(self):
        for widget in (self.navigation, self.overview, self.inspector):
            widget.show()
        self.horizontal.setSizes([1000, 280])
        self.vertical.setSizes([480, 230])

    def scan_preview(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Новый скан · только preview каркаса")
        dialog.resize(500, 320)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.addRow("URL", QLineEdit("https://shop.example.test/"))
        mode = QComboBox()
        mode.addItems(["Native HTML", "Native JavaScript", "Screaming Frog"])
        form.addRow("Источник", mode)
        limit = QSpinBox()
        limit.setRange(1, 50000)
        limit.setValue(5000)
        form.addRow("Лимит URL", limit)
        layout.addLayout(form)
        message = QLabel("Каркас показывает будущий preview. Backend submission ещё не подключён: реальный скан не будет запущен.")
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec_()

    def closeEvent(self, event):
        self.cancel_requests()
        if self.settings:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("horizontal", self.horizontal.saveState())
            self.settings.setValue("vertical", self.vertical.saveState())
        super().closeEvent(event)


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
    load_theme(app)
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
