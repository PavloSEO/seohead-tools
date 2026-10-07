"""Small, bounded display helpers; all values remain owned by the core."""

import json
from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from itertools import islice
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QSizePolicy


@lru_cache(maxsize=1)
def theme_tokens():
    return json.loads((Path(__file__).parents[1] / "theme/tokens.json").read_text())


STATES = {
    "not_initialized": "План не настроен", "unknown": "Неизвестно",
    "not_agreed": "Не согласовано", "remaining": "Ожидает выполнения",
    "started": "Начало запуска", "entered": "Вход в этап", "progress": "Обновление счётчиков",
    "running": "Выполняется", "starting": "Запускается", "queued": "В очереди",
    "completed": "Выполнено", "complete": "Полные данные", "finished": "Завершён",
    "blocked": "Заблокировано", "stale": "Устарело", "unavailable": "Недоступно",
    "review": "Нужна проверка", "deliverable": "Результат подготовлен",
    "excluded": "Исключено", "partial": "Частичный результат",
    "interrupted": "Прерван", "failed": "Ошибка", "error": "Ошибка",
    "stop_requested": "Остановка запрошена", "cancel_requested": "Отмена запрошена",
    "cancelled": "Отменён", "cancelled_before_start": "Отменён до старта",
    "awaiting_core_status": "Ожидание статуса ядра", "status_unavailable": "Статус не подтверждён",
    "rejected": "Отклонено", "available": "Доступно", "fresh": "Актуально",
    "retained": "Сохранённое измерение", "idle": "Нет активной работы",
    "not_requested": "Не запрашивалось", "unsupported": "Не поддерживается",
    "proposed": "Предложено", "accepted": "Принято", "acknowledged": "Обработка подтверждена",
    "read": "Прочитано", "unread": "Не прочитано", "triaged": "Разобрано",
    "measured": "Измерено", "native": "Native", "sitemap": "Sitemap", "sf": "Screaming Frog",
    "crawl": "Новый скан", "resume": "Продолжение", "raw": "Исходный HTML", "js": "JavaScript",
    "admission": "Проверка лимитов", "collection": "Сбор", "analysis": "Анализ",
    "finalizing": "Сохранение результата", "note": "Заметка", "question": "Вопрос",
    "proposed_goal": "Предложенная цель", "specialist": "Специалист", "agent": "Агент",
    "primary": "Основной сайт", "competitor": "Конкурент", "check": "Проверка",
    "custom": "Задача", "method": "Метод", "scope": "Объём работ",
    "Indexable": "Индексируется", "Non-indexable": "Не индексируется", "Redirect": "Редирект",
}

