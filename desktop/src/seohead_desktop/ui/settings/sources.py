"""Settings data-source workspace. Every state is supplied by the core, never a fixture."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import i18n, theming
from ...i18n import Num, joined, trf
from ...source_service import VERIFY_PROVIDERS
from ..icons import material_icon
from .helpers import group_label, page
from .listing import Columns, action_button, badge, buttons_row, hint, list_item
from .source_details import agent_detail, coverage, key_detail, oauth_detail, spend_detail
from .source_layout import ProviderRow, Summary, source_state

ID, ICON, TITLE = "sources", "dns", "Источники данных"
HINT = "Ключи и доступы ядра seohead · значения ключей приложение не показывает"
SCHEMA = ()

# provider id -> (group, name, monogram, paid); unknown ids land in "Прочие"
CATALOGUE = {
    "gsc": ("Поиск и индексация", "Google Search Console", "G", False),
    "yandex_webmaster": ("Поиск и индексация", "Яндекс Вебмастер", "Я", False),
    "bing_webmaster": ("Поиск и индексация", "Bing Webmaster", "B", False),
    "indexnow": ("Поиск и индексация", "IndexNow", "IN", False),
    "ga4": ("Аналитика", "Google Analytics 4", "GA", False),
    "metrika": ("Аналитика", "Яндекс Метрика", "М", False),
    "pagespeed": ("Производительность", "PageSpeed Insights", "PS", False),
    "crux": ("Производительность", "CrUX", "CX", False),
    "yandex_cloud": ("Ключевые слова и позиции", "Яндекс Cloud / Wordstat", "W", True),
    "arsenkin": ("Ключевые слова и позиции", "Arsenkin", "A", True),
    "dataforseo_backlinks": ("Ключевые слова и позиции", "DataForSEO · ссылки", "D4", True),
    "miratext": ("Ключевые слова и позиции", "Miratext", "MT", False),
    "wayback": ("Ссылки", "Wayback", "WB", False),
    "crtsh": ("Ссылки", "crt.sh", "CT", False),
}
GROUP_ORDER = ("Поиск и индексация", "Аналитика", "Производительность", "Ключевые слова и позиции", "Ссылки", "Прочие")
CREDENTIALS = {
    "api_key": "API-ключ", "api_token": "API-токен", "oauth_bearer": "OAuth", "durable_oauth": "OAuth",
    "service_account": "сервисный аккаунт", "login": "логин", "password": "пароль", "submission_key": "ключ отправки",
}
VERIFIED = {"verified", "ready", "connected"}


def describe(provider_id, entry):
    """(badge kind, badge text, icon, sub line) for one readiness entry; never claims more than the core said."""
    state = str(entry.get("readiness_state") or entry.get("state") or "")
    kinds = []
    for component in entry.get("credential_components", {}) or {}:
        label = CREDENTIALS.get(component)
        if label and label not in kinds:
            kinds.append(label)
    sub = [joined(" / ", kinds)] if kinds else []
    refs = [src.get("source_reference") for src in (entry.get("credential_sources") or {}).values() if src.get("source_reference")]
    if refs:
        sub.append(trf("хранится: {refs}", refs=", ".join(dict.fromkeys(refs))))
    if state in VERIFIED or entry.get("verified") is True:
        return "ok", "подключено", "check_circle", sub
    if state == "configured_unverified":
        return "info", "ключ задан · не проверен", "key", sub
    if state in {"missing", "not_configured"}:
        return "warn", "нужен ключ", "key", sub
    if state == "not_required":
        return "ok", "без ключа", "public", ["публичный сервис · ключ не нужен"]
    labels = {"expired": "истёк токен", "revoked": "доступ отозван", "waiting": "ждём браузер", "invalid": "ошибка ключа", "failed": "ошибка проверки"}
    return ("err" if state in {"expired", "revoked", "invalid", "failed"} else "mut"), labels.get(state, "нет данных"), "help", sub


def _kpi(value, label):
    from PyQt5.QtWidgets import QFrame, QVBoxLayout

    frame = QFrame()
    frame.setProperty("kpi", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(4)
    caption = QLabel(label)
    caption.setProperty("kpi_part", "label")
    number = QLabel(value)
    number.setProperty("kpi_part", "value")
    caption.setWordWrap(True)
    number.setWordWrap(True)
    layout.addWidget(caption)
    layout.addWidget(number)
    return frame


class SourcesPage(QWidget):
    def __init__(self, context):
        super().__init__()
        self._context = context
        self.snapshot = {}
        self.current_view = "list"
        self.provider = None
        self.auth = {}
        self._generation = 0
        self._busy = False
        self._doctor = None
        self._error = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._body = None
        theming.signals.changed.connect(self._theme_changed)
        if context.can("sources"):
            self.refresh()
        elif context.can("providers"):
            self.show_message("Читаю состояние источников у ядра…")
            context.request("providers", callback=self.show_providers, on_error=self.show_error)
        else:
            self.show_message(None)

    def _theme_changed(self, _theme):
        if self.snapshot:
            self.render()

    def _set_body(self, widget):
        if self._body is not None:
            self._layout.removeWidget(self._body)
            self._body.hide()
            self._body.setParent(None)
            self._body.deleteLater()
        self._body = widget
        for label in widget.findChildren(QLabel):
            # Core values are text, never HTML. Formatting belongs to the components.
            label.setTextFormat(Qt.PlainText)
        i18n.retranslate(widget)
        self._layout.addWidget(widget)

    def show_message(self, text):
        self._set_body(source_state("loading" if text else "empty", text or "Нет данных",
                                  action=("Повторить", self.refresh) if self._context.can("sources") else None))

    def show_error(self, reason):
        self._busy = False
        self._error = reason
        self._set_body(source_state("error", "Нет данных", reason, action=("Повторить", self.refresh)))

    def request(self, operation, callback, **kwargs):
        if self._busy:
            return
        self._busy = True
        self._generation += 1
        generation = self._generation

        def done(value):
            if generation == self._generation:
                self._busy = False
                callback(value)

        def failed(reason):
            if generation == self._generation:
                self._busy = False
                self._error = reason
                if operation == "verify":
                    entry = self.snapshot.get("readiness", {}).get(self.provider, {})
                    entry["verified"] = False
                    entry["readiness_state"] = "failed"
                self.render()

        self._context.request("sources", operation=operation, callback=done, on_error=failed,
                              owner=self, **kwargs)

    def refresh(self):
        if not self._context.can("sources"):
            self.show_message(None)
            return
        self.show_message("Читаю состояние источников у ядра…")
        self.request("snapshot", self.loaded)

    def loaded(self, result):
        self.snapshot = result
        self._error = None
        self.render()

    def show_providers(self, readiness):
        providers = (readiness or {}).get("providers") if isinstance(readiness, dict) else None
        if not isinstance(providers, dict) or not providers:
            self.show_message(None)
            return
        self.snapshot = {"readiness": providers, "registry": {pid: {} for pid in providers}}
        self.render()

    def navigate(self, view="list", provider=None):
        self.current_view, self.provider = view, provider
        self._error = None
        self._generation += 1
        self._busy = False
        self.render()
        if view == "detail" and provider == "gsc" and self._context.can("sources"):
            self.request("auth-status", self.auth_loaded, provider=provider)

    def auth_loaded(self, result):
        self.auth = result
        self.render()

    def doctor(self):
        if self._busy:
            return
        self.request("doctor", self.doctor_loaded)
        self.render()

    def doctor_loaded(self, result):
        self._doctor = result.get("providers", {})
        self.snapshot["readiness"] = self._doctor
        self.render()

    def verify(self):
        if self._busy:
            return
        self.request("verify", self.verified, provider=self.provider)
        self.render()

    def verified(self, result):
        entry = self.snapshot.get("readiness", {}).get(self.provider, {})
        entry["verified"] = result.get("verified") is True
        if result.get("verified"):
            entry["readiness_state"] = "verified"
        self.render()

    def render(self):
        if self.current_view != "list":
            self.render_detail()
            return
        providers = {pid: self.snapshot.get("readiness", {}).get(pid, {})
                     for pid in dict.fromkeys((*self.snapshot.get("registry", {}), *self.snapshot.get("readiness", {})))}
        if not providers:
            if self._error:
                self.show_error(self._error)
            else:
                self.show_message(None)
            return
        entries = {pid: describe(pid, entry) for pid, entry in providers.items()}
        total = len(entries)
        unverified = sum(1 for kind, *_ in entries.values() if kind == "info")
        missing = sum(1 for kind, *_ in entries.values() if kind == "warn")
        connected = sum(1 for kind, text, *_ in entries.values() if text == "подключено")
        grouped = {}
        for pid, (kind, text, icon, sub) in entries.items():
            group, name, _mono, paid = CATALOGUE.get(pid, ("Прочие", pid, pid[:2].upper(), False))
            paid = self.snapshot.get("registry", {}).get(pid, {}).get("paid", paid)
            row = self._row(pid, name, paid, kind, text, icon, sub)
            grouped.setdefault(group, []).append(row)
        left_groups, right_groups = GROUP_ORDER[:3], GROUP_ORDER[3:]
        columns = []
        for names in (left_groups, right_groups):
            widgets = []
            for group in names:
                if group in grouped:
                    widgets.append(group_label(group))
                    widgets.extend(grouped[group])
            columns.append(widgets)
        cards = []
        totals = {}
        for units in self.snapshot.get("spend", {}).get("by_source", {}).values():
            for unit, amount in units.items():
                totals[unit] = totals.get(unit, 0) + amount
        spend = joined(" · ", [trf("{value} {unit}", value=Num(amount, 2), unit=unit) for unit, amount in totals.items()]) if totals else "Нет данных"
        measured = all(entry and entry.get("readiness_state") != "unknown" for entry in providers.values())
        for value, label in ((str(connected) if measured else "Нет данных", trf("подключено и проверено из {total}", total=total)),
                             (str(unverified) if measured else "Нет данных", "ключ задан, не проверен"),
                             (str(missing) if measured else "Нет данных", "нужен ключ"), (spend, "платные API · расходы за месяц")):
            cards.append(_kpi(value, label))
        kpis = Summary(cards)
        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.addStretch(1)
        for title, ic, callback in (("Расходы", "payments", lambda: self.navigate("spend")),
                                    ("Агент настраивает приложение", "smart_toy", lambda: self.navigate("agent")),
                                    ("Проверить все", "stethoscope", self.doctor)):
            button = action_button(title, icon=ic, role="primary" if title == "Проверить все" else None, enabled=self._context.can("sources") and not self._busy)
            if title == "Проверить все":
                button.setIcon(material_icon(ic, theming.roles()["on_primary"]))
            button.clicked.connect(callback)
            head_layout.addWidget(button)
        agent_button = head_layout.takeAt(2).widget()
        body = page(head, buttons_row(agent_button), kpis, Columns(*columns),
                    hint("«Проверить все» проверяет локальную конфигурацию; доступ к аккаунтам проверяется отдельно."))
        if self._busy:
            body.layout().addWidget(hint("Читаю состояние источников у ядра…"))
        if self._doctor is not None:
            body.layout().addWidget(group_label("Результат проверки конфигурации"))
            for pid, entry in self._doctor.items():
                kind, text, ic, _sub = describe(pid, entry)
                body.layout().addWidget(list_item(ic, CATALOGUE.get(pid, (None, pid))[1], "", badge(kind, text, ic)))
        if self.snapshot.get("errors"):
            body.layout().addWidget(source_state("partial", "Часть данных недоступна", trf("Недоступные чтения ядра: {commands}", commands=", ".join(self.snapshot["errors"])), action=("Повторить", self.refresh)))
        if self._error:
            body.layout().addWidget(source_state("error", "Нет данных", self._error, action=("Повторить", self.refresh)))
        self._set_body(body)

    def _row(self, pid, name, paid, kind, text, icon, sub):
        title = trf("{name}  ₽ платный", name=name) if paid else name
        button = action_button("Настроить", role="text", size="pill", enabled=self._context.can("sources"))
        button.clicked.connect(lambda: self.navigate("detail", pid))
        return ProviderRow(title, joined(" · ", sub), badge(kind, text, icon), button)

    def render_detail(self):
        back = action_button("Все источники", icon="arrow_back", role="text")
        back.clicked.connect(lambda: self.navigate())
        if self.current_view == "spend":
            body = page(group_label("Расходы платных API"), buttons_row(back), spend_detail(self.snapshot.get("spend")))
        elif self.current_view == "agent":
            body = page(group_label("Агент настраивает приложение"), buttons_row(back), agent_detail())
        else:
            entry = self.snapshot.get("readiness", {}).get(self.provider, {})
            meta = self.snapshot.get("registry", {}).get(self.provider, {})
            components = meta.get("credential_components", list(entry.get("credential_components", {})))
            refs = [r for source in entry.get("credential_sources", {}).values()
                    for r in ([source.get("source_reference")] if source.get("source_reference") else source.get("accepted_source_references", []))]
            references = "\n".join(dict.fromkeys(refs))
            supported = self.provider in VERIFY_PROVIDERS and any(entry.get("credential_components", {}).values()) and not self._busy
            oauth = any(c in components for c in ("oauth_bearer", "durable_oauth"))
            if not components:
                detail = source_state("empty", "Ключ не нужен", "Публичный сервис; авторизация не требуется.")
            else:
                detail = oauth_detail(entry, self.auth if self.provider == "gsc" else {}, self.verify, supported, references) if oauth else key_detail(entry, self.verify, supported, references)
            body = page(group_label(CATALOGUE.get(self.provider, (None, self.provider))[1]), buttons_row(back),
                        Columns([detail], [group_label("Сохранённые данные проекта"), coverage(self.snapshot.get("status", []), self.provider)]))
        if self._busy:
            body.layout().addWidget(hint("Читаю состояние источников у ядра…"))
        if self._error:
            body.layout().addWidget(source_state("error", "Нет данных", self._error))
        self._set_body(body)


def build_page(store, context):
    return SourcesPage(context)
