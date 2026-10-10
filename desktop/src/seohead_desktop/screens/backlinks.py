"""External links (canvas BacklinksStates.dc.html): the four states of the «Внешние ссылки» screen.

The core keeps no external link profile yet (#999), so each state is the neutral waiting panel. Nothing is counted,
estimated or sampled here, and the board's sample rows, donor domains and prices are not copied. The screen is registered
as an extra screen; navigation waits for the project overview epic (#984).
"""

from __future__ import annotations

from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..i18n import tr
from ..ui.icons import MaterialIconLabel
from ..ui.kit import StatePanel
from .base import Screen
from .url_widgets import section_label

ISSUE = 999
STATES = (
    ("link_off", "Внешние ссылки проекта", "Профиль ссылок не загружен", "Ядро пока не хранит профиль внешних ссылок: Вебмастер, Bing и выгрузки CSV из приложения недоступны."),
    ("upload_file", "Импорт CSV", "Импорт CSV недоступен", "Сопоставление колонок выгрузки с полями базы появится вместе с хранением ссылок."),
    ("payments", "Оценка стоимости", "Смета пока не считается", "Смета платного запроса считается ядром до запуска. Из приложения её пока нельзя получить."),
    ("report", "Проблемные ссылки", "Список проблемных ссылок недоступен", "Ссылки на 404 и редирект появятся, когда ядро сохранит профиль ссылок проекта."),
)


class BacklinksScreen(Screen):
    slot = ""  # no navigation slot yet, see module docstring
    watches = ()

    def __init__(self, host):
        super().__init__(host)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(section_label("Внешние ссылки"))
        grid = QGridLayout()
        grid.setSpacing(12)
        root.addLayout(grid, 1)
        for index, (icon, caption, title, text) in enumerate(STATES):
            grid.addWidget(self._card(icon, caption, title, text), index // 2, index % 2)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

    def _card(self, icon, caption, title, text):
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(MaterialIconLabel(icon, 16, color="role:text_muted"))
        name = QLabel(tr(caption))
        name.setProperty("text_style", "control")
        row.addWidget(name)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(StatePanel("waiting", title, text, issue=ISSUE), 1)
        return card

    def refresh(self):
        """Static until the core provides the link profile; nothing is re-read from the host."""
