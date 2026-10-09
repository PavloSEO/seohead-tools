"""Main-window methods: pages."""

from __future__ import annotations

import math

from PyQt5.QtCore import (
    QSortFilterProxyModel,
    Qt,
)
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
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
from .crawl_configuration import preview_configuration
from .models import RecordModel, UrlModel
from .scan_runner import crawl_arguments
from .ui.crawl_configuration_dialog import CrawlConfigurationDialog
from .ui.icons import material_icon as icon
from .ui.panels import AuditWorkspace, ProjectPanels
from .ui.presentation import (
    ElidedLabel,
    SwitchCheckBox,
    WorkspaceSplitter,
    theme_tokens,
    value_text,
)
from .ui.work_monitor import WorkMonitor


class PagesMixin:
    def work_page(self):
        page = QWidget()
        page.setProperty("spaciousPage", True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = self.work_heading = QLabel("Работа с агентом")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        caption = QLabel("Согласованный объём, следующие действия и отдельные запуски проекта")
        caption.setObjectName("sectionCaption")
        caption.hide()
        title.setToolTip(caption.text())
        self.work_splitter = WorkspaceSplitter(Qt.Vertical)
        self.progress_text = plain("Откройте проект, чтобы увидеть сохранённые задачи и согласованный план.\n\nДемо не содержит измеренного прогресса проекта.")
        self.progress_text.setAccessibleName("Прогресс задач проекта")
        self.progress_text.setProperty("role", "summary")
        activity = QWidget()
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(0, 8, 0, 0)
        controls = QHBoxLayout()
        self.work_plan_toggle = QToolButton()
        self.work_detail_toggle = QToolButton()
        for button, text, name, kind in (
            (self.work_plan_toggle, "План и действия", "fact_check", "plan"),
            (self.work_detail_toggle, "Детали запуска", "info", "detail"),
        ):
            button.setText(text)
            button.setIcon(icon(name))
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setProperty("role", "panelToggle")
            button.setAccessibleName("Показать или скрыть: " + text)
            button.toggled.connect(lambda visible, panel=kind: self.toggle_work_inspector(panel, visible))
            controls.addWidget(button)
        self.work_plan_summary = ElidedLabel("План не загружен")
        self.work_plan_summary.setObjectName("metadata")
        controls.addWidget(self.work_plan_summary, 1)
        activity_layout.addLayout(controls)
        self.activity_caption = QLabel("Запуски · источник не подключён")
        self.activity_caption.setObjectName("sectionCaption")
        activity_layout.addWidget(self.activity_caption)
        self.activity_model = RecordModel((("Запуск", "label"), ("Источник", "kind"), ("Состояние", "state"), ("Этап", "phase"), ("Получено", "fetched"), ("Очередь", "queued"), ("В работе", "inflight"), ("Сейчас", "rate"), ("Лимит, запр./с", "rate_limit")), parent=self)
        self.activity_table = QTableView()
        self.activity_table.setAccessibleName("Отдельные запуски проекта")
        self.activity_table.setModel(self.activity_model)
        configure_table(self.activity_table)
        for column, width in enumerate((180, 85, 160, 140, 80, 80, 80, 140, 110)):
            self.activity_table.setColumnWidth(column, width)
        self.activity_table.selectionModel().currentRowChanged.connect(self.show_observed_run)
        self.activity_table.clicked.connect(lambda index: self.show_observed_run(index, None))
        self.work_splitter.addWidget(self.activity_table)
        self.activity_text = plain("Нет измерений активности. История появится из сохранённых запусков проекта.")
        self.activity_text.setAccessibleName("Измерения выбранного запуска")
        self.work_inspector = QStackedWidget()
        self.work_inspector.addWidget(self.progress_text)
        self.work_inspector.addWidget(self.activity_text)
        self.work_splitter.addWidget(self.work_inspector)
        self.work_splitter.setStretchFactor(0, 1)
        self.work_splitter.setStretchFactor(1, 0)
        self.work_inspector.hide()
        activity_layout.addWidget(self.work_splitter, 1)
        activity_layout.addWidget(self.run_history_controls())
        self.work_views = QTabWidget()
        self.work_monitor = WorkMonitor()
        self.work_monitor.set_reduced_motion(self.reduced_motion)
        self.work_monitor.openProjectRequested.connect(self.choose_project)
        self.work_monitor.set_project_available(bool(self.project_directory))
        self.work_heading.setVisible(bool(self.project_directory))
        self.work_monitor.runSelected.connect(self.select_observed_identity)
        self.work_monitor.showResult.connect(self.open_observed_result)
        self.work_views.addTab(self.work_monitor, icon("dashboard"), "Монитор")
        self.work_views.addTab(activity, icon("table_chart"), "Таблица и детали")
        self.work_monitor.allRunsRequested.connect(lambda: self.work_views.setCurrentIndex(1))
        self.work_views.tabBar().setVisible(bool(self.project_directory))
        layout.addWidget(self.work_views, 1)
        return page

    def toggle_work_inspector(self, kind, visible):
        buttons = (self.work_plan_toggle, self.work_detail_toggle)
        selected = buttons[0] if kind == "plan" else buttons[1]
        if visible:
            for button in buttons:
                if button is not selected:
                    button.blockSignals(True)
                    button.setChecked(False)
                    button.blockSignals(False)
            self.work_inspector.setCurrentWidget(self.progress_text if kind == "plan" else self.activity_text)
            was_hidden = self.work_inspector.isHidden()
            self.work_inspector.show()
            if was_hidden:
                self.work_splitter.setSizes([max(180, self.work_splitter.height() - 144), 144])
        elif not any(button.isChecked() for button in buttons):
            self.work_inspector.hide()

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
        layout.addWidget(self.run_history_controls())
        return page

    def run_history_controls(self):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("История запусков загружается…")
        label.setObjectName("metadata")
        label.setWordWrap(True)
        previous = QPushButton("Новее")
        following = QPushButton("Старее")
        previous.setIcon(icon("chevron_left"))
        following.setIcon(icon("chevron_right"))
        previous.clicked.connect(lambda: self.change_run_history(-1))
        following.clicked.connect(lambda: self.change_run_history(1))
        layout.addWidget(label, 1)
        layout.addWidget(previous)
        layout.addWidget(following)
        self._run_history_controls.append((container, label, previous, following))
        container.hide()
        return container

    def observer_arguments(self):
        arguments = {"directory": self.project_directory, "consumer": CONSUMER_ID, "scan_limit": 20}
        if self._run_history_supported:
            arguments.update(run_offset=self._run_history_offset, run_limit=self._run_history_limit)
        return arguments

    def update_run_history_controls(self):
        pagination = self._run_envelope.get("pagination")
        self._run_history_supported = isinstance(pagination, dict) and all(
            type(pagination.get(key)) is int and pagination[key] >= 0
            for key in ("offset", "limit", "total")
        ) and 1 <= pagination["limit"] <= 100
        for container, label, previous, following in self._run_history_controls:
            container.setVisible(self._run_history_supported and bool(self.project_directory))
            if not self._run_history_supported:
                continue
            offset, total = pagination["offset"], pagination["total"]
            end = min(offset + pagination["limit"], total)
            selected = f"{offset + 1}–{end} из {total}" if end > offset else "нет записей"
            retained = (self._run_envelope.get("retention") or {}).get("max_runs")
            label.setText(f"Завершённые: {selected} · активные: {value_text(self._run_envelope.get('active_total'))}" + (f" · история: до {retained} запусков" if retained else ""))
            previous.setEnabled(offset > 0)
            following.setEnabled(pagination.get("has_more") is True)

    def change_run_history(self, direction):
        if not self._run_history_supported or self._project_loading or self._pending_note is not None:
            return
        pagination = self._run_envelope["pagination"]
        if direction > 0 and pagination.get("next_offset") is None:
            return
        self.cancel_requests()
        self._run_history_offset = max(0, self._run_history_offset - self._run_history_limit) if direction < 0 else pagination["next_offset"]
        self.last_observer_signature = None
        self.poll_active_scan()

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
        page.setProperty("spaciousPage", True)
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
        self._url_column_bases = [310, 64, 132, 112, 176, 104]
        self._fitting_url_columns = False
        self.table.horizontalHeader().sectionResized.connect(self.remember_url_column_width)
        self.table.viewport().installEventFilter(self)
        self.table.selectionModel().currentRowChanged.connect(self.show_url)
        area.addWidget(self.table)
        self.url_empty = QLabel("Выберите сохранённый скан")
        self.url_empty.setObjectName("panelMessage")
        self.url_empty.setAlignment(Qt.AlignCenter)
        self.url_empty.hide()
        area.addWidget(self.url_empty, 1)
        paging = QHBoxLayout()
        self.url_page_label = QLabel("Демо · без постраничного чтения")
        self.url_page_label.setObjectName("metadata")
        paging.addWidget(self.url_page_label, 1)
        self.url_previous = QPushButton("Назад")
        self.url_previous.setIcon(icon("chevron_left"))
        self.url_next = QPushButton("Далее")
        self.url_next.setIcon(icon("chevron_right"))
        self.url_previous.clicked.connect(lambda: self.request_url_page(max(0, self._url_page_offset - PAGE_LIMIT)))
        self.url_next.clicked.connect(lambda: self.request_url_page(self._url_page_offset + len(self.model.rows)))
        self.url_previous.setEnabled(False)
        self.url_next.setEnabled(False)
        paging.addWidget(self.url_previous)
        paging.addWidget(self.url_next)
        area.addLayout(paging)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.url_context_menu)
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

    def scan_preview(self):
        """«Новый скан» (design-v2 dialog): a plan first, a separate confirmed «Запустить»."""
        from .screens.new_scan import open_new_scan

        return open_new_scan(self)

    def legacy_scan_preview(self):
        if not self.project_directory or self._project_loading:
            self.notice.show_error("Сначала откройте проект и дождитесь его данных")
            return
        project_key = self.note_project_key()
        draft = self._scan_drafts.get(project_key, {})
        dialog = QDialog(self)
        dialog.setWindowTitle("Новый скан")
        dialog.resize(640, 470)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(12)
        target = ((self.project_result or {}).get("project") or {}).get("site", {}).get("target") or "Не измерено"
        target_input = QLineEdit(target)
        target_input.setReadOnly(True)
        target_input.setToolTip("Адрес выбранного проекта")
        form.addRow("URL проекта", target_input)
        source_mode = QComboBox()
        source_mode.setObjectName("scanSourceMode")
        source_mode.setAccessibleName("Источник URL: спайдер или sitemap")
        source_mode.addItem("Спайдер · переход по ссылкам", "spider")
        source_mode.addItem("Только URL из sitemap", "sitemap")
        source_mode.setCurrentIndex(max(0, source_mode.findData(draft.get("source", "spider"))))
        form.addRow("Источник", source_mode)
        sitemap_input = QLineEdit(draft.get("sitemap", ""))
        sitemap_input.setObjectName("scanSitemapUrl")
        sitemap_input.setAccessibleName("Адрес sitemap")
        sitemap_input.setPlaceholderText("https://example.com/sitemap.xml")
        form.addRow("Sitemap", sitemap_input)
        mode = QComboBox()
        mode.setObjectName("scanRenderingMode")
        mode.addItem("Исходный HTML", "raw")
        mode.addItem("С JavaScript", "js")
        mode.setCurrentIndex(max(0, mode.findData(draft.get("mode", "raw"))))
        form.addRow("Режим", mode)
        speed_row = QHBoxLayout()
        rate = QComboBox()
        rate.setEditable(True)
        rate.addItems(["2", "5", "10"])
        rate.setCurrentText(str(draft.get("rps", "2")))
        rate.setObjectName("scanRequestRate")
        rate.setAccessibleName("Предельное число запросов в секунду")
        threads = QSpinBox()
        threads.setRange(1, 1024)
        threads.setValue(draft.get("threads", 1))
        threads.setObjectName("scanConcurrency")
        threads.setAccessibleName("Число потоков сканирования")
        speed_row.addWidget(rate, 1)
        speed_row.addWidget(QLabel("Потоки"))
        speed_row.addWidget(threads, 1)
        form.addRow("Запросов/с", speed_row)
        limit_row = QHBoxLayout()
        limit_enabled = SwitchCheckBox("Включить")
        limit_enabled.setObjectName("scanLimitEnabled")
        limit_enabled.setChecked(draft.get("limit_enabled", False))
        limit_enabled.setAccessibleName("Ограничить число URL")
        limit = QSpinBox()
        limit.setRange(1, 1_000_000)
        limit.setValue(max(1, draft.get("limit", 2000)))
        limit.setSuffix(" URL")
        limit.setObjectName("scanUrlLimit")
        limit_row.addWidget(limit_enabled)
        limit_row.addWidget(limit, 1)
        form.addRow("Лимит URL", limit_row)
        save_html = SwitchCheckBox("Сохранять")
        save_html.setObjectName("scanSaveHtml")
        save_html.setAccessibleName("Сохранять HTML страниц для повторного анализа")
        save_html.setToolTip("Сохраняет код страниц. При выключении повторный анализ и поиск по сохранённому HTML недоступны.")
        save_html.setChecked(draft.get("save_html", draft.get("overrides", {}).get("storage.body_mode", "captured_entity_bytes") != "off"))
        form.addRow("HTML страниц", save_html)
        layout.addLayout(form)
        advanced_overrides = dict(draft.get("overrides", {}))
        advanced_overrides.setdefault("limits.max_requests", draft.get("requests", 0))
        advanced_overrides.setdefault("limits.max_crawl_seconds", draft.get("duration", 0))
        advanced = QPushButton("Расширенные настройки…")
        advanced.setObjectName("scanAdvancedSettings")
        advanced.setProperty("role", "quiet")
        advanced.setIcon(icon("settings"))
        layout.addWidget(advanced, 0, Qt.AlignLeft)
        message = QLabel()
        message.setObjectName("metadata")
        message.setWordWrap(True)
        message.setTextFormat(Qt.PlainText)
        message.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(message)
        feedback = QLabel()
        feedback.setObjectName("scanValidationFeedback")
        feedback.setWordWrap(True)
        feedback.setTextFormat(Qt.PlainText)
        feedback.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(feedback)
        retry = QPushButton("Повторить загрузку настроек")
        retry.clicked.connect(self.load_crawl_descriptor)
        layout.addWidget(retry, 0, Qt.AlignLeft)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        start_button = buttons.button(QDialogButtonBox.Ok)
        start_button.setText("Запустить")
        start_button.setObjectName("scanStartButton")
        start_button.setProperty("role", "primary")
        layout.addWidget(buttons)
        accepted = []

        def values():
            try:
                rps = float(rate.currentText().strip().replace(",", "."))
            except ValueError:
                raise ValueError("Введите положительное число запросов в секунду") from None
            if not math.isfinite(rps) or rps <= 0:
                raise ValueError("Введите положительное конечное число запросов в секунду")
            full_capability = ((self.crawl_descriptor or {}).get("capabilities") or {}).get("full_site_native_sqlite") is True
            defaults = {"limits.max_requests": 0, "limits.max_crawl_seconds": 0}
            if full_capability:
                defaults.update({"limits.max_depth": -1, "storage.min_free_bytes": 12 * 1024**3})
            return {**defaults, **advanced_overrides,
                    "limits.max_urls": limit.value() if limit_enabled.isChecked() else 0,
                    "rendering.mode": mode.currentData(),
                    "storage.body_mode": "captured_entity_bytes" if save_html.isChecked() else "off",
                    "speed.min_delay_seconds": 1 / rps, "speed.concurrency": threads.value()}

        def prepare():
            if self.note_project_key() != project_key or self._project_loading:
                raise ValueError("Проект изменился. Откройте план для текущего проекта.")
            if self.crawl_descriptor is None:
                raise ValueError(self._crawl_descriptor_error or "Параметры ядра загружаются…")
            settings = values()
            if settings["limits.max_urls"] == 0 and (self.crawl_descriptor.get("capabilities") or {}).get("full_site_native_sqlite") is not True:
                raise ValueError("Это ядро не поддерживает обход без лимита URL. Включите явный лимит или используйте обновлённый комплект.")
            sitemap = sitemap_input.text().strip() if source_mode.currentData() == "sitemap" else None
            if sitemap is not None and (self.crawl_descriptor.get("capabilities") or {}).get("sitemap_only_retained") is not True:
                raise ValueError("Подключённое ядро не поддерживает сохранённый sitemap-скан")
            preview = preview_configuration(self.crawl_descriptor, settings)
            crawl_arguments(self.project_directory, settings["limits.max_urls"], mode.currentData(), overrides=tuple(preview["overrides"].items()), approve_large_crawl=True, sitemap_url=sitemap)
            return (settings["limits.max_urls"], mode.currentData(), settings["limits.max_requests"], settings["limits.max_crawl_seconds"], True, settings, sitemap)

        def explain(error):
            text = str(error)
            if "Sitemap URL" in text:
                return "Укажите полный HTTP(S)-адрес sitemap без логина, пароля и #фрагмента, до 4096 символов."
            if "port" in text.lower():
                return "В адресе sitemap некорректный порт. Исправьте адрес; остальные настройки сохранены."
            if "selected scan project" in text:
                return "Папка проекта недоступна. Настройки сохранены; проверьте расположение проекта."
            return text

        def update_plan():
            capabilities = (self.crawl_descriptor or {}).get("capabilities") or {}
            source_mode.model().item(1).setEnabled(capabilities.get("sitemap_only_retained") is True)
            source_mode.model().item(1).setToolTip("Только URL из XML" if capabilities.get("sitemap_only_retained") else "Возможность не объявлена ядром")
            advanced.setEnabled(self.crawl_descriptor is not None)
            retry.setVisible(self.crawl_descriptor is None and bool(self._crawl_descriptor_error))
            limit.setEnabled(limit_enabled.isChecked())
            limit.setVisible(limit_enabled.isChecked())
            sitemap_only = source_mode.currentData() == "sitemap"
            sitemap_input.setVisible(sitemap_only)
            form.labelForField(sitemap_input).setVisible(sitemap_only)
            scope = (f"Только URL из sitemap: {sitemap_input.text().strip() or 'укажите адрес'}\nБез перехода по ссылкам со страниц." if sitemap_only else "Спайдер · по настройкам проекта")
            population = f"Лимит: {limit.value():,} URL".replace(",", " ") if limit_enabled.isChecked() else "Без лимита URL"
            message.setText(scope + "\n" + population)
            try:
                prepare()
                feedback.setText("План проверен. Нажмите «Запустить», когда готовы.")
                feedback.setToolTip("")
                ready = True
            except (TypeError, ValueError, OSError) as exc:
                feedback.setText(explain(exc))
                feedback.setToolTip(str(exc))
                ready = False
            start_button.setEnabled(ready)

        def edit_advanced():
            try:
                editor = CrawlConfigurationDialog(self.crawl_descriptor, dialog, project_directory=self.project_directory, overrides=values())
                if editor.exec_() == QDialog.Accepted:
                    selected = editor.get_overrides()
                    advanced_overrides.clear()
                    advanced_overrides.update(selected)
                    cap = selected.get("limits.max_urls", 0)
                    limit_enabled.setChecked(cap > 0)
                    if cap > 0:
                        limit.setValue(cap)
                    delay = selected.get("speed.min_delay_seconds", .5)
                    rate.setCurrentText(str(1 / delay) if delay > 0 else rate.currentText())
                    count = selected.get("speed.concurrency", threads.value())
                    threads.setMaximum(max(threads.maximum(), count))
                    threads.setValue(count)
                    mode.setCurrentIndex(max(0, mode.findData(selected.get("rendering.mode", mode.currentData()))))
                    save_html.setChecked(selected.get("storage.body_mode", "captured_entity_bytes") != "off")
                    update_plan()
            except (TypeError, ValueError, OSError) as exc:
                feedback.setText(explain(exc))

        def accept_plan():
            try:
                payload = prepare()
            except (TypeError, ValueError, OSError) as exc:
                feedback.setText(explain(exc))
                feedback.setToolTip(str(exc))
                return
            accepted.append(payload)
            dialog.accept()

        def release(*_args):
            try:
                self.crawl_descriptor_changed.disconnect(update_plan)
            except (TypeError, RuntimeError):
                pass

        advanced.clicked.connect(edit_advanced)
        for signal in (limit.valueChanged, limit_enabled.toggled, save_html.toggled, rate.currentTextChanged, threads.valueChanged, source_mode.currentIndexChanged, sitemap_input.textChanged, mode.currentIndexChanged):
            signal.connect(update_plan)
        self.crawl_descriptor_changed.connect(update_plan)
        dialog.finished.connect(release)
        buttons.accepted.connect(accept_plan)
        buttons.rejected.connect(dialog.reject)
        update_plan()
        if self.crawl_descriptor is None:
            self.load_crawl_descriptor()
        dialog.exec_()
        self._scan_drafts[project_key] = {"source": source_mode.currentData(), "sitemap": sitemap_input.text(), "mode": mode.currentData(), "limit": limit.value(), "limit_enabled": limit_enabled.isChecked(), "save_html": save_html.isChecked(), "rps": rate.currentText(), "threads": threads.value(), "overrides": dict(advanced_overrides)}
        release()
        dialog.deleteLater()
        if accepted:
            self.launch_scan(*accepted[0])
