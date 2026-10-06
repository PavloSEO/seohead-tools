"""Presentation models. Demo rows are explicitly distinct from retained scans."""

from PyQt5.QtCore import QAbstractTableModel, Qt


class UrlModel(QAbstractTableModel):
    columns = (
        ("URL", "url"),
        ("HTTP", "status"),
        ("Тип", "type"),
        ("Индексация", "indexability"),
        ("Title", "title"),
        ("Проблемы", "issues"),
    )

    def __init__(self, rows=(), parent=None):
        super().__init__(parent)
        self.rows = list(rows)

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()].get(self.columns[index.column()][1])
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return value if value is not None else "Не измерено"
        if role == Qt.TextAlignmentRole and index.column() in (1, 5):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][0]
        return None

    def replace(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()
