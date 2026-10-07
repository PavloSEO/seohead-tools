"""Material SVGs painted at the actual device scale, without a fixed bitmap cache."""

from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QEvent, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QLabel

from .presentation import theme_tokens

ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "icons"


class _SvgEngine(QIconEngine):
    def __init__(self, raw, color):
        super().__init__()
        self.raw, self.color = raw, color
        colors = theme_tokens()["colors"]
        active_ink = (
            colors["primary"]
            if color.lower() == colors["on_surface_variant"].lower()
            else color
        )
        self.renderers = {}
        for mode, ink in (
            (QIcon.Normal, color),
            (QIcon.Active, active_ink),
            (QIcon.Selected, colors["on_primary_container"]),
            (QIcon.Disabled, colors["outline"]),
        ):
            # Tint only the in-memory root; original licensed asset bytes stay intact.
            tinted = raw.replace(b"<svg ", ('<svg fill="' + ink + '" ').encode(), 1)
            self.renderers[mode] = QSvgRenderer(tinted)

    def clone(self):
        return _SvgEngine(self.raw, self.color)

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        self.renderers.get(mode, self.renderers[QIcon.Normal]).render(
            painter, QRectF(rect)
        )
        painter.restore()

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        scale = max(1.0, float(scale))
        image = QPixmap(
            max(1, round(size.width() * scale)), max(1, round(size.height() * scale))
        )
        image.fill(Qt.transparent)
        image.setDevicePixelRatio(scale)
        painter = QPainter(image)
        self.paint(
            painter, QRectF(0, 0, size.width(), size.height()).toRect(), mode, state
        )
        painter.end()
        return image


@lru_cache(maxsize=128)
def material_icon(name, color=None):
    if (
        not isinstance(name, str)
        or not name
        or any(c not in "abcdefghijklmnopqrstuvwxyz_0123456789" for c in name)
    ):
        return QIcon()
    path = ASSET_ROOT / (name + ".svg")
    if not path.is_file():
        return QIcon()
    ink = QColor(color or theme_tokens()["colors"]["on_surface_variant"])
    if not ink.isValid():
        raise ValueError("Material icon color must be a valid Qt color")
    return QIcon(_SvgEngine(path.read_bytes(), ink.name()))


class MaterialIconLabel(QLabel):
    """Paint vector paths on every screen; QLabel.pixmap(18,18) would blur at 2x."""

    def __init__(self, name, size=18, parent=None, color=None):
        super().__init__(parent)
        self.name, self.color = name, color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        if parent:
            parent.installEventFilter(self)

    def set_material_icon(self, name, color=None):
        self.name, self.color = name, color
        self.update()

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Enter, QEvent.Leave, QEvent.EnabledChange}:
            self.update()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        owner = self.parentWidget()
        mode = (
            QIcon.Disabled
            if not self.isEnabled()
            else QIcon.Active
            if owner and owner.underMouse()
            else QIcon.Normal
        )
        material_icon(self.name, self.color).paint(
            painter, self.rect(), Qt.AlignCenter, mode
        )

    def sizeHint(self):
        return QSize(self.width(), self.height())
