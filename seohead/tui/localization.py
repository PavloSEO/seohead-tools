"""Terminal UI labels; user evidence and command identifiers remain verbatim."""

from __future__ import annotations

# Ruff ambiguous-character detection does not apply to this intentional locale table.
# ruff: noqa: RUF001
import locale
import os

LANGUAGE = "en"


def set_language(code: str) -> None:
    """Select the UI language for this process; tests should monkeypatch LANGUAGE instead."""
    global LANGUAGE
    LANGUAGE = code


RU = {
    "Finalizing": "Завершение",
    "Spider": "Сайт целиком",
    "List": "Список URL",
    "SF scans": "Сканы SF",
    "Mode": "Режим",
    "In flight": "В работе",
    "critical": "критично",
    "warning": "внимание",
    "notice": "замечания",
    "no active workflow": "нет активного сценария",
    "Next": "Далее",
    "rate unavailable": "скорость не измерялась",
    "initialize the checklist and record the agreed audit plan": "создать список задач и записать согласованный план аудита",
    "Finished": "Завершён",
    "Retained": "Сохранённое наблюдение",
    "interactive shell": "интерактивный терминал",
    "PROJECT OBSERVER": "НАБЛЮДЕНИЕ ПРОЕКТА",
    "SEOHEAD  /  PROJECT OBSERVER": "SEOHEAD  /  НАБЛЮДЕНИЕ ПРОЕКТА",
    "READ ONLY  ·  LOCAL": "ТОЛЬКО ЧТЕНИЕ  ·  ЛОКАЛЬНО",
    "NOTE DRAFT  ·  LOCAL": "ЧЕРНОВИК ЗАМЕТКИ  ·  ЛОКАЛЬНО",
    "WORKSPACE": "ПРОЕКТ",
    "Local evidence": "Сохранённые данные",
    "n  Add note": "n  Заметка",
    "g  Propose goal": "g  Предложить цель",
    "Pages retained": "Сохранено страниц",
    "Findings": "Находки",
    "Saved scans": "Сканы",
    "Collection & scope": "Скан сейчас",
    "Scenarios & skills": "Сценарии и методы",
    "Findings & notes": "Проблемы и заметки",
    "Scenarios": "Сценарии",
    "Skills": "Методы",
    "CURRENT WORK": "ЗАДАЧИ В РАБОТЕ",
    "NEXT ACTIONS": "СЛЕДУЮЩИЕ ДЕЙСТВИЯ",
    "FINDINGS BY SEVERITY": "ПРОБЛЕМЫ ПО ВАЖНОСТИ",
    "PROJECT NOTES": "ВХОДЯЩИЕ",
    "COMPETITORS": "КОНКУРЕНТЫ",
    "WORKFLOW": "СЦЕНАРИЙ",
    "PROJECT LOG": "ЖУРНАЛ",
    "No competitors configured": "Конкуренты не указаны",
    "n  Write or dictate a note": "n  Написать заметку",
    "No agent task recorded · 2 opens the complete checklist": "Задач агента нет · 2 открывает все задачи",
    "No project run recorded": "Запусков проекта нет",
    "Not measured": "Не измерялось",
    "None in agreed scope": "Нет в согласованной области",
    "Discovered URL coverage · scope can grow": "Обработано найденных URL · область может расти",
    "Run URL budget · not whole-site completion": "Лимит URL запуска · полнота сайта не измерена",
    "Retained evidence": "Сохранённые данные",
    "Stale sample": "Устаревшее наблюдение",
    "Loading retained project evidence…": "Чтение данных проекта…",
    "Loading retained entries…": "Чтение записей…",
    "Project": "Проект",
    "Compose note": "Заметка",
    "Overview": "Обзор",
    "Tasks": "Задачи",
    "Methods": "Методы",
    "Scans": "Сканы",
    "Views": "Виды",
    "Activity": "Агент",
    "Log": "Журнал",
    "Inbox": "Входящие",
    "Sites": "Сайты",
    "Schema": "Разметка",
    "Not recorded": "Нет данных",
    "Not started": "Не запускался",
    "In progress": "В работе",
    "Complete": "Завершён",
    "Partial": "Частичный",
    "Succeeded": "Успешно",
    "Blocked": "Заблокирован",
    "Failed": "Ошибка",
    "Unavailable": "Недоступно",
    "Native crawler": "Встроенный краулер",
    "Evidence needs refresh": "Данные устарели",
    "Needs review": "Нужно проверить",
    "Ready to start": "Готово к запуску",
    "Evidence recorded": "Данные сохранены",
    "Outside agreed scope": "Вне согласованной области",
    "Not applicable": "Не применяется",
    "Proposed goal": "Предложенная цель",
    "Fresh": "Актуально",
    "Stale": "Устарело",
    "Stopped": "Остановлен",
    "Goal": "Цель",
    "Scan now": "Скан сейчас",
    "Queue": "Очередь",
    "Notes": "Заметки",
    "Competitors": "Конкуренты",
    "Discovered URL coverage": "Обработано найденных URL",
    "Sitemap": "Карта сайта",
    "Errors": "Ошибки",
    "Excluded": "Исключено",
    "No scan recorded": "Сканов нет",
    "No matching scan": "Запуск не найден",
    "Loading…": "Чтение…",
    "unknown": "нет данных",
    "observed active": "наблюдается активный запуск",
    "n Note   g Goal   ? Keys   q Quit": "n Заметка   g Цель   ? Клавиши   q Выход",
    "2 Task details   3 Methods   a Structured data   p Sites\n": "2 Задачи   3 Методы   a Разметка   p Сайты\n",
    "↑ ↓ Browse   Enter Details   PgUp/PgDn Page   f Filter\n": "↑ ↓ Выбор   Enter Детали   PgUp/PgDn Страница   f Фильтр\n",
}


def resolve_language(value: str | None) -> str:
    if value is not None:
        if value not in {"ru", "en"}:
            raise ValueError("language must be ru or en")
        return value
    local = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
        or locale.getlocale()[0]
        or "en"
    )
    return "ru" if local.lower().startswith(("ru", "be")) else "en"


def number(value: int) -> str:
    """Group thousands: ``1,330`` in English, ``1 330`` in Russian."""
    return f"{value:,}".replace(",", " ") if LANGUAGE == "ru" else f"{value:,}"


def ui(value: str) -> str:
    return RU.get(value, value) if LANGUAGE == "ru" else value
