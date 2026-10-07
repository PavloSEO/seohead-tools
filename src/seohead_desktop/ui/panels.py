"""Composable three-pane audit workspace and SEOHEAD project panels."""

from itertools import islice
from string import Template

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .components import PAGE_LIMIT, TabDeck, TablePanel, display_value
from .tabcatalogue import DETAIL_TABS, MAIN_TABS, PROJECT_TABS, RIGHT_TABS, TAB_BY_ID


def component_stylesheet(tokens):
    """Consume the existing canonical theme; no duplicate color constants."""
    return Template("""
QLabel#panelMessage { background: $surface_container_lowest; color: $on_surface_variant; padding: 24px; border: 1px solid $outline_variant; border-radius: 8px; }
QWidget#evidencePreview { background: $surface_container_lowest; border: 1px solid $outline_variant; border-radius: 8px; }
QLabel#snippetTitle { color: $primary; font-size: 18px; }
QLabel#snippetURL { color: $on_surface_variant; }
QLabel#componentHeading { font-size: 20px; font-weight: 500; }
QListWidget#tabNavigation { background: $surface_container_low; border: none; padding: 8px; }
QListWidget#tabNavigation::item { min-height: 30px; padding: 6px 10px; border-radius: 6px; }
QListWidget#tabNavigation::item:selected { background: $primary_container; color: $on_primary_container; }
QListWidget#tabNavigation::item:hover { background: $surface_container; }
QPlainTextEdit#noteDraft { border: 1px solid $outline_variant; border-radius: 6px; }
QPlainTextEdit#noteDraft:focus { border: 2px solid $primary; }
QComboBox QAbstractItemView { background: $surface_container_lowest; selection-background-color: $primary_container; selection-color: $on_primary_container; }
QMenu { background: $surface_container_lowest; border: 1px solid $outline_variant; padding: 4px; }
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background: $primary_container; color: $on_primary_container; }
QTabBar QToolButton { border: none; border-radius: 0; padding: 0; width: 22px; }
""").substitute(tokens["colors"])


class SourcePanel(TablePanel):
    """Retained HTML is plain text; never creates a browser or executes scripts."""

    TEXT_LIMIT = 65536

    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setAccessibleName("Сохранённый исходный HTML")
        self.viewer.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.stack.addWidget(self.viewer)
        self.search.setPlaceholderText("Найти в сохранённом тексте · Enter")
        self.search.returnPressed.connect(self.find_text)

    def set_page(self, items, **kwargs):
        page = list(islice(iter(items), PAGE_LIMIT + 1))
        super().set_page(page, **kwargs)
        if not hasattr(self, "viewer"):
            return
        self.viewer.clear()
        if self.state == "ready" and page and page[0].get("source") is not None:
            source = str(page[0]["source"])
            self.viewer.setPlainText(source[: self.TEXT_LIMIT])
            self.stack.setCurrentWidget(self.viewer)
            if len(source) > self.TEXT_LIMIT:
                self.count_label.setText(
                    f"Показаны первые {self.TEXT_LIMIT:,} символов сохранённого текста"
                )

    def find_text(self):
        if not self.viewer.find(self.search.text()):
            cursor = self.viewer.textCursor()
            cursor.movePosition(cursor.Start)
            self.viewer.setTextCursor(cursor)
            self.viewer.find(self.search.text())


