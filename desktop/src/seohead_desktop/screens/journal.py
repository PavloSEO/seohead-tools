"""Журнал (canvas Log.dc.html): the last 200 lifecycle events of the runs the core observed.

The core reports only run events (time, phase, code) with no actor and no text, so the actor filters of the sheet
(agent / you / app) are disabled and say so; project-wide paging and a structured journal wait for #923.
"""

from __future__ import annotations

from PyQt5.QtCore import QAbstractTableModel, Qt, QUrl
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
)

from .. import i18n, theming
from ..i18n import tr, trf
from ..ui.icons import material_icon
from ..ui.kit import StatePanel, no_project_panel, style_table, waiting_badge
from ..ui.presentation import short_run_id
from . import scan_common
from .base import Screen
from .scan_common import CODES, EVENT_ICONS, JOURNAL_ISSUE, PHASES, parse_time

LIMIT = 200
KIND_NAMES = {"native": "Native", "sitemap": "Sitemap", "screaming_frog": "Screaming Frog"}
ICON_ROLES = {"started": "success", "failed": "error", "finished": "success"}
FILTERS = (("all", "apps", "Все"), ("scan", "manage_search", "Сканы"), ("agent", "smart_toy", "Агент"), ("me", "person", "Вы"), ("app", "desktop_windows", "Приложение"))
AVAILABLE_SOURCES = {"all", "scan"}
COLUMNS = ("Время", "Источник", "Событие", "Запуск скана")


class Event:
    def __init__(self, run, event):
        self.run_id = run.get("id") or ""
        self.kind = run.get("kind")
        self.code, self.phase = event.get("code"), event.get("phase")
        self.message = event.get("message") if isinstance(event.get("message"), str) else None
        self.at = parse_time(event.get("at"))

    @property
    def source(self):
        name = KIND_NAMES.get(self.kind)
        return trf("Скан · {kind}", kind=name) if name else tr("Скан")

    @property
    def text(self):
        if self.message:
            return self.message
        text = trf("{code} · {phase}", code=CODES.get(self.code, "событие"), phase=PHASES.get(self.phase, "этап не указан"))
        return text[:1].upper() + text[1:]

    def when(self, today):
        if self.at is None:
            return None
        local = self.at.astimezone()
        return local.strftime("%H:%M:%S") if local.date() == today else local.strftime("%d.%m %H:%M:%S")

    def haystack(self, today):
        return " ".join(part for part in (self.when(today), self.source, self.text, self.run_id) if part).lower()


def collect_events(runs):
    """Newest first, at most ``LIMIT``; also the number of events the loaded runs hold."""
    events = [Event(run, event) for run in runs or [] for event in run.get("events") or []]
    events.sort(key=lambda item: item.at.timestamp() if item.at else 0.0, reverse=True)
    return events[:LIMIT], len(events)


class EventModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.today = scan_common.now().astimezone().date()

    def set_rows(self, rows, today):
        self.beginResetModel()
        self.rows, self.today = list(rows), today
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        return tr(COLUMNS[section]) if orientation == Qt.Horizontal and role == Qt.DisplayRole else None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        event, column = self.rows[index.row()], index.column()
        if role == Qt.DisplayRole:
            return (event.when(self.today) or tr("Нет данных"), event.source, event.text, short_run_id(event.run_id) or tr("Нет данных"))[column]
        if role == Qt.DecorationRole and column == 1:
            colour = theming.roles()[ICON_ROLES.get(event.code, "text_2")]
            return material_icon(EVENT_ICONS.get(event.code, "info"), colour)
        if role == Qt.ToolTipRole:
            return event.run_id if column == 3 else event.text if column == 2 else None
        if role == Qt.ForegroundRole and column in (0, 3):
            return QColor(theming.roles()["text_3"])
        return None


