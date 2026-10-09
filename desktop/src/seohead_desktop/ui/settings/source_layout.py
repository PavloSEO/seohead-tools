"""Responsive layouts for the source sheets, using shared theme properties."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QGridLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from ...i18n import trf
from ..kit import StatePanel
from .listing import key_values


class Summary(QWidget):
    def __init__(self, cards):
        super().__init__()
        self.cards = cards
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 8, 0, 8)
        self.grid.setSpacing(8)
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
        columns = 2 if self.width() < 640 else 4
        if columns != self._columns:
            self._place(columns)


class Actions(QWidget):
    def __init__(self, *buttons):
        super().__init__()
        self.buttons = buttons
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 10, 0, 6)
        self.grid.setSpacing(8)
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
        narrow = self.width() < 500
        if narrow != self._narrow:
            self._place(narrow)


class ProviderRow(QFrame):
    def __init__(self, title, sub, status, configure):
        super().__init__()
        self.setProperty("list_item", True)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 8, 0, 8)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        text = QWidget()
        layout = QVBoxLayout(text)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
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
        for widget in (self.text, self.status, self.configure):
            self.grid.removeWidget(widget)
        self.grid.addWidget(self.text, 0, 0, 1, 3 if narrow else 1)
        self.grid.addWidget(self.status, 1 if narrow else 0, 0 if narrow else 1, Qt.AlignLeft)
        self.grid.addWidget(self.configure, 1 if narrow else 0, 2, Qt.AlignRight)
        self.grid.setColumnStretch(0, 0 if narrow else 1)
        self.grid.setColumnStretch(1, 1 if narrow else 0)
        self.setMinimumHeight(90 if narrow else 64)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 440
        if narrow != self._narrow:
            self._place(narrow)


def source_state(kind, title, text="", **kwargs):
    panel = StatePanel(kind, title, text, **kwargs)
    panel.layout().setContentsMargins(12, 16, 12, 16)
    panel.layout().setSizeConstraint(QLayout.SetMinimumSize)
    panel.title.setMinimumHeight(42)
    if text:
        panel.text.setMinimumHeight(42)
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
