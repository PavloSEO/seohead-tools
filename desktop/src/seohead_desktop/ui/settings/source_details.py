"""Source detail, spend and agent-settings sheets built from existing Qt components."""

from __future__ import annotations

from PyQt5.QtCore import QAbstractTableModel, Qt
from PyQt5.QtWidgets import QAbstractItemView, QHeaderView, QLineEdit, QTableView

from ... import i18n
from ...i18n import Num, tr, trf
from ..controls import SettingRow, Switch
from .helpers import group_label, page
from .listing import Columns, action_button, hint
from .source_layout import Actions, source_state, source_values

OAUTH_GAP, SECRET_GAP, CONFIG_GAP = 964, 965, 966


class _TableModel(QAbstractTableModel):
    def __init__(self, headers, rows, parent):
        super().__init__(parent)
        self.headers, self.rows = headers, rows[:200]
        i18n.signals.changed.connect(self._language_changed)

    def _language_changed(self, _language):
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(self.headers) - 1)
        self.layoutChanged.emit()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.ToolTipRole):
            value = self.rows[index.row()][index.column()]
            return tr("Нет данных") if value is None else i18n.convert(str(value))
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return tr(self.headers[section])


def table(headers, rows):
    if not rows:
        return source_state("empty", "Нет данных")
    view = QTableView()
    view.setModel(_TableModel(headers, rows, view))
    view.setEditTriggers(QAbstractItemView.NoEditTriggers)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    view.verticalHeader().hide()
    view.setMinimumHeight(180)
    view.setAccessibleName("Данные источника")
    return view


def waiting(issue, text):
    return source_state("waiting", "Функция ждёт доработки ядра", text, issue=issue)


def coverage(status, provider=None):
    aliases = {"gsc": "gsc", "ga4": "ga4", "metrika": "metrika", "yandex_webmaster": "webmaster"}
    rows = [e for e in status if provider is None or e.get("source") == aliases.get(provider, provider)]
    if not rows:
        return source_state("empty", "Нет данных", "Сохранённых данных источника в этом проекте нет.")
    return table(("Ресурс", "Данные до", "Строк", "Последняя синхронизация"),
                 [(e.get("resource"), e.get("last_date"), e.get("rows"), e.get("last_fetched_at")) for e in rows])


def key_detail(entry, check, supported, references):
    configured = any(entry.get("credential_components", {}).values())
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setAccessibleName("Новый API-ключ")
    field.setPlaceholderText(tr("Введите новый ключ"))
    # No core write-only contract: do not accept an unsaveable secret into memory.
    field.setEnabled(False)
    field.setToolTip(trf("ждёт #{issue}", issue=SECRET_GAP))
    save = action_button("Сохранить ключ", enabled=False, tooltip=trf("ждёт #{issue}", issue=SECRET_GAP))
    limit = QLineEdit()
    limit.setPlaceholderText(tr("Нет данных"))
    limit.setEnabled(False)
    limit.setAccessibleName("Лимит в месяц")
    limit.setToolTip(trf("ждёт #{issue}", issue=949))
    consent = Switch("Всегда спрашивать перед платным вызовом", True)
    consent.setEnabled(False)
    test = action_button("Тестовый вызов (бесплатно)", icon="stethoscope", enabled=supported,
                         tooltip=None if supported else tr("Проверка недоступна · ждёт #934"))
    test.clicked.connect(check)
    return page(group_label("API-ключ"),
                source_values((("Сохранённый ключ", "задан · не проверен" if configured and not entry.get("verified") else "задан" if configured else "не задан"),
                            ("Проверен", entry.get("verified_at")), ("Где хранится", references or None))),
                SettingRow("Новый API-ключ", "Сохранённый секрет приложение никогда не показывает.", field),
                Actions(save, test), waiting(SECRET_GAP, "Сохранение ключа требует контракта ядра."),
                SettingRow("Лимит в месяц", "Лимиты расходов должны применяться ядром.", limit),
                SettingRow("Всегда спрашивать перед платным вызовом", "Платные вызовы из этого экрана не выполняются.", consent),
                waiting(949, "Месячные лимиты и подтверждение расходов пока недоступны."))


def oauth_detail(entry, auth, check, supported, references):
    state = entry.get("readiness_state")
    titles = {"waiting": "Ждём ответа из браузера…", "expired": "Истёк токен", "revoked": "Доступ отозван",
              "missing": "Не подключено", "not_configured": "Не подключено", "configured_unverified": "Доступ задан · не проверен"}
    title = "Подключено" if entry.get("verified") else titles.get(state, "Нет данных")
    kind = "loading" if state == "waiting" else "error" if state in {"expired", "revoked", "invalid", "failed"} else "empty"
    connect = action_button("Подключить через браузер", icon="open_in_browser", enabled=False,
                            tooltip=trf("ждёт #{issue}", issue=OAUTH_GAP))
    test = action_button("Проверить доступ (бесплатно)", icon="stethoscope", enabled=supported,
                         tooltip=None if supported else tr("Проверка недоступна · ждёт #934"))
    test.clicked.connect(check)
    return page(source_state(kind, title, "Наличие ключа или grant-файла не подтверждает доступ к ресурсам."),
                Actions(connect, test), waiting(OAUTH_GAP, "Ядро импортирует готовый grant-файл; браузерный OAuth ещё недоступен."),
                source_values((("Токен обновления", "задан · не проверен" if auth.get("configured") else None),
                            ("Где хранится", references or None), ("Проверен", entry.get("verified_at")),
                            ("Срок токена", None), ("Права (scopes)", "webmasters.readonly" if auth.get("configured") else None))),
                hint("Секреты и пароли вводятся только владельцем; значения не возвращаются приложению."))


def spend_detail(report):
    if not report or not report.get("calls"):
        return page(source_state("empty", "Нет данных", "В журнале нет расходов за выбранный месяц."),
                    waiting(949, "Месячные лимиты и подтверждение расходов пока недоступны."))
    def rows(mapping):
        return [(name, trf("{value} {unit}", value=Num(amount, 2), unit=unit))
                for name, units in mapping.items() for unit, amount in units.items()]
    return page(hint(trf("С {date} · вызовов: {count}", date=report.get("since"), count=report.get("calls"))),
                hint("Единицы провайдера сохранены как есть; в рубли не пересчитываются."),
                group_label("По провайдерам"), table(("Провайдер", "Расход"), rows(report.get("by_source", {}))),
                group_label("По операциям"), table(("Операция", "Расход"), rows(report.get("by_operation", {}))),
                hint(trf("Вызовов с неизвестной стоимостью: {count}", count=report.get("uncertain_count", 0))),
                waiting(949, "Месячные лимиты и подтверждение расходов пока недоступны."))


def agent_detail():
    rows = []
    for title in ("Вид и тема", "Привязка источников", "Сканы по умолчанию", "Проекты", "Уведомления", "Применять без подтверждения"):
        switch = Switch(title, False)
        switch.setEnabled(False)
        switch.setToolTip(trf("ждёт #{issue}", issue=CONFIG_GAP))
        rows.append(SettingRow(title, "Нет контракта ядра для разрешения этой области.", switch))
    return Columns([group_label("Разрешённые области"), *rows,
                    hint("Ключи, лимиты расходов, удаление данных и права агента закрыты для агента.")],
                   [waiting(CONFIG_GAP, "Изменения агентом требуют прав, ревизии и журнала в ядре."),
                    group_label("Журнал изменений агентом"), source_state("waiting", "Нет данных", issue=CONFIG_GAP)])
