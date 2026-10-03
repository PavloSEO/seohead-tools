"""Render a Metrica traffic document as a static, print-ready HTML dashboard (A4 landscape).

The input is the ``seohead.metrika-traffic/1`` document built by
:func:`seohead.data_sources.metrika_traffic.build_traffic_document`. This module formats it and
nothing else: every number, share and change shown is read from the document, and a block the
document marks ``unavailable`` is drawn as an explicit "data unavailable" panel with its reason,
never as zeros. The page is self-contained: inline CSS and SVG, a system font stack with Cyrillic
coverage, no scripts, and no external resource of any kind, so printing it cannot reach the
network. :mod:`seohead.reports.chromium_pdf` turns the HTML into a PDF.

Branding is data. A :class:`Brand` sets the name, colours, font stack and a short logo text;
labels come from an English or Russian dictionary.
"""

# ruff: noqa: RUF001
# The Russian label dictionary and typographic dashes and minus signs are intentional.

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any

from seohead.data_sources.metrika_traffic import SCHEMA
from seohead.reports.svg_charts import Series, bar_chart, donut_chart, line_chart

PX_PER_MM = 96 / 25.4
DEFAULT_FONT_STACK = (
    '"Segoe UI", "Helvetica Neue", Arial, "Noto Sans", "DejaVu Sans", "Liberation Sans", sans-serif'
)
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FONT_RE = re.compile(r"^[A-Za-z0-9 ,'\"\-]{1,300}$")
_PALETTE = (
    "#0EA5A4",
    "#F59E0B",
    "#E11D48",
    "#7C3AED",
    "#16A34A",
    "#0284C7",
    "#DB2777",
    "#CA8A04",
    "#475569",
    "#9333EA",
)
OTHER_COLOR = "#CBD5E1"
GSC_ROWS_PER_PAGE = 20


@dataclass(frozen=True)
class Brand:
    """Report branding; colours are ``#RRGGBB``."""

    name: str = ""
    accent: str = "#2F5BD3"
    ink: str = "#0F1F3D"
    card: str = "#F5F7FB"
    table_header: str = "#DCE3EE"
    positive: str = "#1E8E3E"
    negative: str = "#C62828"
    font_stack: str = DEFAULT_FONT_STACK
    logo_text: str = ""


