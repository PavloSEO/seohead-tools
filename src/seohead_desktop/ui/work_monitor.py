"""Bounded native monitoring of supplied observer and owned-queue snapshots."""

import math
from collections.abc import Mapping
from itertools import islice

from PyQt5.QtCore import QEasingCurve, QEvent, Qt, QVariantAnimation, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .components import PageModel, material_icon
from .icons import MaterialIconLabel
from .presentation import (
    ElidedLabel,
    StateBadge,
    field_text,
    run_projection,
    state_text,
    theme_tokens,
    value_text,
)
from .workspace import system_reduced_motion

RUN_LIMIT = 50
EVENT_LIMIT = 20


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _counter(value):
    return value if type(value) is int and value >= 0 else None


def _display(value):
    return "—" if value is None else value_text(value)


def _duration(value):
    measured = _number(value)
    if measured is None:
        return "не измерено"
    seconds = round(measured)
    if seconds >= 3600:
        return f"{seconds // 3600} ч {seconds % 3600 // 60} мин"
    if seconds >= 60:
        return f"{seconds // 60} мин {seconds % 60} с"
    return f"{seconds} с"


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _source(run):
    kind = run.get("source_kind") or run.get("kind")
    mode = _mapping(run.get("collector")).get("mode")
    if kind == "sitemap" or mode in {"sitemap", "sitemap-only"} or run.get("input_mode") == "sitemap":
        return "Sitemap", "account_tree"
    if kind in {"screaming_frog", "sf"} or mode in {"sf_live", "sf_exports"}:
        return "Screaming Frog", "manage_search"
    if kind == "native" or (run.get("owned") and kind in {"crawl", "resume"}):
        return "Native", "travel_explore"
    return "Источник не указан", "help"


def _phase(run):
    return next((item.get("phase") for item in reversed(run.get("events") or []) if item.get("phase")), None)


def _rate(run):
    telemetry = _mapping(run.get("telemetry"))
    if telemetry.get("state") == "fresh" and _number(telemetry.get("current_rate_per_second")) is None:
        return "Не измерено"
    return run_projection(run)["rate"]


def _snapshot(runs):
    """Join only exact supplied IDs; never resolve files or process identities."""
    envelope = _mapping(runs)
    observed = list(islice(iter(envelope.get("items") or [] if envelope else runs or []), RUN_LIMIT + 1))
    owned = list(islice(iter(envelope.get("owned") or []), RUN_LIMIT + 1))
    if any(not isinstance(item, Mapping) for item in observed + owned):
        raise TypeError("Run snapshots must contain mappings")
    owners = {}
    for item in owned[:RUN_LIMIT]:
        identity = item.get("core_run_id") or item.get("observer_run_id")
        if isinstance(identity, str) and identity:
            owners[identity] = dict(item)
    joined = {}
    for item in observed[:RUN_LIMIT]:
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or identity in joined:
            continue
        row = dict(item)
        row["_owned"] = owners.pop(identity, None)
        row["_observed"] = True
        row["events"] = [dict(event) for event in (item.get("events") or [])[-EVENT_LIMIT:] if isinstance(event, Mapping)]
        joined[identity] = row
    queued = [{**item, "id": identity, "_owned": item, "_observed": False, "events": []}
              for identity, item in owners.items()]
    rows = (queued + list(joined.values()))[:RUN_LIMIT]
    truncated = bool(envelope.get("has_more")) or len(observed) > RUN_LIMIT or len(owned) > RUN_LIMIT or len(queued) + len(joined) > RUN_LIMIT
    return rows, truncated


