"""Проблемы (canvas Issues.dc.html, StatesIssues.dc.html): findings of the selected saved scan.

The core gives per scan the totals by severity, the first 20 findings and the list of checks it skipped (the scan row of
``project-scans``). Per-check counters for the whole scan and the filter by check wait for #981, the paged findings for #980; the path
«found → task → fixed → confirmed» waits for the remediation ledger (#926) and tasks (#922). Nothing is counted here from the 20-row sample.
"""

from __future__ import annotations

import re

from PyQt5.QtCore import QAbstractTableModel, Qt
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, theming
from ..i18n import tr, trf
from ..ui.icons import MaterialIconLabel, material_icon
from ..ui.kit import (
    BADGE_ROLE,
    BadgeDelegate,
    StatePanel,
    no_project_panel,
    style_table,
    unavailable_tip,
    waiting_badge,
)
from .base import Screen
from .scan_common import number, parse_time
from .url_detail import StackBar, section_label

SEVERITIES = (("critical", "Критичные", "error", "error"), ("warning", "Важные", "warning", "warning"), ("notice", "Советы", "lightbulb", "text_2"))
CHECK_NAMES = {
    "SERVER_ERROR_5XX": "Ответ 5xx", "BROKEN_PAGE_4XX": "Страницы с ответом 4xx", "HTTP_URL": "Адрес по HTTP, а не HTTPS",
    "DESC_MISSING": "Без meta description", "DESC_DUPLICATE": "Дубли meta description", "TITLE_MISSING": "Без title",
    "TITLE_DUPLICATE": "Дубли title", "DEEP_CRAWL_DEPTH": "Большая глубина вложенности", "H1_MULTIPLE": "Несколько H1",
    "H1_MISSING": "Без H1", "CANONICAL_MISSING": "Без canonical", "CANONICAL_CHAIN": "Цепочка canonical",
    "CANONICAL_NON_INDEXABLE": "Canonical на неиндексируемый URL", "HREFLANG_INVALID_CODE": "Неверный код hreflang",
    "HREFLANG_INCONSISTENT_CONFIRMATION": "Hreflang без обратного подтверждения", "REDIRECT_CHAIN": "Цепочки редиректов",
    "IMG_MISSING_ALT": "Изображения без alt", "IMG_OVER_KB": "Тяжёлые изображения", "IMG_MISSING_DIMENSIONS": "Изображения без width и height",
    "MIXED_CONTENT": "Смешанный контент HTTP на HTTPS", "MISSING_HSTS": "Нет заголовка HSTS", "STRUCTURED_DATA_MISSING": "Нет микроразметки",
    "HREFLANG_ERROR": "Ошибки hreflang", "HREFLANG_BROKEN_TARGET": "Hreflang ведёт на битый URL", "HREFLANG_NOINDEX_TARGET": "Hreflang ведёт на noindex",
    "HREFLANG_MISSING_RETURN_LINK": "Hreflang без обратной ссылки", "SITEMAP_URL_4XX_5XX": "В sitemap URL с ответом 4xx/5xx",
    "SITEMAP_URL_3XX": "В sitemap URL с редиректом", "SITEMAP_URL_NON_INDEXABLE": "В sitemap неиндексируемые URL",
    "SITEMAP_URL_DUPLICATED": "Дубли URL в sitemap", "SITEMAP_NOT_IN_ROBOTS": "Sitemap не указан в robots.txt",
    "SITEMAP_TOO_MANY_URLS": "В sitemap больше 50 000 URL", "SITEMAP_TOO_LARGE": "Sitemap тяжелее 50 МБ", "SITEMAP_STALE_LASTMOD": "Устаревший lastmod в sitemap",
    "SITEMAP_DESYNC": "Sitemap расходится со сканом", "SITEMAP_ORPHAN": "URL из sitemap без внутренних ссылок", "SITEMAP_FETCH_INCOMPLETE": "Sitemap загружен не полностью",
    "URL_NOT_IN_SITEMAP": "Индексируемый URL вне sitemap", "H2_MISSING": "Без H2", "META_KEYWORDS_PRESENT": "Заполнен meta keywords",
    "PAGINATION_CANONICAL_POLICY": "Canonical на страницах пагинации", "FILTER_CANONICAL_POLICY": "Canonical на страницах фильтров",
    "SCHEMA_VALIDATION_ERROR": "Ошибки валидации микроразметки", "DUPLICATE_BY_HASH": "Точные дубли страниц", "NEAR_DUPLICATE": "Почти дубли страниц",
    "READABILITY_DIFFICULT": "Текст трудно читать", "LONG_SENTENCES": "Длинные предложения", "SPELLING_ERRORS": "Орфографические ошибки",
    "GRAMMAR_ERRORS": "Грамматические ошибки", "CANONICAL_MULTIPLE": "Несколько canonical", "CANONICAL_TARGET_ERROR": "Canonical на URL с ошибкой",
    "PAGINATION_LOOP": "Петля в пагинации", "UNLINKED_PAGINATION_SERIES": "Пагинация без связи между страницами",
    "PAGINATION_SEQUENCE_ERROR": "Нарушен порядок пагинации", "PAGINATION_MULTIPLE": "Несколько rel=next/prev", "PAGINATION_URL_NOT_IN_ANCHOR": "Страница пагинации без ссылки в тексте",
    "HTTP1_ONLY": "Только HTTP/1.1", "AMPHTML_PRESENT": "Есть AMP-версия", "DECLARED_MIME_MISMATCH": "Тип контента не совпадает с расширением",
    "TITLE_MULTIPLE": "Несколько title", "BROKEN_INTERNAL_LINK": "Битые внутренние ссылки", "BROKEN_EXTERNAL_LINK": "Битые внешние ссылки",
    "LINK_TO_5XX": "Ссылки на страницы с ответом 5xx", "INTERNAL_LINK_TO_REDIRECT": "Внутренние ссылки на редирект",
    "EXTERNAL_LINK_TO_REDIRECT": "Внешние ссылки на редирект", "HTTP_LINK_ON_HTTPS": "Ссылка по HTTP на странице HTTPS",
    "INSECURE_SUBRESOURCE": "Небезопасные ресурсы страницы", "ROBOTS_BLOCKS_RESOURCES": "robots.txt закрывает JS и CSS",
    "OG_MISSING": "Нет разметки Open Graph", "NO_AUTHOR_BYLINE": "У статьи нет автора", "NO_CONTENT_DATES": "У статьи нет дат",
    "FEW_CITATIONS": "В статье мало источников", "DOM_TOO_DEEP": "Слишком глубокий DOM", "DOM_TOO_MANY_NODES": "Слишком много узлов DOM",
}
# Core finding message (English, may embed values) -> Russian; matched fully, case-insensitive. Groups are re-inserted as {1}, {2}.
MESSAGES = (
    (r"Page returns a 4xx response \(broken page\)", "Битая страница, ответ 4xx"),
    (r"Page returns a 5xx response \(server error\)", "Ошибка сервера, ответ 5xx"),
    (r"Page has excessive crawl depth", "Страница слишком глубоко от главной по кликам"),
    (r"Duplicate meta description", "Дубль meta description"),
    (r"Meta description is missing", "Нет meta description"),
    (r"Multiple H1 headings on the page", "На странице несколько заголовков H1"),
    (r"This page declares a counterpart under a language and region code the counterpart does not confirm for itself",
     "Страница указывает альтернативу с языком и регионом, которые сама альтернатива не подтверждает"),
    (r"Hreflang value is not a valid ISO 639-1 language / ISO 3166-1 region code", "Значение hreflang не является корректным кодом языка ISO 639-1 и региона ISO 3166-1"),
    (r"URL uses HTTP instead of HTTPS", "Адрес использует HTTP вместо HTTPS"),
)
NO_TRANSLATION = "описание на языке ядра — в блоке «Исходный ответ ядра»"
# Core reason of a skipped check (English, free text) -> Russian. First matching pattern wins; unknown stays «причина не указана ядром».
SKIP_REASONS = (
    (r"missing export|export \S+ not available|no \S+ export", "нет выгрузки в источнике"),
    (r"requirements\.\S+ is false|disabled", "отключено в профиле"),
    (r"policy configured", "политика не задана в профиле"),
    (r"no stored html", "нет сохранённого HTML"),
    (r"sitemap", "нет данных sitemap"),
    (r"article-scope", "в скане нет страниц-статей"),
    (r"no .*column|has no \S+ column|link inventory carries no|link_attributes", "в выгрузке нет нужной колонки"),
    (r"partial|unmeasured|incomplete", "данные скана неполные"),
    (r"no url with", "нет подходящих URL"),
    (r"not captured", "ответ цели не сохранён в скане"),
)
COLUMNS = ("Адрес", "HTTP", "Доказательство", "Состояние")


