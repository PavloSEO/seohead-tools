"""Detail panels of the URL screen (canvas Main.dc.html bottom panel and aside, MainB.dc.html right accordion).

Everything shown here comes from ``scan-url-detail`` of the selected URL and from the saved scan row (counters the core
already holds). Tabs whose data the core does not give yet keep their place and say which core issue they wait for.
"""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theming
from ..i18n import joined, tr, trf
from ..ui.controls import polish
from ..ui.icons import MaterialIconLabel, material_icon
from ..ui.kit import StatePanel, waiting_badge
from .scan_common import Pairs, StatusBadge, number, parse_time
from .url_query import http_badge, index_badge, index_reason, seconds_to_ms
from .url_widgets import StackBar, section_label

# tab id -> (label, title of the waiting panel, text, core issue); ``None`` issue = built from the loaded page
WAITING_TABS = {
    "links": ("Ссылки", "Входящие и исходящие ссылки URL", "Ядро пока не отдаёт ссылки одной страницы: источник, анкор, rel, HTTP цели, внутренняя или внешняя; счётчик входящих и позиция ссылки на странице", 924),
    "graph": ("Граф", "Граф ссылок вокруг URL", "Постраничное чтение графа вокруг страницы ждёт ядра", 975),
    "html": ("HTML", "Исходник страницы", "Читатель сохранённого тела страницы (статического и после рендеринга) ждёт ядра", 936),
    "res": ("Ресурсы", "Ресурсы страницы", "Изображения, CSS и JS страницы со статусом и весом ждут ядра", 935),
    "redir": ("Редиректы", "Цепочка редиректов", "Цепочка и петли по одному URL одним ответом ждут ядра", 930),
    "hist": ("История", "История URL по сканам", "Статус и поля URL по сканам («был 404») ждут ядра", 972),
    "schema": ("Schema", "Структурированные данные", "Блоки JSON-LD, microdata и ошибки разметки по URL ждут ядра", 948),
    "checks": ("Проверки", "Проверки выбранного URL", "Находки по одному URL ядро пока не отдаёт: в скане они есть только списком проверок", 980),
}
TAB_ORDER = ("info", "links", "graph", "hdr", "html", "res", "redir", "hist", "schema", "snip", "checks")
TAB_LABELS = {"info": "Обзор", "hdr": "Заголовки", "snip": "Сниппет", **{k: v[0] for k, v in WAITING_TABS.items()}}
SOURCE_KINDS = {"native": "Встроенный краулер", "sitemap": "Sitemap", "screaming_frog": "Импорт Screaming Frog"}


def clean(value):
    """Text of a stored field; empty strings (an absent tag) and non-text stay None -> «Нет данных»."""
    return value if isinstance(value, str) and value.strip() else None


def source_text(source, page):
    kind = SOURCE_KINDS.get((source or {}).get("source_kind"))
    if kind is None:
        return None
    mode = (page or {}).get("representation")
    suffix = {"static": "без рендеринга", "rendered": "с рендерингом"}.get(mode)
    return f"{tr(kind)} · {tr(suffix)}" if suffix else tr(kind)


def short_id(value):
    return f"{value[:4]}…{value[-4:]}" if isinstance(value, str) and len(value) > 10 else value


def stamp_text(value):
    stamp = parse_time(value)
    return stamp.astimezone().strftime("%d.%m.%Y %H:%M:%S") if stamp else None


def first_response(detail):
    items = ((detail or {}).get("responses") or {}).get("items") or []
    return items[0] if items and isinstance(items[0], dict) else {}


def headers_text(detail):
    """Status line and header lines of the first saved response; None when the core saved none."""
    response = first_response(detail)
    headers = response.get("response_headers")
    status = response.get("status_code")
    if not isinstance(headers, list) or type(status) is not int:
        return None
    lines = [f"HTTP {status}"]
    for pair in headers:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            lines.append(f"{pair[0]}: {pair[1]}")
    return "\n".join(lines)