def load_brand(value: Any) -> Brand:
    """Build a :class:`Brand` from ``None``, a dict, inline JSON text, or a JSON file path."""
    if value is None or value == "":
        return Brand()
    if isinstance(value, Brand):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                value = json.loads(text)
            except ValueError as exc:
                raise ValueError(f"brand is not valid JSON: {exc}") from None
        else:
            path = Path(text)
            if not path.is_file():
                raise ValueError(f"brand file not found: {text}")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(f"brand file is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("brand must be an object, inline JSON, or a JSON file path")
    known = {f.name for f in fields(Brand)}
    unknown = sorted(set(value) - known)
    if unknown:
        raise ValueError(f"unknown brand keys: {unknown}; expected some of {sorted(known)}")
    brand = replace(Brand(), **value)
    for key in ("accent", "ink", "card", "table_header", "positive", "negative"):
        colour = getattr(brand, key)
        if not isinstance(colour, str) or not _COLOR_RE.match(colour):
            raise ValueError(f"brand.{key} must be a #RRGGBB colour, got {colour!r}")
    if not isinstance(brand.font_stack, str) or not _FONT_RE.match(brand.font_stack):
        raise ValueError("brand.font_stack may contain only font names, quotes, commas and spaces")
    for key in ("name", "logo_text"):
        text = getattr(brand, key)
        if not isinstance(text, str) or len(text) > 80:
            raise ValueError(f"brand.{key} must be text of at most 80 characters")
    return brand


# --- labels ------------------------------------------------------------------

LABELS: dict[str, dict[str, Any]] = {
    "en": {
        "report_title": "Search traffic report",
        "report_title_all": "Website traffic report",
        "summary": "Summary",
        "detail": "Search traffic compared with the previous period",
        "detail_all": "Traffic compared with the previous period",
        "daily": "Search traffic by day",
        "daily_all": "Traffic by day",
        "window": "Search traffic over {months} vs the previous {months}",
        "window_all": "Traffic over {months} vs the previous {months}",
        "geo": "Search traffic by device and city",
        "geo_all": "Traffic by device and city",
        "demo": "Search traffic by age and gender",
        "demo_all": "Traffic by age and gender",
        "pages": "Top landing pages from search",
        "pages_all": "Top landing pages",
        "phrases": "Top search phrases",
        "channels": "Traffic channels",
        "gsc": "Search Console queries",
        "months": {3: "3 months", 6: "6 months", 12: "12 months"},
        "kpi": {
            "users": "Users",
            "visits": "Visits",
            "pageviews": "Pageviews",
            "page_depth": "Pages per visit",
            "visits_per_day": "Visits per day",
            "bounce_rate": "Bounce rate",
            "avg_visit_duration_seconds": "Avg. visit duration",
            "repeat_visitors_2_3_pct": "Visitors with 2–3 visits",
            "new_visitors_pct": "New visitors",
        },
        "dimension": {
            "search_engines": "Search engine",
            "cities": "City",
            "countries": "Country",
            "devices": "Device",
            "age": "Age",
            "gender": "Gender",
            "landing_pages": "Landing page",
            "search_phrases": "Search phrase",
            "channels": "Traffic channel",
        },
        "visits": "Visits",
        "share": "Share",
        "change": "% Δ",
        "total": "Total",
        "other": "Other",
        "not_set": "(not set)",
        "vs_previous": "vs previous period",
        "vs_year": "vs a year ago",
        "current_series": "Visits",
        "previous_series": "Visits, previous {days} days",
        "year_series": "Visits, a year earlier",
        "window_previous_series": "Previous {months}",
        "new": "new",
        "na": "n/a",
        "unavailable": "Data unavailable",
        "key_points": "Key points",
        "method": "Method and data quality",
        "warnings": "Warnings",
        "period": "Reporting period",
        "prepared": "Prepared {date}",
        "data_source": "Data source: Yandex Metrica, counter {ids}",
        "footer_source": "Yandex Metrica · counter {ids}",
        "page_of": "Page {page} of {pages}",
        "attribution": {
            "last_significant": "last significant traffic source",
            "last_click": "last traffic source (last click)",
        },
        "scope": {"organic": "search engine traffic only", "all": "all traffic"},
        "method_attribution": "Attribution: {value}.",
        "method_scope": "Scope: {value}.",
        "method_robots": "Robot visits excluded by Metrica; robot share in this view: {value}.",
        "method_robots_na": "Robot share unavailable: {reason}",
        "method_compare": "Changes compare with {previous} (previous period) and {year} (a year earlier).",
        "method_share": "Shares in breakdowns are of visits with a determined value.",
        "method_gsc_skipped": "Search Console queries: not supplied, so not included.",
        "method_gsc_unavailable": "Search Console queries unavailable: {reason}",
        "warning_text": {
            "robots_high": "Metrica classified {value}% of visits as robots; check the data.",
            "no_visits": "No visits matched the filter in this period; check the counter.",
            "blocks_unavailable": "Not collected: {blocks}. Each block states its reason.",
        },
        "block_names": {
            "summary": "summary",
            "daily": "daily dynamics",
            "windows": "3/6/12-month windows",
            "search_engines": "search engines",
            "cities": "cities",
            "countries": "countries",
            "devices": "devices",
            "age": "age",
            "gender": "gender",
            "landing_pages": "landing pages",
            "search_phrases": "search phrases",
            "channels": "traffic channels",
            "gsc_queries": "Search Console queries",
        },
        "channel_short": {
            "organic": "Search engines",
            "direct": "Direct",
            "referral": "Links on sites",
            "ad": "Ads",
            "social": "Social networks",
            "internal": "Internal",
            "email": "Mailing lists",
            "recommend": "Recommendations",
            "messenger": "Messengers",
            "saved": "Saved pages",
            "undefined": "Not determined",
        },
        "bullet_visits": "Visits: {value} — {prev} vs the previous period, {year} vs a year ago.",
        "bullet_users": "Users: {value} ({prev}); visits per day: {per_day}.",
        "bullet_engine": "Leading search engine: {name} — {value} visits ({share}).",
        "bullet_city": "Leading city: {name} — {value} visits ({share}).",
        "bullet_device": "Leading device type: {name} — {share} of visits.",
        "bullet_page": "Top landing page: {name} — {value} visits.",
        "bullet_window": "Last {months}: {value} visits, {delta} vs the previous {months}.",
        "bullet_channel": "Search engines bring {share} of all visits ({value}).",
        "phrases_note": (
            "Google withholds most search phrases, so this list mainly reflects other "
            "search engines."
        ),
        "gsc_note": (
            "Clicks and impressions in Google Search. They are not Metrica visits and must not "
            "be added to them."
        ),
        "gsc_query": "Query",
        "gsc_clicks": "Clicks",
        "gsc_impressions": "Impressions",
        "gsc_ctr": "CTR",
        "gsc_position": "Position",
        "gsc_totals": "{count} queries · {clicks} clicks · {impressions} impressions",
        "gsc_truncated": "The Search Console response was truncated at its row limit.",
        "comparison_unknown": "n/a — beyond the comparison lookup",
        "months_short": [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
        "months_nominative": None,
        "decimal": ".",
        "group": ",",
        "percent": "{value}%",
        "channel_names": {
            "organic": "Search engine traffic",
            "direct": "Direct traffic",
            "referral": "Link traffic",
            "ad": "Ad traffic",
            "social": "Social network traffic",
            "internal": "Internal traffic",
            "email": "Mailing traffic",
            "recommend": "Recommendation system traffic",
            "messenger": "Messenger traffic",
            "saved": "Saved page traffic",
            "undefined": "Not determined",
        },
        "device_names": {"desktop": "PC", "mobile": "Smartphones", "tablet": "Tablets", "tv": "TV"},
        "gender_names": {"male": "Male", "female": "Female"},
        "age_names": {
            "17": "Under 18",
            "18": "18–24",
            "25": "25–34",
            "35": "35–44",
            "45": "45–54",
            "55": "55 and older",
        },
    },
    "ru": {
        "report_title": "Отчёт по поисковому трафику",
        "report_title_all": "Отчёт по трафику сайта",
        "summary": "Итоги",
        "detail": "Посещаемость из поисковых систем в сравнении с предыдущим периодом",
        "detail_all": "Посещаемость сайта в сравнении с предыдущим периодом",
        "daily": "Посещаемость из поисковых систем по дням",
        "daily_all": "Посещаемость сайта по дням",
        "window": "Посещаемость из ПС за {months} в сравнении с предыдущим периодом",
        "window_all": "Посещаемость сайта за {months} в сравнении с предыдущим периодом",
        "geo": "Трафик из ПС по устройствам и городам",
        "geo_all": "Трафик по устройствам и городам",
        "demo": "Трафик из ПС по возрасту и полу",
        "demo_all": "Трафик по возрасту и полу",
        "pages": "Популярные посадочные страницы из ПС",
        "pages_all": "Популярные посадочные страницы",
        "phrases": "Популярные поисковые фразы",
        "channels": "Каналы трафика",
        "gsc": "Запросы Search Console",
        "months": {3: "3 месяца", 6: "6 месяцев", 12: "12 месяцев"},
        "kpi": {
            "users": "Посетители",
            "visits": "Визиты",
            "pageviews": "Просмотры страниц",
            "page_depth": "Глубина просмотра",
            "visits_per_day": "Визитов в день",
            "bounce_rate": "Отказы",
            "avg_visit_duration_seconds": "Время на сайте",
            "repeat_visitors_2_3_pct": "2–3 визита посетителя",
            "new_visitors_pct": "Доля новых посетителей",
        },
        "dimension": {
            "search_engines": "Поисковая система",
            "cities": "Город",
            "countries": "Страна",
            "devices": "Устройство",
            "age": "Возраст",
            "gender": "Пол",
            "landing_pages": "Страница входа",
            "search_phrases": "Поисковая фраза",
            "channels": "Канал трафика",
        },
        "visits": "Визиты",
        "share": "Доля",
        "change": "% Δ",
        "total": "Общий итог",
        "other": "Другое",
        "not_set": "(не определено)",
        "vs_previous": "к предыдущему периоду",
        "vs_year": "к прошлому году",
        "current_series": "Визиты",
        "previous_series": "Визиты, предыдущие {days} дн.",
        "year_series": "Визиты, год назад",
        "window_previous_series": "Предыдущие {months}",
        "new": "новое",
        "na": "н/д",
        "unavailable": "Нет данных",
        "key_points": "Главное",
        "method": "Методика и качество данных",
        "warnings": "Предупреждения",
        "period": "Отчётный период",
        "prepared": "Подготовлено {date}",
        "data_source": "Источник данных: Яндекс Метрика, счётчик {ids}",
        "footer_source": "Яндекс Метрика · счётчик {ids}",
        "page_of": "Стр. {page} из {pages}",
        "attribution": {
            "last_significant": "последний значимый источник",
            "last_click": "последний источник (последний переход)",
        },
        "scope": {"organic": "только переходы из поисковых систем", "all": "весь трафик"},
        "method_attribution": "Атрибуция: {value}.",
        "method_scope": "Охват: {value}.",
        "method_robots": "Роботы исключены Метрикой; доля роботов в этом срезе: {value}.",
        "method_robots_na": "Доля роботов недоступна: {reason}",
        "method_compare": "Изменения — к периоду {previous} (предыдущий) и {year} (год назад).",
        "method_share": "Доли в разрезах — от визитов с определённым значением.",
        "method_gsc_skipped": "Запросы Search Console не переданы и не включены в отчёт.",
        "method_gsc_unavailable": "Запросы Search Console недоступны: {reason}",
        "warning_text": {
            "robots_high": "Метрика отнесла к роботам {value} % визитов; проверьте данные.",
            "no_visits": "За период нет визитов, подходящих под фильтр; проверьте счётчик.",
            "blocks_unavailable": "Не собраны: {blocks}. Причина указана в каждом блоке.",
        },
        "block_names": {
            "summary": "итоги",
            "daily": "динамика по дням",
            "windows": "окна 3/6/12 месяцев",
            "search_engines": "поисковые системы",
            "cities": "города",
            "countries": "страны",
            "devices": "устройства",
            "age": "возраст",
            "gender": "пол",
            "landing_pages": "посадочные страницы",
            "search_phrases": "поисковые фразы",
            "channels": "каналы трафика",
            "gsc_queries": "запросы Search Console",
        },
        "channel_short": {
            "organic": "Поисковые системы",
            "direct": "Прямые заходы",
            "referral": "Ссылки на сайтах",
            "ad": "Реклама",
            "social": "Социальные сети",
            "internal": "Внутренние переходы",
            "email": "Почтовые рассылки",
            "recommend": "Рекомендательные системы",
            "messenger": "Мессенджеры",
            "saved": "Сохранённые страницы",
            "undefined": "Не определено",
        },
        "bullet_visits": "Визиты: {value} — {prev} к предыдущему периоду, {year} к прошлому году.",
        "bullet_users": "Посетители: {value} ({prev}); визитов в день: {per_day}.",
        "bullet_engine": "Основная поисковая система: {name} — {value} визитов ({share}).",
        "bullet_city": "Основной город: {name} — {value} визитов ({share}).",
        "bullet_device": "Основной тип устройства: {name} — {share} визитов.",
        "bullet_page": "Самая посещаемая страница входа: {name} — {value} визитов.",
        "bullet_window": "За {months}: {value} визитов, {delta} к предыдущим {months}.",
        "bullet_channel": "Поисковые системы дают {share} всех визитов ({value}).",
        "phrases_note": (
            "Google скрывает большинство поисковых фраз, поэтому список отражает в основном "
            "другие поисковые системы."
        ),
        "gsc_note": (
            "Клики и показы в поиске Google. Это не визиты Метрики, складывать их с визитами "
            "нельзя."
        ),
        "gsc_query": "Запрос",
        "gsc_clicks": "Клики",
        "gsc_impressions": "Показы",
        "gsc_ctr": "CTR",
        "gsc_position": "Позиция",
        "gsc_totals": "{count} запросов · {clicks} кликов · {impressions} показов",
        "gsc_truncated": "Ответ Search Console обрезан по лимиту строк.",
        "comparison_unknown": "н/д — за пределами выборки для сравнения",
        "months_short": [
            "янв",
            "фев",
            "мар",
            "апр",
            "мая",
            "июн",
            "июл",
            "авг",
            "сен",
            "окт",
            "ноя",
            "дек",
        ],
        "months_nominative": [
            "янв",
            "фев",
            "мар",
            "апр",
            "май",
            "июн",
            "июл",
            "авг",
            "сен",
            "окт",
            "ноя",
            "дек",
        ],
        "decimal": ",",
        "group": "\u00a0",
        "percent": "{value}\u00a0%",
        "channel_names": {
            "organic": "Переходы из поисковых систем",
            "direct": "Прямые заходы",
            "referral": "Переходы по ссылкам на сайтах",
            "ad": "Переходы по рекламе",
            "social": "Переходы из социальных сетей",
            "internal": "Внутренние переходы",
            "email": "Переходы с почтовых рассылок",
            "recommend": "Переходы из рекомендательных систем",
            "messenger": "Переходы из мессенджеров",
            "saved": "Переходы с сохранённых страниц",
            "undefined": "Не определено",
        },
        "device_names": {
            "desktop": "ПК",
            "mobile": "Смартфоны",
            "tablet": "Планшеты",
            "tv": "ТВ",
        },
        "gender_names": {"male": "мужской", "female": "женский"},
        "age_names": {
            "17": "младше 18 лет",
            "18": "18–24 года",
            "25": "25–34 года",
            "35": "35–44 года",
            "45": "45–54 года",
            "55": "55 лет и старше",
        },
    },
}


class _Fmt:
    """Locale-aware formatting of numbers, percentages, durations and dates."""

    def __init__(self, lang: str):
        self.lang = lang
        self.labels = LABELS[lang]

    def t(self, key: str, scope: str = "organic") -> Any:
        if scope == "all" and f"{key}_all" in self.labels:
            return self.labels[f"{key}_all"]
        return self.labels[key]

    def number(self, value: float | None, digits: int = 0) -> str:
        if value is None:
            return self.labels["na"]
        text = f"{value:,.{digits}f}"
        return (
            text.replace(",", "\x00")
            .replace(".", self.labels["decimal"])
            .replace("\x00", self.labels["group"])
        )

    def share(self, value: float | None) -> str:
        """A share that rounds to zero but is not zero reads as "<0.1%", not "0.0%"."""
        if value is not None and 0 < value < 0.05:
            return "<" + self.pct(0.1)
        return self.pct(value)

    def pct(self, value: float | None, digits: int = 1) -> str:
        if value is None:
            return self.labels["na"]
        return self.labels["percent"].format(value=self.number(value, digits))

    def signed_pct(self, value: float) -> str:
        sign = "+" if value > 0 else ("−" if value < 0 else "")
        return sign + self.pct(abs(value))

    def duration(self, seconds: float | None) -> str:
        if seconds is None:
            return self.labels["na"]
        total = round(seconds)
        return f"{total // 60}:{total % 60:02d}"

    def kpi(self, key: str, value: float | None) -> str:
        if key in ("bounce_rate", "new_visitors_pct", "repeat_visitors_2_3_pct"):
            return self.pct(value)
        if key == "avg_visit_duration_seconds":
            return self.duration(value)
        if key == "page_depth":
            return self.number(value, 2)
        if key == "visits_per_day":
            return self.number(value, 1)
        return self.number(value)

    def day(self, iso: str, *, year: bool = True) -> str:
        day = date.fromisoformat(iso)
        month = self.labels["months_short"][day.month - 1]
        return f"{day.day} {month}" + (f" {day.year}" if year else "")

    def month(self, iso: str) -> str:
        day = date.fromisoformat(iso)
        names = self.labels["months_nominative"] or self.labels["months_short"]
        return f"{names[day.month - 1]} {day.year}"

    def span(self, span: dict[str, Any]) -> str:
        return f"{self.day(span['date1'])} – {self.day(span['date2'])}"


# --- small HTML helpers ------------------------------------------------------


def _mix(colour: str, other: str, weight: float) -> str:
    """Blend ``colour`` toward ``other`` by ``weight`` (0 keeps colour, 1 gives other)."""
    a = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * weight):02X}" for x, y in zip(a, b, strict=True))