class _RunCard(QPushButton):
    def __init__(self, identity, parent):
        super().__init__(parent)
        self.identity = identity
        self.setObjectName("workRunCard")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self.source = StateBadge()
        self.source.setObjectName("workSource")
        self.state = StateBadge()
        self.source.setWordWrap(True)
        self.state.setWordWrap(True)
        self.source_icon = MaterialIconLabel("history", size=18, parent=self)
        self.title = ElidedLabel()
        self.title.setObjectName("workStage")
        self.counts = QLabel()
        self.counts.setObjectName("workMetricLabel")
        self.activity = QLabel()
        self.activity.setObjectName("metadata")
        self.activity.setWordWrap(True)
        layout.addWidget(self.source, 0, 0)
        layout.addWidget(self.state, 0, 1, Qt.AlignRight)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addWidget(self.source_icon)
        title_row.addWidget(self.title, 1)
        layout.addLayout(title_row, 1, 0, 1, 2)
        layout.addWidget(self.counts, 2, 0, 1, 2)
        layout.addWidget(self.activity, 3, 0, 1, 2)
        for child in (self.source, self.state, self.source_icon, self.title, self.counts, self.activity):
            child.setTextFormat(Qt.PlainText)
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.counts.setWordWrap(True)

    def sizeHint(self):
        return self.layout().sizeHint()

    def set_run(self, run):
        source, icon = _source(run)
        self.source_icon.set_material_icon(icon)
        self.source.set_state("retained")
        self.source.setText(source)
        self.state.set_state(run.get("state", "unknown"))
        collector, counters = _mapping(run.get("collector")), _mapping(run.get("counters"))
        self.title.setText(str(collector.get("origin") or "Запуск") + " · " + self.identity[:8])
        self.counts.setText(f"Получено: {_display(_counter(counters.get('fetched')))} · В очереди: {_display(_counter(counters.get('queued')))}")
        phase = state_text(_phase(run)) if _phase(run) else "Этап не сообщён"
        self.activity.setText(f"{phase} · {_rate(run)}" if run.get("_observed") else "Очередь этого окна · ожидается наблюдение ядра")
        self.setToolTip(f"{source} · {self.identity}\n{self.activity.text()}")
        self.setAccessibleName(f"{self.title.text()}. {source}. {self.state.text()}. {self.counts.text()}. {self.activity.text()}")

    def set_active(self, active):
        self.setChecked(active)
        if self.property("active") != active:
            self.setProperty("active", active)
            self.style().unpolish(self)
            self.style().polish(self)