FIELDS = {
    "id": "ID", "uuid": "ID скана", "scan_uuid": "ID скана", "project_uuid": "ID проекта",
    "title": "Title", "text": "Текст", "state": "Состояние", "display_state": "Состояние",
    "reason": "Причина", "status_reason": "Причина статуса", "kind": "Тип", "scope": "Объём",
    "url": "URL", "start_url": "Начальный URL", "target": "Цель", "path": "Файл",
    "artifact": "Сохранённый результат", "lifecycle": "Состояние скана", "source_kind": "Источник",
    "created_at": "Создано", "started_at": "Начало", "finished_at": "Завершено",
    "observed_at": "Наблюдение", "sampled_at": "Измерение", "updated_at": "Обновлено",
    "status_code": "HTTP", "status": "HTTP", "content_type": "Тип содержимого", "type": "Тип",
    "indexability": "Индексация", "indexable": "Индексируется", "meta_description": "Description",
    "h1": "H1", "h2": "H2", "canonical": "Canonical", "meta_robots": "Meta robots",
    "x_robots": "X-Robots-Tag", "response_time": "Время ответа, с", "size_bytes": "Размер, байт",
    "word_count": "Слов", "crawl_depth": "Глубина", "outlinks": "Исходящих ссылок",
    "external_outlinks": "Внешних ссылок", "representation": "Представление",
    "final_url": "Конечный URL", "redirect_url": "Редирект", "url_id": "ID URL",
    "document_id": "ID документа", "evidence_revision": "Ревизия данных",
    "format_version": "Формат", "writer_version": "Версия ядра", "writer_revision": "Сборка ядра",
    "crawl_partial": "Частичный сбор", "corpus_partial": "Частичный корпус",
    "finish_reason": "Причина завершения", "source": "Источник", "config_fingerprint": "Конфигурация",
    "revision": "Ревизия", "priority": "Приоритет", "next_action": "Следующий шаг",
    "action": "Действие", "definition": "Определение", "evidence": "Доказательства",
    "consumer": "Получатель", "author_role": "Автор", "goal_state": "Состояние цели",
    "task": "Задача", "item": "Пункт", "events": "События", "phase": "Этап",
    "code": "Событие", "at": "Время", "complete": "Завершено", "remaining": "Осталось",
    "stale": "Устарело", "total": "Всего", "done": "Обработано", "fetched": "Получено",
    "queued": "В очереди", "inflight": "В работе", "excluded": "Исключено", "failed": "С ошибкой",
    "max_urls": "Лимит URL", "max_requests": "Лимит HTTP-запросов",
    "max_crawl_seconds": "Лимит времени, с", "max_requests_per_second": "Лимит запросов/с",
    "rate_per_second": "Последняя измеренная скорость", "rate_window_seconds": "Окно измерения, с",
    "age_seconds": "Возраст измерения, с", "unit": "Единица", "telemetry": "Измерения",
    "controller": "Контроллер", "collector": "Сборщик", "counters": "Счётчики",
    "core_state": "Состояние ядра", "core_run_id": "ID запуска ядра", "observer_run_id": "ID наблюдения",
    "rendering_mode": "Режим", "owned": "Запущен этим окном", "resume_path": "Продолжение из файла",
    "offset": "Смещение", "has_more": "Есть следующая страница", "bytes": "Размер ответа, байт",
    "truncated": "Ответ ограничен", "next_offset": "Следующее смещение",
}
PANEL_LABELS = {
    "internal": "Внутренние", "external": "Внешние", "security": "Безопасность",
    "response_codes": "Ответы", "page_titles": "Title", "meta_description": "Description",
    "meta_keywords": "Keywords", "content": "Контент", "images": "Изображения",
    "canonicals": "Canonical", "pagination": "Пагинация", "directives": "Директивы",
    "links": "Ссылки", "structured_data": "Разметка", "sitemaps": "Sitemap",
    "validation": "Валидация", "url_details": "Сведения", "inlinks": "Входящие",
    "outlinks": "Исходящие", "image_details": "Изображение", "resources": "Ресурсы",
    "serp_snippet": "Сниппет", "view_source": "Исходный HTML", "http_headers": "HTTP headers",
    "duplicate_details": "Дубликаты", "structured_data_details": "Детали разметки",
    "spelling_grammar_details": "Орфография", "overview": "Сводка", "issues": "Проблемы",
    "site_structure": "Структура", "response_times": "Время ответа", "spelling_grammar": "Орфография",
}
COLUMN_LABELS = {
    "Address": "URL", "Content Type": "Тип", "Status Code": "HTTP", "Status": "Статус",
    "Indexability": "Индексация", "Indexability Status": "Причина", "Title 1": "Title",
    "Findings": "Проблемы", "Crawl Depth": "Глубина", "Inlinks": "Входящие", "Outlinks": "Исходящие",
    "Name": "Поле", "Value": "Значение", "Category / filter": "Категория", "% of Total": "% всего",
    "Issue Name": "Проблема", "Issue Type": "Тип", "Issue Priority": "Приоритет",
    "From": "Откуда", "To": "Куда", "Anchor Text": "Анкор", "Type": "Тип",
    "State": "Состояние", "Reason": "Причина", "Observed at": "Наблюдение",
}


def panel_title(spec):
    return PANEL_LABELS.get(spec.id, spec.title)


