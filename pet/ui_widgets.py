"""Small, DPI-independent controls used by the assistant views."""
from PySide6.QtCore import QEasingCurve, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .character_art import DECOR
from .ui_theme import colors


def disclosure(text, body, layout):
    toggle = QPushButton()
    toggle.setProperty("role", "link")
    toggle.setCheckable(True)

    def update(expanded):
        toggle.setText(("▾  " if expanded else "▸  ") + text)
        body.setVisible(expanded)

    toggle.toggled.connect(update)
    layout.addWidget(toggle)
    layout.addWidget(body)
    update(False)
    return toggle


class WrappingTitle(QLabel):
    """Fit long reminder titles, including names without word boundaries."""

    _flags = Qt.AlignLeft.value | Qt.AlignTop.value | Qt.TextWrapAnywhere.value

    def __init__(self, text):
        super().__init__(text)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setProperty("role", "heading")

    def sizeHint(self):
        width = min(self.fontMetrics().horizontalAdvance(self.text()), 280)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def heightForWidth(self, width):
        bounds = QRect(0, 0, max(1, width), 0)
        return self.fontMetrics().boundingRect(bounds, self._flags, self.text()).height()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor(colors(self.window())["text"]))
        painter.drawText(self.rect(), self._flags, self.text())


class PageSections(QStackedWidget):
    """Size the page scroll area to the selected section."""

    def __init__(self):
        super().__init__()
        self.currentChanged.connect(self.updateGeometry)

    def sizeHint(self):
        section = self.currentWidget()
        return section.sizeHint() if section else super().sizeHint()

    def minimumSizeHint(self):
        section = self.currentWidget()
        return section.minimumSizeHint() if section else super().minimumSizeHint()


class TimerRing(QWidget):
    def __init__(self, countdown, status):
        super().__init__()
        self.setFixedSize(210, 210)
        self.progress = 0.0
        self.token = "accent"
        self._progress_animation = QVariantAnimation(self)
        self._progress_animation.setDuration(160)
        self._progress_animation.valueChanged.connect(self._set_progress)
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.addStretch()
        layout.addWidget(countdown)
        layout.addWidget(status)
        layout.addStretch()

    def set_compact(self, compact):
        edge = 174 if compact else 210
        if self.width() != edge:
            self.setFixedSize(edge, edge)

    def set_progress(self, progress, token):
        target = max(0, min(1, progress))
        if self.token != token:
            self.token = token
            self.update()
        if target != self._progress_animation.endValue():
            self._progress_animation.stop()
            if target >= self.progress or target == 0:
                self._progress_animation.setEndValue(float(target))
                self._set_progress(target)
            else:
                self._progress_animation.setStartValue(float(self.progress))
                self._progress_animation.setEndValue(float(target))
                self._progress_animation.start()

    def _set_progress(self, value):
        self.progress = value
        self.update()

    def paintEvent(self, event):
        t = colors(self.window())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(15, 15, -15, -15)
        painter.setPen(QPen(QColor(t["line"]), 8))
        painter.drawEllipse(rect)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.setPen(QPen(QColor(t["faint"]), 1))
        radius = self.width() // 2 - 4
        for tick in range(48):
            painter.drawLine(0, -radius, 0, -radius + (3 if tick % 4 else 6))
            painter.rotate(7.5)
        painter.resetTransform()
        if self.progress:
            painter.setPen(QPen(QColor(t[self.token]), 8, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 90 * 16, -round(self.progress * 360 * 16))


class ToggleSwitch(QCheckBox):
    """Retain native checkbox input and accessibility with a compact visual track."""

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 58, 31)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        t = colors(self.window())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.width() - 44, (self.height() - 20) / 2, 38, 20)
        painter.setPen(QPen(QColor(t["accent"] if self.underMouse() else t["faint"]), 1))
        painter.setBrush(QColor(t["accent"] if self.isChecked() else t["panel"]))
        painter.drawRoundedRect(track, 10, 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t["on_accent"] if self.isChecked() else t["card"]))
        knob_x = track.left() + (21 if self.isChecked() else 3)
        painter.drawEllipse(QRectF(knob_x, track.top() + 3, 14, 14))
        painter.setPen(QColor(t["text"] if self.isEnabled() else t["disabled"]))
        painter.drawText(self.rect().adjusted(6, 0, -50, 0), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setPen(QPen(QColor(t["accent"]), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)


class KeiHalo(QWidget):
    """Brief entrance motion around the character's geometric mark."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(34, 34)
        self.angle = 0.0
        self._art = QSvgRenderer(str(DECOR / "kei_halo_motif.svg"), self)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(195)
        self._animation.setStartValue(-17.0)
        self._animation.setEndValue(0.0)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._turn)

    def animate(self):
        self._animation.start()

    def _turn(self, angle):
        self.angle = angle
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(17, 17)
        painter.rotate(self.angle)
        painter.translate(-17, -17)
        self._art.render(painter, QRectF(1, 5, 32, 24))


class StatusLight(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(12, 12)
        self.token = "green"

    def set_status(self, token):
        if token != self.token:
            self.token = token
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        t = colors(self.window())
        painter.setBrush(QColor(t[self.token]))
        painter.setPen(QPen(QColor(t["line"]), 1))
        painter.drawEllipse(QRectF(1.5, 1.5, 9, 9))