def check_name(check, _message=None):
    """Russian name of a check; an unknown code is never shown raw (it stays in the tooltip)."""
    return tr(CHECK_NAMES[check]) if check in CHECK_NAMES else tr("Другая проверка")


def message_ru(message):
    """Russian text of a core finding message, or the neutral fallback; the raw text lives only in the «Исходный ответ ядра» block."""
    text = (message or "").strip()
    for pattern, ru in MESSAGES:
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if match:
            return tr(ru).format(*match.groups()) if match.groups() else tr(ru)
    return tr(NO_TRANSLATION) if text else tr("Нет данных")


def skip_reason(reason):
    text = (reason or "").casefold()
    return tr(next((ru for pattern, ru in SKIP_REASONS if re.search(pattern, text)), "причина не указана ядром"))


def selected_scan(host):
    path = getattr(host, "selected_scan_path", None)
    return next((row for row in host.scan_model.rows if row.get("path") == path), None) if path else None


def sample_checks(findings):
    """Checks present in the bounded sample: {check: {severity, message, items}} in first-seen order."""
    checks = {}
    for item in findings.get("items") or []:
        if isinstance(item, dict) and item.get("check"):
            entry = checks.setdefault(item["check"], {"severity": item.get("severity"), "message": item.get("message"), "items": []})
            entry["items"].append(item)
    return checks


class FindingModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
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
        item, column = self.rows[index.row()], index.column()
        if column == 3 and role == BADGE_ROLE:
            return "mut", tr("Найдена в скане")
        if role == Qt.DisplayRole:
            return (item.get("target_url") or tr("Нет данных"), "—", message_ru(item.get("message")), tr("Найдена в скане"))[column]
        if role == Qt.ToolTipRole:
            return item.get("target_url") if column == 0 else tr("Нет данных") if column == 1 else item.get("message") if column == 2 else item.get("fingerprint") if column == 3 else None
        return None


class IssuesScreen(Screen):
    slot = "issues"
    watches = ("project", "scans", "scan_status")

    def __init__(self, host):
        super().__init__(host)
        self.severity = "all"
        self.selected = None
        self.checks = {}
        self.signature = None
        self.panel_state = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.content = QWidget()
        self.stack.addWidget(self.content)
        self.state_holder = QWidget()
        self.state_layout = QVBoxLayout(self.state_holder)
        self.stack.addWidget(self.state_holder)
        body = QHBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_list())
        body.addWidget(self._build_detail(), 1)
        i18n.signals.changed.connect(self._language_changed)
        self.refresh()

    # ---- construction ----------------------------------------------------------------------------------------
    def _build_list(self):
        side = QFrame()
        side.setProperty("aside", False)
        side.setFixedWidth(330)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Найти проверку…"))
        self.search.setAccessibleName(tr("Поиск проблемы"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill_list())
        layout.addWidget(self.search)
        pills = QGridLayout()
        pills.setSpacing(4)
        self.pills = {}
        for slot, (key, label) in enumerate((("all", "Все"), *[(k, n) for k, n, _i, _c in SEVERITIES])):
            button = QToolButton()
            button.setProperty("pill", "group")
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.clicked.connect(lambda _c=False, k=key: self._set_severity(k))
            self.pills[key] = (button, label)
            pills.addWidget(button, slot // 2, slot % 2)
        layout.addLayout(pills)
        self.list_note = QLabel()
        self.list_note.setProperty("text_style", "meta")
        self.list_note.setWordWrap(True)
        layout.addWidget(self.list_note)
        self.list_waiting = waiting_badge(981, "Число находок по каждой проверке для всего скана и фильтр по проверке")
        layout.addWidget(self.list_waiting, 0, Qt.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.list_layout = QVBoxLayout(holder)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(1)
        self.list_layout.addStretch(1)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        return side

    def _build_detail(self):
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QFrame()
        head.setProperty("page_bar", True)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(20, 16, 20, 12)
        head_layout.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.icon = MaterialIconLabel("rule", 24, color="role:text_2")
        top.addWidget(self.icon)
        self.title = QLabel()
        self.title.setProperty("text_style", "dialog")
        self.title.setWordWrap(True)
        top.addWidget(self.title, 1)
        self.open_url = QPushButton(tr("Открыть в URL"))
        self.open_url.setIcon(material_icon("table_view"))
        self.open_url.clicked.connect(self._open_in_url)
        self.to_task = QPushButton(tr("В задачу"))
        self.to_task.setProperty("role", "primary")
        self.to_task.setIcon(material_icon("assignment_add", theming.roles()["on_primary"]))
        self.to_task.setEnabled(False)
        self.to_task.setToolTip(unavailable_tip("Создание задач из проверок"))
        top.addWidget(self.open_url)
        top.addWidget(self.to_task)
        head_layout.addLayout(top)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        head_layout.addWidget(self.desc)
        self.lane_found = QLabel()
        self.lane_found.setWordWrap(True)
        self.lane_found.setProperty("text_style", "meta")
        head_layout.addWidget(self.lane_found)
        lane = QHBoxLayout()
        lane.setSpacing(8)
        lane.addWidget(waiting_badge(926, "Путь находки: задача, исправление и подтверждение перепроверкой"))
        lane.addStretch(1)
        self.recheck = QPushButton(tr("Перепроверить"))
        self.recheck.setIcon(material_icon("replay"))
        self.recheck.setEnabled(False)
        self.recheck.setToolTip(unavailable_tip("Перепроверка по проверке"))
        lane.addWidget(self.recheck)
        head_layout.addLayout(lane)
        layout.addWidget(head)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.overview = self._build_overview()
        self.pages.addWidget(self.overview)
        self.model = FindingModel(self)
        self.table = style_table(QTableView())
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, BadgeDelegate(self.table))
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column, width in ((1, 90), (3, 150)):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(lambda index: self._open_row(index.row()))
        self.pages.addWidget(self.table)
        self.raw_toggle = QToolButton()
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.raw_toggle.setStyleSheet("QToolButton { border: none; background: transparent; padding: 4px 12px; }")
        self.raw_toggle.toggled.connect(lambda _on: self._sync_raw())
        layout.addWidget(self.raw_toggle, 0, Qt.AlignLeft)
        self.raw = QLabel()
        self.raw.setWordWrap(True)
        self.raw.setContentsMargins(12, 0, 12, 4)
        self.raw.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.raw.setVisible(False)
        layout.addWidget(self.raw)
        self.foot = QLabel()
        self.foot.setProperty("text_style", "meta")
        self.foot.setContentsMargins(12, 8, 12, 8)
        self.foot.setWordWrap(True)
        layout.addWidget(self.foot)
        return pane

    def _build_overview(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        self.partial_note = QFrame()
        self.partial_note.setProperty("note", "warn")
        note_layout = QHBoxLayout(self.partial_note)
        note_layout.addWidget(MaterialIconLabel("warning", 18, color="role:warning"), 0, Qt.AlignTop)
        self.partial_text = QLabel()
        self.partial_text.setWordWrap(True)
        note_layout.addWidget(self.partial_text, 1)
        self.resume = QPushButton(tr("Продолжить скан"))
        self.resume.clicked.connect(lambda _c=False: self.host.resume_selected_scan())
        note_layout.addWidget(self.resume)
        layout.addWidget(self.partial_note)
        layout.addWidget(section_label("Находки по важности"))
        self.bars = {}
        for key, label, icon, colour in SEVERITIES:
            line = QHBoxLayout()
            line.setSpacing(10)
            line.addWidget(MaterialIconLabel(icon, 18, color=f"role:{colour}"))
            caption = QLabel(tr(label))
            caption.setMinimumWidth(110)
            line.addWidget(caption)
            bar = StackBar(10)
            line.addWidget(bar, 1)
            count = QLabel()
            count.setMinimumWidth(80)
            count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            line.addWidget(count)
            layout.addLayout(line)
            self.bars[key] = (bar, count, caption)
        self.total = QLabel()
        self.total.setProperty("text_style", "meta")
        layout.addWidget(self.total)
        groups = StatePanel("waiting", "Крупнейшие группы и счётчики по проверкам", "Ядро отдаёт итоги по важности и первые 20 находок; счётчики по каждой проверке и постраничный список URL появятся позже",
                            issue=980, hint="Находки по проверкам для всего скана и постраничный список URL")
        groups.setMinimumHeight(220)
        layout.addWidget(groups)
        self.skipped_toggle = QToolButton()
        self.skipped_toggle.setCheckable(True)
        self.skipped_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.skipped_toggle.setStyleSheet("QToolButton { border: none; background: transparent; text-align: left; padding: 4px 0; }")
        self.skipped_toggle.toggled.connect(lambda _on: self._sync_skipped())
        layout.addWidget(self.skipped_toggle, 0, Qt.AlignLeft)
        self.skipped = QLabel()
        self.skipped.setWordWrap(True)
        self.skipped.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.skipped.setVisible(False)
        layout.addWidget(self.skipped)
        layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = event.size().width() < 1000
        for button, text in ((self.open_url, "Открыть в URL"), (self.to_task, "В задачу"), (self.recheck, "Перепроверить")):
            button.setText("" if narrow else tr(text))
            button.setToolTip(button.toolTip() or tr(text))
        for column in (1, 3):  # constant «—» and «Найдена в скане» only steal width from address and evidence when narrow
            self.table.setColumnHidden(column, narrow)

    # ---- state -----------------------------------------------------------------------------------------------
    def _set_state(self, kind, panel=None):
        if kind != self.panel_state:
            self.panel_state = kind
            while self.state_layout.count():
                item = self.state_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if panel is not None:
                self.state_layout.addWidget(panel)
        self.stack.setCurrentIndex(1 if panel is not None else 0)

    def refresh(self):
        host = self.host
        scan = selected_scan(host)
        if not host.project_directory:
            return self._set_state("none", no_project_panel(host, "Откройте проект, чтобы увидеть проблемы его сканов."))
        if scan is None:
            if host._project_loading:
                return self._set_state("loading", StatePanel("loading", "Чтение проекта", "Находки появятся после чтения сканов."))
            return self._set_state("noscan", StatePanel("empty", "Нет выбранного скана", "Проблемы показываются по сохранённому скану проекта.", action=("Новый скан", host.scan_preview)))
        findings = (scan.get("evidence") or {}).get("findings") or {}
        if findings.get("state") != "available":
            reason = findings.get("reason") or "Ядро не вернуло находок для этого скана"
            return self._set_state("partial", StatePanel("partial", "Находки недоступны", f"{reason}. {tr('Это не «0 проблем».')}"))
        if findings.get("total") == 0:
            skipped = (scan.get("evidence") or {}).get("skipped_checks") or []
            panel = StatePanel("empty", "Проблем не найдено", trf("Находок нет по выбранному скану. Это измеренный ноль; проверки, которые не запускались: {n}.", n=len(skipped)))
            return self._set_state("zero", panel)
        self._set_state(None)
        signature = (scan.get("path"), findings.get("total"), tuple((findings.get("by_severity") or {}).items()), scan.get("crawl_partial"))
        if signature != self.signature:
            self.signature = signature
            self.checks = sample_checks(findings)
            if self.selected not in self.checks:
                self.selected = None
        self._fill_all()

    def _language_changed(self, _language):
        try:
            self.signature = None
            self.refresh()
        except RuntimeError:  # the screen was deleted (tests, shutdown)
            pass

    def _set_severity(self, key):
        self.severity = key
        for name, (button, _label) in self.pills.items():
            button.setChecked(name == key)
        self._fill_list()

    def _fill_all(self):
        scan = selected_scan(self.host) or {}
        evidence = scan.get("evidence") or {}
        findings = evidence.get("findings") or {}
        by_severity = findings.get("by_severity") or {}
        total = findings.get("total")
        for key, (button, label) in self.pills.items():
            count = total if key == "all" else by_severity.get(key, 0)
            button.setText(f"{tr(label)}  {number(count)}")
        peak = max([v for v in by_severity.values() if type(v) is int] or [0])
        for key, (bar, count, _caption) in self.bars.items():
            value = by_severity.get(key, 0)
            bar.set_parts([(value, theming.roles()[{"critical": "error", "warning": "warning", "notice": "text_3"}[key]]), (max(0, peak - value), theming.roles()["disabled_bg"])] if peak else [])
            count.setText(number(value))
        self.total.setText(trf("Всего находок: {n}", n=number(total)))
        partial = scan.get("crawl_partial") is True or scan.get("lifecycle") in ("interrupted", "running")
        self.partial_note.setVisible(partial)
        self.partial_text.setText(tr("Скан частичный. Находки — только по обойдённой части; «нет данных» не равно нулю. Не закрывайте задачи по неполному скану."))
        self.resume.setVisible(partial and hasattr(self.host, "resume_selected_scan"))
        skipped = [item for item in evidence.get("skipped_checks") or [] if isinstance(item, dict)]
        self.skipped_rows = [f"{check_name(item.get('id'))} — {skip_reason(item.get('reason'))}" for item in skipped]
        self._sync_skipped()
        self.list_note.setText(trf("Проверки из первых {n} находок. Число рядом с проверкой — находки в этой выборке, не во всём скане.", n=len(findings.get("items") or [])))
        self._fill_list()
        self._fill_detail()

    def _sync_raw(self):
        entry = self.checks.get(self.selected)
        self.raw_toggle.setVisible(entry is not None)
        opened = self.raw_toggle.isChecked() and entry is not None
        self.raw_toggle.setText(tr("Исходный ответ ядра"))
        self.raw_toggle.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        lines = []
        if entry is not None:
            lines = [f"check: {self.selected}", f"severity: {entry['severity']}", f"message: {entry['message']}"]
            lines += [f"{item.get('target_url')} — {item.get('message')} [{item.get('fingerprint')}]" for item in entry["items"][:20]]
        self.raw.setText("\n".join(lines))
        self.raw.setVisible(opened)

    def _sync_skipped(self):
        rows = getattr(self, "skipped_rows", [])
        opened = self.skipped_toggle.isChecked() and bool(rows)
        self.skipped_toggle.setText(trf("Не выполнялось: {n}", n=len(rows)) if rows else tr("Не выполнялось: нет данных"))
        self.skipped_toggle.setEnabled(bool(rows))
        self.skipped_toggle.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        self.skipped.setText("\n".join(rows))
        self.skipped.setVisible(opened)

    def _fill_list(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()  # deleteLater alone leaves it painted until the event loop runs
                item.widget().deleteLater()
        needle = self.search.text().strip().casefold()
        self.row_buttons = {}
        for check, entry in self.checks.items():
            if self.severity != "all" and entry["severity"] != self.severity:
                continue
            name = check_name(check, entry["message"])
            if needle and needle not in f"{name} {check}".casefold():
                continue
            icon, colour = next(((i, c) for k, _n, i, c in SEVERITIES if k == entry["severity"]), ("help", "text_muted"))
            button = QToolButton()
            button.setProperty("pill", "group")
            button.setCheckable(True)
            button.setChecked(check == self.selected)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setIcon(material_icon(icon, f"role:{colour}"))
            button.setText(f"{name}  ·  {len(entry['items'])}")
            button.setToolTip(f"{check} · {tr('число находок этой проверки в выборке')}")
            button.setSizePolicy(button.sizePolicy().Expanding, button.sizePolicy().Fixed)
            button.setMinimumHeight(36)
            button.clicked.connect(lambda _c=False, c=check: self._select(c))
            self.list_layout.insertWidget(self.list_layout.count() - 1, button)
            self.row_buttons[check] = button
        if not self.row_buttons:
            empty = QLabel(tr("В первых находках нет проверок с такой важностью или названием."))
            empty.setProperty("text_style", "meta")
            empty.setWordWrap(True)
            self.list_layout.insertWidget(0, empty)

    def _select(self, check):
        self.selected = None if check == self.selected else check
        for key, button in self.row_buttons.items():
            button.setChecked(key == self.selected)
        self._fill_detail()

    def _fill_detail(self):
        entry = self.checks.get(self.selected)
        scan = selected_scan(self.host) or {}
        if entry is None:
            self.pages.setCurrentIndex(0)
            self.title.setText(tr("Проблемы скана"))
            self.icon.set_material_icon("rule", "role:text_2")
            self.desc.setText(tr("Выберите проверку слева: в списке проверки из первых находок скана."))
            self.lane_found.setText(self._lane(scan))
            self.open_url.setEnabled(False)
            self.open_url.setToolTip(tr("Выберите проверку"))
            self.foot.setText("")
            self.foot.setVisible(False)
            self._sync_raw()
            return
        self.pages.setCurrentIndex(1)
        self.foot.setVisible(True)
        name = check_name(self.selected, entry["message"])
        icon, colour = next(((i, c) for k, _n, i, c in SEVERITIES if k == entry["severity"]), ("help", "text_muted"))
        self.title.setText(name)
        self.icon.set_material_icon(icon, f"role:{colour}")
        self.desc.setText(message_ru(entry["message"]))
        self._sync_raw()
        self.lane_found.setText(self._lane(scan))
        self.model.set_rows(entry["items"])
        urls = [i.get("target_url") for i in entry["items"] if i.get("target_url")]
        self.open_url.setEnabled(bool(urls))
        self.open_url.setToolTip(trf("Откроет URL из выборки находок этой проверки ({n}). Фильтр по проверке для всего скана пока недоступен", n=len(urls)))
        self.foot.setText(trf("{n} URL в выборке находок этой проверки. Полный список по всему скану пока недоступен.", n=len(entry["items"])))

    @staticmethod
    def _lane(scan):
        steps = [tr("Найдена в скане"), tr("Задача программисту"), tr("Исправлено по словам исполнителя"), tr("Подтверждено перепроверкой")]
        return "  →  ".join([f"1 {steps[0]}", *(f"{n} {text}" for n, text in enumerate(steps[1:], 2))])

    # ---- actions ---------------------------------------------------------------------------------------------
    def _open_in_url(self):
        entry = self.checks.get(self.selected)
        if not entry:
            return
        urls = [i["target_url"] for i in entry["items"] if i.get("target_url")][:100]
        label = trf("Проверка «{name}» · выборка {n} URL", name=check_name(self.selected, entry["message"]), n=len(urls))
        self.host.open_url_filtered(label, [{"column": "url", "op": "in", "value": urls}])

    def _open_row(self, row):
        item = self.model.rows[row] if 0 <= row < len(self.model.rows) else None
        if item and item.get("target_url"):
            self.host.open_url_filtered(trf("URL · {url}", url=item["target_url"]), [{"column": "url", "op": "eq", "value": item["target_url"]}])


__all__ = ["IssuesScreen", "parse_time"]