def _delta_html(
    fmt: _Fmt, delta: float | None, status: str | None, *, lower_is_better: bool = False
) -> str:
    if status == "new":
        return f'<span class="delta new">{escape(fmt.labels["new"])}</span>'
    if delta is None:
        return f'<span class="delta none">{escape(fmt.labels["na"])}</span>'
    if delta == 0:
        return f'<span class="delta flat">{escape(fmt.pct(0))}</span>'
    up = delta > 0
    good = up != lower_is_better
    arrow = "▲" if up else "▼"
    css = "good" if good else "bad"
    return f'<span class="delta {css}"><i>{arrow}</i>{escape(fmt.signed_pct(delta))}</span>'


def _label_for(fmt: _Fmt, block_key: str, row: dict[str, Any]) -> str:
    names = {
        "channels": fmt.labels["channel_names"],
        "devices": fmt.labels["device_names"],
        "gender": fmt.labels["gender_names"],
        "age": fmt.labels["age_names"],
    }.get(block_key)
    if names and row.get("id") in names:
        return names[row["id"]]
    return row.get("name") or fmt.labels["not_set"]


def _unavailable(fmt: _Fmt, block: dict[str, Any] | None, *, tall: bool = False) -> str:
    reason = (block or {}).get("reason") or fmt.labels["na"]
    css = "unavailable tall" if tall else "unavailable"
    return (
        f'<div class="card {css}"><div class="ua-title">{escape(fmt.labels["unavailable"])}'
        f'</div><div class="ua-reason">{escape(str(reason))}</div></div>'
    )


