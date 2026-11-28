"""Visual tokens shared by the assistant, notification and desktop menus."""
import ctypes
from functools import lru_cache
import sys

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from .character_art import DECOR

PAGE_MARGIN = 22
SECTION_GAP = 16
CONTROL_GAP = 8

LIGHT = {
    "sidebar": "#ebeff7", "arrow": "light",
    "bg": "#f4f6fb", "card": "#ffffff", "panel": "#e8edf8", "line": "#dce2ee",
    "text": "#293449", "muted": "#565f75", "faint": "#9099ad", "accent": "#596a91",
    "hover": "#dbe3f5", "press": "#c9d5ed", "on_accent": "#ffffff",
    "pink": "#d88eae", "rose": "#973b63", "bubble": "#f7e7ef",
    "green": "#78b6a6", "positive": "#326857", "disabled": "#687185",
}
DARK = {
    "sidebar": "#202636", "arrow": "dark",
    "bg": "#171b27", "card": "#242c3e", "panel": "#30394e", "line": "#353e53",
    "text": "#e7ecf7", "muted": "#a1aec6", "faint": "#8894ae", "accent": "#b2a6dc",
    "hover": "#394562", "press": "#475473", "on_accent": "#21253a",
    "pink": "#d88eae", "rose": "#ffc1db", "bubble": "#493346",
    "green": "#78b6a6", "positive": "#a2d6c5", "disabled": "#99a3ba",
}


def colors(widget):
    return DARK if widget.property("darkTheme") else LIGHT


ICONS = {
    "focus": '<circle cx="12" cy="13" r="8"/><path d="M9 2h6m-3 6v5l3 2"/>',
    "reminder": '<path d="M5 17h14l-2-4V9a5 5 0 0 0-10 0v4l-2 4Zm5 3h4"/>',
    "sound": '<path d="M4 9h4l5-4v14l-5-4H4zm12 0a4 4 0 0 1 0 6m2-9a8 8 0 0 1 0 12"/>',
    "chat": '<path d="M4 5h16v11h-9l-5 4v-4H4zM8 9h8m-8 3h5"/>',
    "tools": '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/>'
             '<rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
    "app": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 8h18m-11 3-3 3 3 3m4-6 3 3-3 3"/>',
    "file": '<path d="M6 3h8l4 4v14H6zM14 3v5h4m-9 5h6m-6 4h6"/>',
    "folder": '<path d="M3 7V5h7l2 2h9v12H3z"/>',
    "web": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/>'
           '<path d="M3 12h18"/>',
    "delete": '<path d="M4 6h16m-11 0V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
    "gift": '<rect x="4" y="9" width="16" height="12" rx="1"/><path d="M3 9h18m-9 0v12M12 9C5 9 4 3 8 3c3 0 4 6 4 6s1-6 4-6c4 0 3 6-4 6Z"/>',
    "add": '<path d="M12 4v16m-8-8h16"/>',
    "halo": '<path d="M3 9h10l-1 12H2zm8-5h11l-2 16h-8M5 6h2"/>',
}


@lru_cache(maxsize=64)
def icon(name, color, size=20):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<g fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
           f'stroke-linejoin="round">{ICONS[name]}</g></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    result = QIcon()
    for scale in (1, 2, 3):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result


def resolved_dark(preference):
    return (preference == "dark" or
            preference == "system" and QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)