class WorkMonitor(QWidget):
    """A view of at most 50 runs and the selected run's last 20 events.

    ``runs`` accepts observer rows, or an envelope with ``items``, ``total``,
    ``has_more`` and optional ``owned`` manager-snapshot rows. Owners must supply
    one project only. Signals always carry exact observer/core IDs, including a
    queued owned run's reserved observer ID. Setters never emit selection intents.
    No polling, process control, scan-file reads, core dispatch or implied site score.
    """

    runSelected = pyqtSignal(str)
    showResult = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workMonitor")
        self.setAccessibleName("Монитор запусков и плана проекта")
        self.runs = {}
        self.run_cards = {}
        self.selected_run_id = None
        self._detail_signature = None
        self._columns = 0
        self.system_reduced_motion = system_reduced_motion()
        self.reduced_motion = self.system_reduced_motion
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 0, 4, 0)
        self.body.setSpacing(12)
        self.scroll.setWidget(self.content)
        self.plan = QWidget()
        self.plan.setObjectName("workPlan")
        plan_layout = QVBoxLayout(self.plan)
        plan_layout.setContentsMargins(12, 8, 12, 8)
        self.plan_label = self._label("workStage")
        self.plan_note = self._label("metadata")
        self.plan_progress = QProgressBar()
        self.plan_progress.setRange(0, 1000)
        self.plan_progress.setTextVisible(False)
        self.plan_progress.setAccessibleName("Выполнение согласованных задач плана")
        for widget in (self.plan_label, self.plan_note, self.plan_progress):
            plan_layout.addWidget(widget)
        self.body.addWidget(self.plan)
        self.observation = self._label("metadata")
        self.body.addWidget(self.observation)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QScrollArea.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_widget = QWidget()
        self.cards = QGridLayout(self.cards_widget)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(10)
        self.cards_scroll.setWidget(self.cards_widget)
        self.cards_scroll.viewport().installEventFilter(self)
        self.body.addWidget(self.cards_scroll)
        self.empty = self._label("metadata")
        self.body.addWidget(self.empty)
        self.selected = QWidget()
        self.selected.setObjectName("workSelected")
        selected_layout = QVBoxLayout(self.selected)
        selected_layout.setContentsMargins(12, 8, 12, 8)
        selected_layout.setSpacing(8)
        heading = QHBoxLayout()
        self.selected_title = ElidedLabel()
        self.selected_title.setObjectName("workStage")
        self.selected_badge = StateBadge()
        self.result_button = QPushButton("Открыть результат")
        self.result_button.setIcon(material_icon("open_in_new"))
        self.result_button.clicked.connect(self._show_result)
        heading.addWidget(self.selected_title, 1)
        heading.addWidget(self.selected_badge)
        heading.addWidget(self.result_button)
        selected_layout.addLayout(heading)
        self.phase_label = self._label("workStage")
        selected_layout.addWidget(self.phase_label)
        self.metrics = QWidget()
        self.metrics_layout = QGridLayout(self.metrics)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(16)
        self.metric_values = {}
        self.metric_labels = {}
        for column, (key, title) in enumerate((("fetched", "Получено страниц"), ("queued", "В очереди"), ("inflight", "В работе"), ("excluded", "Исключено"), ("rate", "Сейчас"))):
            label = self._label("workMetricLabel")
            label.setText(title)
            value = self._label("workMetricValue")
            self.metric_labels[key], self.metric_values[key] = label, value
            self.metrics_layout.addWidget(label, 0, column)
            self.metrics_layout.addWidget(value, 1, column)
            self.metrics_layout.setColumnStretch(column, 1)
        selected_layout.addWidget(self.metrics)
        self.effect = QGraphicsOpacityEffect(self.metrics)
        self.metrics.setGraphicsEffect(self.effect)
        self.effect.setOpacity(1.0)
        self.transition = QVariantAnimation(self)
        self.transition.setDuration(theme_tokens()["motion"]["duration_ms"])
        self.transition.setEasingCurve(QEasingCurve.OutCubic)
        self.transition.valueChanged.connect(self.effect.setOpacity)
        self.sample_label = self._label("metadata")
        self.budget_label = self._label("metadata")
        self.queue_label = self._label("metadata")
        for label in (self.sample_label, self.budget_label, self.queue_label):
            selected_layout.addWidget(label)
        self.events_caption = self._label("workMetricLabel")
        selected_layout.addWidget(self.events_caption)
        self.events_model = PageModel((("at", "Время"), ("phase", "Этап"), ("code", "Событие")), self)
        self.events = QTableView()
        self.events.setAccessibleName("Последние двадцать событий выбранного запуска")
        self.events.setModel(self.events_model)
        self.events.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.events.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.events.setSelectionMode(QAbstractItemView.SingleSelection)
        self.events.setAlternatingRowColors(True)
        self.events.setWordWrap(False)
        self.events.setShowGrid(False)
        self.events.verticalHeader().hide()
        self.events.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.events.setMinimumHeight(96)
        self.events.setMaximumHeight(168)
        selected_layout.addWidget(self.events)
        self.body.addWidget(self.selected)
        self.body.addStretch()
        self.set_observation([], {}, None)

    @staticmethod
    def _label(name):
        label = QLabel()
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        return label

    def eventFilter(self, watched, event):
        if watched is self.cards_scroll.viewport() and event.type() == QEvent.Resize:
            self._arrange_cards()
        return super().eventFilter(watched, event)

    def _arrange_cards(self):
        width = self.cards_scroll.viewport().width()
        columns = min(len(self.run_cards) or 1, 4 if width >= 1120 else 3 if width >= 840 else 2 if width >= 540 else 1)
        for column in range(4):
            self.cards.setColumnStretch(column, 0)
        for index, card in enumerate(self.run_cards.values()):
            self.cards.removeWidget(card)
            self.cards.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.cards.setColumnStretch(column, 1)
        self._columns = columns
        rows = (len(self.run_cards) + columns - 1) // columns
        height = max((card.sizeHint().height() for card in self.run_cards.values()), default=120)
        self.cards_scroll.setFixedHeight(min(rows, 2) * (height + 10) + 4)

    def set_reduced_motion(self, enabled):
        self.reduced_motion = bool(enabled) or self.system_reduced_motion
        if self.reduced_motion:
            self.transition.stop()
            self.effect.setOpacity(1.0)

    def set_observation(self, runs, progress, observed_at):
        rows, truncated = _snapshot(runs)
        self.runs = {row["id"]: row for row in rows}
        for identity in set(self.run_cards) - self.runs.keys():
            card = self.run_cards.pop(identity)
            self.cards.removeWidget(card)
            card.hide()
            card.deleteLater()
        ordered = {}
        for row in rows:
            identity = row["id"]
            card = self.run_cards.get(identity)
            if card is None:
                card = _RunCard(identity, self.cards_widget)
                card.clicked.connect(lambda _checked, identity=identity: self._select(identity))
            card.set_run(row)
            ordered[identity] = card
        self.run_cards = ordered
        self.cards_scroll.setVisible(bool(rows))
        self._arrange_cards()
        suffix = " · показаны не все запуски" if truncated else ""
        self.observation.setText(f"Запуски проекта · {len(rows)} в этой выборке{suffix} · Наблюдение ядра: {field_text('observed_at', observed_at)}")
        self.empty.setText("В этом наблюдении запусков нет" if not rows else "Выбранный запуск не входит в текущее наблюдение")
        if self.selected_run_id is None and rows:
            self.selected_run_id = rows[0]["id"]
        self.set_selected_run(self.selected_run_id)
        progress = _mapping(progress)
        completion = _mapping(progress.get("audit_task_completion"))
        done, total = _number(completion.get("numerator")), _number(completion.get("denominator"))
        measured = completion.get("state") == "measured" and done is not None and total is not None and 0 <= done <= total and total > 0
        self.plan_progress.setVisible(measured)
        if measured:
            self.plan_label.setText(f"План аудита · {_display(done)} из {_display(total)} согласованных задач")
            self.plan_progress.setValue(round(1000 * done / total))
            self.plan_progress.setAccessibleDescription(self.plan_label.text())
        else:
            self.plan_label.setText("План аудита · выполнение не измерено")
        counts = _mapping(progress.get("counts"))
        self.plan_note.setText(
            f"Осталось: {_display(_counter(counts.get('remaining')))} · Устарело: {_display(_counter(counts.get('stale')))}"
            if measured else "Для доли выполнения нужен согласованный план с известным числом задач"
        )

    def _select(self, identity):
        self.set_selected_run(identity)
        self.runSelected.emit(identity)

    def set_selected_run(self, identity):
        self.selected_run_id = identity
        for key, card in self.run_cards.items():
            card.set_active(key == identity)
        run = self.runs.get(identity)
        self.empty.setVisible(run is None)
        self.selected.setVisible(run is not None)
        if run is None:
            self.transition.stop()
            self.effect.setOpacity(1.0)
            self._detail_signature = None
            self.events_model.replace([])
            self.result_button.setEnabled(False)
            return
        source, _icon = _source(run)
        telemetry, counters = _mapping(run.get("telemetry")), _mapping(run.get("counters"))
        collector, owned = _mapping(run.get("collector")), _mapping(run.get("_owned"))
        self.selected_title.setText(f"{source} · {collector.get('origin') or 'Запуск'} · {identity[:8]}")
        self.selected_title.setToolTip(identity)
        self.selected_badge.set_state(run.get("state", "unknown"))
        phase = _phase(run)
        self.phase_label.setText("Последний этап по журналу: " + state_text(phase) if phase else "Этап ещё не сообщён")
        values = {key: _display(_counter(counters.get(key))) for key in ("fetched", "queued", "inflight", "excluded")}
        measured_rate = telemetry.get("state") == "fresh" and _number(telemetry.get("current_rate_per_second")) is not None
        values["rate"] = _rate(run) if measured_rate else "—"
        self.metric_labels["rate"].setText("Сейчас" if measured_rate else "Измерение устарело" if telemetry.get("state") == "stale" else "Нет текущей скорости")
        self.metric_labels["fetched"].setText({"pages": "Получено страниц", "sitemap_documents": "Sitemap-документов", "urls_including_resources": "URL и ресурсов"}.get(telemetry.get("unit"), "Получено"))
        for key, value in values.items():
            self.metric_values[key].setText(value)
            self.metric_values[key].setAccessibleName(self.metric_labels[key].text() + ": " + value)
        signature = (identity, tuple(values.items()))
        if self._detail_signature and self._detail_signature[0] == identity and self._detail_signature != signature:
            self.transition.stop()
            if not self.reduced_motion and self.isVisible():
                self.transition.setStartValue(0.65)
                self.transition.setEndValue(1.0)
                self.transition.start()
            else:
                self.effect.setOpacity(1.0)
        elif not self._detail_signature or self._detail_signature[0] != identity:
            self.transition.stop()
            self.effect.setOpacity(1.0)
        self._detail_signature = signature
        self.sample_label.setText(
            f"{state_text(telemetry.get('state', 'unavailable'))} · Измерено: {field_text('sampled_at', telemetry.get('sampled_at'))} · "
            f"Возраст: {_duration(telemetry.get('age_seconds'))} · Окно: {_duration(telemetry.get('rate_window_seconds'))}"
        )
        self.sample_label.setToolTip("Возраст на момент наблюдения ядра.\n" + str(dict(telemetry)))
        limit = _counter(collector.get("max_urls", owned.get("max_urls")))
        rate_limit = _number(collector.get("max_requests_per_second", owned.get("max_urls_per_second")))
        self.budget_label.setText(f"Лимиты запуска · URL: {_display(limit)} · запросов/с: {_display(rate_limit)} · время: {_display(_number(collector.get('max_crawl_seconds')))} с")
        semantics = telemetry.get("queue_semantics")
        self.queue_label.setText(
            "Очередь и URL в работе учтены раздельно · размер сайта не измерен" if semantics == "separate" else
            "Очередь включает URL в работе · размер сайта не измерен" if semantics == "outstanding" else
            "Состав очереди не уточнён · размер сайта не измерен"
        )
        events = run["events"]
        self.events_model.replace([{"at": field_text("at", event.get("at")), "phase": state_text(event.get("phase")), "code": state_text(event.get("code"))} for event in events])
        self.events_caption.setText(f"Последние события · {len(events)} из не более {EVENT_LIMIT}" if events else "События ещё не получены")
        self.events.setVisible(bool(events))
        self.result_button.setEnabled(bool(run.get("_observed") and isinstance(run.get("artifact"), str) and run["artifact"]))
        self.result_button.setAccessibleName("Открыть сохранённый результат запуска " + identity)

    def _show_result(self):
        if self.result_button.isEnabled() and self.selected_run_id in self.runs:
            self.showResult.emit(self.selected_run_id)