class SerpPreviewPanel(TablePanel):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.original = {}
        self.preview = QWidget()
        self.preview.setObjectName("evidencePreview")
        layout = QVBoxLayout(self.preview)
        fields = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setAccessibleName("Title для локального предпросмотра")
        self.description_edit = QLineEdit()
        self.description_edit.setAccessibleName(
            "Description для локального предпросмотра"
        )
        fields.addRow("Title", self.title_edit)
        fields.addRow("Description", self.description_edit)
        layout.addLayout(fields)
        self.preview_url = QLabel()
        self.preview_url.setObjectName("snippetURL")
        self.preview_title = QLabel()
        self.preview_title.setObjectName("snippetTitle")
        self.preview_description = QLabel()
        for label in (self.preview_url, self.preview_title, self.preview_description):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        reset = QPushButton("Вернуть сохранённые значения")
        reset.clicked.connect(self.reset_values)
        layout.addWidget(reset, 0, Qt.AlignLeft)
        note = QLabel(
            "Локальный предпросмотр. Поисковая выдача и её оформление не измерены."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        self.stack.addWidget(self.preview)
        self.title_edit.textChanged.connect(self.preview_title.setText)
        self.description_edit.textChanged.connect(self.preview_description.setText)

    def set_page(self, items, **kwargs):
        page = list(islice(iter(items), PAGE_LIMIT + 1))
        super().set_page(page, **kwargs)
        if not hasattr(self, "preview"):
            return
        self.original = dict(page[0]) if self.state == "ready" and page else {}
        self.reset_values()
        if self.original:
            self.stack.setCurrentWidget(self.preview)

    def reset_values(self):
        self.title_edit.setText(display_value(self.original.get("title")))
        self.description_edit.setText(display_value(self.original.get("description")))
        self.preview_url.setText(display_value(self.original.get("url")))


class InboxPanel(TablePanel):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.submission_enabled = False
        self.note = QPlainTextEdit()
        self.note.setObjectName("noteDraft")
        self.note.setAccessibleName("Новая заметка проекта")
        self.note.setPlaceholderText("Заметка или вопрос по проекту")
        self.note.setMaximumHeight(100)
        self.layout().addWidget(self.note)
        controls = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.setAccessibleName("Тип заметки")
        self.kind.addItem("Заметка", "note")
        self.kind.addItem("Вопрос", "question")
        controls.addWidget(self.kind)
        self.note_status = QLabel("Отправка не подключена")
        self.note_status.setObjectName("muted")
        self.note_status.setWordWrap(True)
        controls.addWidget(self.note_status, 1)
        self.submit_button = QPushButton("Отправить заметку")
        self.submit_button.setProperty("role", "primary")
        self.submit_button.setEnabled(False)
        self.submit_button.clicked.connect(self.submit)
        controls.addWidget(self.submit_button)
        self.layout().addLayout(controls)
        self.note.textChanged.connect(self._validate_note)

    def set_submission_enabled(self, enabled, reason=""):
        self.submission_enabled = bool(enabled)
        self.note_status.setText(
            reason
            or ("Отправка явным действием" if enabled else "Отправка не подключена")
        )
        self._validate_note()

    def _validate_note(self):
        length = len(self.note.toPlainText().strip())
        self.submit_button.setEnabled(self.submission_enabled and 0 < length <= 4000)
        if length > 4000:
            self.note_status.setText("Максимум 4 000 символов; текст сохранён в форме")

    def submit(self):
        text = self.note.toPlainText().strip()
        if self.submission_enabled and 0 < len(text) <= 4000:
            self.intent_requested.emit(
                "submit_note", {"text": text, "kind": self.kind.currentData()}
            )

    def submission_succeeded(self):
        self.note.clear()
        self.note_status.setText("Заметка сохранена ядром")

    def set_page(self, items, **kwargs):
        super().set_page(items, **kwargs)
        if hasattr(self, "note") and self.state == "unavailable":
            self.set_submission_enabled(False)


class ComparePanel(TablePanel):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        self.before = QComboBox()
        self.before.setAccessibleName("Исходный сохранённый скан")
        self.after = QComboBox()
        self.after.setAccessibleName("Новый сохранённый скан")
        self.compare_button = QPushButton("Предпросмотр сравнения")
        self.compare_button.setEnabled(False)
        chooser = QHBoxLayout()
        chooser.addWidget(self.before, 1)
        chooser.addWidget(self.after, 1)
        chooser.addWidget(self.compare_button)
        self.layout().insertLayout(0, chooser)
        self.before.currentIndexChanged.connect(self._valid_pair)
        self.after.currentIndexChanged.connect(self._valid_pair)
        self.compare_button.clicked.connect(self.preview_comparison)

    def set_scans(self, items):
        scans = list(islice(iter(items), PAGE_LIMIT + 1))
        if len(scans) > PAGE_LIMIT:
            raise ValueError("Scan chooser is bounded to one page")
        for combo in (self.before, self.after):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Выберите сохранённый скан", None)
            for scan in scans:
                label = f"{scan.get('finished_at', 'Дата неизвестна')} · {scan.get('uuid', 'ID неизвестен')}"
                combo.addItem(label, dict(scan))
            combo.blockSignals(False)
        self._valid_pair()

    def _valid_pair(self):
        before, after = self.before.currentData(), self.after.currentData()
        valid = bool(
            before
            and after
            and before.get("uuid")
            and after.get("uuid")
            and before["uuid"] != after["uuid"]
        )
        self.compare_button.setEnabled(valid)

    def preview_comparison(self):
        if self.compare_button.isEnabled():
            self.intent_requested.emit(
                "preview_compare",
                {
                    "before": self.before.currentData(),
                    "after": self.after.currentData(),
                },
            )

    def set_page(self, items, **kwargs):
        super().set_page(items, **kwargs)
        if hasattr(self, "before") and self.state == "unavailable":
            self.set_scans([])


def detail_factory(spec, parent):
    if spec.id == "view_source":
        return SourcePanel(spec, parent)
    if spec.id == "serp_snippet":
        return SerpPreviewPanel(spec, parent)
    return TablePanel(spec, parent)


class AuditWorkspace(QWidget):
    """SF-like organization with SEOHEAD native styling and explicit adapters."""

    intent_requested = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.setContentsMargins(12, 6, 12, 6)
        heading = QLabel("Аудит сохранённых данных")
        heading.setObjectName("sectionCaption")
        controls.addWidget(heading, 1)
        self.detail_toggle = QToolButton()
        self.detail_toggle.setText("Детали")
        self.detail_toggle.setToolTip("Скрыть или показать детали URL")
        self.detail_toggle.setAccessibleName("Скрыть или показать детали URL")
        self.detail_toggle.clicked.connect(lambda: self.detail.setVisible(self.detail.isHidden()))
        controls.addWidget(self.detail_toggle)
        self.right_toggle = QToolButton()
        self.right_toggle.setText("Сводка")
        self.right_toggle.setToolTip("Скрыть или показать сводку аудита")
        self.right_toggle.setAccessibleName("Скрыть или показать сводку аудита")
        self.right_toggle.clicked.connect(lambda: self.right.setVisible(self.right.isHidden()))
        controls.addWidget(self.right_toggle)
        layout.addLayout(controls)
        self.horizontal = QSplitter(Qt.Horizontal)
        self.vertical = QSplitter(Qt.Vertical)
        self.main = TabDeck(MAIN_TABS)
        self.detail = TabDeck(DETAIL_TABS, detail_factory)
        self.right = TabDeck(RIGHT_TABS)
        self.vertical.addWidget(self.main)
        self.vertical.addWidget(self.detail)
        self.horizontal.addWidget(self.vertical)
        self.horizontal.addWidget(self.right)
        self.horizontal.setChildrenCollapsible(True)
        self.vertical.setChildrenCollapsible(True)
        self.vertical.setStretchFactor(0, 1)
        self.vertical.setStretchFactor(1, 1)
        self.horizontal.setStretchFactor(0, 3)
        self.horizontal.setStretchFactor(1, 1)
        layout.addWidget(self.horizontal)
        for deck in (self.main, self.detail, self.right):
            deck.intent_requested.connect(self._relay)
            deck.current_changed.connect(
                lambda id: self.intent_requested.emit("open_panel", {"tab_id": id})
            )
        self.main.current_changed.connect(self._main_changed)
        self._shown = False
        self.restore_panels()

    def panel(self, id):
        pane = TAB_BY_ID[id].pane
        return {"main": self.main, "detail": self.detail, "right": self.right}[
            pane
        ].panel(id)

    def set_page(self, tab_id, items, **kwargs):
        if TAB_BY_ID[tab_id].pane == "main" and tab_id == self.main.current_id:
            self.detail.clear("Выберите URL в обновлённой странице.")
        self.panel(tab_id).set_page(items, **kwargs)

    def clear(self, reason="Выберите сохранённый источник."):
        for deck in (self.main, self.detail, self.right):
            deck.clear(reason)

    def restore_panels(self):
        self.main.show()
        self.detail.show()
        self.right.show()
        self.horizontal.setSizes([940, 330])
        self.vertical.setSizes([520, 300])

    def showEvent(self, event):
        if not self._shown:
            self.vertical.setSizes([520, 300])
            self.horizontal.setSizes([940, 330])
            self._shown = True
        super().showEvent(event)

    def _relay(self, intent, payload):
        if intent == "select_url":
            self.detail.clear("Выбран другой URL. Загрузите его сохранённые измерения.")
            source = self.panel(payload["tab_id"]).source_label.text()
            row = payload["row"]
            fields = [
                {"name": label, "value": row.get(key)}
                for key, label in TAB_BY_ID[payload["tab_id"]].columns
            ]
            self.set_page("url_details", fields, total=len(fields), source=source)
        self.intent_requested.emit(intent, payload)

    def _main_changed(self, id):
        panel = self.main.panel(id)
        current = panel.table.currentIndex()
        if panel.state == "ready" and current.isValid():
            panel._selected(current, None)
        else:
            self.detail.clear("Выберите URL в текущем разделе.")


class ProjectPanels(TabDeck):
    """Project views use plain projections and semantic signals only."""

    refresh = pyqtSignal()
    select_task = pyqtSignal(str)
    select_scan = pyqtSignal(dict)
    submit_note = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(PROJECT_TABS, self._factory, parent)
        self.intent_requested.connect(self._semantic_intent)
        self.current_changed.connect(
            lambda id: self.intent_requested.emit("open_panel", {"tab_id": id})
        )

    @staticmethod
    def _factory(spec, parent):
        return {"inbox": InboxPanel, "compare": ComparePanel}.get(spec.id, TablePanel)(
            spec, parent
        )

    def set_page(self, tab_id, items, **kwargs):
        self.panel(tab_id).set_page(items, **kwargs)

    def _semantic_intent(self, intent, payload):
        if intent == "refresh":
            self.refresh.emit()
        elif intent == "select_task":
            id = payload["row"].get("id")
            if id is not None:
                self.select_task.emit(str(id))
        elif intent == "select_scan":
            self.select_scan.emit(dict(payload["row"]))
        elif intent == "submit_note":
            self.submit_note.emit(payload["text"], payload["kind"])