def page_metrics(row, page):
    """(words, depth, ms) of the selected URL: the detail answer first, the table row while it loads."""
    page = page or {}
    words = page.get("word_count", row.get("word_count"))
    depth = page.get("crawl_depth", row.get("crawl_depth"))
    ms = seconds_to_ms(page.get("response_time", row.get("response_time")))
    return (words if type(words) is int else None, depth if type(depth) is int else None, ms)



def waiting_page(tab):
    _label, title, text, issue = WAITING_TABS[tab]
    return StatePanel("waiting", title, text, issue=issue)


def key_label(text):
    label = QLabel(tr(text))
    label.setProperty("text_style", "meta")
    return label


def icon_button(icon, tip, enabled=True):
    button = QToolButton()
    button.setProperty("role", "icon")
    button.setIcon(material_icon(icon))
    button.setToolTip(tr(tip))
    button.setAccessibleName(tr(tip))
    button.setEnabled(enabled)
    return button


class UrlHead(QWidget):
    """HTTP badge, URL (mono, selectable), copy / open in browser, optional note to the agent."""

    note_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.url = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.http = StatusBadge()
        self.http.hide()
        layout.addWidget(self.http)
        self.label = QLabel()
        self.label.setProperty("text_style", "mono")
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setSizePolicy(self.label.sizePolicy().horizontalPolicy(), self.label.sizePolicy().verticalPolicy())
        self.label.setMinimumWidth(40)
        layout.addWidget(self.label, 1)
        self.copy = icon_button("content_copy", "Копировать URL", False)
        self.copy.clicked.connect(lambda _c=False: QApplication.clipboard().setText(self.url or ""))
        self.browser = icon_button("open_in_new", "Открыть в системном браузере", False)
        self.browser.clicked.connect(lambda _c=False: self.url and QDesktopServices.openUrl(QUrl(self.url)))
        self.note = QPushButton(tr("Заметка агенту"))
        self.note.setProperty("size", "sm")
        self.note.setIcon(material_icon("edit_note"))
        self.note.clicked.connect(self.note_requested.emit)
        for widget in (self.copy, self.browser, self.note):
            layout.addWidget(widget)

    def set_row(self, row):
        self.url = row.get("url") if row else None
        self.label.setText(self.url or "")
        self.label.setToolTip(self.url or "")
        self.copy.setEnabled(bool(self.url))
        self.browser.setEnabled(bool(self.url))
        if row:
            kind, text = http_badge(row.get("status_code"))
            self.http.set_state(kind, text, {"ok": "check_circle", "info": "turn_right", "warn": "warning", "err": "error"}.get(kind, "help"))
            self.http.show()
        else:
            self.http.hide()