def _table(
    fmt: _Fmt,
    block_key: str,
    block: dict[str, Any] | None,
    *,
    limit: int,
    numbered: bool = False,
    show_share: bool = False,
    wide_label: bool = False,
    roomy: bool = False,
) -> str:
    if not block or block.get("status") != "ok":
        return _unavailable(fmt, block)
    rows = block["rows"][:limit]
    head = fmt.labels["dimension"][block_key]
    cols = '<col class="c-num">' if numbered else ""
    cols += '<col class="c-label">'
    cols += '<col class="c-val">' + ('<col class="c-share">' if show_share else "")
    cols += '<col class="c-delta">'
    th = '<th class="num idx">#</th>' if numbered else ""
    th += f"<th>{escape(head)}</th>"
    th += f'<th class="num">{escape(fmt.labels["visits"])}</th>'
    if show_share:
        th += f'<th class="num">{escape(fmt.labels["share"])}</th>'
    th += f'<th class="num">{escape(fmt.labels["change"])}</th>'
    body = []
    for index, row in enumerate(rows, 1):
        cells = f'<td class="num idx">{index}.</td>' if numbered else ""
        label = _label_for(fmt, block_key, row)
        cells += f'<td class="label" title="{escape(label)}">{escape(label)}</td>'
        cells += f'<td class="num">{escape(fmt.number(row["visits"]))}</td>'
        if show_share:
            cells += f'<td class="num muted">{escape(fmt.share(row.get("share_pct")))}</td>'
        cells += f'<td class="num">{_delta_html(fmt, row.get("delta_pct"), row.get("delta_status"))}</td>'
        body.append(f"<tr>{cells}</tr>")
    foot = '<td class="num idx"></td>' if numbered else ""
    foot += f"<td>{escape(fmt.labels['total'])}</td>"
    foot += f'<td class="num">{escape(fmt.number(block.get("total")))}</td>'
    if show_share:
        foot += (
            '<td class="num muted">'
            + escape(fmt.pct(100.0 if block.get("total") else None))
            + "</td>"
        )
    foot += (
        '<td class="num">'
        + _delta_html(fmt, block.get("total_delta_pct"), block.get("total_delta_status"))
        + "</td>"
    )
    css = "data" + (" wide" if wide_label else "") + (" roomy" if roomy else "")
    return (
        f'<div class="card table-card"><table class="{css}"><colgroup>{cols}</colgroup>'
        f"<thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody>"
        f"<tfoot><tr>{foot}</tr></tfoot></table></div>"
    )


def _legend(items: list[tuple[str, str]], *, dashed: set[int] | None = None) -> str:
    chips = []
    for index, (colour, label) in enumerate(items):
        style = (
            f"border-top:0.6mm dashed {colour};background:none;height:0"
            if dashed and index in dashed
            else f"background:{colour}"
        )
        chips.append(f'<span class="chip"><i style="{style}"></i>{escape(label)}</span>')
    return f'<div class="legend">{"".join(chips)}</div>'


# --- page renderer -----------------------------------------------------------


