"""«Расписание сканов» (sheet ProjSchedule), the second tab of «Настройки проекта».

The sheet's structure is kept: the schedule list with its columns and the 14-day run history. The core keeps no project
schedules yet (no list, repeat, profile, next or last run, no run history) and tracks it in issue #976, so both blocks show
the neutral «Недоступно в этой версии ядра» state. Nothing is scheduled, started or written here.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..i18n import tr
from ..ui.controls import Note
from ..ui.icons import material_icon
from ..ui.kit import UNAVAILABLE, PageHeader, StatePanel, unavailable_tip, waiting_badge

ISSUE = 976
HINT = "Расписания проекта: список, повтор, профиль, следующий и последний запуск, история запусков"
COLUMNS = ("Название", "Повтор", "Профиль", "Следующий запуск", "Последний", "Вкл.")
COLUMN_STRETCH = (12, 10, 8, 10, 8, 4)  # the sheet's 1.2fr / 200 / 160 / 200 / 120 / 60 proportions, as stretch


def card(title):
    frame = QFrame()
    frame.setProperty("card", "panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    caption = QLabel(tr(title).upper())
    caption.setProperty("text_style", "overline")
    layout.addWidget(caption)
    return frame, layout


class SchedulePage(QWidget):
    """States: the core has no schedules (always, for now). A ready list needs the core model from #976."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self.header = PageHeader("Расписание сканов", "запуски по профилю проекта")
        add = QPushButton(tr("Новое расписание"))
        add.setProperty("role", "primary")
        add.setIcon(material_icon("add", "#FFFFFF"))
        add.setEnabled(False)
        add.setToolTip(unavailable_tip(HINT))
        self.add_button = self.header.add_action(add)
        outer.addWidget(self.header)
        outer.addWidget(Note("info", tr("Расписаний в ядре пока нет."),
                             tr("Ничего не запускается по расписанию. Список и история появятся вместе с расписаниями в ядре."),
                             action=waiting_badge(ISSUE, HINT)))
        schedules, layout = card("Расписания")
        layout.addLayout(self._columns())
        layout.addWidget(StatePanel("waiting", "Расписаний пока нет", "Список расписаний проекта появится, когда ядро начнёт его хранить.",
                                    issue=ISSUE, hint=HINT))
        outer.addWidget(schedules)
        history, layout = card("История запусков · 14 дней")
        row = QHBoxLayout()
        row.setSpacing(10)
        text = QLabel(tr("Запуски по расписанию появятся вместе с расписаниями."))
        text.setProperty("text_style", "meta")
        row.addWidget(text, 1)
        row.addWidget(waiting_badge(ISSUE, HINT))
        layout.addLayout(row)
        outer.addWidget(history)
        outer.addStretch(1)

    @staticmethod
    def _columns():
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        for name, stretch in zip(COLUMNS, COLUMN_STRETCH):
            label = QLabel(tr(name).upper())
            label.setProperty("text_style", "overline")
            row.addWidget(label, stretch)
        return row
