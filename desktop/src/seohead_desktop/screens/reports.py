"""Screen «Отчёт · предпросмотр» (canvas ReportPreview.dc.html): page rail, A4 frame and the export column.

The core builds the report (``seohead report-build``). It does not yet give the application page previews, a PDF
export or a language choice for the report (#928), so the page shows an honest waiting state and no sample text.
Without an open project the frame says «Откройте проект». The rail and the controls keep the sheet's layout and are
disabled with the neutral badge until the core gives the data.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout

from .. import theming
from ..i18n import tr
from ..ui.controls import KeyValue, Note, Segmented
from ..ui.icons import material_icon
from ..ui.kit import Gate, StatePanel, waiting_badge
from .base import Screen

ISSUE = 928
HINT = "Предпросмотр страниц отчёта и выгрузка PDF из приложения"
# Page names of the sheet's rail. They describe the report structure, not data.
PAGES = ("Резюме", "Проблемы", "Задачи", "Методика")
PAGE_SIZE = (500, 707)  # A4 proportions of the sheet


class ReportsScreen(Screen):
    slot = "reports"
    watches = ("project",)

    def __init__(self, host):
        super().__init__(host)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setProperty("page_bar", True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 8, 16, 8)
        title = QLabel(tr("Отчёт · предпросмотр"))
        title.setProperty("text_style", "title")
        bar_layout.addWidget(title)
        bar_layout.addStretch(1)
        root.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._rail())
        body.addWidget(self._canvas(), 1)
        body.addWidget(self._aside())
        root.addLayout(body, 1)

    def _rail(self):
        rail = QFrame()
        rail.setProperty("report_rail", True)
        rail.setFixedWidth(140)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(4)
        for index, name in enumerate(PAGES, 1):
            button = QToolButton()
            button.setText(f"{index} · {tr(name)}")
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setEnabled(False)
            button.setToolTip(f"{tr(name)} · {tr('Недоступно в этой версии ядра')}")
            button.setAccessibleName(tr("Страница {n}").format(n=index))
            button.setProperty("report_thumb", True)
            layout.addWidget(button, 0, Qt.AlignHCenter)
        layout.addStretch(1)
        return rail

    def _canvas(self):
        canvas = QFrame()
        canvas.setProperty("report_canvas", True)
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(20, 20, 20, 20)
        page = QFrame()
        page.setProperty("report_page", True)
        page.setMaximumSize(*PAGE_SIZE)
        page.setMinimumWidth(300)
        page.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(24, 24, 24, 24)
        waiting = StatePanel("waiting", "Предпросмотр страниц ждёт ядро",
                             "Отчёт строится командой seohead report-build. Страницы в приложении пока не показываются.",
                             issue=ISSUE, hint=HINT)
        self.gate = Gate(waiting, lambda: "content" if self.project_open else "open", self.host.choose_project)
        page_layout.addWidget(self.gate)
        layout.addWidget(page, 1, Qt.AlignHCenter)
        return canvas

    def _aside(self):
        aside = QFrame()
        aside.setProperty("report_aside", True)
        aside.setFixedWidth(300)
        layout = QVBoxLayout(aside)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        heading = QLabel(tr("Экспорт отчёта"))
        heading.setProperty("text_style", "section")
        layout.addWidget(heading)

        language_box = QVBoxLayout()
        language_box.setSpacing(6)
        caption = QLabel(tr("Язык"))
        caption.setProperty("text_style", "meta")
        language_box.addWidget(caption)
        self.language = Segmented([("ru", "Русский"), ("en", "English")], "ru", tr("Язык отчёта"))
        self.language.setEnabled(False)
        self.language.setToolTip(tr("Язык отчёта · Недоступно в этой версии ядра"))
        language_box.addWidget(self.language)
        layout.addLayout(language_box)

        self.facts = KeyValue([(tr("Скан"), None), (tr("Формат"), None)])
        layout.addWidget(self.facts)
        layout.addWidget(Note("info", tr("Отчёт строит ядро."), tr("Тот же файл получится из CLI: seohead report-build.")))
        layout.addStretch(1)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.addWidget(waiting_badge(ISSUE, HINT), 0, Qt.AlignVCenter)
        badge_row.addStretch(1)
        layout.addLayout(badge_row)
        self.export = QPushButton(tr("Сохранить PDF"))
        self.export.setProperty("role", "primary")
        self.export.setIcon(material_icon("picture_as_pdf", theming.roles()["on_primary"]))
        self.export.setEnabled(False)
        self.export.setToolTip(tr("Сохранить PDF") + " · " + tr("Недоступно в этой версии ядра"))
        layout.addWidget(self.export)
        return aside

    def refresh(self):
        self.gate.refresh()
