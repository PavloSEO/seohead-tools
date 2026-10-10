"""Settings → Вид (sheet SetView). The application reacts to these keys through AppSettings.changed."""

from __future__ import annotations

from PyQt5.QtCore import QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import i18n, theming
from ...i18n import tr, trf
from ...settings_store import Setting
from ..controls import Note, SettingRow
from .helpers import column_stack, keyed, page, segmented_row, switch_row

ID, ICON, TITLE = "view", "palette", "Вид"
HINT = "Тема, язык, плотность и раскладка"

THEME_CHOICES = ("light", "dark", "hc", "system")
CARD_W, CARD_H, SAMPLE_H = 152, 142, 84
THEME_NOTES = {"light": "по умолчанию", "dark": "вечером и в презентациях", "hc": "доступность"}

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


SWATCH = QSize(112, 58)


def _swatch(value):
    """Miniature of a theme (nav, accent bar, text lines) painted from that theme's own role tokens."""
    light, dark, hc = theming.roles("light"), theming.roles("dark"), theming.roles("hc")
    bg, nav, accent, line = {
        "light": (light["surface"], light["raised"], light["primary"], light["divider_inner"]),
        "dark": (dark["base"], dark["raised"], dark["primary"], dark["divider_inner"]),
        "hc": (hc["surface"], hc["raised"], hc["primary"], hc["text"]),
        "system": (light["surface"], dark["raised"], light["primary"], light["outline"]),
    }[value]
    pixmap = QPixmap(SWATCH)
    pixmap.fill(QColor(bg))
    painter = QPainter(pixmap)
    painter.setPen(Qt.NoPen)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(nav))
    painter.drawRoundedRect(QRectF(7, 7, 18, 44), 3, 3)
    painter.setBrush(QColor(accent))
    painter.drawRoundedRect(QRectF(30, 7, 41, 7), 2, 2)
    painter.setBrush(QColor(line))
    for y, width in ((19, 75), (29, 75), (39, 52)):
        painter.drawRoundedRect(QRectF(30, y, width, 5), 2, 2)
    painter.end()
    return QIcon(pixmap)


def _theme_picker(store):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    group = QButtonGroup(box)
    group.setExclusive(True)
    for value, label in (("light", "Светлая"), ("dark", "Тёмная"), ("hc", "Контраст"), ("system", "Как в системе")):
        button = QToolButton()
        button.setProperty("card", "choice")
        button.setCheckable(True)
        button.setText(label)
        button.setIcon(_swatch(value))
        button.setIconSize(SWATCH)
        button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        button.setFixedSize(SWATCH.width() + 4, SWATCH.height() + 34)
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

    down, up = QPushButton("−"), QPushButton("+")
    down.setAccessibleName("Уменьшить масштаб")
    up.setAccessibleName("Увеличить масштаб")
    slider = QSlider(Qt.Horizontal)
    slider.setRange(80, 150)
    slider.setSingleStep(10)
    slider.setPageStep(10)
    slider.setFixedWidth(110)
    slider.setAccessibleName("Масштаб интерфейса")
    slider.valueChanged.connect(lambda value: store.set("view.zoom", value))
    down.clicked.connect(lambda: step(-10))
    up.clicked.connect(lambda: step(10))
    for widget in (down, slider, up):
        layout.addWidget(widget)
    layout.addSpacing(8)
    layout.addWidget(label)

    def sync_slider(_key=None):
        slider.blockSignals(True)
        slider.setValue(store.get("view.zoom"))
        slider.blockSignals(False)
        refresh()

    store.changed.connect(sync_slider)
    sync_slider()
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
    themes = keyed(
        SettingRow(
            "Тема",
            "Системный «высокий контраст» включает контрастную тему сама",
            _theme_picker(store),
        ),
        "view.theme",
    )
    rows = [
        segmented_row(
            store,
            "view.language",
            "Язык интерфейса",
            "Меню, подписи и подсказки. Перезапуск не нужен",
            [("ru", "Русский"), ("en", "English")],
        ),
        segmented_row(
            store,
            "view.density",
            "Плотность таблиц",
            "Высота строки 28 / 32 / 40 px",
            [("compact", "Плотно"), ("standard", "Стандарт"), ("comfortable", "Просторно")],
        ),
        segmented_row(
            store,
            "view.details_position",
            "Детали URL",
            "Панель выбранного адреса",
            [("bottom", "Снизу"), ("right", "Справа")],
        ),
        keyed(SettingRow("Масштаб интерфейса", "80–150 %", _zoom_control(store)), "view.zoom"),
        switch_row(
            store,
            "view.reduce_motion",
            "Уменьшить движение",
            "Отключает анимации панелей, тостов и меню. По умолчанию — как в системе",
        ),
        switch_row(
            store,
            "view.mono_urls",
            "Моноширинный шрифт для URL",
            "Roboto Mono в колонках адресов и путей",
        ),
        switch_row(
            store,
            "view.rail_when_narrow",
            "Сворачивать навигацию в узком окне",
            "Меньше 900 px — только иконки",
        ),
        switch_row(
            store,
            "view.status_badges",
            "Цветные статусы в таблице",
            "Плашки статусов вместо текста",
        ),
    ]
    note = Note(
        "info",
        "Что это меняет.",
        "Только внешний вид. Данные сканов, фильтры и экспорт не зависят от темы и плотности.",
    )
    return page(themes, column_stack(rows[:4], rows[4:]), _preview(store), note)
