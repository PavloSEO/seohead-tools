"""Screen «Краулер» (canvas Crawler): the project-less crawl board, drawn with the design-v2 layout.

The core cannot run a project-less crawl with a run log and per-host pacing yet (core gap #942), so the screen shows
no addresses, counts or progress: the start control and the table are waiting states with the neutral «Недоступно»
badge. Nothing is started from here and no sample data is shown.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..ui.icons import MaterialIconLabel, material_icon
from ..ui.kit import UNAVAILABLE, StatePanel

WAITING_ISSUE = (
    942  # core gap: project-less crawl run log and per-host rate limit (code only, never shown)
)
CHECK_TABS = (
    "Внутренние",
    "Внешние",
    "Безопасность",
    "Коды ответа",
    "URL",
    "Title",
    "Description",
    "H1",
    "H2",
    "Контент",
    "Изображения",
    "Canonical",
    "Директивы",
    "Ссылки",
    "Sitemap",
)
OVERVIEW = (
    ("folder", "Всё", 0),
    ("folder_open", "Внутренние", 1),
    ("description", "HTML", 2),
    ("javascript", "JavaScript", 2),
    ("css", "CSS", 2),
    ("image", "Изображения", 2),
    ("folder_open", "Коды ответа", 0),
    ("check_circle", "2xx успешно", 1),
    ("turn_right", "3xx редирект", 1),
    ("warning", "4xx ошибка клиента", 1),
    ("error", "5xx ошибка сервера", 1),
    ("folder_open", "Title", 0),
    ("remove", "Отсутствует", 1),
    ("content_copy", "Дубли", 1),
    ("straighten", "Длиннее 60 знаков", 1),
    ("folder", "Canonical", 0),
    ("folder", "Директивы", 0),
    ("folder", "Hreflang", 0),
)
# column: (header, stretch or fixed width); the same proportions as the board
COLUMNS = (
    ("Адрес", 1),
    ("Тип", 150),
    ("Код", 62),
    ("Индексация", 132),
    ("Title", 0.7),
    ("Длина", 64),
    ("Глубина", 72),
)


class CrawlerScreen(QWidget):
    """Board «Краулер»: toolbar, check tabs, results table, overview tree and status line, all waiting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("spaciousPage", False)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._toolbar())
        root.addWidget(self._check_tabs())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addLayout(self._main(), 1)
        body.addWidget(self._overview())
        root.addLayout(body, 1)
        root.addWidget(self._status())

    def _toolbar(self):
        bar = QFrame()
        bar.setProperty("role", "toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        chip = QLabel(tr("Краулер · без проекта"))
        chip.setProperty("badge", "info")
        row.addWidget(chip)
        address = QLineEdit()
        address.setPlaceholderText(tr("Адрес сайта"))
        address.setEnabled(False)
        row.addWidget(address, 1)
        segmented = QFrame()
        segmented.setProperty("segmented", "true")
        seg_row = QHBoxLayout(segmented)
        seg_row.setContentsMargins(2, 2, 2, 2)
        seg_row.setSpacing(2)
        group = QButtonGroup(segmented)
        group.setExclusive(True)
        for text in ("Сайт", "Список", "Sitemap"):
            button = QToolButton()
            button.setProperty("segment", "true")
            button.setText(tr(text))
            button.setCheckable(True)
            button.setEnabled(False)
            group.addButton(button)
            seg_row.addWidget(button)
        row.addWidget(segmented)
        start = QPushButton(tr("Старт"))
        start.setProperty("role", "primary")
        start.setEnabled(False)
        start.setIcon(material_icon("play_arrow"))
        start.setToolTip(tr(UNAVAILABLE))
        row.addWidget(start)
        clear = QPushButton(tr("Очистить"))
        clear.setEnabled(False)
        row.addWidget(clear)
        save = QPushButton(tr("Сохранить как проект"))
        save.setToolTip(tr("Сохранять пока нечего: краул без проекта не запускается"))
        save.setEnabled(False)
        row.addWidget(save)
        return bar

    def _check_tabs(self):
        tabs = QTabBar()
        tabs.setProperty("tabs", "underline")
        tabs.setEnabled(False)
        tabs.setExpanding(False)
        tabs.setUsesScrollButtons(False)
        for text in CHECK_TABS:
            tabs.addTab(tr(text))
        return tabs

    def _main(self):
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        filters = QHBoxLayout()
        filters.setContentsMargins(10, 8, 10, 8)
        filters.setSpacing(8)
        selector = QPushButton(tr("Фильтр: Всё"))
        selector.setEnabled(False)
        filters.addWidget(selector)
        search = QLineEdit()
        search.setPlaceholderText(tr("Поиск в адресах…"))
        search.setEnabled(False)
        search.setFixedWidth(260)
        filters.addWidget(search)
        filters.addStretch(1)
        export = QPushButton(tr("Экспорт"))
        export.setEnabled(False)
        filters.addWidget(export)
        column.addLayout(filters)
        column.addWidget(self._table_header())
        column.addWidget(
            StatePanel(
                "waiting",
                "Краул без проекта пока не запускается",
                "Ядро ещё не отдаёт обход без проекта с журналом и ограничением скорости. "
                "Адреса, коды ответа и проверки появятся после этого.",
                issue=WAITING_ISSUE,
            ),
            1,
        )
        details = QLabel(tr("Сведения появятся, когда будет выбран адрес краула"))
        details.setProperty("text_style", "meta")
        details.setContentsMargins(16, 10, 16, 10)
        column.addWidget(details)
        return column

    def _table_header(self):
        header = QFrame()
        header.setProperty("role", "table_header")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 6, 16, 6)
        row.setSpacing(8)
        for text, width in COLUMNS:
            label = QLabel(tr(text))
            label.setProperty("text_style", "overline")
            if isinstance(width, int) and width > 1:
                label.setFixedWidth(width)
                row.addWidget(label)
            else:
                row.addWidget(label, int(width * 10))
        return header

    def _overview(self):
        panel = QFrame()
        panel.setFixedWidth(300)
        panel.setProperty("role", "overview")
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        tabs = QTabBar()
        tabs.setProperty("tabs", "underline")
        tabs.setEnabled(False)
        for text in ("Обзор", "Проблемы", "Структура"):
            tabs.addTab(tr(text))
        column.addWidget(tabs)
        for icon_name, text, depth in OVERVIEW:
            row = QHBoxLayout()
            row.setContentsMargins(6 + depth * 16, 2, 10, 2)
            row.setSpacing(6)
            row.addWidget(MaterialIconLabel(icon_name, 16, color="role:text_muted"))
            label = QLabel(tr(text))
            label.setEnabled(False)
            row.addWidget(label, 1)
            count = QLabel("—")
            count.setProperty("text_style", "meta")
            count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(count)
            column.addLayout(row)
        column.addStretch(1)
        return panel

    def _status(self):
        bar = QFrame()
        bar.setProperty("role", "status")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 4, 12, 4)
        row.setSpacing(20)
        state = QLabel(tr("Краул не запущен"))
        state.setProperty("badge", "mut")
        row.addWidget(state)
        for text in ("Средняя скорость: —", "Пройдено: —", "Осталось: —"):
            label = QLabel(tr(text))
            label.setProperty("text_style", "meta")
            row.addWidget(label)
        row.addStretch(1)
        note = QLabel(tr("Без проекта · данные краула недоступны"))
        note.setProperty("text_style", "meta")
        row.addWidget(note)
        return bar
