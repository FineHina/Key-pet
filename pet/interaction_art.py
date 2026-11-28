"""Raster artwork for interaction tiles and revealed gifts."""
from functools import lru_cache

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap

from .character_art import ASSETS

ART = ASSETS / "interaction" / "kei"
# Pixel bounds in the approved concept sheet, read in place without resaving the original artwork.
GIFT_TILES = {
    "perfume": (57, 23, 400, 477),
    "lipstick": (602, 22, 348, 478),
    "blush": (1032, 64, 460, 427),
    "handheld": (12, 570, 530, 401),
    "retro": (599, 533, 325, 446),
}


@lru_cache(maxsize=1)
def _sheet():
    return QImage(str(ART / "gift_concepts.png"))


@lru_cache(maxsize=32)
def item_icon(item_id, size):
    source = _sheet().copy(*GIFT_TILES[item_id]) if item_id in GIFT_TILES else QImage(str(ART / f"{item_id}.png"))
    result = QIcon()
    if not source.isNull():
        for scale in (1, 2, 3):
            raster = source.scaled(QSize(size * scale, size * scale), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pixmap = QPixmap.fromImage(raster)
            pixmap.setDevicePixelRatio(scale)
            result.addPixmap(pixmap)
            result.addPixmap(pixmap, QIcon.Disabled)
    return result
