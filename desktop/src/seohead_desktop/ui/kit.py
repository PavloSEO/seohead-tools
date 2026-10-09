"""Building blocks shared by the design-v2 screens: honest state panel, KPI card, page header, badge delegate, table styling."""

from __future__ import annotations

from PyQt5.QtCore import QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .. import theming
from ..i18n import tr
from .icons import MaterialIconLabel

STATE_ICONS = {"empty": "inbox", "loading": "hourglass_top", "error": "error", "partial": "incomplete_circle", "waiting": "schedule"}
CORE_ISSUES = "https://github.com/PavloSEO/seohead-tools/issues/"


class StatePanel(QFrame):
    """Empty / loading / error / partial / waiting state with an optional next-step button. Never shows a number.

    ``kind="waiting"`` names the core issue the feature waits for («ждёт #923»), so the state is truthful about why
    nothing is shown instead of pretending the data is empty.
    """

    def __init__(self, kind, title, text="", action=None, issue=None, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setProperty("state_panel", kind)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        layout.addWidget(MaterialIconLabel(STATE_ICONS[kind], 32, color="role:text_muted"), 0, Qt.AlignHCenter)
        self.title = QLabel(tr(title))
        self.title.setProperty("text_style", "section")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.text = QLabel(tr(text))
        self.text.setProperty("text_style", "meta")
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setVisible(bool(text))
        layout.addWidget(self.text)
        self.issue_label = None
        if issue is not None:
            self.issue_label = waiting_badge(issue)
            layout.addWidget(self.issue_label, 0, Qt.AlignHCenter)
        self.action = None
        if action is not None:
            label, callback = action
            self.action = QPushButton(tr(label))
            self.action.setProperty("role", "primary")
            self.action.clicked.connect(callback)
            layout.addWidget(self.action, 0, Qt.AlignHCenter)


def waiting_badge(issue):
    """Neutral badge «ждёт #N» for functionality blocked on a core issue."""
    label = QLabel(f"{tr('ждёт')} #{issue}")
    label.setProperty("badge", "mut")
    label.setToolTip(tr("Функция ждёт доработки ядра") + f" · {CORE_ISSUES}{issue}")
    return label


class Kpi(QFrame):
    """Caption, value (22/500) and optional sub line. ``value`` None means not measured: «Нет данных», never 0."""

    def __init__(self, label, value=None, sub="", parent=None):
        super().__init__(parent)
        self.setProperty("kpi", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.caption = QLabel(tr(label))
        self.caption.setProperty("kpi_part", "label")
        self.number = QLabel()
        self.number.setProperty("kpi_part", "value")
        self.sub = QLabel()
        self.sub.setProperty("text_style", "meta")
        for widget in (self.caption, self.number, self.sub):
            layout.addWidget(widget)
        self.set_value(value, sub)

    def set_value(self, value, sub=""):
        measured = value is not None
        self.number.setText(str(value) if measured else tr("Нет данных"))
        self.number.setProperty("na", not measured)
        self.number.style().unpolish(self.number)
        self.number.style().polish(self.number)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))


class PageHeader(QWidget):
    """Screen title (20/500) with a meta line and a right-aligned row of actions."""

    def __init__(self, title, meta="", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = QLabel(tr(title))
        self.title.setProperty("text_style", "title")
        self.meta = QLabel(meta)
        self.meta.setProperty("text_style", "meta")
        self.meta.setVisible(bool(meta))
        texts.addWidget(self.title)
        texts.addWidget(self.meta)
        layout.addLayout(texts, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, widget):
        self.actions.addWidget(widget)
        return widget

    def set_meta(self, text):
        self.meta.setText(text)
        self.meta.setVisible(bool(text))


BADGE_ROLE = Qt.UserRole + 40  # (kind, text) pair for BadgeDelegate


class BadgeDelegate(QStyledItemDelegate):
    """Draws a status badge (22 px, radius 6) from the item's BADGE_ROLE value; plain text when the role is empty.

    Badges are painted by the delegate (never a widget per cell). The text always accompanies the colour.
    """

    def paint(self, painter, option, index):
        value = index.data(BADGE_ROLE)
        if not value:
            return super().paint(painter, option, index)
        kind, text = value
        pair = theming.theme()["badges"].get(kind) or theming.theme()["badges"]["mut"]
        background, ink = pair
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        cell.text = ""
        (option.widget.style() if option.widget else QApplication.style()).drawControl(QStyle.CE_ItemViewItem, cell, painter, option.widget)
        font = QFont(painter.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Medium)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 16
        rect = QRectF(option.rect.left() + 10, option.rect.center().y() - 11, min(width, option.rect.width() - 20), 22)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(ink))
        painter.drawText(rect, Qt.AlignCenter, painter.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width()) - 8))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), theming.metrics()["row"]["standard"])


def style_table(table, density="standard"):
    """Apply the v2 table contract: row height by density, zebra, no grid, row selection, per-pixel scrolling."""
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(theming.metrics()["row"][density])
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(True)
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setFixedHeight(theming.metrics()["row"]["header"])
    return table