STATE_FIELDS = {"state", "display_state", "lifecycle", "kind", "source_kind", "goal_state", "author_role", "phase", "core_state", "rendering_mode", "finish_reason", "code"}


def state_text(value):
    return STATES.get(value, str(value)) if isinstance(value, str) else value_text(value)


def value_text(value):
    if value is None:
        return "Не измерено"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if value == "":
        return "Пустое значение"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (tuple, list)):
        return ", ".join(value_text(item) for item in value[:8]) if value else "Нет записей"
    if isinstance(value, Mapping):
        return "; ".join(f"{FIELDS.get(key, key)}: {value_text(item)}" for key, item in islice(value.items(), 8)) or "Нет полей"
    return str(value)


def field_text(key, value):
    if key in STATE_FIELDS:
        return state_text(value)
    if (key.endswith("_at") or key == "at") and isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return stamp.astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
        except ValueError:
            pass
    return value_text(value)


def readable_record(record, *, heading="", max_lines=100):
    """Readable bounded metadata, not another persistence or inference layer."""
    lines = [heading, ""] if heading else []
    def visit(value, depth=0):
        if len(lines) >= max_lines:
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                if len(lines) >= max_lines:
                    break
                if key == "ok":
                    continue
                name = FIELDS.get(key, key.replace("_", " "))
                if isinstance(item, (Mapping, list, tuple)) and item and depth < 2:
                    lines.append(f"{'  ' * depth}{name}")
                    visit(item, depth + 1)
                    if depth == 0:
                        lines.append("")
                else:
                    lines.append(f"{'  ' * depth}{name}: {field_text(key, item)[:700]}")
        elif isinstance(value, (list, tuple)):
            for item in value[:20]:
                visit(item, depth)
        else:
            lines.append("  " * depth + value_text(value)[:700])
    visit(record)
    if len(lines) >= max_lines:
        lines.append("… Показана ограниченная часть метаданных.")
    return "\n".join(lines)[:24000]


def run_projection(run):
    counters = run.get("counters") or {}
    telemetry = run.get("telemetry") or {}
    collector = run.get("collector") or {}
    rate = telemetry.get("current_rate_per_second")
    units = {"pages": "стр./с", "sitemap_documents": "sitemap/с", "urls_including_resources": "URL/с"}
    rate_text = "Не измерено"
    if telemetry.get("state") == "fresh" and isinstance(rate, (float, int)) and not isinstance(rate, bool):
        rate_text = f"{value_text(rate)} {units.get(telemetry.get('unit'), '/с')}"
    elif telemetry.get("state") == "retained":
        rate_text = "Нет текущей"
    elif telemetry.get("state") == "stale":
        rate_text = "Устарело"
    return {
        "id": run.get("id"), "kind": run.get("kind"), "state": run.get("state"),
        "phase": (run.get("events") or [{}])[-1].get("phase"),
        "fetched": counters.get("fetched"), "queued": counters.get("queued"),
        "inflight": counters.get("inflight"), "rate": rate_text,
        "rate_limit": collector.get("max_requests_per_second"),
        "sampled_at": telemetry.get("sampled_at"), "_run": run,
    }


class ElidedLabel(QLabel):
    """Single-line context stays readable without widening the window."""
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self.setAccessibleName(self._full_text)
        self._elide()

    def text(self):
        return self._full_text

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle, max(40, self.width() - 8)))

    def resizeEvent(self, event):
        self._elide()
        super().resizeEvent(event)


class StateBadge(QLabel):
    def __init__(self, text="Не измерено", parent=None):
        super().__init__(parent)
        self.setObjectName("stateBadge")
        self.setTextFormat(Qt.PlainText)
        self.set_state(text)

    def set_state(self, state):
        self.setText(state_text(state))
        tone = "warning" if state in {"partial", "interrupted", "stale", "blocked", "review", "not_initialized", "status_unavailable"} else "error" if state in {"failed", "error", "rejected"} else "active" if state in {"running", "starting", "queued"} else "neutral"
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setAccessibleName("Состояние: " + self.text())
