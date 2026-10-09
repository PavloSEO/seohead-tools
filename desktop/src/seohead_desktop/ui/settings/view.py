"""Settings → Вид (sheet SetView). The application reacts to these keys through AppSettings.changed."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ... import theming
from ...i18n import trf
from ...settings_store import Setting
from ..controls import Note, SettingRow
from .helpers import keyed, page, segmented_row, switch_row, two_columns

ID, ICON, TITLE = "view", "palette", "Вид"
HINT = "Тема, язык, плотность и раскладка"

THEME_CHOICES = ("light", "dark", "hc", "system")

SCHEMA = (
    Setting("view.theme", "light", str, choices=THEME_CHOICES),
    Setting("view.language", "ru", str, choices=("ru", "en")),
    Setting("view.density", "standard", str, choices=("compact", "standard", "comfortable")),
    Setting("view.details_position", "bottom", str, choices=("bottom", "right")),
    Setting("view.zoom", 100, int, 80, 150, error="Масштаб — от 80 до 150 %"),
    Setting("view.reduce_motion", False, bool),
    Setting("view.mono_urls", True, bool),
    Setting("view.rail_when_narrow", True, bool),
    Setting("view.status_badges", True, bool),
)


def _theme_picker(store):
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    group = QButtonGroup(box)
    group.setExclusive(True)
    for value, label in (("light", "Светлая"), ("dark", "Тёмная"), ("hc", "Контраст"), ("system", "Как в системе")):
        button = QToolButton()
        button.setProperty("card", "choice")
        button.setCheckable(True)
        button.setText(label)
        button.setFixedSize(116, 76)
        button.setAccessibleName(trf("Тема: {name}", name=label))
        button.setChecked(store.get("view.theme") == value)
        group.addButton(button)
        button.clicked.connect(lambda _c, v=value: store.set("view.theme", v))
        layout.addWidget(button)
    return box


def _zoom_control(store):
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel()

    def refresh():
        label.setText(f"{store.get('view.zoom')} %")

    def step(delta):
        store.set("view.zoom", max(80, min(150, store.get("view.zoom") + delta)))
        refresh()

    from PyQt5.QtWidgets import QPushButton
    down, up = QPushButton("−"), QPushButton("+")
    down.setAccessibleName("Уменьшить масштаб")
    up.setAccessibleName("Увеличить масштаб")
    down.clicked.connect(lambda: step(-10))
    up.clicked.connect(lambda: step(10))
    for widget in (down, label, up):
        layout.addWidget(widget)
    refresh()
    return box


def _preview(store):
    """Row preview that follows density / mono / badge settings."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 12, 0, 0)
    title = QLabel("ПРЕДПРОСМОТР СТРОКИ ТАБЛИЦЫ")
    title.setProperty("text_style", "overline")
    layout.addWidget(title)
    row = QLabel()
    row.setProperty("card", "panel")
    row.setTextFormat(Qt.RichText)
    layout.addWidget(row)

    def refresh(_key=None):
        height = {"compact": 28, "standard": 32, "comfortable": 40}[store.get("view.density")]
        row.setFixedHeight(height + 8)
        badge = "200" if not store.get("view.status_badges") else "<b>200</b>"
        family = theming.substitution()["mono_family"] if store.get("view.mono_urls") else "Roboto"
        row.setText(trf("&nbsp;{badge} &nbsp; <span style=\"font-family:'{family}'\">https://shop.example.test/catalog/chair-oak/</span>"
                        " &nbsp; индексируется · 1,2 с", badge=badge, family=family))

    store.changed.connect(refresh)
    refresh()
    return box


def build_page(store, context):
    themes = keyed(SettingRow("Тема", "Системный «высокий контраст» включает контрастную тему сама", _theme_picker(store)), "view.theme")
    left = [
        segmented_row(store, "view.language", "Язык интерфейса", "Меню, подписи и подсказки. Перезапуск не нужен",
                      [("ru", "Русский"), ("en", "English")]),
        segmented_row(store, "view.density", "Плотность таблиц", "Высота строки 28 / 32 / 40 px",
                      [("compact", "Плотно"), ("standard", "Стандарт"), ("comfortable", "Просторно")]),
        segmented_row(store, "view.details_position", "Детали URL", "Панель выбранного адреса",
                      [("bottom", "Снизу"), ("right", "Справа")]),
        keyed(SettingRow("Масштаб интерфейса", "80–150 %", _zoom_control(store)), "view.zoom"),
    ]
    right = [
        switch_row(store, "view.reduce_motion", "Уменьшить движение", "Отключает анимации панелей, тостов и меню. По умолчанию — как в системе"),
        switch_row(store, "view.mono_urls", "Моноширинный шрифт для URL", "Roboto Mono в колонках адресов и путей"),
        switch_row(store, "view.rail_when_narrow", "Сворачивать навигацию в узком окне", "Меньше 1 100 px — только иконки"),
        switch_row(store, "view.status_badges", "Цветные статусы в таблице", "Плашки статусов вместо текста"),
    ]
    note = Note("info", "Что это меняет.", "Только внешний вид. Данные сканов, фильтры и экспорт не зависят от темы и плотности.")
    return page(themes, two_columns(left, right), _preview(store), note)