class OverviewPage(QWidget):
    """«Обзор»: indexation / title / canonical / redirect / words / H1 on the left, origin of the saved data on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(20, 14, 20, 14)
        grid.setHorizontalSpacing(32)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)
        self.head = UrlHead()
        grid.addWidget(self.head, 0, 0, 1, 2)
        self.left = Pairs(("Индексация", "Title", "Canonical", "Цель редиректа", "Слов в контенте", "H1"), mono=("Canonical", "Цель редиректа"))
        self.right_box = QWidget()
        right = QVBoxLayout(self.right_box)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(section_label("Происхождение"))
        self.right = Pairs(("Источник", "Скан", "Снято", "Ответ"), mono=("Скан",))
        right.addWidget(self.right)
        self.related = QHBoxLayout()
        self.related.setSpacing(6)
        key = key_label("Связано")
        key.setMinimumWidth(140)
        self.related.addWidget(key)
        self.related.addWidget(self._waiting("Проблемы URL", 980))
        self.related.addWidget(self._waiting("Задача", 922))
        self.related.addStretch(1)
        right.addLayout(self.related)
        right.addStretch(1)
        self.grid = grid
        grid.addWidget(self.left, 1, 0, Qt.AlignTop)
        grid.addWidget(self.right_box, 1, 1, Qt.AlignTop)
        grid.setRowStretch(3, 1)
        self.note = QLabel()
        self.note.setProperty("text_style", "meta")
        self.note.setWordWrap(True)
        grid.addWidget(self.note, 3, 0, 1, 2, Qt.AlignTop)
        self._narrow = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = event.size().width() < 900
        if narrow != self._narrow:
            self._narrow = narrow
            self.grid.removeWidget(self.left)
            self.grid.removeWidget(self.right_box)
            if narrow:
                self.grid.addWidget(self.left, 1, 0, 1, 2, Qt.AlignTop)
                self.grid.addWidget(self.right_box, 2, 0, 1, 2, Qt.AlignTop)
            else:
                self.grid.addWidget(self.left, 1, 0, Qt.AlignTop)
                self.grid.addWidget(self.right_box, 1, 1, Qt.AlignTop)

    @staticmethod
    def _waiting(text, issue):
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(tr(text))
        label.setProperty("text_style", "meta")
        layout.addWidget(label)
        layout.addWidget(waiting_badge(issue))
        return holder

    def fill(self, row, page, detail, state, text=""):
        self.head.set_row(row)
        page = page or {}
        badge = index_badge(page.get("status_code", row.get("status_code")), row.get("indexable"))[1] if row else None
        reason = index_reason(page) if page else None
        reasons = (reason,) if reason and (badge or "").casefold() != reason.casefold() else ()
        self.left.set("Индексация", joined(" · ", [badge, *reasons]) if badge else None)
        self.left.set("Title", clean(page.get("title")))
        self.left.set("Canonical", clean(page.get("canonical")))
        self.left.set("Цель редиректа", clean(page.get("redirect_url")) or clean(page.get("final_url")))
        words, _depth, ms = page_metrics(row or {}, page)
        self.left.set("Слов в контенте", number(words))
        self.left.set("H1", clean(page.get("h1")))
        source = (detail or {}).get("source") or {}
        response = first_response(detail)
        self.right.set("Источник", source_text(source, page))
        self.right.set("Скан", short_id(source.get("scan_uuid")))
        self.right.set("Снято", stamp_text(response.get("received_at")))
        self.right.set("Ответ", trf("{ms} мс", ms=number(ms)) if ms is not None else None)
        self.note.setText(text)
        self.note.setVisible(bool(text))


class HeadersPage(QWidget):
    """«Заголовки»: the saved response as text; values the core masked arrive masked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setProperty("mono", True)
        self.text.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self.text, 1)
        side = QFrame()
        side.setProperty("aside", True)
        side.setFixedWidth(280)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 12, 16, 12)
        side_layout.addWidget(section_label("Сохранённый ответ"))
        self.caption = QLabel()
        self.caption.setProperty("text_style", "meta")
        self.caption.setWordWrap(True)
        side_layout.addWidget(self.caption)
        side_layout.addStretch(1)
        layout.addWidget(side)

    def fill(self, detail):
        text = headers_text(detail)
        self.text.setPlainText(text or tr("Нет данных"))
        self.caption.setText(tr("Заголовки из сохранённого ответа. Просмотр не отправляет новый запрос.") if text
                             else tr("Ядро не сохранило заголовки этого ответа."))