class JournalScreen(Screen):
    slot = "journal"
    watches = ("project", "observer")

    def __init__(self, host):
        super().__init__(host)
        self.filter = "all"
        self.query = ""
        self.events, self.total = [], 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setProperty("page_bar", True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 8, 16, 8)
        bar_layout.setSpacing(8)
        title = QLabel(tr("Журнал работы"))
        title.setProperty("text_style", "title")
        bar_layout.addWidget(title)
        bar_layout.addSpacing(12)
        self.pills = {}
        for key, icon, label in FILTERS:
            pill = QToolButton()
            pill.setProperty("pill", "group")
            pill.setCheckable(True)
            pill.setChecked(key == "all")
            pill.setText(tr(label))
            pill.setIcon(material_icon(icon, theming.roles()["text_2"]))
            pill.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            if key in AVAILABLE_SOURCES:
                pill.setToolTip(tr(label))
                pill.clicked.connect(lambda _checked=False, value=key: self.set_filter(value))
            else:
                pill.setEnabled(False)
                pill.setToolTip(tr("Источник не указан: ядро не записывает действия агента, ваши действия и события приложения в журнал. Недоступно в этой версии ядра"))
            self.pills[key] = pill
            bar_layout.addWidget(pill)
        bar_layout.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Поиск в журнале…"))
        self.search.setAccessibleName(tr("Поиск в журнале"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(material_icon("search", theming.roles()["text_3"]), QLineEdit.LeadingPosition)
        self.search.setMinimumWidth(120)
        self.search.setMaximumWidth(240)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search.textChanged.connect(self.set_query)
        bar_layout.addWidget(self.search)
        root.addWidget(bar)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.model = EventModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        style_table(self.table, "standard")
        self.table.setAccessibleName(tr("События запусков"))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column, width in ((0, 130), (1, 180), (3, 110)):
            self.table.setColumnWidth(column, width)
        self.stack.addWidget(self.table)
        self.state = None
        foot = QFrame()
        foot.setProperty("table_head", True)
        foot_layout = QHBoxLayout(foot)
        foot_layout.setContentsMargins(16, 6, 16, 6)
        foot_layout.setSpacing(8)
        self.foot_text = QLabel()
        self.foot_text.setProperty("text_style", "meta")
        self.foot_text.setWordWrap(True)
        foot_layout.addWidget(self.foot_text, 1)
        foot_layout.addWidget(waiting_badge(JOURNAL_ISSUE))
        self.open_folder = QPushButton(tr("Открыть папку проекта"))
        self.open_folder.setProperty("size", "pill")
        self.open_folder.setIcon(material_icon("folder_open"))
        self.open_folder.clicked.connect(self.reveal_project)
        foot_layout.addWidget(self.open_folder)
        root.addWidget(foot)
        i18n.signals.changed.connect(self._language_changed)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        style = Qt.ToolButtonIconOnly if event.size().width() < 900 else Qt.ToolButtonTextBesideIcon
        for pill in self.pills.values():
            pill.setToolButtonStyle(style)

    def _language_changed(self, _language):
        self.refresh()

    def set_filter(self, key):
        self.filter = key
        for name, pill in self.pills.items():
            pill.setChecked(name == key)
        self.refresh()

    def set_query(self, text):
        self.query = text.strip().lower()
        self.refresh()

    def reveal_project(self):
        if self.host.project_directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.host.project_directory))

    def _set_state(self, kind, panel=None):
        if self.state is not None:
            old = self.stack.widget(1)
            self.stack.removeWidget(old)
            old.deleteLater()
            self.state = None
        if panel is not None:
            self.stack.addWidget(panel)
            self.stack.setCurrentIndex(1)
            self.state = kind
        else:
            self.stack.setCurrentIndex(0)

    def refresh(self):
        host = self.host
        self.events, self.total = collect_events(host.observed_runs) if host.project_directory else ([], 0)
        today = scan_common.now().astimezone().date()
        shown = [event for event in self.events if not self.query or self.query in event.haystack(today)]
        if not host.project_directory:
            kind, build = "noproject", lambda: no_project_panel(host, "Откройте проект, чтобы увидеть события его запусков.")
        elif not self.events and host._project_loading:
            kind, build = "loading", lambda: StatePanel("loading", "Чтение проекта", "События запусков появятся после чтения.")
        elif not self.events:
            kind, build = "empty", lambda: StatePanel("empty", "Событий запусков нет", "Ядро не вернуло запусков проекта: события появятся после первого скана.")
        elif not shown:
            kind, build = "nomatch", lambda: StatePanel("empty", "Ничего не найдено", "В загруженных событиях нет совпадений с поиском.", action=("Сбросить поиск", self.search.clear))
        else:
            kind, build = None, None
        if kind != self.state:
            self._set_state(kind, build() if build else None)
        self.model.set_rows(shown, today)
        loaded = len(self.events)
        self.foot_text.setText(
            trf("Показаны последние {n} событий запусков из загруженного наблюдения: постраничное чтение журнала", n=loaded) if loaded
            else tr("Журнал проекта читается только как события запусков: постраничное чтение журнала"))
        self.open_folder.setEnabled(bool(host.project_directory))
