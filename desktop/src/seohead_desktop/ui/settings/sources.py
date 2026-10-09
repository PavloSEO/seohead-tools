"""Settings → Источники данных (sheet SetSources): connections known to the core, never their secrets.

Rows come from the core's ``provider-readiness`` (no network, ``verification_performed`` is false), so
"ключ задан" is never reported as "подключено". OAuth, key entry, verification and spend are step 7.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import i18n
from ...i18n import joined, trf
from .helpers import group_label, page
from .listing import Columns, action_button, badge, hint, list_item, no_data

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
    return "mut", state or "нет данных", "help", sub


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
    layout.addWidget(caption)
    layout.addWidget(number)
    return frame


class SourcesPage(QWidget):
    def __init__(self, context):
        super().__init__()
        self._context = context
        from PyQt5.QtWidgets import QVBoxLayout

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._body = None
        if context.can("providers"):
            self.show_message("Читаю состояние источников у ядра…")
            context.request("providers", callback=self.show_providers, on_error=self.show_error)
        else:
            self.show_message(None)

    def _set_body(self, widget):
        if self._body is not None:
            self._layout.removeWidget(self._body)
            self._body.deleteLater()
        self._body = widget
        i18n.retranslate(widget)
        self._layout.addWidget(widget)

    def show_message(self, text):
        self._set_body(hint(text) if text else no_data())

    def show_error(self, reason):
        self._set_body(hint(trf("Ядро не вернуло состояние источников: {reason}", reason=reason)))

    def show_providers(self, readiness):
        providers = (readiness or {}).get("providers") if isinstance(readiness, dict) else None
        if not isinstance(providers, dict) or not providers:
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
            quota = str(providers[pid].get("quota_mode") or "")
            row = self._row(name, paid or "paid" in quota.lower(), kind, text, icon, sub)
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
        kpis = QWidget()
        kpi_layout = QHBoxLayout(kpis)
        kpi_layout.setContentsMargins(0, 8, 0, 8)
        for value, label in ((str(connected), trf("подключено и проверено из {total}", total=total)), (str(unverified), "ключ задан, не проверен"),
                             (str(missing), "нужен ключ"), ("Нет данных", "платные API · расходы за месяц")):
            kpi_layout.addWidget(_kpi(value, label))
        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.addStretch(1)
        head_layout.addWidget(action_button("Расходы", icon="payments", enabled=False))
        head_layout.addWidget(action_button("Проверить все", icon="stethoscope", role="primary", enabled=False))
        body = page(head, kpis, Columns(*columns),
                    hint("Ядро сообщает только, задан ли ключ: проверка доступа делает сетевые вызовы и выполняется по команде "
                         "«Проверить все» (seohead sources doctor), когда она станет доступна."))
        self._set_body(body)

    @staticmethod
    def _row(name, paid, kind, text, icon, sub):
        title = trf("{name}  ₽ платный", name=name) if paid else name
        return list_item("dns", title, joined(" · ", sub), badge(kind, text, icon), action_button("Настроить", role="text", size="pill", enabled=False))


def build_page(store, context):
    return SourcesPage(context)
