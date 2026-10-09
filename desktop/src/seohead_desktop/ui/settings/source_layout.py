"""Responsive layouts for the source sheets, using shared theme properties."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QFrame, QGridLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from ... import theming
from ...i18n import trf
from ..kit import StatePanel
from .listing import key_values


class Summary(QWidget):
    def __init__(self, cards):
        super().__init__()
        self.cards = cards
        self.grid = QGridLayout(self)
        m = theming.metrics()["sources"]
        self.grid.setContentsMargins(0, m["kpi_padding_y"], 0, theming.metrics()["spacing"][0])
        self.grid.setSpacing(m["row_gap"])
        self._columns = None
        self._place(4)

    def _place(self, columns):
        self._columns = columns
        for index, card in enumerate(self.cards):
            self.grid.removeWidget(card)
            self.grid.addWidget(card, index // columns, index % columns)
        for col in range(4):
            self.grid.setColumnStretch(col, int(col < columns))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns = 2 if self.width() < theming.metrics()["sources"]["compact_breakpoint"] else 4
        if columns != self._columns:
            self._place(columns)


class Actions(QWidget):
    def __init__(self, *buttons):
        super().__init__()
        self.buttons = buttons
        self.grid = QGridLayout(self)
        s = theming.metrics()["spacing"]
        self.grid.setContentsMargins(0, s[1], 0, s[0])
        self.grid.setSpacing(s[1])
        self._narrow = None
        self._place(False)

    def _place(self, narrow):
        self._narrow = narrow
        for index, button in enumerate(self.buttons):
            self.grid.removeWidget(button)
            self.grid.addWidget(button, index if narrow else 0, 0 if narrow else index, Qt.AlignLeft)
        for column in range(len(self.buttons) + 1):
            self.grid.setColumnStretch(column, int(column == (1 if narrow else len(self.buttons))))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < theming.metrics()["sources"]["detail_actions_breakpoint"]
        if narrow != self._narrow:
            self._place(narrow)


class SourceAvatar(QLabel):
    """Brand initials are painted, so locale switching never translates them."""

    def __init__(self, provider, monogram):
        super().__init__()
        self.provider, self.monogram = provider, monogram
        self.setProperty("source_avatar", True)
        self.setProperty("provider", provider)
        side = theming.metrics()["sources"]["avatar"]
        self.setFixedSize(side, side)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(theming.roles().get("source_" + self.provider + "_fg", theming.roles()["on_primary"])))
        painter.drawText(self.rect(), Qt.AlignCenter, self.monogram)


class ProviderRow(QFrame):
    def __init__(self, title, sub, status, configure, provider="", monogram=""):
        super().__init__()
        self.setProperty("list_item", True)
        self.grid = QGridLayout(self)
        m = theming.metrics()["sources"]
        self.grid.setContentsMargins(0, m["row_padding"], 0, m["row_padding"])
        self.grid.setHorizontalSpacing(m["row_gap"])
        self.grid.setVerticalSpacing(theming.metrics()["spacing"][0])
        self.avatar = SourceAvatar(provider, monogram)
        text = QWidget()
        layout = QVBoxLayout(text)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for value, meta in ((title, False), (sub, True)):
            label = QLabel(value)
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
            if meta:
                label.setProperty("text_style", "meta")
            layout.addWidget(label)
        self.text, self.status, self.configure = text, status, configure
        self._narrow = None
        self._place(False)

    def _place(self, narrow):
        self._narrow = narrow
        for widget in (self.avatar, self.text, self.status, self.configure):
            self.grid.removeWidget(widget)
        self.grid.addWidget(self.avatar, 0, 0, Qt.AlignVCenter)
        self.grid.addWidget(self.text, 0, 1)
        self.grid.addWidget(self.status, 0, 2, Qt.AlignRight)
        self.grid.addWidget(self.configure, 0, 3, Qt.AlignRight)
        self.grid.setColumnStretch(1, 1)
        m = theming.metrics()["sources"]
        self.setMinimumHeight(m["avatar"] + 2 * m["row_padding"])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = False
        if narrow != self._narrow:
            self._place(narrow)


def source_state(kind, title, text="", **kwargs):
    panel = StatePanel(kind, title, text, **kwargs)
    m = theming.metrics()["spacing"]
    panel.layout().setContentsMargins(m[2], m[3], m[2], m[3])
    panel.layout().setSizeConstraint(QLayout.SetMinimumSize)
    panel.title.setMinimumHeight(theming.metrics()["control"]["lg"])
    if text:
        panel.text.setMinimumHeight(theming.metrics()["control"]["lg"])
    if panel.issue_label is not None:
        panel.issue_label.setText(trf("ждёт #{issue}", issue=kwargs["issue"]))
    return panel


def source_values(rows):
    widget = key_values(rows)
    for label in widget.findChildren(QLabel):
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    return widget