class SnippetPage(QWidget):
    """«Сниппет»: title and description with their lengths, as the page stores them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        self.pairs = Pairs(("Title", "Длина title", "Description", "Длина description"))
        layout.addWidget(self.pairs)
        layout.addStretch(1)

    def fill(self, page):
        page = page or {}
        title, description = clean(page.get("title")), clean(page.get("meta_description"))
        self.pairs.set("Title", title)
        self.pairs.set("Длина title", trf("{n} зн.", n=len(title)) if title else None)
        self.pairs.set("Description", description)
        self.pairs.set("Длина description", trf("{n} зн.", n=len(description)) if description else None)


class UrlDetail(QFrame):
    """Bottom panel: tab strip + pages. ``show_*`` calls come from the screen; the panel never reads the core."""

    back_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    note_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("aside", False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 0, 8, 0)
        self.tabs = QTabBar()
        self.tabs.setProperty("tabs", "underline")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        for tab in TAB_ORDER:
            self.tabs.addTab(tr(TAB_LABELS[tab]))
        self.tabs.currentChanged.connect(self._tab_changed)
        bar.addWidget(self.tabs, 1)
        self.back = icon_button("arrow_back", "Предыдущий URL в истории", False)
        self.back.clicked.connect(self.back_requested.emit)
        self.forward = icon_button("arrow_forward", "Следующий URL в истории", False)
        self.hide_button = icon_button("bottom_panel_close", "Скрыть детали")
        self.hide_button.clicked.connect(self.hide_requested.emit)
        for widget in (self.back, self.forward, self.hide_button):
            bar.addWidget(widget)
        layout.addLayout(bar)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.state = QStackedWidget()
        self.overview = OverviewPage()
        self.overview.head.note_requested.connect(self.note_requested.emit)
        self.headers = HeadersPage()
        self.snippet = SnippetPage()
        self.index = {}
        self.summary = RunSummary()
        self.summary.setProperty("aside", False)
        self.summary.hide_button.hide()
        overview_scroll = QScrollArea()
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setFrameShape(QFrame.NoFrame)
        overview_scroll.setWidget(self.overview)
        for tab in TAB_ORDER:
            widget = {"info": overview_scroll, "hdr": self.headers, "snip": self.snippet}.get(tab) or waiting_page(tab)
            self.index[tab] = self.pages.addWidget(widget)
        self.tab = "info"
        self.placeholder = StatePanel("empty", "Выберите URL в таблице", "Детали сохранённого ответа появятся здесь")
        self.index["none"] = self.pages.addWidget(self.placeholder)
        self.index["summary"] = self.pages.addWidget(self.summary)
        self.pages.setCurrentIndex(self.index["none"])
        self.has_row = False

    def _tab_changed(self, index):
        self.tab = TAB_ORDER[index] if index < len(TAB_ORDER) else "summary"
        self._show()

    def _show(self):
        self.pages.setCurrentIndex(self.index[self.tab] if self.has_row or self.tab == "summary" else self.index["none"])

    def set_summary_tab(self, visible):
        """The run summary lives in this panel as a tab when the window is too narrow for the right-hand aside."""
        present = self.tabs.count() > len(TAB_ORDER)
        if visible and not present:
            self.tabs.addTab(tr("Сводка"))
        elif not visible and present:
            if self.tab == "summary":
                self.select_tab("info")
            self.tabs.removeTab(len(TAB_ORDER))

    def select_tab(self, tab):
        self.tabs.setCurrentIndex(len(TAB_ORDER) if tab == "summary" else TAB_ORDER.index(tab))

    def set_agent_visible(self, visible):
        self.overview.head.note.setVisible(visible)

    def render(self, row, page, detail, state, text=""):
        """state: none | loading | ready | error. ``row`` is the table row, ``page`` the detail page of the core."""
        self.has_row = state != "none"
        if self.has_row:
            self.overview.fill(row, page, detail, state, text)
            self.headers.fill(detail)
            self.snippet.fill(page)
        self._show()

    def retranslate(self):
        for index, tab in enumerate(TAB_ORDER):
            self.tabs.setTabText(index, tr(TAB_LABELS[tab]))
        if self.tabs.count() > len(TAB_ORDER):
            self.tabs.setTabText(len(TAB_ORDER), tr("Сводка"))


class Accordion(QFrame):
    """One section of the right-hand details: header row (icon, title, badge, chevron) and a text body."""

    def __init__(self, icon, title, parent=None):
        super().__init__(parent)
        self.setProperty("card", "section")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.head = QPushButton()
        self.head.setProperty("accordion", True)
        self.head.setCheckable(True)
        self.head.setFlat(True)
        self.head.setText(tr(title))
        self.head.setIcon(material_icon(icon))
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.head.setMinimumHeight(40)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.addWidget(self.head, 1)
        self.badge = QLabel()
        self.badge.setProperty("badge", "mut")
        head_row.addWidget(self.badge)
        head_row.addSpacing(8)
        layout.addLayout(head_row)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setContentsMargins(16, 4, 16, 12)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setVisible(False)
        layout.addWidget(self.body)
        self.head.toggled.connect(self.body.setVisible)

    def set_content(self, badge_kind, badge, text):
        self.badge.setText(badge)
        self.badge.setVisible(bool(badge))
        self.badge.setProperty("badge", badge_kind)
        polish(self.badge)
        self.body.setText(text)


class UrlSideDetail(QFrame):
    """Layout B (MainB.dc.html): details to the right of the table, header + metrics + chips + accordion."""

    hide_requested = pyqtSignal()
    note_requested = pyqtSignal()
    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("aside", True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = QWidget()
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(16, 12, 12, 10)
        head_layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.http = StatusBadge()
        self.index = StatusBadge()
        self.http.hide()
        self.index.hide()
        top.addWidget(self.http)
        top.addWidget(self.index)
        top.addStretch(1)
        self.back = icon_button("arrow_back", "Назад", False)
        self.back.clicked.connect(self.back_requested.emit)
        self.browser = icon_button("open_in_new", "Открыть в системном браузере", False)
        self.browser.clicked.connect(lambda _c=False: self.url and QDesktopServices.openUrl(QUrl(self.url)))
        self.close_button = icon_button("right_panel_close", "Скрыть детали")
        self.close_button.clicked.connect(self.hide_requested.emit)
        for widget in (self.back, self.browser, self.close_button):
            top.addWidget(widget)
        head_layout.addLayout(top)
        self.url = None
        self.address = QLabel()
        self.address.setProperty("text_style", "mono")
        self.address.setWordWrap(True)
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head_layout.addWidget(self.address)
        self.title = QLabel()
        self.title.setProperty("text_style", "section")
        self.title.setWordWrap(True)
        head_layout.addWidget(self.title)
        root.addWidget(head)
        self.metrics = {}
        strip = QFrame()
        strip.setProperty("table_head", True)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(0)
        for key in ("Слов", "Входящих", "Глубина", "Ответ"):
            cell = QVBoxLayout()
            cell.setContentsMargins(16, 8, 8, 8)
            cell.setSpacing(0)
            caption = key_label(key)
            value = QLabel()
            value.setProperty("text_style", "section")
            cell.addWidget(caption)
            cell.addWidget(value)
            strip_layout.addLayout(cell, 1)
            self.metrics[key] = value
        root.addWidget(strip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.sections = {}
        for key, icon, title in (("meta", "sell", "Метаданные"), ("in", "link", "Входящие ссылки"), ("out", "call_made", "Исходящие ссылки"),
                                 ("hdr", "http", "HTTP-заголовки"), ("html", "code", "HTML"), ("checks", "rule", "Проверки")):
            section = Accordion(icon, title)
            self.sections[key] = section
            body_layout.addWidget(section)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.placeholder = StatePanel("empty", "Выберите URL в таблице", "Детали сохранённого ответа появятся здесь")
        root.addWidget(self.placeholder)

    def render(self, row, page, detail, state, text=""):
        has = state != "none" and bool(row)
        self.placeholder.setVisible(not has)
        for widget in (self.http, self.index):
            widget.setVisible(has)
        if not has:
            self.url = None
            self.address.setText("")
            self.title.setText("")
            self.browser.setEnabled(False)
            for label in self.metrics.values():
                label.setText("")
            return
        page = page or {}
        self.url = row.get("url")
        self.browser.setEnabled(bool(self.url))
        self.address.setText(self.url or "")
        self.title.setText(clean(page.get("title")) or clean(row.get("title")) or tr("Нет данных"))
        kind, label = http_badge(row.get("status_code"))
        self.http.set_state(kind, label, {"ok": "check_circle", "info": "turn_right", "warn": "warning", "err": "error"}.get(kind, "help"))
        ikind, itext = index_badge(row.get("status_code"), row.get("indexable"))
        self.index.set_state(ikind, itext, {"ok": "check_circle", "info": "turn_right"}.get(ikind, "block"))
        words, depth, ms = page_metrics(row, page)
        self.metrics["Слов"].setText(number(words) or tr("Нет данных"))
        self.metrics["Входящих"].setText(tr("Недоступно"))
        self.metrics["Глубина"].setText(number(depth) or tr("Нет данных"))
        self.metrics["Ответ"].setText(trf("{ms} мс", ms=number(ms)) if ms is not None else tr("Нет данных"))
        title, description = clean(page.get("title")), clean(page.get("meta_description"))
        meta = joined("\n", [trf("Title: {t} ({n} зн.)", t=title, n=len(title)) if title else tr("Title: нет данных"),
                             trf("Description: {t} ({n} зн.)", t=description, n=len(description)) if description else tr("Description: нет данных"),
                             trf("Canonical: {t}", t=clean(page.get("canonical")) or tr("нет данных"))])
        self.sections["meta"].set_content("mut", "", meta)
        self.sections["in"].set_content("mut", tr("Недоступно в этой версии ядра"), tr("Счётчик и список входящих ссылок ядро пока не отдаёт. Это не «0»."))
        outlinks, external = page.get("outlinks"), page.get("external_outlinks")
        out_text = (trf("Исходящих ссылок: {n}, из них внешних: {e}. Список ссылок с rel и HTTP цели недоступен в этой версии ядра.", n=number(outlinks), e=number(external))
                    if type(outlinks) is int and type(external) is int else tr("Исходящие ссылки в ответе ядра не пришли."))
        self.sections["out"].set_content("mut", number(outlinks) or tr("Нет данных"), out_text)
        headers = headers_text(detail)
        self.sections["hdr"].set_content("mut", number(len(headers.splitlines()) - 1) if headers else tr("Нет данных"), headers or tr("Ядро не сохранило заголовки этого ответа."))
        self.sections["html"].set_content("mut", tr("Недоступно в этой версии ядра"), tr("Читатель сохранённого исходника ждёт ядра."))
        self.sections["checks"].set_content("mut", tr("Недоступно в этой версии ядра"), tr("Находки по одному URL ядро пока не отдаёт."))


class RunSummary(QFrame):
    """Right aside (296): «Обзор» / «Проблемы» / «Время» of the selected saved scan."""

    class_requested = pyqtSignal(str)   # a status class legend row was clicked: "2xx" … "5xx"
    issues_requested = pyqtSignal()
    hide_requested = pyqtSignal()

    CLASSES = (("2xx", "2xx · успешно", "s2xx"), ("3xx", "3xx · редирект", "s3xx"), ("4xx", "4xx · ошибка клиента", "s4xx"), ("5xx", "5xx · ошибка сервера", "s5xx"))
    BUCKETS = (("lt200", "< 200 мс"), ("200_500", "200–500 мс"), ("500_1000", "0,5–1 с"), ("1000_3000", "1–3 с"), ("gt3000", "> 3 с"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("aside", True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = QFrame()
        head.setProperty("aside_head", True)
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(4, 0, 4, 0)
        self.tabs = QTabBar()
        self.tabs.setProperty("tabs", "underline")
        self.tabs.setDrawBase(False)
        for name in ("Обзор", "Проблемы", "Время"):
            self.tabs.addTab(tr(name))
        head_layout.addWidget(self.tabs, 1)
        self.hide_button = icon_button("right_panel_close", "Скрыть сводку")
        self.hide_button.clicked.connect(self.hide_requested.emit)
        head_layout.addWidget(self.hide_button)
        root.addWidget(head)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        self._overview()
        self._issues()
        self._time()
        self.scan = None
        self.counts = {}

    @staticmethod
    def _scrolled(layout):
        body = QWidget()
        body.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)
        return scroll

    def _overview(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(16)
        self.partial = QFrame()
        self.partial.setProperty("note", "warn")
        partial_layout = QHBoxLayout(self.partial)
        partial_layout.setContentsMargins(10, 8, 10, 8)
        partial_layout.addWidget(MaterialIconLabel("warning", 18, color="role:warning"), 0, Qt.AlignTop)
        self.partial_text = QLabel()
        self.partial_text.setWordWrap(True)
        partial_layout.addWidget(self.partial_text, 1)
        layout.addWidget(self.partial)
        layout.addWidget(section_label("Ответы"))
        self.responses = QLabel()
        self.responses.setProperty("text_style", "meta")
        layout.addWidget(self.responses)
        self.bar = StackBar()
        layout.addWidget(self.bar)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(0, 1)
        self.class_buttons, self.class_counts = {}, {}
        for row, (value, label, role) in enumerate(self.CLASSES):
            button = QToolButton()
            button.setProperty("pill", "group")
            button.setText(tr(label))
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setToolTip(tr("Показать эти URL в таблице"))
            button.clicked.connect(lambda _c=False, v=value: self.class_requested.emit(v))
            count = QLabel()
            count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(button, row, 0, Qt.AlignLeft)
            grid.addWidget(count, row, 1)
            self.class_buttons[value], self.class_counts[value] = button, count
        layout.addLayout(grid)
        self.other = QLabel()
        self.other.setProperty("text_style", "meta")
        self.other.setWordWrap(True)
        layout.addWidget(self.other)
        layout.addWidget(section_label("Индексация"))
        self.index_pairs = Pairs(("Индексируемые", "Не индексируемые", "Без canonical"))
        layout.addWidget(self.index_pairs)
        layout.addWidget(section_label("Охват запуска"))
        self.coverage = QLabel()
        self.coverage.setWordWrap(True)
        layout.addWidget(self.coverage)
        self.coverage_bar = StackBar(8)
        layout.addWidget(self.coverage_bar)
        self.coverage_note = QLabel()
        self.coverage_note.setProperty("text_style", "meta")
        self.coverage_note.setWordWrap(True)
        layout.addWidget(self.coverage_note)
        layout.addStretch(1)
        self.pages.addWidget(self._scrolled(layout))

    def _issues(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(section_label("Находки по важности"))
        self.severity = Pairs(("Критичные", "Важные", "Советы"))
        layout.addWidget(self.severity)
        layout.addWidget(StatePanel("waiting", "Топ проверок со счётчиками", "Счётчики по каждой проверке ядро отдаёт только вместе с полным аудитом", issue=980))
        button = QPushButton(tr("Открыть «Проблемы»"))
        button.setIcon(material_icon("rule"))
        button.clicked.connect(self.issues_requested.emit)
        layout.addWidget(button)
        layout.addStretch(1)
        self.pages.addWidget(self._scrolled(layout))

    def _time(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(section_label("Время ответа"))
        self.time_rows = {}
        for key, label in self.BUCKETS:
            line = QHBoxLayout()
            caption = QLabel(tr(label))
            caption.setProperty("text_style", "meta")
            caption.setMinimumWidth(84)
            bar = StackBar(10)
            count = QLabel()
            count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count.setMinimumWidth(48)
            line.addWidget(caption)
            line.addWidget(bar, 1)
            line.addWidget(count)
            layout.addLayout(line)
            self.time_rows[key] = (bar, count)
        self.time_note = QLabel()
        self.time_note.setProperty("text_style", "meta")
        self.time_note.setWordWrap(True)
        layout.addWidget(self.time_note)
        layout.addStretch(1)
        self.pages.addWidget(self._scrolled(layout))

    # ---- data -------------------------------------------------------------------------------------------------
    def set_scan(self, scan):
        self.scan = scan
        scan = scan or {}
        evidence = scan.get("evidence") or {}
        outcomes = evidence.get("committed_page_outcomes") or {}
        counts = {key: outcomes.get(key) if type(outcomes.get(key)) is int else None for key in ("2xx", "3xx", "4xx", "5xx", "other", "no_response")}
        classes = [counts[v] for v, _l, _r in self.CLASSES]
        measured = all(c is not None for c in classes)
        data = theming.theme()["data"]
        total = sum(c for c in classes if c is not None) + sum(counts[k] or 0 for k in ("other", "no_response"))
        self.responses.setText(trf("{n} URL", n=number(total)) if measured else tr("Нет данных"))
        self.bar.set_parts([(counts[v], data[role]) for v, _l, role in self.CLASSES] if measured else [])
        for value, _label, _role in self.CLASSES:
            self.class_counts[value].setText(number(counts[value]) or tr("Нет данных"))
            self.class_buttons[value].setEnabled(measured and counts[value] is not None)
        extra = [(counts["other"], "прочие ответы"), (counts["no_response"], "без ответа")]
        self.other.setText(joined(" · ", [f"{tr(label)}: {number(n)}" for n, label in extra if n]))
        self.other.setVisible(bool(self.other.text()))
        lifecycle, crawl_partial = scan.get("lifecycle"), scan.get("crawl_partial")
        frontier = (evidence.get("frontier") or {}).get("counts") or {}
        queued = frontier.get("queued")
        reasons = []
        if crawl_partial is True or lifecycle in ("interrupted", "running"):
            reasons.append(tr("Запуск частичный."))
            if type(queued) is int and queued > 0:
                reasons.append(trf("В очереди остались необработанные URL: {n}.", n=number(queued)))
        elif crawl_partial is False and scan.get("corpus_partial") is True:
            reasons.append(tr("Обход полный, но корпус неполный: часть сохранённых тел страниц отсутствует."))
        self.partial.setVisible(bool(reasons))
        self.partial_text.setText(" ".join(reasons))
        done, inflight = frontier.get("done"), frontier.get("inflight")
        known = all(type(v) is int and v >= 0 for v in (done, queued, inflight))
        if known and done + queued + inflight > 0:
            found = done + queued + inflight
            self.coverage.setText(trf("Обработано известных URL: {done} из {found}", done=number(done), found=number(found)))
            self.coverage_bar.set_parts([(done, theming.theme()["data"]["progress"]), (found - done, theming.roles()["disabled_bg"])])
            self.coverage_note.setText(tr("Лимит скана не равен размеру сайта"))
        else:
            self.coverage.setText(tr("Нет данных"))
            self.coverage_bar.set_parts([])
            self.coverage_note.setText(tr("Счётчики очереди ядро не вернуло"))
        findings = evidence.get("findings") or {}
        by_severity = findings.get("by_severity") if findings.get("state") == "available" else None
        for key, label in (("critical", "Критичные"), ("warning", "Важные"), ("notice", "Советы")):
            value = by_severity.get(key, 0) if isinstance(by_severity, dict) else None
            self.severity.set(label, number(value) if type(value) is int else None)
        self.set_counts(self.counts)

    def set_counts(self, counts):
        """Answers of the counter queries: ``indexable_yes``, ``indexable_no`` and the response time buckets."""
        self.counts = dict(counts)
        self.index_pairs.set("Индексируемые", number(counts.get("indexable_yes")))
        self.index_pairs.set("Не индексируемые", number(counts.get("indexable_no")))
        self.index_pairs.set("Без canonical", None)
        bucket_values = [counts.get(key) for key, _label in self.BUCKETS]
        measured = all(type(v) is int for v in bucket_values)
        total = sum(v for v in bucket_values if type(v) is int)
        palette = ("s2xx", "s3xx", "s4xx", "s5xx", "s5xx")
        data = theming.theme()["data"]
        for (key, _label), role in zip(self.BUCKETS, palette):
            bar, label = self.time_rows[key]
            value = counts.get(key)
            label.setText(number(value) or tr("Нет данных"))
            bar.set_parts([(value, data[role]), (max(0, total - value), theming.roles()["disabled_bg"])] if type(value) is int and total else [])
        self.time_note.setText(tr("Медиана и p95 ядро не считает — числа не показываем.") if measured else tr("Подсчёт по корзинам идёт или недоступен."))


def scan_file_name(scan):
    return os.path.basename((scan or {}).get("path") or "")
