"""Проблемы (canvas Issues.dc.html, StatesIssues.dc.html): findings of the selected saved scan.

The core gives per scan the totals by severity, the first 20 findings and the list of checks it skipped (the scan row of
``project-scans``). Per-check counters and the full list of affected URLs wait for #932; the path «found → task → fixed →
confirmed» waits for the remediation ledger (#926) and tasks (#922). Nothing is counted here from the 20-row sample.
"""

from __future__ import annotations

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
}
COLUMNS = ("Адрес", "HTTP", "Доказательство", "Состояние")


def check_name(check, message=None):
    return tr(CHECK_NAMES[check]) if check in CHECK_NAMES else (message or check or tr("Нет данных"))


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
            return (item.get("target_url") or tr("Нет данных"), tr("Нет данных"), item.get("message") or tr("Нет данных"), tr("Найдена в скане"))[column]
        if role == Qt.ToolTipRole:
            return item.get("target_url") if column == 0 else item.get("fingerprint") if column == 3 else None
        return None


class IssuesScreen(Screen):
    slot = "audit"
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
        self.list_waiting = waiting_badge(932)
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
        self.to_task.setToolTip(f"{tr('Создание задач из проверок ждёт ядра')} · {tr('ждёт')} #922, #926")
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
        lane.addWidget(waiting_badge(926))
        lane.addStretch(1)
        self.recheck = QPushButton(tr("Перепроверить"))
        self.recheck.setIcon(material_icon("replay"))
        self.recheck.setEnabled(False)
        self.recheck.setToolTip(f"{tr('Перепроверка по проверке ждёт журнала исправлений')} · {tr('ждёт')} #926")
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
        groups = StatePanel("waiting", "Крупнейшие группы и счётчики по проверкам", "Ядро отдаёт итоги по важности и первые 20 находок; счётчики по каждой проверке и постраничный список URL ждут ядра", issue=932)
        groups.setMinimumHeight(220)
        layout.addWidget(groups)
        layout.addWidget(section_label("Не выполнялись этим источником"))
        self.skipped = QLabel()
        self.skipped.setWordWrap(True)
        self.skipped.setTextInteractionFlags(Qt.TextSelectableByMouse)
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
        skipped = evidence.get("skipped_checks") or []
        self.skipped.setText("\n".join(f"{item.get('id')} · {item.get('reason') or tr('причина не указана')}" for item in skipped if isinstance(item, dict)) or tr("Нет данных"))
        self.list_note.setText(trf("Показаны проверки из первых {n} находок. Счётчики по каждой проверке и полный список — ждут ядра.", n=len(findings.get("items") or [])))
        self._fill_list()
        self._fill_detail()

    def _fill_list(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
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
            self.foot.setText(trf("Проверка core: findings · скан {id}", id=(scan.get("uuid") or "")[:8]))
            return
        self.pages.setCurrentIndex(1)
        name = check_name(self.selected, entry["message"])
        icon, colour = next(((i, c) for k, _n, i, c in SEVERITIES if k == entry["severity"]), ("help", "text_muted"))
        self.title.setText(name)
        self.icon.set_material_icon(icon, f"role:{colour}")
        self.desc.setText(entry["message"] or tr("Нет данных"))
        self.lane_found.setText(self._lane(scan))
        self.model.set_rows(entry["items"])
        urls = [i.get("target_url") for i in entry["items"] if i.get("target_url")]
        self.open_url.setEnabled(bool(urls))
        self.open_url.setToolTip(trf("Откроет URL из выборки находок этой проверки ({n}); полный фильтр по проверке ждёт #932", n=len(urls)))
        self.foot.setText(trf("{n} URL в выборке находок · проверка core:{id} · скан {scan}. Полный список ждёт #932.", n=len(entry["items"]), id=self.selected, scan=(scan.get("uuid") or "")[:8]))

    @staticmethod
    def _lane(scan):
        steps = [trf("Найдена · {scan}", scan=(scan.get("uuid") or "")[:8]), tr("Задача программисту"), tr("Исправлено по словам исполнителя"), tr("Подтверждено перепроверкой")]
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
