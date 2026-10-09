"""«Новый скан»: the modal window of the design (ScanDialog / ScanList) and its settings window (Sc*).

The dialog shows a plan first and starts nothing by itself: «Запустить» is a separate button that stays disabled until
the volume is confirmed and every field is valid. It reads state from the window (project, core descriptor, settings),
asks it for the project's crawl policy and hands the finished plan to ``host.launch_scan``; it never calls the core,
the network or a scan itself.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, theming
from ..i18n import tr, trf
from ..ui.controls import Note, Segmented, Switch, polish
from ..ui.icons import MaterialIconLabel
from ..ui.icons import material_icon as icon
from ..ui.kit import StatePanel, waiting_badge
from ..ui.presentation import ElidedLabel
from .new_scan_draft import (
    ISSUE_PROFILES,
    ISSUE_URL_QUERY,
    LIST_FILE_CAP,
    PlanError,
    ScanDraft,
    grouped,
    list_from_file_text,
)
from .new_scan_pages import ChoiceCard, FieldBox, flow, number_edit

SOURCE_CARDS = (
    ("site", "travel_explore", "Сайт целиком", "Обход по ссылкам со стартового URL проекта"),
    ("sitemap", "account_tree", "По sitemap", "Только URL из sitemap.xml, без обхода ссылок"),
    ("list", "format_list_bulleted", "Список URL", "Вставить или загрузить свой список адресов"),
    ("sf", "bug_report", "Screaming Frog", "Недоступно в этой сборке"),
)
SOURCE_NAMES = {key: name for key, _icon, name, _text in SOURCE_CARDS}
FIELD_TITLES = {
    "rps": "Запросов/с", "threads": "Потоки", "limit": "Лимит URL", "depth": "Глубина", "requests": "Лимит запросов",
    "minutes": "Время скана", "sitemap": "Адрес sitemap", "robots": "robots.txt", "min_delay": "Минимальная пауза",
    "max_delay": "Максимальная пауза", "timeouts": "Тайм-ауты подряд", "script_timeout": "Время JavaScript",
    "body_mb": "Максимум на один ответ", "free_gb": "Свободное место", "viewport": "Окно просмотра", "list": "Список URL",
}
ROBOTS_NAMES = {"respect": "соблюдать", "report_only": "только отчёт", "ignore": "игнорировать"}


def rps_label(draft):
    rate = draft.rps()
    return "—" if rate is None else f"{float(f'{rate:.3g}'):g}".replace(".", ",")


def hbox(*items, spacing=8, stretch=()):
    """A plain row widget: widgets in order, ``None`` is a stretch."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for item in items:
        layout.addStretch(1) if item is None else layout.addWidget(item, 1 if item in stretch else 0)
    return box