class _Renderer:
    def __init__(self, document: dict[str, Any], brand: Brand, lang: str):
        self.doc = document
        self.brand = brand
        self.fmt = _Fmt(lang)
        self.scope = document.get("traffic") or "organic"
        self.blocks = document.get("blocks") or {}
        self.period = document["period"]
        self.ids = ", ".join(document.get("counter_ids") or [])
        self.site = document.get("site_label") or brand.name or ""
        self.series_colors = (
            brand.accent,
            _mix(brand.accent, "#FFFFFF", 0.55),
            "#94A3B8",
        )

    # chart helpers
    def palette(self, count: int) -> list[str]:
        colours = [self.brand.accent, *_PALETTE]
        return [colours[i % len(colours)] for i in range(count)]

    def axis_fmt(self, value: float) -> str:
        return self.fmt.number(value)

    def title(self, key: str, **values: Any) -> str:
        return str(self.fmt.t(key, self.scope)).format(**values)

    def page(self, title: str, body: str, *, period: str | None = None) -> str:
        eyebrow = escape(self.site) if self.site else ""
        span = escape(period or self.fmt.span(self.period))
        return (
            '<section class="page"><header class="page-head"><div class="head-text">'
            + (f'<div class="eyebrow">{eyebrow}</div>' if eyebrow else "")
            + f"<h2>{escape(title)}</h2></div>"
            + f'<div class="period">{span}</div></header>'
            + f'<main class="page-body">{body}</main>'
            + '<footer class="page-foot"><span>'
            + escape(self.fmt.labels["footer_source"].format(ids=self.ids))
            + "</span><span>{PAGE}</span></footer></section>"
        )

    # pages
    def cover(self) -> str:
        fmt = self.fmt
        logo = (
            f'<div class="logo">{escape(self.brand.logo_text)}</div>'
            if self.brand.logo_text
            else ""
        )
        site = f'<div class="cover-site">{escape(self.site)}</div>' if self.site else ""
        generated = self.doc.get("generated_at") or ""
        prepared = ""
        if generated:
            try:
                prepared = fmt.labels["prepared"].format(
                    date=fmt.day(datetime.fromisoformat(generated).date().isoformat())
                )
            except ValueError:
                prepared = ""
        attribution = fmt.labels["attribution"].get(self.doc.get("attribution"), "")
        return (
            '<section class="page cover"><div class="cover-bar"></div>'
            f'<div class="cover-inner">{logo}'
            f"<h1>{escape(self.title('report_title'))}</h1>{site}"
            f'<div class="cover-meta"><div><span>{escape(fmt.labels["period"])}</span>'
            f"<b>{escape(fmt.span(self.period))}</b></div>"
            f"<div>{escape(fmt.labels['data_source'].format(ids=self.ids))}</div>"
            f"<div>{escape(fmt.labels['method_attribution'].format(value=attribution))}</div>"
            f"<div>{escape(prepared)}</div></div></div></section>"
        )

    def summary(self) -> str:
        fmt = self.fmt
        summary = self.blocks.get("summary") or {}
        metrics = summary.get("metrics") or {}
        cards = []
        for key in ("visits", "users", "pageviews", "visits_per_day"):
            cards.append(self.kpi_card(key, metrics.get(key), big=True))
        bullets = self.key_points()
        method = self.method_lines()
        warnings = self.doc.get("warnings") or []
        warn_html = ""
        if warnings:
            items = "".join(f"<li>{escape(self.warning_text(w))}</li>" for w in warnings)
            warn_html = (
                f'<div class="card note warn"><h3>{escape(fmt.labels["warnings"])}</h3>'
                f"<ul>{items}</ul></div>"
            )
        body = (
            f'<div class="kpis four">{"".join(cards)}</div>'
            '<div class="split">'
            f'<div class="card note"><h3>{escape(fmt.labels["key_points"])}</h3>'
            f"<ul>{''.join(f'<li>{b}</li>' for b in bullets)}</ul></div>"
            f'<div class="stack"><div class="card note"><h3>{escape(fmt.labels["method"])}</h3>'
            f"<ul>{''.join(f'<li>{escape(m)}</li>' for m in method)}</ul></div>{warn_html}</div>"
            "</div>"
        )
        return self.page(fmt.labels["summary"], body)

    def key_points(self) -> list[str]:
        fmt = self.fmt
        out: list[str] = []
        metrics = (self.blocks.get("summary") or {}).get("metrics") or {}

        def change(entry: dict, key: str) -> str:
            value = entry.get(f"{key}_delta_pct")
            status = entry.get(f"{key}_delta_status")
            if status == "new":
                return fmt.labels["new"]
            return fmt.labels["na"] if value is None else fmt.signed_pct(value)

        def strong(text: str) -> str:
            return f"<b>{escape(text)}</b>"

        visits = metrics.get("visits")
        if visits and visits.get("status") == "ok":
            out.append(
                escape(fmt.labels["bullet_visits"])
                .replace("{value}", strong(fmt.number(visits["value"])))
                .replace("{prev}", escape(change(visits, "previous")))
                .replace("{year}", escape(change(visits, "year_ago")))
            )
        users = metrics.get("users")
        per_day = metrics.get("visits_per_day")
        if users and users.get("status") == "ok":
            out.append(
                escape(fmt.labels["bullet_users"])
                .replace("{value}", strong(fmt.number(users["value"])))
                .replace("{prev}", escape(change(users, "previous")))
                .replace("{per_day}", escape(fmt.number((per_day or {}).get("value"), 1)))
            )
        for key, template in (
            ("search_engines", "bullet_engine"),
            ("cities", "bullet_city"),
            ("devices", "bullet_device"),
            ("landing_pages", "bullet_page"),
        ):
            block = self.blocks.get(key) or {}
            if block.get("status") != "ok" or not block.get("rows"):
                continue
            row = block["rows"][0]
            out.append(
                escape(fmt.labels[template])
                .replace("{name}", strong(_label_for(fmt, key, row)))
                .replace("{value}", escape(fmt.number(row["visits"])))
                .replace("{share}", escape(fmt.pct(row.get("share_pct"))))
            )
        for item in (self.blocks.get("windows") or {}).get("items") or []:
            if item.get("status") != "ok" or item["months"] not in (3, 12):
                continue
            entry = item["metrics"]["visits"]
            months = fmt.labels["months"][item["months"]]
            delta = (
                fmt.labels["new"]
                if entry.get("delta_status") == "new"
                else (
                    fmt.labels["na"]
                    if entry["delta_pct"] is None
                    else fmt.signed_pct(entry["delta_pct"])
                )
            )
            out.append(
                escape(fmt.labels["bullet_window"])
                .replace("{months}", escape(months))
                .replace("{value}", strong(fmt.number(entry["value"])))
                .replace("{delta}", escape(delta))
            )
        channels = self.blocks.get("channels") or {}
        if channels.get("status") == "ok":
            organic = next((r for r in channels["rows"] if r.get("id") == "organic"), None)
            if organic:
                out.append(
                    escape(fmt.labels["bullet_channel"])
                    .replace("{share}", strong(fmt.pct(organic.get("share_pct"))))
                    .replace("{value}", escape(fmt.number(organic["visits"])))
                )
        return out

    def method_lines(self) -> list[str]:
        fmt = self.fmt
        doc = self.doc
        lines = [
            fmt.labels["method_attribution"].format(
                value=fmt.labels["attribution"].get(doc.get("attribution"), "")
            ),
            fmt.labels["method_scope"].format(value=fmt.labels["scope"].get(self.scope, "")),
        ]
        robots = (doc.get("methodology") or {}).get("robots") or {}
        if robots.get("status") == "ok":
            lines.append(fmt.labels["method_robots"].format(value=fmt.pct(robots.get("robot_pct"))))
        else:
            lines.append(
                fmt.labels["method_robots_na"].format(
                    reason=robots.get("reason") or fmt.labels["na"]
                )
            )
        comparisons = doc.get("comparisons") or {}
        if comparisons.get("previous") and comparisons.get("year_ago"):
            lines.append(
                fmt.labels["method_compare"].format(
                    previous=fmt.span(comparisons["previous"]),
                    year=fmt.span(comparisons["year_ago"]),
                )
            )
        lines.append(fmt.labels["method_share"])
        gsc = self.blocks.get("gsc_queries") or {}
        if gsc.get("status") != "ok":
            key = (
                "method_gsc_skipped" if gsc.get("status") == "skipped" else "method_gsc_unavailable"
            )
            lines.append(fmt.labels[key].format(reason=gsc.get("reason") or fmt.labels["na"]))
        return lines

    def warning_text(self, warning: dict[str, Any]) -> str:
        """Localise a document warning by its code; unknown codes keep the document's text."""
        template = self.fmt.labels["warning_text"].get(warning.get("code"))
        if not template:
            return str(warning.get("message") or "")
        names = self.fmt.labels["block_names"]
        blocks = ", ".join(names.get(b, b) for b in warning.get("blocks") or [])
        value = warning.get("value")
        return template.format(blocks=blocks, value=self.fmt.number(value, 1) if value else "")

    def kpi_card(self, key: str, entry: dict[str, Any] | None, *, big: bool = False) -> str:
        fmt = self.fmt
        label = fmt.labels["kpi"][key]
        css = "card kpi big" if big else "card kpi"
        if not entry or entry.get("status") != "ok":
            reason = (entry or {}).get("reason") or fmt.labels["na"]
            return (
                f'<div class="{css} off"><div class="kpi-label">{escape(label)}</div>'
                f'<div class="kpi-value">—</div><div class="kpi-sub">{escape(fmt.labels["unavailable"])}: '
                f"{escape(str(reason))}</div></div>"
            )
        lower = bool(entry.get("lower_is_better"))
        prev = _delta_html(
            fmt,
            entry.get("previous_delta_pct"),
            entry.get("previous_delta_status"),
            lower_is_better=lower,
        )
        year = _delta_html(
            fmt,
            entry.get("year_ago_delta_pct"),
            entry.get("year_ago_delta_status"),
            lower_is_better=lower,
        )
        return (
            f'<div class="{css}"><div class="kpi-label">{escape(label)}</div>'
            f'<div class="kpi-value">{escape(fmt.kpi(key, entry.get("value")))}</div>'
            f'<div class="kpi-delta">{prev}<span class="vs">{escape(fmt.labels["vs_previous"])}</span></div>'
            f'<div class="kpi-sub">{year}<span class="vs">{escape(fmt.labels["vs_year"])}</span></div></div>'
        )

    def detail(self) -> str:
        summary = self.blocks.get("summary") or {}
        if summary.get("status") != "ok":
            grid = _unavailable(self.fmt, summary, tall=True)
        else:
            metrics = summary["metrics"]
            order = (
                "users",
                "visits",
                "pageviews",
                "page_depth",
                "visits_per_day",
                "bounce_rate",
                "avg_visit_duration_seconds",
                "repeat_visitors_2_3_pct",
                "new_visitors_pct",
            )
            grid = f'<div class="kpis">{"".join(self.kpi_card(k, metrics.get(k)) for k in order)}</div>'
        tables = (
            '<div class="two">'
            + _table(self.fmt, "search_engines", self.blocks.get("search_engines"), limit=7)
            + _table(self.fmt, "cities", self.blocks.get("cities"), limit=7)
            + "</div>"
        )
        return self.page(self.title("detail"), grid + tables)

    def daily(self) -> str:
        fmt = self.fmt
        block = self.blocks.get("daily") or {}
        if block.get("status") != "ok":
            return self.page(self.title("daily"), _unavailable(fmt, block, tall=True))
        points = block["points"]
        one_month = len({p["date"][:7] for p in points}) == 1
        labels = [
            str(date.fromisoformat(p["date"]).day) if one_month else fmt.day(p["date"], year=False)
            for p in points
        ]
        width = 263 * PX_PER_MM
        current = Series(
            fmt.labels["current_series"],
            [p["visits"] for p in points],
            self.series_colors[0],
            width=2.8,
        )
        previous = Series(
            fmt.labels["previous_series"].format(days=len(points)),
            [p.get("previous") for p in points],
            self.series_colors[1],
            labels=False,
        )
        year = Series(
            fmt.labels["year_series"],
            [p.get("year_ago") for p in points],
            self.series_colors[2],
            dashed=True,
            width=1.8,
            labels=False,
        )
        line = line_chart(
            labels,
            [current, previous, year],
            width=width,
            height=58 * PX_PER_MM,
            fmt=fmt.number,
            axis_fmt=self.axis_fmt,
            ink=self.brand.ink,
        )
        bars = bar_chart(
            labels,
            [
                Series(
                    fmt.labels["current_series"], [p["visits"] for p in points], self.brand.accent
                )
            ],
            width=width,
            height=60 * PX_PER_MM,
            fmt=fmt.number,
            axis_fmt=self.axis_fmt,
            ink=self.brand.ink,
        )
        legend = _legend(
            [
                (c, s.name)
                for c, s in zip(self.series_colors, (current, previous, year), strict=True)
            ],
            dashed={2},
        )
        body = (
            f'<div class="card panel"><div class="panel-head">{legend}</div>{line}</div>'
            f'<div class="card panel"><div class="panel-head">'
            f"{_legend([(self.brand.accent, fmt.labels['current_series'])])}</div>{bars}</div>"
        )
        return self.page(self.title("daily"), body)

    def window(self, item: dict[str, Any]) -> str:
        fmt = self.fmt
        months = fmt.labels["months"].get(item["months"], str(item["months"]))
        title = self.title("window", months=months)
        current_span = item.get("current") or self.period
        if item.get("status") != "ok":
            return self.page(
                title, _unavailable(fmt, item, tall=True), period=fmt.span(current_span)
            )
        cards = []
        for key in ("visits", "users", "pageviews"):
            entry = item["metrics"][key]
            cards.append(
                f'<div class="card kpi"><div class="kpi-label">{escape(fmt.labels["kpi"][key])}</div>'
                f'<div class="kpi-value">{escape(fmt.number(entry["value"]))}</div>'
                f'<div class="kpi-delta">{_delta_html(fmt, entry.get("delta_pct"), entry.get("delta_status"))}'
                f'<span class="vs">{escape(fmt.labels["window_previous_series"].format(months=months).lower())}: '
                f"{escape(fmt.number(entry.get('previous')))}</span></div></div>"
            )
        series = item["series"]
        current = series.get("current") or []
        previous = series.get("previous") or []
        width = 263 * PX_PER_MM
        height = 116 * PX_PER_MM
        name_current = fmt.labels["current_series"]
        name_previous = fmt.labels["window_previous_series"].format(months=months)
        if item["granularity"] == "month":
            labels = [fmt.month(p["label"]) for p in current]
            chart = bar_chart(
                labels,
                [
                    Series(name_current, [p["visits"] for p in current], self.series_colors[0]),
                    Series(
                        name_previous,
                        [p["visits"] for p in previous[: len(current)]],
                        self.series_colors[1],
                    ),
                ],
                width=width,
                height=height,
                fmt=fmt.number,
                axis_fmt=self.axis_fmt,
                ink=self.brand.ink,
            )
        else:
            labels = [fmt.day(p["label"], year=False) for p in current]
            chart = line_chart(
                labels,
                [
                    Series(
                        name_current,
                        [p["visits"] for p in current],
                        self.series_colors[0],
                        width=2.4,
                    ),
                    Series(
                        name_previous,
                        [p["visits"] for p in previous],
                        self.series_colors[1],
                        width=1.8,
                        labels=False,
                    ),
                ],
                width=width,
                height=height,
                fmt=fmt.number,
                axis_fmt=self.axis_fmt,
                ink=self.brand.ink,
            )
        legend = _legend(
            [
                (self.series_colors[0], f"{name_current} · {fmt.span(item['current'])}"),
                (self.series_colors[1], f"{name_previous} · {fmt.span(item['previous'])}"),
            ]
        )
        body = (
            f'<div class="kpis three">{"".join(cards)}</div>'
            f'<div class="card panel grow"><div class="panel-head">{legend}</div>{chart}</div>'
        )
        return self.page(title, body, period=fmt.span(current_span))

    def donut_panel(self, block_key: str, *, limit: int = 10, size_mm: float = 58) -> str:
        fmt = self.fmt
        block = self.blocks.get(block_key) or {}
        if block.get("status") != "ok":
            return _unavailable(fmt, block, tall=True)
        rows = block["rows"][:limit]
        values = [row["visits"] for row in rows]
        colours = self.palette(len(rows))
        short = fmt.labels["channel_short"] if block_key == "channels" else {}
        labels = [short.get(row.get("id")) or _label_for(fmt, block_key, row) for row in rows]
        shares = [row.get("share_pct") for row in rows]
        other = (block.get("other") or {}).get("visits") or 0
        if other > 0:
            values.append(other)
            colours.append(OTHER_COLOR)
            labels.append(fmt.labels["other"])
            shares.append((block.get("other") or {}).get("share_pct"))
        svg = donut_chart(
            values,
            colours,
            [fmt.pct(s, 0 if (s or 0) >= 10 else 1) if s is not None else "" for s in shares],
            size=size_mm * PX_PER_MM,
            center_value=fmt.number(block.get("total")),
            center_label=fmt.labels["visits"].lower(),
            ink=self.brand.ink,
        )
        items = "".join(
            f'<li><i style="background:{c}"></i><span class="lg-name">{escape(n)}</span>'
            f'<span class="lg-val">{escape(fmt.share(s))}</span></li>'
            for c, n, s in zip(colours, labels, shares, strict=True)
        )
        title = fmt.labels["dimension"][block_key]
        return (
            f'<div class="card panel donut-panel"><div class="panel-title">{escape(title)}</div>'
            f'<div class="donut-row"><div class="donut" style="width:{size_mm:g}mm">{svg}</div>'
            f'<ul class="donut-legend">{items}</ul></div></div>'
        )

    def geo(self) -> str:
        body = (
            '<div class="two">'
            + self.donut_panel("devices")
            + self.donut_panel("cities")
            + "</div>"
            + '<div class="two">'
            + _table(self.fmt, "search_engines", self.blocks.get("search_engines"), limit=7)
            + _table(self.fmt, "countries", self.blocks.get("countries"), limit=7)
            + "</div>"
        )
        return self.page(self.title("geo"), body)

    def demographics(self) -> str:
        body = (
            '<div class="two">'
            + self.donut_panel("age")
            + self.donut_panel("gender")
            + "</div>"
            + '<div class="two">'
            + _table(self.fmt, "age", self.blocks.get("age"), limit=6, show_share=True)
            + _table(self.fmt, "gender", self.blocks.get("gender"), limit=6, show_share=True)
            + "</div>"
        )
        return self.page(self.title("demo"), body)

    def ranked(self, key: str, title_key: str, *, note: str | None = None) -> str:
        block = self.blocks.get(key) or {}
        table = _table(
            self.fmt, key, block, limit=15, numbered=True, show_share=True, wide_label=True
        )
        extra = f'<div class="footnote">{escape(note)}</div>' if note else ""
        return self.page(self.title(title_key), table + extra)

    def channels(self) -> str:
        fmt = self.fmt
        block = self.blocks.get("channels") or {}
        if block.get("status") != "ok":
            return self.page(fmt.labels["channels"], _unavailable(fmt, block, tall=True))
        table = _table(
            fmt, "channels", block, limit=20, show_share=True, wide_label=True, roomy=True
        )
        body = (
            f'<div class="split"><div>{table}</div>{self.donut_panel("channels", size_mm=48)}</div>'
        )
        return self.page(fmt.labels["channels"], body)

    def gsc_pages(self) -> list[str]:
        fmt = self.fmt
        block = self.blocks.get("gsc_queries") or {}
        if block.get("status") != "ok":
            return []
        rows = block["rows"]
        pages = []
        totals = block.get("totals") or {}
        summary = fmt.labels["gsc_totals"].format(
            count=fmt.number(block.get("count")),
            clicks=fmt.number(totals.get("clicks")),
            impressions=fmt.number(totals.get("impressions")),
        )
        notes = fmt.labels["gsc_note"] + (
            " " + fmt.labels["gsc_truncated"] if block.get("truncated") else ""
        )
        for start in range(0, len(rows), GSC_ROWS_PER_PAGE):
            chunk = rows[start : start + GSC_ROWS_PER_PAGE]
            body_rows = "".join(
                f'<tr><td class="num idx">{start + i}.</td>'
                f'<td class="label" title="{escape(r["query"])}">{escape(r["query"])}</td>'
                f'<td class="num">{escape(fmt.number(r.get("clicks")))}</td>'
                f'<td class="num">{escape(fmt.number(r.get("impressions")))}</td>'
                f'<td class="num">{escape(fmt.pct(None if r.get("ctr") is None else r["ctr"] * 100, 2))}</td>'
                f'<td class="num">{escape(fmt.number(r.get("position"), 1))}</td></tr>'
                for i, r in enumerate(chunk, 1)
            )
            head = (
                '<th class="num idx">#</th>'
                f"<th>{escape(fmt.labels['gsc_query'])}</th>"
                f'<th class="num">{escape(fmt.labels["gsc_clicks"])}</th>'
                f'<th class="num">{escape(fmt.labels["gsc_impressions"])}</th>'
                f'<th class="num">{escape(fmt.labels["gsc_ctr"])}</th>'
                f'<th class="num">{escape(fmt.labels["gsc_position"])}</th>'
            )
            table = (
                '<div class="card table-card"><table class="data wide gsc"><colgroup>'
                '<col class="c-num"><col class="c-label"><col class="c-val"><col class="c-val">'
                '<col class="c-delta"><col class="c-delta"></colgroup>'
                f"<thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table></div>"
            )
            lead = f'<div class="footnote top">{escape(summary)} · {escape(notes)}</div>'
            pages.append(self.page(fmt.labels["gsc"], lead + table))
        return pages

    def render(self) -> str:
        pages = [self.cover(), self.summary(), self.detail(), self.daily()]
        for item in (self.blocks.get("windows") or {}).get("items") or []:
            pages.append(self.window(item))
        windows = self.blocks.get("windows") or {}
        if not windows.get("items"):
            pages.append(
                self.page(
                    self.title("window", months="—"), _unavailable(self.fmt, windows, tall=True)
                )
            )
        pages.append(self.geo())
        pages.append(self.demographics())
        pages.append(self.ranked("landing_pages", "pages"))
        pages.append(self.ranked("search_phrases", "phrases", note=self.fmt.labels["phrases_note"]))
        pages.append(self.channels())
        pages += self.gsc_pages()
        total = len(pages)
        numbered = [
            page.replace(
                "{PAGE}",
                escape(self.fmt.labels["page_of"].format(page=index, pages=total)),
            )
            for index, page in enumerate(pages, 1)
        ]
        title = self.title("report_title") + (f" — {self.site}" if self.site else "")
        return (
            f'<!doctype html><html lang="{self.fmt.lang}"><head><meta charset="utf-8">'
            f"<title>{escape(title)}</title><style>{_css(self.brand)}</style></head>"
            f"<body>{''.join(numbered)}</body></html>"
        )


