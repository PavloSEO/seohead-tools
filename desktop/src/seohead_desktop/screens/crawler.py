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
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
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
COMPACT_WIDTH = 1000  # below this the run/clear/save actions move to a second toolbar row
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
DETAIL_TABS = (
    "Сведения",
    "Входящие",
    "Исходящие",
    "Изображения",
    "Ресурсы",
    "Сниппет",
    "Исходник",
    "HTTP-заголовки",
)
# two grid columns of captions; values stay empty until a row is selected (no sample data)
DETAIL_FIELDS = (("Адрес", "Код ответа", "Индексация"), ("Title", "Глубина", "Время ответа"))
OVERVIEW = (
    ("folder", "Всё", 0),
    ("folder_open", "Внутренние", 1),
    ("description", "HTML", 2),
    ("javascript", "JavaScript", 2),
    ("code", "CSS", 2),
    ("photo_size_select_large", "Изображения", 2),
    ("folder_open", "Коды ответа", 0),
    ("check_circle", "2xx успешно", 1),
    ("subdirectory_arrow_right", "3xx редирект", 1),
    ("warning", "4xx ошибка клиента", 1),
    ("error", "5xx ошибка сервера", 1),
    ("folder_open", "Title", 0),
    ("remove", "Отсутствует", 1),
    ("content_copy", "Дубли", 1),
    ("fit_width", "Длиннее 60 знаков", 1),
    ("folder", "Canonical", 0),
    ("folder", "Директивы", 0),
    ("folder", "Hreflang", 0),
)
# column: (header, width in px); the header row scrolls horizontally instead of squeezing the labels
COLUMNS = (
    ("Адрес", 160),
    ("Тип", 120),
    ("Код", 56),
    ("Индексация", 120),
    ("Title", 160),
    ("Длина", 56),
    ("Глубина", 64),
)


class CrawlerScreen(QWidget):
    """Board «Краулер»: toolbar, check tabs, results table, detail tabs, overview tree and status line, all waiting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("spaciousPage", False)
        self._compact = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._toolbar())
        root.addWidget(self._check_tabs())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._split(), 1)
        body.addWidget(self._overview())
        root.addLayout(body, 1)
        root.addWidget(self._status())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_actions(self.width() < COMPACT_WIDTH)

    def _toolbar(self):
        bar = QFrame()
        bar.setProperty("role", "toolbar")
        column = QVBoxLayout(bar)
        column.setContentsMargins(12, 8, 12, 8)
        column.setSpacing(8)
        self._row = QHBoxLayout()
        self._row.setSpacing(8)
        chip = QLabel(tr("Краулер · без проекта"))
        chip.setProperty("badge", "info")
        self._row.addWidget(chip)
        address = QLineEdit()
        address.setPlaceholderText(tr("Адрес сайта"))
        address.setEnabled(False)
        address.setMinimumWidth(200)
        self._row.addWidget(address, 1)
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
            button.setMinimumWidth(button.sizeHint().width())  # labels are never elided to «…»
            button.setCheckable(True)
            button.setEnabled(False)
            group.addButton(button)
            seg_row.addWidget(button)
        self._row.addWidget(segmented)
        self._actions = self._action_buttons()
        self._row.addWidget(self._actions)
        column.addLayout(self._row)
        # second row for narrow windows: the actions are re-parented here by _reflow_actions
        self._second = QWidget()
        self._second_layout = QHBoxLayout(self._second)
        self._second_layout.setContentsMargins(0, 0, 0, 0)
        self._second_layout.setSpacing(8)
        self._second_layout.addStretch(1)
        self._second.setVisible(False)
        column.addWidget(self._second)
        return bar

    def _action_buttons(self):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        start = QPushButton(tr("Старт"))
        start.setProperty("role", "primary")
        start.setEnabled(False)
        start.setIcon(material_icon("play_arrow"))
        start.setToolTip(tr(UNAVAILABLE))
        row.addWidget(start)
        tune = QToolButton()
        tune.setIcon(material_icon("tune"))
        tune.setToolTip(tr("Настройки скана"))
        tune.setEnabled(False)
        row.addWidget(tune)
        clear = QPushButton(tr("Очистить"))
        clear.setEnabled(False)
        row.addWidget(clear)
        save = QPushButton(tr("Сохранить как проект"))
        save.setToolTip(tr("Сохранять пока нечего: краул без проекта не запускается"))
        save.setEnabled(False)
        row.addWidget(save)
        return box

    def _reflow_actions(self, compact):
        if compact == self._compact:
            return
        self._compact = compact
        source, target = (self._row, self._second_layout) if compact else (self._second_layout, self._row)
        source.removeWidget(self._actions)
        target.addWidget(self._actions)
        self._second.setVisible(compact)

    def _check_tabs(self):
        tabs = QTabBar()
        tabs.setProperty("tabs", "underline")
        tabs.setEnabled(False)
        tabs.setExpanding(False)
        tabs.setUsesScrollButtons(True)
        for text in CHECK_TABS:
            tabs.addTab(tr(text))
        return tabs

    def _split(self):
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._main())
        splitter.addWidget(self._details())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _main(self):
        widget = QWidget()
        column = QVBoxLayout(widget)
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
        search.setMinimumWidth(160)
        filters.addWidget(search, 1)
        export = QPushButton(tr("Экспорт"))
        export.setEnabled(False)
        export.setSizePolicy(QSizePolicy.Fixed, export.sizePolicy().verticalPolicy())
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
        return widget

    def _table_header(self):
        """Column titles in a horizontal scroll area: narrow windows scroll, the labels never collide."""
        inner = QFrame()
        inner.setProperty("role", "table_header")
        row = QHBoxLayout(inner)
        row.setContentsMargins(16, 6, 16, 6)
        row.setSpacing(8)
        for text, width in COLUMNS:
            label = QLabel(tr(text))
            label.setProperty("text_style", "overline")
            label.setMinimumWidth(width)
            if text == COLUMNS[0][0]:
                row.addWidget(label, 1)
            else:
                label.setFixedWidth(width)
                row.addWidget(label)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        inner.setMinimumWidth(sum(width for _, width in COLUMNS) + 32 + 8 * (len(COLUMNS) - 1))
        scroll.setFixedHeight(inner.sizeHint().height() + scroll.horizontalScrollBar().sizeHint().height())
        return scroll

    def _details(self):
        widget = QWidget()
        column = QVBoxLayout(widget)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        note = QLabel(tr("Сведения появятся, когда будет выбран адрес краула"))
        note.setProperty("text_style", "meta")
        note.setContentsMargins(16, 10, 16, 10)
        column.addWidget(note)
        tabs = QTabWidget()
        tabs.tabBar().setProperty("tabs", "underline")
        tabs.tabBar().setUsesScrollButtons(True)
        tabs.setEnabled(False)
        for text in DETAIL_TABS:
            tabs.addTab(self._detail_page(text), tr(text))
        column.addWidget(tabs, 1)
        return widget

    def _detail_page(self, name):
        page = QWidget()
        if name != DETAIL_TABS[0]:
            return page
        grid = QGridLayout(page)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        for column_index, keys in enumerate(DETAIL_FIELDS):
            for row_index, key in enumerate(keys):
                caption = QLabel(tr(key))
                caption.setProperty("text_style", "meta")
                grid.addWidget(caption, row_index, column_index * 2)
                grid.addWidget(QLabel(), row_index, column_index * 2 + 1)
        grid.setRowStretch(len(DETAIL_FIELDS[0]), 1)
        return page

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
        row.setContentsMargins(12, 4, 16, 4)
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
