"""Граф ссылок (canvas GraphFilters.dc.html): the link graph of a scan with its filters.

The core has no whole-graph read yet (paged graph by section or around a URL is #975), so the canvas shows the
honest waiting state and the filter panel stays inert with the same controls the sheet shows. No node, edge or
count is drawn from sample data; the defaults of the checkboxes are only the sheet's starting choices.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..ui.controls import Segmented, polish
from ..ui.icons import material_icon
from ..ui.kit import PageHeader, StatePanel, no_project_panel
from .base import Screen
from .issues import selected_scan

GRAPH_ISSUE = 975
INCLUDE = (("head", "Шапка и меню", False), ("content", "Контент", True), ("foot", "Подвал", False),
           ("side", "Боковая колонка", False), ("crumbs", "Хлебные крошки", False))
SHOW = (("nofollow", "nofollow-ссылки", False), ("orphans", "Сироты", True), ("bad", "Ссылки на 3xx и 4xx", True),
        ("indexonly", "Только индексируемые", False))
COLOR_BY = (("section", "Раздел"), ("code", "Код"), ("index", "Индекс"), ("depth", "Глубина"))
SIZE_BY = (("inlinks", "Входящие"), ("weight", "Вес"), ("words", "Слова"))


def _heading(text):
    label = QLabel(tr(text))
    label.setProperty("text_style", "control")
    return label


def _checks(rows):
    boxes = []
    for _key, label, checked in rows:
        box = QCheckBox(tr(label))
        box.setChecked(checked)
        polish(box)  # a checked box created before the first polish paints unchecked
        boxes.append(box)
    return boxes


class GraphScreen(Screen):
    """The graph canvas and its filter panel; the canvas is the only place that can show real data later."""

    slot = "graph"
    watches = ("project",)

    def __init__(self, host):
        super().__init__(host)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        header = PageHeader("Граф ссылок")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Найти страницу…"))
        self.search.setAccessibleName(tr("Найти страницу"))
        self.search.setFixedWidth(260)
        self.search.setEnabled(False)
        header.add_action(self.search)
        self.route = header.add_action(QPushButton(material_icon("route"), tr("Путь от главной")))
        self.fit = header.add_action(QPushButton(material_icon("fit_screen"), tr("Вписать")))
        for button in (self.route, self.fit):
            button.setProperty("size", "sm")
            button.setEnabled(False)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.canvas = QFrame()
        self.canvas.setProperty("card", "panel")
        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.states = QStackedWidget()
        self.no_project = no_project_panel(host, "Откройте проект, чтобы увидеть граф ссылок его скана.")
        self.waiting = StatePanel("waiting", "Граф ссылок ждёт данных ядра",
                                  "Ядро пока отдаёт граф только вокруг одной страницы или раздела; общий граф скана появится вместе с фильтрами.",
                                  issue=GRAPH_ISSUE)
        self.no_scan = StatePanel("empty", "Графа пока нет",
                                  "Граф строится из таблицы ссылок сохранённого скана. В проекте ещё нет скана: запустите краул или импортируйте выгрузку Screaming Frog.",
                                  action=("Новый скан", host.scan_preview))
        self.states.addWidget(self.no_project)
        self.states.addWidget(self.no_scan)
        self.states.addWidget(self.waiting)
        canvas_layout.addWidget(self.states)
        body.addWidget(self.canvas, 1)
        body.addWidget(self._filters())
        root.addLayout(body, 1)
        self.refresh()

    def _filters(self):
        aside = QFrame()
        aside.setProperty("card", "panel")
        aside.setFixedWidth(336)
        layout = QVBoxLayout(aside)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel(tr("Фильтры графа"))
        title.setProperty("text_style", "section")
        top.addWidget(title)
        top.addStretch(1)
        reset = QPushButton(tr("Сбросить"))
        reset.setProperty("role", "text")
        top.addWidget(reset)
        layout.addLayout(top)

        layout.addWidget(_heading("Учитывать ссылки из"))
        for box in _checks(INCLUDE):
            layout.addWidget(box)
        hint = QLabel(tr("Сквозные ссылки шапки и подвала есть на каждой странице и прячут реальную перелинковку"))
        hint.setProperty("text_style", "meta")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(_heading("Цвет по"))
        layout.addWidget(Segmented([(key, tr(label)) for key, label in COLOR_BY], value="section", accessible_name=tr("Цвет по")))
        layout.addWidget(_heading("Размер по"))
        layout.addWidget(Segmented([(key, tr(label)) for key, label in SIZE_BY], value="inlinks", accessible_name=tr("Размер по")))

        layout.addWidget(_heading("Показывать"))
        for box in _checks(SHOW):
            layout.addWidget(box)

        layout.addWidget(_heading("Глубина до 4"))
        depth = QSlider(Qt.Horizontal)
        depth.setRange(1, 4)
        depth.setValue(4)
        layout.addWidget(depth)
        layout.addStretch(1)

        # Inert until the core gives the graph: the sheet's values are a layout, not data. Pointer and focus are
        # ignored instead of disabling the widgets, because Qt draws a disabled checked box without its check.
        for child in aside.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
            child.setFocusPolicy(Qt.NoFocus)
        aside.setToolTip(tr("Фильтры включатся, когда ядро отдаст граф ссылок"))
        return aside

    def showEvent(self, event):
        super().showEvent(event)
        for box in self.findChildren(QCheckBox):  # checked boxes first painted before the window polished them
            polish(box)

    def refresh(self):
        if not self.project_open:
            self.states.setCurrentWidget(self.no_project)
        elif selected_scan(self.host) is None:
            self.states.setCurrentWidget(self.no_scan)
        else:
            self.states.setCurrentWidget(self.waiting)
