"""One sampled character frame, shared by painting and pointer hit testing."""
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QTransform


PET_W, PET_H = 446, 602
BUBBLE_BAND = 120
RIGHT_MARGIN = 90
ALPHA_THRESHOLD = 10


def image_alpha(image: QImage) -> np.ndarray:
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    return np.frombuffer(alpha.constBits(), dtype=np.uint8).reshape(
        alpha.height(), alpha.bytesPerLine())[:, :alpha.width()].copy()


@dataclass(frozen=True)
class CharacterLayer:
    """A source image and its masks, all in the same pixel coordinates."""

    image: QImage
    alpha: np.ndarray
    transform: QTransform
    region: str = "body"
    head_mask: np.ndarray | None = None
    head_cut_y: int | None = None
    highlight: QImage | None = None
    highlight_opacity: float = 0.0


@dataclass(frozen=True)
class CharacterFrame:
    """The renderer's snapshot of one pose in window coordinates.

    Image and mask buffers must stay valid and unchanged while this frame is held.
    """

    # Layers are ordered from back to front.
    layers: tuple[CharacterLayer, ...]
    target: QRectF

    def paint(self, painter: QPainter):
        for layer in self.layers:
            painter.save()
            painter.setWorldTransform(layer.transform, True)
            painter.drawImage(self.target, layer.image)
            if layer.highlight is not None and layer.highlight_opacity:
                painter.setOpacity(painter.opacity() * layer.highlight_opacity)
                painter.drawImage(self.target, layer.highlight)
            painter.restore()

    def hit_region(self, position: QPointF) -> str | None:
        for layer in reversed(self.layers):
            inverse, ok = layer.transform.inverted()
            if not ok:
                continue
            point = inverse.map(QPointF(position))
            height, width = layer.alpha.shape
            x = (point.x() - self.target.x()) * width / self.target.width()
            y = (point.y() - self.target.y()) * height / self.target.height()
            if not (0 <= x < width and 0 <= y < height):
                continue
            row, column = int(y), int(x)
            if layer.alpha[row, column] <= ALPHA_THRESHOLD:
                continue
            if layer.head_mask is not None:
                return "head" if layer.head_mask[row, column] > ALPHA_THRESHOLD else "body"
            if layer.head_cut_y is not None:
                return "head" if y < layer.head_cut_y else "body"
            return layer.region
        return None

    def bounds(self) -> QRectF:
        bounds = QRectF()
        for layer in self.layers:
            opaque = layer.alpha > ALPHA_THRESHOLD
            xs = np.flatnonzero(opaque.any(axis=0))
            if not len(xs):
                continue
            ys = np.flatnonzero(opaque.any(axis=1))
            height, width = layer.alpha.shape
            sx, sy = self.target.width() / width, self.target.height() / height
            rect = QRectF(self.target.x() + int(xs[0]) * sx,
                          self.target.y() + int(ys[0]) * sy,
                          (int(xs[-1]) - int(xs[0]) + 1) * sx,
                          (int(ys[-1]) - int(ys[0]) + 1) * sy)
            mapped = layer.transform.mapRect(rect)
            bounds = mapped if bounds.isNull() else bounds.united(mapped)
        return QRectF(self.target) if bounds.isNull() else bounds
