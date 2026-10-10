"""Row builders that bind one SettingRow to one store key (value in, validated value out)."""

from __future__ import annotations

from PyQt5.QtWidgets import QComboBox, QLabel, QLineEdit, QVBoxLayout, QWidget

from ...i18n import trf
from ..controls import Segmented, SettingRow, Switch


def group_label(text, **values):
    """Overline group title; a template with values is passed already upper-case."""
    label = QLabel(trf(text, **values) if values else text.upper())
    label.setProperty("text_style", "overline")
    label.setContentsMargins(0, 16, 0, 0)
    return label


def keyed(row, key):
    row.setProperty("setting_key", key)
    return row


def switch_row(store, key, title, description=""):
    switch = Switch(title, bool(store.get(key)))
    switch.toggled.connect(lambda checked: store.set(key, checked))
    return keyed(SettingRow(title, description, switch), key)


def segmented_row(store, key, title, description, options):
    seg = Segmented(options, store.get(key), accessible_name=title)
    seg.changed.connect(lambda value: store.set(key, value))
    return keyed(SettingRow(title, description, seg), key)


def choice_row(store, key, title, description, options, width=220):
    box = QComboBox()
    box.setFixedWidth(width)
    for value, label in options:
        box.addItem(label, value)
    box.setCurrentIndex(max(0, box.findData(store.get(key))))
    box.currentIndexChanged.connect(lambda _i: store.set(key, box.currentData()))
    return keyed(SettingRow(title, description, box), key)


def number_row(store, key, title, description, width=96):
    """Text field; invalid input keeps the field, shows the Russian error under the description, stores nothing."""
    field = QLineEdit(str(store.get(key)))
    field.setFixedWidth(width)
    row = keyed(SettingRow(title, description, field), key)

    def commit():
        error = store.set(key, field.text())
        row.set_error(error)

    field.editingFinished.connect(commit)
    field.textEdited.connect(lambda _t: row.set_error(None) if row._error_box.isVisible() else None)
    return row


def text_row(store, key, title, description, width=260):
    field = QLineEdit(str(store.get(key)))
    field.setFixedWidth(width)
    row = keyed(SettingRow(title, description, field), key)
    field.editingFinished.connect(lambda: row.set_error(store.set(key, field.text())))
    return row


def column_stack(*columns):
    """Rows of every column in one vertical stack: design v2 settings sheets are single-column, max 760 px."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for column in columns:
        for widget in column:
            layout.addWidget(widget)
    return holder


def page(*widgets):
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for widget in widgets:
        layout.addWidget(widget)
    return holder