def summary_rows(draft):
    """(caption, value) pairs of the configuration summary; every value is a setting, not an estimate."""
    v = draft.values
    depth = v.get("limits.max_depth")
    seconds = v.get("limits.max_crawl_seconds") or 0
    rows = [
        ("Старт", draft.target or None),
        ("Запросов/с", trf("{n} на хост", n=rps_label(draft)) if draft.rps() else None),
        ("Потоков", v.get("speed.concurrency")),
        ("Лимит URL", grouped(draft.url_limit) if draft.limit_enabled else tr("без лимита")),
        ("Глубина", None if depth is None else tr("без ограничения") if depth == -1 else depth),
        ("Время", trf("{n} мин", n=grouped(round(seconds / 60, 1) if seconds % 60 else seconds // 60)) if seconds else tr("без лимита")),
        ("robots.txt", tr(ROBOTS_NAMES.get(v.get("robots.policy"), "")) or None),
        ("JS", tr("всегда") if v.get("rendering.mode") == "js" else tr("выкл")),
        ("Тело", tr("не хранится") if v.get("storage.body_mode") == "off" else tr("сохраняется")),
        ("Экстракторов", tr("не подключены (недоступно в этой версии ядра)")),
    ]
    agent = v.get("http.user_agent")
    if agent:
        rows.append(("User-Agent", agent))
    if v.get("scope.internal") == "registrable_domain":
        rows.append(("Хосты", tr("весь домен")))
    return rows


class NewScanDialog(QDialog):
    """The dialog. ``plan`` is set only when the user pressed «Запустить» on a valid, confirmed draft."""

    def __init__(self, host, parent=None):
        super().__init__(parent or host)
        self.host = host
        self.plan = None
        self.draft = None
        self._busy = False
        self._confirmed_signature = None
        self._list_timer = QTimer(self)
        self._list_timer.setSingleShot(True)
        self._list_timer.timeout.connect(lambda: self.draft.emit_changed())
        self.setWindowTitle("Новый скан")
        self.setModal(True)
        self.setObjectName("newScanDialog")
        area = parent.size() if parent is not None else host.size()
        self.resize(min(1000, max(720, area.width() - 40)), min(820, max(640, area.height() - 40)))
        self.setMinimumSize(640, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.state = StatePanel("loading", "Читаем настройки ядра…", "Параметры скана берутся из crawl-describe-settings.")
        self.retry = QPushButton(tr("Повторить загрузку настроек"))
        self.retry.setObjectName("scanRetryDescriptor")
        self.retry.clicked.connect(self._retry)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.addStretch(1)
        holder_layout.addWidget(self.state)
        holder_layout.addWidget(self.retry, 0, Qt.AlignHCenter)
        holder_layout.addStretch(1)
        self.stack.addWidget(holder)
        self.form = QWidget()
        self.stack.addWidget(self.form)
        root.addWidget(self._footer())
        host.crawl_descriptor_changed.connect(self._descriptor_changed)
        self.finished.connect(self._release)
        self._descriptor_changed()
        if host.crawl_descriptor is None and hasattr(host, "load_crawl_descriptor"):
            host.load_crawl_descriptor()
        i18n.retranslate(self)

    # ---- frame ---------------------------------------------------------------------------------------------------
    def _header(self):
        bar = QFrame()
        bar.setObjectName("scanHeader")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 10, 12, 10)
        layout.setSpacing(12)
        layout.addWidget(MaterialIconLabel("play_circle", 24, color="role:primary"))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        title = QLabel(tr("Новый скан"))
        title.setProperty("text_style", "dialog")
        self.subtitle = QLabel()
        self.subtitle.setProperty("text_style", "meta")
        texts.addWidget(title)
        texts.addWidget(self.subtitle)
        layout.addLayout(texts, 1)
        close = QToolButton()
        close.setProperty("role", "icon")
        close.setIcon(icon("close"))
        close.setFocusPolicy(Qt.NoFocus)
        close.setAccessibleName(tr("Закрыть без запуска"))
        close.setToolTip(tr("Закрыть без запуска"))
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        label = ((getattr(self.host, "project_result", None) or {}).get("project") or {}).get("site") or {}
        self.subtitle.setText(" · ".join(part for part in (label.get("label"), label.get("host"), tr("основной сайт")) if part))
        return bar

    def _footer(self):
        bar = QFrame()
        bar.setObjectName("dialogFooter")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(8)
        self.problem_icon = MaterialIconLabel("error", 18, color="role:error")
        self.problem = ElidedLabel()
        self.problem.setObjectName("scanValidationFeedback")
        self.problem.setProperty("field_error", True)
        self.problem.setProperty("text_style", "meta")
        layout.addWidget(self.problem_icon)
        layout.addWidget(self.problem, 1)
        profile = QPushButton(tr("Сохранить как профиль"))
        profile.setObjectName("scanSaveProfile")
        profile.setProperty("role", "text")
        profile.setIcon(icon("bookmark_add"))
        profile.setEnabled(False)
        profile.setToolTip(f"{tr('Недоступно в этой версии ядра')}: {tr('ядро не хранит именованные профили скана')}")
        layout.addWidget(profile)
        layout.addWidget(waiting_badge(ISSUE_PROFILES))
        cancel = QPushButton(tr("Отмена"))
        cancel.setProperty("role", "text")
        cancel.setProperty("size", "lg")
        cancel.clicked.connect(self.reject)
        self.start = QPushButton(tr("Запустить"))
        self.start.setObjectName("scanStartButton")
        self.start.setProperty("role", "primary")
        self.start.setProperty("size", "lg")
        self.start.setIcon(icon("play_arrow", theming.roles()["on_primary"]))
        self.start.clicked.connect(self._accept_plan)
        layout.addWidget(cancel)
        layout.addWidget(self.start)
        return bar

    # ---- descriptor lifecycle ------------------------------------------------------------------------------------
    def _descriptor_changed(self):
        host = self.host
        if host.crawl_descriptor is not None:
            if self.draft is None:
                self._build_form()
            self.stack.setCurrentIndex(1)
        else:
            error = getattr(host, "_crawl_descriptor_error", None)
            self.state.deleteLater()
            kind, title, text = (("error", "Настройки ядра недоступны", error) if error
                                 else ("loading", "Читаем настройки ядра…", "Параметры скана берутся из crawl-describe-settings."))
            self.state = StatePanel(kind, title, text or "")
            layout = self.stack.widget(0).layout()
            layout.insertWidget(1, self.state)
            i18n.retranslate(self.state)
            self.stack.setCurrentIndex(0)
        self.retry.setVisible(host.crawl_descriptor is None and bool(getattr(host, "_crawl_descriptor_error", None)))
        self._refresh()

    def _retry(self):
        self.host.load_crawl_descriptor()
        self._descriptor_changed()

    def _release(self, *_args):
        try:
            self.host.crawl_descriptor_changed.disconnect(self._descriptor_changed)
        except (TypeError, RuntimeError):
            pass

    # ---- form ----------------------------------------------------------------------------------------------------
    def _build_form(self):
        host = self.host
        site = ((getattr(host, "project_result", None) or {}).get("project") or {}).get("site") or {}
        self.draft = draft = ScanDraft(host.crawl_descriptor, target=site.get("target") or "", host=site.get("host") or "",
                                       project_directory=host.project_directory or "", prefs=getattr(host, "prefs", None), parent=self)
        key = host.note_project_key() if hasattr(host, "note_project_key") else None
        draft.restore(getattr(host, "_scan_drafts", {}).get(key))
        layout = QHBoxLayout(self.form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(24, 18, 24, 18)
        column.setSpacing(18)
        column.addWidget(self._sources())
        column.addWidget(self._source_blocks())
        column.addWidget(self._options())
        column.addStretch(1)
        scroll.setWidget(left)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._aside())
        draft.changed.connect(self._refresh)
        self._request_policy()
        self._refresh()
        self.cards[draft.source if draft.source != "sf" else "site"].setFocus()

    def _sources(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        caption = QLabel(tr("ЧТО СКАНИРОВАТЬ"))
        caption.setProperty("text_style", "overline")
        layout.addWidget(caption)
        self.cards = {}
        self.card_grid = QGridLayout()
        self.card_grid.setSpacing(10)
        for key, glyph, name, text in SOURCE_CARDS:
            card = ChoiceCard(key, glyph, name, text, "scanSource_" + key)
            if key == "sf":
                card.setEnabled(False)
                card.setToolTip(tr("Недоступно в этой сборке"))
            card.clicked.connect(lambda _c, k=key: self._pick_source(k))
            self.cards[key] = card
        layout.addLayout(self.card_grid)
        self._layout_cards(4)
        return box

    def _layout_cards(self, columns):
        for index, card in enumerate(self.cards.values()):
            self.card_grid.addWidget(card, index // columns, index % columns)
        for column in range(4):
            self.card_grid.setColumnStretch(column, 1 if column < columns else 0)
        self._card_columns = columns

    def _layout_options(self, columns):
        for index, item in enumerate(self.option_items):
            self.option_grid.addWidget(item, index // columns, index % columns, 1, 1)
        self._option_columns = columns

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "cards", None):
            columns = 4 if self.width() >= 940 else 2
            if columns != self._card_columns:
                self._layout_cards(columns)
        if getattr(self, "option_items", None):
            columns = 2 if self.width() >= 900 else 1
            if columns != self._option_columns:
                self._layout_options(columns)

    def _source_blocks(self):
        draft = self.draft
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        # site
        self.site_block = QWidget()
        site = QVBoxLayout(self.site_block)
        site.setContentsMargins(0, 0, 0, 0)
        start = QLineEdit(draft.target)
        start.setObjectName("scanStartUrl")
        start.setReadOnly(True)
        start.setAccessibleName(tr("Стартовый URL"))
        badge = QLabel(tr("Сайт проекта"))
        badge.setProperty("badge", "ok")
        site.addWidget(FieldBox(tr("Стартовый URL"), hbox(start, badge, stretch=(start,)), tr("Адрес берётся из проекта")))
        layout.addWidget(self.site_block)
        # sitemap
        self.sitemap_edit = QLineEdit(draft.sitemap_url)
        self.sitemap_edit.setObjectName("scanSitemapUrl")
        self.sitemap_edit.setAccessibleName(tr("Адрес sitemap"))
        self.sitemap_edit.setPlaceholderText("https://example.test/sitemap.xml")
        self.sitemap_edit.textEdited.connect(self._sitemap_typed)
        self.sitemap_box = FieldBox(tr("Адрес sitemap"), self.sitemap_edit,
                                    f"{tr('Состав sitemap покажет скан')} · {tr('предпросмотр без запуска')}: {tr('Недоступно в этой версии ядра')}")
        layout.addWidget(self.sitemap_box)
        # list
        self.list_block = QWidget()
        listing = QVBoxLayout(self.list_block)
        listing.setContentsMargins(0, 0, 0, 0)
        listing.setSpacing(6)
        title = QLabel(tr("Список URL · по одному в строке"))
        title.setProperty("text_style", "control")
        from_file = QPushButton(tr("Из файла .txt / .csv"))
        from_file.setObjectName("scanListFile")
        from_file.setIcon(icon("upload_file"))
        from_file.clicked.connect(self._list_from_file)
        paste = QPushButton(tr("Вставить"))
        paste.setObjectName("scanListPaste")
        paste.setIcon(icon("content_paste"))
        paste.clicked.connect(self._list_paste)
        listing.addWidget(title)
        listing.addWidget(flow(from_file, paste))
        self.list_edit = QPlainTextEdit(draft.list_text)
        self.list_edit.setObjectName("scanListText")
        self.list_edit.setAccessibleName(tr("Список URL"))
        self.list_edit.setMinimumHeight(110)
        self.list_edit.setMaximumHeight(150)
        self.list_edit.textChanged.connect(self._list_typed)
        listing.addWidget(self.list_edit)
        self.counter_labels = {}
        parts = []
        for name, kind in (("ready", "ok"), ("added", "info"), ("duplicates", "mut"), ("foreign", "warn"), ("invalid", "err")):
            label = QLabel()
            label.setObjectName("count_" + name)
            label.setProperty("badge", kind)
            self.counter_labels[name] = label
            parts.append(label)
        approx = QLabel(tr("оценка приложения"))
        approx.setProperty("text_style", "meta")
        approx.setToolTip(f"{tr('Предпросмотр списка ядро не умеет')} · {tr('Недоступно в этой версии ядра')}")
        four = QPushButton(tr("Взять 4xx из скана"))
        four.setProperty("role", "text")
        four.setEnabled(False)
        four.setToolTip(tr('Недоступно в этой версии ядра'))
        listing.addWidget(flow(*parts, approx, four, waiting_badge(ISSUE_URL_QUERY)))
        self.list_note = Note("warn", tr("Запуск списка URL из приложения недоступен в этой версии ядра."), tr("Скан списка не попадает в наблюдение проекта: список можно проверить, но не запустить."))
        listing.addWidget(self.list_note)
        layout.addWidget(self.list_block)
        # Screaming Frog
        self.sf_block = Note("info", tr("Недоступно в этой сборке."), tr("Импорт и запуск Screaming Frog не подключены."))
        layout.addWidget(self.sf_block)
        return box

    def _options(self):
        draft = self.draft
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(16)
        # mode
        self.mode = Segmented([("raw", tr("Исходный HTML")), ("js", tr("С рендерингом JS"))], draft.value("rendering.mode", "raw"), tr("Режим загрузки"))
        self.mode.setObjectName("scanRenderingMode")
        self.mode.changed.connect(lambda value: draft.set_value("rendering.mode", value))
        self.mode_hint = QLabel()
        self.mode_hint.setProperty("text_style", "meta")
        self.mode_hint.setWordWrap(True)
        mode_box = QWidget()
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        caption = QLabel(tr("Режим"))
        caption.setProperty("text_style", "control")
        for widget in (caption, self.mode, self.mode_hint):
            mode_layout.addWidget(widget)
        self.option_items = [mode_box]
        # profile
        profile_box = QWidget()
        profile_layout = QVBoxLayout(profile_box)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        caption = QLabel(tr("Профиль настроек"))
        caption.setProperty("text_style", "control")
        row.addWidget(caption, 1)
        all_profiles = QPushButton(tr("Все профили…"))
        all_profiles.setObjectName("scanAllProfiles")
        all_profiles.setProperty("role", "text")
        all_profiles.clicked.connect(lambda: self._open_settings("profiles"))
        row.addWidget(all_profiles)
        profile_layout.addLayout(row)
        self.profile_line = QLineEdit()
        self.profile_line.setReadOnly(True)
        self.profile_line.setAccessibleName(tr("Профиль настроек"))
        profile_layout.addWidget(self.profile_line)
        self.profile_hint = QLabel()
        self.profile_hint.setProperty("text_style", "meta")
        self.profile_hint.setWordWrap(True)
        profile_layout.addWidget(self.profile_hint)
        self.option_items.append(profile_box)
        # speed
        self.rps_edit, self._sync_rps = number_edit(draft, "rps", tr("Запросов в секунду"), 0, "scanRequestRate")
        minus, plus = QToolButton(), QToolButton()
        for button, glyph, name, step in ((minus, "remove", "Меньше", -1), (plus, "add", "Больше", 1)):
            button.setProperty("role", "icon")
            button.setIcon(icon(glyph))
            button.setAccessibleName(tr(name))
            button.setToolTip(tr(name))
            button.clicked.connect(lambda _c, s=step: self._step_rps(s))
        self.rps_box = FieldBox(tr("Запросов/с"), hbox(self.rps_edit, minus, plus, spacing=4, stretch=(self.rps_edit,)), tr("Бережно для боевого сайта: не больше 2"), self.rps_edit)
        self.threads_edit, self._sync_threads = number_edit(draft, "threads", tr("Потоки"), 0, "scanConcurrency")
        self.threads_box = FieldBox(tr("Потоки"), self.threads_edit)
        self.threads_box.setFixedWidth(96)
        self.option_items.append(hbox(self.rps_box, self.threads_box, spacing=10, stretch=(self.rps_box,)))
        # limits
        limits = QVBoxLayout()
        limits.setSpacing(10)
        caption = QLabel(tr("Ограничения"))
        caption.setProperty("text_style", "control")
        limits.addWidget(caption)
        self.limit_switch = Switch(tr("Ограничить число URL"), draft.limit_enabled)
        self.limit_switch.setObjectName("scanLimitEnabled")
        self.limit_switch.toggled.connect(draft.set_limit_enabled)
        self.limit_edit, self._sync_limit = number_edit(draft, "limit", tr("Лимит URL"), 110, "scanUrlLimit")
        limits.addWidget(hbox(self.limit_switch, QLabel(tr("Лимит URL")), None, self.limit_edit, spacing=10))
        self.limit_error = QLabel()
        self.limit_error.setProperty("field_error", True)
        self.limit_error.setProperty("text_style", "meta")
        self.limit_error.setWordWrap(True)
        limits.addWidget(self.limit_error)
        self.html_switch = Switch(tr("Сохранять HTML страниц"), draft.value("storage.body_mode") != "off")
        self.html_switch.setObjectName("scanSaveHtml")
        self.html_switch.setEnabled(draft.has("storage.body_mode"))
        self.html_switch.toggled.connect(lambda state: draft.set_value("storage.body_mode", "captured_entity_bytes" if state else "off"))
        hint = QLabel(tr("для поиска и сравнений"))
        hint.setProperty("text_style", "meta")
        limits.addWidget(hbox(self.html_switch, QLabel(tr("Сохранять HTML страниц")), None, hint, spacing=10))
        limit_box = QWidget()
        limit_box.setLayout(limits)
        limits.setContentsMargins(0, 0, 0, 0)
        self.option_items.append(limit_box)
        # why
        why = QLineEdit()
        why.setEnabled(False)
        why.setAccessibleName(tr("Зачем этот скан"))
        why.setPlaceholderText(tr("Связь запуска с задачей проекта"))
        why_box = QVBoxLayout()
        head = QHBoxLayout()
        caption = QLabel(tr("Зачем этот скан"))
        caption.setProperty("text_style", "control")
        head.addWidget(caption)
        head.addWidget(waiting_badge(922))
        head.addStretch(1)
        why_box.addLayout(head)
        why_box.addWidget(why)
        reason = QLabel(tr("В записи запуска ядра нет поля цели или задачи"))
        reason.setProperty("text_style", "meta")
        why_box.addWidget(reason)
        why_wrap = QWidget()
        why_wrap.setLayout(why_box)
        why_box.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(why_wrap, 4, 0, 1, 2)
        self.settings_link = QPushButton()
        self.settings_link.setObjectName("scanOpenSettings")
        self.settings_link.setProperty("role", "text")
        self.settings_link.setIcon(icon("tune"))
        self.settings_link.clicked.connect(lambda: self._open_settings("speed"))
        grid.addWidget(self.settings_link, 5, 0, 1, 2, Qt.AlignLeft)
        self.option_grid = grid
        self._layout_options(2)
        for column in (0, 1):
            grid.setColumnStretch(column, 1)
        return box

    def _aside(self):
        aside = QFrame()
        aside.setObjectName("scanAside")
        aside.setFixedWidth(280)
        layout = QVBoxLayout(aside)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        caption = QLabel(tr("ЧТО ПРОИЗОЙДЁТ"))
        caption.setProperty("text_style", "overline")
        layout.addWidget(caption)
        self.plan_grid = QGridLayout()
        self.plan_grid.setHorizontalSpacing(12)
        self.plan_grid.setVerticalSpacing(5)
        self.plan_grid.setColumnStretch(1, 1)
        self.plan_values = {}
        for index, key in enumerate(("source", "address", "speed", "urls", "requests", "time", "mode", "html", "estimate", "paid", "impact", "profile")):
            name = QLabel()
            name.setProperty("text_style", "meta")
            name.setMinimumWidth(118)
            value = QLabel()
            value.setObjectName("plan_" + key)
            value.setWordWrap(True)
            value.setAlignment(Qt.AlignRight | Qt.AlignTop)
            name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.plan_grid.addWidget(name, index, 0)
            self.plan_grid.addWidget(value, index, 1)
            self.plan_values[key] = (name, value)
        layout.addLayout(self.plan_grid)
        info = Note("info", tr("Запуск получит ID."), tr("Он появится в «Сканах» и у наблюдателя; запуск начнётся только после «Запустить»."))
        layout.addWidget(info)
        layout.addStretch(1)
        self.confirm = QCheckBox()
        self.confirm.setObjectName("scanLargeApproval")
        self.confirm.setAccessibleName(tr("Я проверил объём и разрешаю обращаться к сайту"))
        self.confirm.toggled.connect(self._confirm_toggled)
        consent = QLabel(tr("Я проверил объём и разрешаю обращаться к сайту"))
        consent.setWordWrap(True)
        consent.setBuddy(self.confirm)
        consent.mousePressEvent = lambda _event: self.confirm.toggle()
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.confirm, 0, Qt.AlignTop)
        row.addWidget(consent, 1)
        layout.addLayout(row)
        return aside

    # ---- actions -------------------------------------------------------------------------------------------------
    def _pick_source(self, key):
        self.draft.source = key
        self.draft.emit_changed()

    def _sitemap_typed(self, text):
        self.draft.sitemap_url = text
        self.draft.emit_changed()

    def _list_typed(self):
        if self._busy:
            return
        self.draft.list_text = self.list_edit.toPlainText()
        if len(self.draft.list_text) > 50_000:  # a very long list is counted once typing pauses
            self._list_timer.start(300)
        else:
            self.draft.emit_changed()

    def _list_paste(self):
        from PyQt5.QtWidgets import QApplication

        text = QApplication.clipboard().text()
        if text:
            self.list_edit.setPlainText((self.list_edit.toPlainText().rstrip("\n") + "\n" if self.list_edit.toPlainText() else "") + text)

    def _list_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Список URL"), "", tr("Списки (*.txt *.csv)"))
        if path:
            self.load_list_file(path)

    def load_list_file(self, path):
        file = Path(path)
        try:
            if file.stat().st_size > LIST_FILE_CAP:
                self.problem.setText(trf("Файл больше {n} МБ", n=LIST_FILE_CAP // 1024**2))
                return
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            self.problem.setText(trf("Не удалось прочитать файл: {error}", error=exc.strerror or str(exc)))
            return
        self.list_edit.setPlainText(list_from_file_text(text, file.suffix))

    def _step_rps(self, step):
        rate = self.draft.rps() or 1
        self.draft.set_text("rps", f"{max(1, round(rate) + step)}")

    def _confirm_toggled(self, state):
        self.draft.confirmed = bool(state)
        self._confirmed_signature = self.draft.signature() if state else None
        self._refresh()

    def _open_settings(self, page):
        from .new_scan_settings import ScanSettingsDialog

        editor = ScanSettingsDialog(self.draft, self.host, self, page)
        editor.exec_()
        editor.deleteLater()

    def _request_policy(self):
        host, draft = self.host, self.draft
        if draft.policy_state != "unknown" or not hasattr(host, "request_scan_policy"):
            return
        draft.policy_state = "loading"

        def arrived(overrides):
            try:
                draft.apply_project_policy(overrides)
            except RuntimeError:
                pass

        def failed(_text):
            try:
                draft.policy_state = "unavailable"
                draft.emit_changed()
            except RuntimeError:
                pass

        if not host.request_scan_policy(arrived, failed):
            draft.policy_state = "unavailable"

    def _accept_plan(self):
        draft = self.draft
        if draft is None:
            return
        try:
            plan = draft.build_plan()
        except PlanError as exc:
            self.problem.setText(str(exc))
            return
        if not draft.confirmed:
            self.problem.setText(tr("Подтвердите объём перед запуском"))
            return
        self.plan = plan
        self.accept()

    def done(self, result):
        if self.draft is not None and hasattr(self.host, "_scan_drafts") and hasattr(self.host, "note_project_key"):
            key = self.host.note_project_key()
            if key is not None:
                self.host._scan_drafts[key] = self.draft.snapshot()
        super().done(result)

    # ---- refresh -------------------------------------------------------------------------------------------------
    def _refresh(self):
        draft = self.draft
        if draft is None:
            self.start.setEnabled(False)
            self.problem.setText(tr("Параметры ядра загружаются…") if self.host.crawl_descriptor is None else "")
            self.problem_icon.setVisible(False)
            return
        if self._busy:
            return
        self._busy = True
        try:
            self._sync(draft)
        finally:
            self._busy = False

    def _sync(self, draft):
        problems = draft.problems()
        source = draft.source
        for key, card in self.cards.items():
            card.setChecked(key == source)
        declared = draft.capabilities.get("sitemap_only_retained") is True
        self.cards["sitemap"].setEnabled(declared)
        self.cards["sitemap"].setToolTip("" if declared else tr("Возможность не объявлена ядром"))
        self.site_block.setVisible(source == "site")
        self.sitemap_box.setVisible(source == "sitemap")
        self.list_block.setVisible(source == "list")
        self.sf_block.setVisible(source == "sf")
        if self.sitemap_edit.text() != draft.sitemap_url:
            self.sitemap_edit.setText(draft.sitemap_url)
        self.sitemap_box.set_error(problems.get("sitemap", ""))
        if self.list_edit.toPlainText() != draft.list_text:
            self.list_edit.setPlainText(draft.list_text)
        report = draft.list_report()
        texts = {
            "ready": trf("{n} готовы", n=grouped(report.ready)), "added": trf("{n} дополнен https://", n=grouped(report.added)),
            "duplicates": trf("{n} дубль убран", n=grouped(report.duplicates)), "foreign": trf("{n} чужой домен — пропущен", n=grouped(report.foreign)),
            "invalid": trf("{n} не адрес — пропущено", n=grouped(report.invalid)),
        }
        for name, label in self.counter_labels.items():
            label.setText(texts[name])
            label.setVisible(name in ("ready",) or bool(getattr(report, name)))
        self.list_note.setVisible(source == "list")
        for edit, sync in ((self.rps_edit, self._sync_rps), (self.threads_edit, self._sync_threads), (self.limit_edit, self._sync_limit)):
            sync()
        self.rps_box.set_error(problems.get("rps", ""))
        self.threads_box.set_error(problems.get("threads", ""))
        self.limit_edit.setProperty("invalid", "limit" in problems)
        polish(self.limit_edit)
        self.limit_error.setText(problems.get("limit", ""))
        self.limit_error.setVisible("limit" in problems)
        self.limit_edit.setEnabled(draft.limit_enabled)
        for switch, state in ((self.limit_switch, draft.limit_enabled), (self.html_switch, draft.value("storage.body_mode") != "off")):
            if switch.isChecked() != state:
                switch.blockSignals(True)
                switch.setChecked(state)
                switch.blockSignals(False)
        self.mode.setValue(draft.value("rendering.mode", "raw"))
        self.mode_hint.setText(tr("Выполняет JavaScript страницы; нужен браузер на этом компьютере")
                               if draft.value("rendering.mode") == "js" else tr("Быстрее; страницы разбираются без JavaScript"))
        names = {"core": tr("ядро"), "app": tr("настройки приложения"), "project": tr("профиль проекта")}
        state = draft.policy_state
        self.profile_line.setText(trf("Умолчания: {chain}", chain=" → ".join(names[k] for k in ("core", "app") + (("project",) if draft.project_layer else ()))))
        self.profile_hint.setText({"ready": trf("Профиль проекта: {n} парам.", n=len(draft.project_layer)) if draft.project_layer else tr("Профиль проекта не задан"),
                                   "loading": tr("Профиль проекта загружается…"), "unavailable": tr("Профиль проекта не прочитан"),
                                   "unknown": tr("Профиль проекта не прочитан")}[state])
        self.settings_link.setText(trf("Настройки скана… · доступно {n} из {total} параметров ядра", n=len(draft.core), total=len(draft.rows)))
        self._plan_block(draft)
        if draft.confirmed and self._confirmed_signature != draft.signature():
            draft.confirmed = False
            self._confirmed_signature = None
        if self.confirm.isChecked() != draft.confirmed:
            self.confirm.blockSignals(True)
            self.confirm.setChecked(draft.confirmed)
            self.confirm.blockSignals(False)
        self._footer_state(draft, problems)

    def _plan_block(self, draft):
        v = draft.values
        seconds = v.get("limits.max_crawl_seconds") or 0
        requests = v.get("limits.max_requests") or 0
        report = draft.list_report()
        address = {"site": draft.target, "sitemap": draft.sitemap_url.strip() or tr("не указан"),
                   "list": trf("Адресов: {n} (оценка приложения)", n=grouped(report.usable)), "sf": ""}[draft.source]
        rps = trf("до {n} запросов/с на хост · потоков {t}", n=rps_label(draft), t=v.get("speed.concurrency", "—")) if draft.rps() else tr("Нет данных")
        rows = {
            "source": ("Источник", tr(SOURCE_NAMES[draft.source])),
            "address": ("Адрес", address),
            "speed": ("Скорость", rps),
            "urls": ("Лимит URL", grouped(draft.url_limit) if draft.limit_enabled else tr("без лимита")),
            "requests": ("Лимит запросов", grouped(requests) if requests else tr("без лимита")),
            "time": ("Лимит времени", trf("{n} мин", n=grouped(seconds // 60 if seconds % 60 == 0 else round(seconds / 60, 1))) if seconds else tr("без лимита")),
            "mode": ("Загрузка", tr("С рендерингом JS") if v.get("rendering.mode") == "js" else tr("Исходный HTML")),
            "html": ("HTML страниц", tr("не сохраняется") if v.get("storage.body_mode") == "off" else tr("сохраняется")),
            "estimate": ("Длительность и диск", tr("оценка недоступна в этой версии ядра")),
            "paid": ("Платные провайдеры", tr("нет")),
            "impact": ("Влияние на сайт", tr("только чтение: GET-запросы")),
            "profile": ("Профиль проекта", {"ready": trf("{n} парам.", n=len(draft.project_layer)) if draft.project_layer else tr("не задан"),
                                            "loading": tr("загружается…")}.get(draft.policy_state, tr("не прочитан"))),
        }
        for key, (caption, value) in rows.items():
            name, label = self.plan_values[key]
            name.setText(tr(caption))
            label.setText(str(value))
            label.setToolTip(str(value))

    def _footer_state(self, draft, problems):
        titles = {key: tr(FIELD_TITLES[key]) for key in FIELD_TITLES}
        shown = ""
        for key, message in problems.items():
            shown = trf("Исправьте «{field}»", field=titles[key]) if key in titles and key != "source" else message
            break
        self.problem.setText(shown)
        self.problem.setToolTip("; ".join(problems.values()))
        self.problem_icon.setVisible(bool(shown))
        ready = bool(draft.confirmed) and not problems
        self.start.setEnabled(ready)
        self.start.setToolTip("" if ready else tr("Подтвердите объём и исправьте поля"))


def open_new_scan(host):
    """Entry used by the «Новый скан» button and ⌘N: opens the dialog, then launches only a confirmed plan."""
    if not host.project_directory or host._project_loading:
        host.notice.show_error("Сначала откройте проект и дождитесь его данных")
        return None
    dialog = NewScanDialog(host)
    dialog.exec_()
    plan = dialog.plan
    dialog.deleteLater()
    if plan:
        return host.launch_scan(*plan)
    return None
