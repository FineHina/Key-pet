"""Local KEI character manifest and aspect-preserving artwork widgets."""
from functools import lru_cache
import json
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter
from PySide6.QtWidgets import QWidget

ASSETS = Path(__file__).resolve().parents[1] / "assets"
CHARACTER = ASSETS / "character" / "kei"
DECOR = ASSETS / "ui" / "kei"


@lru_cache(maxsize=1)
def _manifest():
    try:
        data = json.loads((CHARACTER / "manifest.json").read_text(encoding="utf-8"))
        return data["assets"], data["fallbacks"]
    except (OSError, ValueError, KeyError):
        return {}, {}


@lru_cache(maxsize=18)
def character_image(state):
    assets, fallbacks = _manifest()
    for stem in fallbacks.get(state, ()):
        details = assets.get(stem)
        if not details:
            continue
        image = QImage(str(CHARACTER / details["path"]))
        if not image.isNull():
            return image
    if state == "avatar":
        return QImage(str(ASSETS / "layers" / "tray.png"))
    return QImage()


@lru_cache(maxsize=48)
def _scaled_character(state, width, height):
    return character_image(state).scaled(QSize(width, height), Qt.KeepAspectRatio, Qt.SmoothTransformation)


def brand_icon():
    return QIcon(str(DECOR / "kei_app_mark.svg"))


class CharacterArt(QWidget):
    def __init__(self, state, width, height=None):
        super().__init__()
        self.setFixedSize(width, height or width)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.state = ""
        self.set_state(state)

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.art = character_image(state)
            self.update()

    def paintEvent(self, event):
        if self.art.isNull():
            return
        ratio = self.devicePixelRatioF()
        image = _scaled_character(self.state, round(self.width() * ratio), round(self.height() * ratio))
        width, height = image.width() / ratio, image.height() / ratio
        target = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(target, image)