def apply_theme(widget, dark):
    t = DARK if dark else LIGHT
    palette = QPalette()
    for role, token in (
        (QPalette.Window, "bg"), (QPalette.Base, "card"), (QPalette.AlternateBase, "panel"),
        (QPalette.WindowText, "text"), (QPalette.Text, "text"), (QPalette.Button, "panel"),
        (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "on_accent"),
        (QPalette.PlaceholderText, "muted"), (QPalette.ToolTipBase, "card"), (QPalette.ToolTipText, "text"),
    ):
        palette.setColor(role, QColor(t[token]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(t["disabled"]))
    widget.setProperty("darkTheme", dark)
    widget.setPalette(palette)
    widget.setStyleSheet(stylesheet(t))


def apply_titlebar(widget):
    if (sys.platform != "win32" or widget.windowHandle() is None
            or QGuiApplication.platformName() != "windows"):
        return
    dwm = ctypes.windll.dwmapi
    dark = ctypes.c_int(bool(colors(widget) is DARK))
    handle = ctypes.c_void_p(int(widget.winId()))
    for attribute in (20, 19):
        if dwm.DwmSetWindowAttribute(handle, attribute, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
            break


def stylesheet(t):
    down = (DECOR / f"down-{t['arrow']}.svg").as_posix()
    up = (DECOR / f"up-{t['arrow']}.svg").as_posix()
    return f"""
QDialog, QWidget#notificationWindow, QWidget#assistantViewport, QWidget#assistantBody {{
    background: {t['bg']};
}}
QWidget {{ font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'; font-size: 13px; color: {t['text']}; }}
QFrame[role="sidebar"] {{ background: {t['sidebar']}; border-radius: 14px; }}
QFrame[role="card"] {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 14px; }}
QFrame[role="tile"] {{ background: {t['bg']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QLabel[role="title"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="metric"] {{ font-size: 22px; color: {t['accent']}; font-weight: 600; }}
QLabel[role="muted"] {{ color: {t['muted']}; }}
QLabel[role="brand"] {{ color: {t['accent']}; font-size: 14px; font-weight: 700; }}
QLabel[role="countdown"] {{ font-family: 'Consolas'; font-size: 48px; color: {t['accent']}; }}
QLabel[role="countdown"][longDurationStyle="true"] {{ font-size: 32px; }}
QLabel[role="countdown"][phase="paused"] {{ color: {t['rose']}; }}
QLabel[role="countdown"][phase="complete"], QLabel[role="countdown"][phase="rest"] {{ color: {t['positive']}; }}
QGroupBox {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 14px;
             margin-top: 12px; padding: 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
QPushButton {{ background: {t['panel']}; border: 1px solid transparent; border-radius: 9px; padding: 7px 10px; }}
QPushButton[role="nav"] {{ background: transparent; text-align: left; padding: 10px 11px; }}
QPushButton[role="outline"] {{ background: transparent; border-color: {t['line']}; }}
QPushButton[role="link"] {{ background: transparent; color: {t['accent']}; text-align: left; }}
QPushButton:hover, QPushButton[role="nav"]:hover, QPushButton[role="outline"]:hover {{ background: {t['hover']}; }}
QPushButton:pressed {{ background: {t['press']}; }}
QPushButton[role="segment"]:checked {{
    background: {t['panel']}; border-color: {t['accent']}; color: {t['text']}; font-weight: 600;
}}
QPushButton[role="primary"], QPushButton[role="nav"]:checked {{
    background: {t['accent']}; color: {t['on_accent']}; font-weight: 600;
}}
QPushButton[role="primary"]:hover, QPushButton:checked:hover {{ border-color: {t['pink']}; }}
QPushButton[role="primary"]:pressed, QPushButton:checked:pressed {{ border-color: {t['on_accent']}; }}
QPushButton[role="danger"] {{ color: {t['rose']}; background: {t['bubble']}; }}
QPushButton[role="danger"]:disabled {{ background: {t['panel']}; color: {t['disabled']}; }}
QPushButton[role="danger"]:hover {{ border-color: {t['rose']}; }}
QPushButton:disabled {{ color: {t['disabled']}; background: {t['panel']}; border-color: transparent; }}
QPushButton[role="memoryTile"] {{ font-family: 'Segoe UI Symbol'; font-size: 28px; }}
QPushButton[role="memoryTile"][lit="true"] {{ background: {t['accent']}; color: {t['on_accent']}; }}
QPushButton:checked:disabled {{ color: {t['accent']}; border-color: {t['line']}; }}
QPushButton:focus, QLineEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QTimeEdit:focus,
QComboBox:focus, QTableWidget:focus, QListWidget:focus, QCheckBox:focus {{ border: 1px solid {t['accent']}; }}
QPushButton:checked:focus, QPushButton[role="primary"]:focus {{ border: 1px solid {t['on_accent']}; }}
QLineEdit, QSpinBox, QDateTimeEdit, QTimeEdit, QComboBox, QTextEdit, QTableWidget, QListWidget {{
    background: {t['card']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 5px 7px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDateTimeEdit:disabled, QTimeEdit:disabled, QComboBox:disabled {{
    background: {t['panel']}; color: {t['disabled']};
}}
QComboBox, QSpinBox, QTimeEdit, QDateTimeEdit {{ padding-right: 25px; }}
QComboBox::drop-down, QDateTimeEdit::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right; width: 23px; border: 0;
}}
QComboBox::down-arrow, QDateTimeEdit::down-arrow, QPushButton::menu-indicator {{
    image: url("{down}"); width: 12px; height: 12px;
}}
QSpinBox::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: 0;
}}
QSpinBox::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: 0;
}}
QSpinBox::up-arrow, QTimeEdit::up-arrow, QDateTimeEdit::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QTimeEdit::up-button:hover,
QTimeEdit::down-button:hover, QComboBox::drop-down:hover {{ background: {t['panel']}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t['faint']}; border-radius: 4px; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border: 2px solid {t['card']}; }}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {t['panel']}; }}
QCalendarWidget QToolButton {{ color: {t['text']}; background: {t['panel']}; border: 0; padding: 4px; }}
QCalendarWidget QToolButton:hover {{ background: {t['hover']}; }}
QComboBox QAbstractItemView, QCalendarWidget QAbstractItemView {{
    background: {t['card']}; color: {t['text']}; selection-background-color: {t['accent']};
    selection-color: {t['on_accent']};
}}
QListWidget[role="catalog"]::item {{ padding: 5px 7px; border-radius: 5px; }}
QListWidget[role="catalog"]::item:hover {{ background: {t['hover']}; }}
QListWidget[role="catalog"]::item:selected {{ background: {t['panel']}; color: {t['text']}; }}
QListWidget[role="launcherGrid"] {{ background: transparent; border: 0; padding: 0; outline: none; }}
QListWidget[role="launcherGrid"]::item {{ background: {t['card']}; color: {t['text']};
    border: 1px solid {t['line']}; border-radius: 12px; margin: 4px; padding: 7px; }}
QListWidget[role="launcherGrid"]::item:hover {{ background: {t['hover']}; }}
QListWidget[role="launcherGrid"]::item:selected {{ background: {t['panel']}; border-color: {t['accent']}; }}
QHeaderView::section {{ background: {t['panel']}; padding: 6px; border: 0; }}
QTabWidget::pane, QScrollArea {{ border: 0; background: transparent; }}
QSlider::groove:horizontal {{ height: 6px; background: {t['line']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -5px 0; background: {t['accent']}; border-radius: 8px; }}
QProgressBar {{ border: 0; background: {t['line']}; border-radius: 2px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 2px; }}
QScrollBar:vertical {{ width: 9px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['faint']}; border-radius: 2px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QMenu {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 25px 7px 27px; border-radius: 8px; }}
QMenu::item:selected {{ background: {t['hover']}; }}
QMenu::item:disabled {{ color: {t['muted']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 8px; }}
"""


def label(text="", role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    widget.setProperty("role", role or "")
    return widget


def card(role="card", margin=16):
    widget = QFrame()
    widget.setProperty("role", role)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(CONTROL_GAP)
    return widget, layout


def scroll_body(parent, margin=PAGE_MARGIN):
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    body.setObjectName("assistantBody")
    area.setWidget(body)
    area.viewport().setObjectName("assistantViewport")
    parent.addWidget(area, 1)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(SECTION_GAP)
    return layout