def _css(brand: Brand) -> str:
    stripe = _mix(brand.card, brand.ink, 0.03)
    border = _mix(brand.table_header, "#FFFFFF", 0.45)
    return f"""
@page {{ size: 297mm 210mm; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #FFFFFF; }}
body {{ font-family: {brand.font_stack}; color: {brand.ink}; font-size: 9pt; line-height: 1.35;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
.page {{ width: 297mm; height: 210mm; padding: 11mm 13mm 8mm; display: flex; flex-direction: column;
  overflow: hidden; position: relative; break-after: page; page-break-after: always;
  background: #FFFFFF; }}
.page:last-child {{ break-after: auto; page-break-after: auto; }}
.page-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8mm;
  margin-bottom: 5mm; flex: none; }}
.eyebrow {{ font-size: 7.5pt; letter-spacing: .09em; text-transform: uppercase; font-weight: 700;
  color: {brand.accent}; margin-bottom: 1.2mm; }}
h2 {{ font-size: 18pt; line-height: 1.15; margin: 0; font-weight: 800; color: {brand.ink};
  max-width: 190mm; }}
.period {{ flex: none; background: #FFFFFF; border-radius: 1.6mm; padding: 2.4mm 4.5mm;
  font-size: 9pt; white-space: nowrap; box-shadow: 0 0.5mm 1.8mm rgba(15,23,42,.18),
  0 0 0 0.2mm rgba(15,23,42,.06); }}
.page-body {{ flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 4.5mm; }}
.page-foot {{ flex: none; display: flex; justify-content: space-between; font-size: 7pt;
  color: #64748B; border-top: 0.25mm solid #E2E8F0; padding-top: 1.8mm; margin-top: 4mm; }}
.card {{ background: #FFFFFF; border-radius: 2.4mm; box-shadow: 0 0.6mm 2.4mm rgba(15,23,42,.10),
  0 0 0 0.2mm rgba(15,23,42,.07); }}
.kpis {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 3.6mm; flex: none; }}
.kpis.four {{ grid-template-columns: repeat(4, 1fr); }}
.kpi {{ padding: 2.8mm 4.4mm 2.6mm; border-left: 1.1mm solid {brand.accent}; min-width: 0; }}
.kpi.off {{ border-left-color: #CBD5E1; }}
.kpi-label {{ font-size: 8pt; color: #64748B; font-weight: 600; }}
.kpi-value {{ font-size: 18pt; font-weight: 750; line-height: 1.15; margin: 0.6mm 0 0.8mm;
  font-variant-numeric: tabular-nums; }}
.kpi.big {{ padding: 4mm 5mm; }}
.kpi.big .kpi-value {{ font-size: 25pt; }}
.kpi-delta, .kpi-sub {{ font-size: 8pt; display: flex; align-items: baseline; gap: 1.6mm;
  white-space: nowrap; overflow: hidden; }}
.kpi-sub {{ margin-top: 0.5mm; opacity: .85; }}
.kpi.off .kpi-sub {{ white-space: normal; color: #64748B; }}
.vs {{ color: #64748B; font-weight: 400; }}
.delta {{ font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.delta i {{ font-style: normal; font-size: 6.5pt; margin-right: 0.8mm; position: relative;
  top: -0.2mm; }}
.delta.good {{ color: {brand.positive}; }}
.delta.bad {{ color: {brand.negative}; }}
.delta.none, .delta.flat {{ color: #94A3B8; font-weight: 600; }}
.delta.new {{ color: {brand.accent}; font-weight: 700; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5mm; min-height: 0; }}
.split {{ display: grid; grid-template-columns: 1.25fr 1fr; gap: 5mm; min-height: 0;
  align-items: start; }}
.stack {{ display: flex; flex-direction: column; gap: 4mm; min-width: 0; }}
.table-card {{ overflow: hidden; align-self: start; }}
table.data {{ width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed;
  font-size: 8.4pt; font-variant-numeric: tabular-nums; }}
.data col.c-num {{ width: 9mm; }}
.data col.c-val {{ width: 22mm; }}
.data col.c-share {{ width: 19mm; }}
.data col.c-delta {{ width: 22mm; }}
.data thead th {{ background: {brand.table_header}; color: {brand.ink}; font-weight: 700;
  text-align: left; padding: 1.9mm 2.6mm; font-size: 8.6pt; }}
.data th.num, .data td.num {{ text-align: right; }}
.data td {{ padding: 1.35mm 2.6mm; border-bottom: 0.25mm solid {border}; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }}
.data tbody tr:nth-child(even) td {{ background: {stripe}; }}
.data td.idx, .data th.idx {{ color: #64748B; padding-right: 1mm; }}
.data td.muted {{ color: #64748B; }}
.data tfoot td {{ font-weight: 750; padding: 1.9mm 2.6mm; border-top: 0.35mm solid {brand.ink};
  border-bottom: none; background: #FFFFFF; }}
.data.wide {{ font-size: 8.8pt; }}
.data.wide td {{ padding-top: 2.05mm; padding-bottom: 2.05mm; }}
.data.roomy td {{ padding-top: 2.7mm; padding-bottom: 2.7mm; font-size: 9.2pt; }}
.data.gsc td {{ padding-top: 1.25mm; padding-bottom: 1.25mm; }}
.panel {{ padding: 3.2mm 4mm 2.6mm; min-width: 0; }}
.panel.grow {{ flex: 1; min-height: 0; }}
.panel svg {{ display: block; width: 100%; height: auto; }}
.panel-head {{ display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 1mm; }}
.panel-title {{ font-size: 9.5pt; font-weight: 700; margin-bottom: 2mm; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 2mm 5mm; font-size: 8.4pt; }}
.chip {{ display: inline-flex; align-items: center; gap: 1.6mm; white-space: nowrap; }}
.chip i {{ display: inline-block; width: 5.5mm; height: 2.4mm; border-radius: 0.6mm; }}
.donut-panel {{ display: flex; flex-direction: column; }}
.donut-row {{ display: flex; align-items: center; gap: 6mm; }}
.donut {{ flex: none; }}
.donut svg {{ display: block; width: 100%; height: auto; }}
.donut-legend {{ list-style: none; margin: 0; padding: 0; flex: 1; min-width: 0;
  font-size: 8.4pt; }}
.donut-legend li {{ display: flex; align-items: center; gap: 2mm; padding: 0.55mm 0;
  border-bottom: 0.2mm solid #F1F5F9; }}
.donut-legend i {{ flex: none; width: 2.8mm; height: 2.8mm; border-radius: 50%; }}
.lg-name {{ flex: 1; min-width: 0; line-height: 1.2; overflow: hidden; }}
.lg-val {{ flex: none; color: #475569; font-variant-numeric: tabular-nums; }}
.note {{ padding: 4mm 5mm; min-width: 0; }}
.note h3 {{ margin: 0 0 2.4mm; font-size: 10.5pt; color: {brand.ink}; }}
.note ul {{ margin: 0; padding-left: 4.5mm; }}
.note li {{ margin: 0 0 1.8mm; font-size: 9pt; }}
.note li::marker {{ color: {brand.accent}; }}
.note.warn {{ border-left: 1.1mm solid {brand.negative}; }}
.unavailable {{ padding: 6mm; color: #475569; background: #F8FAFC;
  border: 0.3mm dashed #CBD5E1; box-shadow: none; }}
.unavailable.tall {{ flex: 1; display: flex; flex-direction: column; justify-content: center;
  align-items: center; text-align: center; }}
.ua-title {{ font-weight: 700; font-size: 11pt; color: {brand.ink}; margin-bottom: 1.5mm; }}
.ua-reason {{ font-size: 8.5pt; }}
.footnote {{ font-size: 7.8pt; color: #64748B; }}
.footnote.top {{ margin-bottom: -1mm; }}
.cover {{ padding: 0; justify-content: center; }}
.cover-bar {{ position: absolute; left: 0; top: 0; bottom: 0; width: 9mm;
  background: {brand.accent}; }}
.cover-inner {{ padding: 0 30mm 0 34mm; }}
.logo {{ display: inline-block; font-weight: 800; letter-spacing: .06em; font-size: 11pt;
  color: #FFFFFF; background: {brand.ink}; padding: 2mm 4mm; border-radius: 1.2mm;
  margin-bottom: 12mm; }}
.cover h1 {{ font-size: 40pt; line-height: 1.08; margin: 0; font-weight: 800;
  color: {brand.ink}; max-width: 220mm; }}
.cover-site {{ font-size: 28pt; font-weight: 800; color: {brand.accent}; margin-top: 6mm; }}
.cover-meta {{ margin-top: 16mm; font-size: 10pt; color: #475569; display: flex;
  flex-direction: column; gap: 2mm; border-top: 0.3mm solid #E2E8F0; padding-top: 6mm;
  max-width: 200mm; }}
.cover-meta span {{ display: block; font-size: 8pt; text-transform: uppercase;
  letter-spacing: .08em; color: #94A3B8; }}
.cover-meta b {{ font-size: 14pt; color: {brand.ink}; }}
"""


def validate_document(document: Any) -> dict[str, Any]:
    """Refuse anything but a ``seohead.metrika-traffic/1`` document with its required parts."""
    if not isinstance(document, dict):
        raise ValueError("the traffic document must be a JSON object")
    if document.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} document (schema: {document.get('schema')!r})")
    period = document.get("period")
    if not isinstance(period, dict) or not {"date1", "date2"} <= set(period):
        raise ValueError("the traffic document has no period")
    if not isinstance(document.get("blocks"), dict):
        raise ValueError("the traffic document has no blocks")
    return document


def render_html(document: dict[str, Any], *, brand: Any = None, lang: str | None = None) -> str:
    """Return the complete HTML report for ``document``; nothing is fetched or calculated."""
    validate_document(document)
    language = lang or document.get("lang") or "en"
    if language not in LABELS:
        raise ValueError(f"lang must be one of {sorted(LABELS)}")
    return _Renderer(document, load_brand(brand), language).render()
