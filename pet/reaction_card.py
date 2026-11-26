"""Original-art reaction card: local, non-activating and mouse-transparent."""
from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .expressions import Expressions, LABELS
from .scene import PET_H, PET_W


def card_position(pet_rect, available, card_size, gap=12):
    width, height = card_size
    x = round(pet_rect.center().x() - (width - 1) / 2)
    above = pet_rect.top() - height - gap
    below = pet_rect.bottom() + gap + 1
    if above >= available.top():
        y = above
    elif below + height <= available.bottom() + 1:
        y = below
    else:
        y = available.top()
    x = max(available.left(), min(x, available.right() - width + 1))
    y = max(available.top(), min(y, available.bottom() - height + 1))
    return QPoint(x, y)


class ReactionCard(QWidget):
    def __init__(self, pet, expressions=None):
        super().__init__(pet, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
                         | Qt.NoDropShadowWindowHint)
        self.pet = pet
        self.expressions = expressions if expressions is not None else Expressions()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.active = None
        self.image = QImage()
        self._image_path = None
        self._image_rect = QRectF()
        self._text_rect = QRectF()
        self._font = QFont("Microsoft YaHei UI")

    def character_rect(self, now, scale):
        scene = getattr(self.pet, "scene", None)
        if scene is None:
            return self.pet.frameGeometry()
        ox, oy = scene.pet_origin(scale)
        current = scene.current_animation_frame(now, self.pet.state)
        if current is not None:
            sources = ((current[1], scene._authored_transform(self.pet.state, scale)),)
        else:
            transforms = scene.transforms(now, self.pet.state, scale)
            sources = tuple((alpha, transforms[name]) for name, alpha in scene._layer_alpha.items())
        bounds = QRectF()
        for alpha, transform in sources:
            ys, xs = (alpha > 10).nonzero()
            if not len(xs):
                continue
            rect = QRectF(ox + int(xs.min()) * scale, oy + int(ys.min()) * scale,
                          (int(xs.max()) - int(xs.min()) + 1) * scale,
                          (int(ys.max()) - int(ys.min()) + 1) * scale)
            mapped = transform.mapRect(rect)
            bounds = mapped if bounds.isNull() else bounds.united(mapped)
        if bounds.isNull():
            bounds = QRectF(ox, oy, PET_W * scale, PET_H * scale)
        origin = self.pet.mapToGlobal(QPoint())
        bounds.translate(origin.x(), origin.y())
        return bounds.toAlignedRect()

    def sync(self, reaction, now, scale=0.42):
        if (reaction is None or not self.pet.isVisible()
                or now >= reaction["t0"] + reaction["dur"]):
            self.active = None
            self.hide()
            return
        self.active = reaction
        path = reaction.get("image")
        if path != self._image_path:
            self.image = self.expressions.image(path) if path else QImage()
            self._image_path = path
        pet_rect = self.character_rect(now, scale)
        screen = QApplication.screenAt(pet_rect.center()) or self.pet.screen()
        available = screen.availableGeometry()
        width = min(max(200, round(240 * (scale / 0.42) ** .2)), max(100, available.width() - 20))
        image_width = (width - 24) * 0.5
        image_height = min(image_width, max(80, available.height() - 115))
        self._font.setPixelSize(14)
        metrics = QFontMetricsF(self._font)
        text = self.display_text()
        text_bounds = metrics.boundingRect(QRectF(0, 0, width - 32, 240), Qt.TextWordWrap, text)
        text_height = min(160, max(28, round(text_bounds.height()) + 12))
        height = min(available.height() - 16, image_height + text_height + 55)
        self.setFixedSize(width, max(80, height))
        self._image_rect = QRectF((width - image_width) / 2, 31, image_width,
                                  min(image_height, height - text_height - 43))
        self._text_rect = QRectF(16, self._image_rect.bottom() + 8, width - 32, text_height)
        self.move(card_position(pet_rect, available, (self.width(), self.height())))
        self.show()
        self.update()

    def display_text(self):
        if self.active is None:
            return ""
        text = self.active.get("text", "")
        error = self.active.get("error")
        return f"{text}\n{error}" if error else text

    def image_target(self):
        if self.image.isNull():
            return self._image_rect
        target_size = self.image.size().scaled(self._image_rect.size().toSize(), Qt.KeepAspectRatio)
        return QRectF(self._image_rect.center().x() - target_size.width() / 2,
                      self._image_rect.center().y() - target_size.height() / 2,
                      target_size.width(), target_size.height())

    def paintEvent(self, event):
        if self.active is None:
            return
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        painter.setPen(QPen(QColor(215, 221, 235), 1))
        painter.setBrush(QColor(248, 250, 255, 247))
        painter.drawRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 12, 12)
        painter.setFont(self._font)
        painter.setPen(QColor(97, 111, 143))
        painter.drawText(QRectF(14, 7, self.width() - 28, 22), Qt.AlignLeft | Qt.AlignVCenter,
                         LABELS.get(self.active.get("category"), "反应"))
        if not self.image.isNull():
            painter.drawImage(self.image_target(), self.image)
        painter.setPen(QColor(51, 60, 80))
        painter.drawText(self._text_rect, Qt.TextWordWrap | Qt.AlignTop, self.display_text())
