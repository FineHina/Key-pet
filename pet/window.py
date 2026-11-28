"""Transparent desktop shell, gesture routing, animated hit-testing and tray menu."""
import ctypes
import os
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QCursor, QIcon, QPainter
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from . import config
from .behaviors import Behaviors, EventBus
from .bubbles import BubbleManager
from .expressions import LABELS
from .gestures import Gestures
from .reaction_card import ReactionCard
from .scene import BUBBLE_BAND, PET_H, PetScene
from .ui_theme import apply_theme, colors, icon, resolved_dark

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS = os.path.join(ROOT, "assets", "layers")

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _GetWLS = user32.GetWindowLongPtrW
    _SetWLS = user32.SetWindowLongPtrW
else:
    _GetWLS = user32.GetWindowLongW
    _SetWLS = user32.SetWindowLongW
_GetWLS.restype = ctypes.c_ssize_t
_GetWLS.argtypes = [ctypes.c_void_p, ctypes.c_int]
_SetWLS.restype = ctypes.c_ssize_t
_SetWLS.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]


def set_click_through(hwnd, enable):
    style = _GetWLS(hwnd, GWL_EXSTYLE)
    if enable:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
    _SetWLS(hwnd, GWL_EXSTYLE, style)


class PetWindow(QWidget):
    def __init__(self, enable_assistant=True, assistant_database=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.scene = PetScene()
        self.displayed_character_frame = None
        self.bus = EventBus()
        self.bubbles = BubbleManager()
        self.reaction_card = ReactionCard(self, self.bubbles.expressions)
        self.state = {"dragging": False, "blink": False, "sway": 0.0}
        self.behaviors = Behaviors(
            self.state, self.bubbles,
            animation_clips=self.scene.animations.behavior_metadata())
        hints = QApplication.instance().styleHints()
        self.gestures = Gestures(double_interval=hints.mouseDoubleClickInterval() / 1000,
                                 drag_distance=hints.startDragDistance())
        for event in ("press", "release", "click", "double_click", "long_press",
                      "hover", "drag_start", "drag_end", "cancel"):
            self.bus.on(event, getattr(self.behaviors, "on_" + event))

        cfg = config.load()
        self.s = float(cfg.get("scale", 0.42))
        self._apply_size()
        self._hwnd = None
        self._passthrough = None
        self._grab_off = QPoint()
        self._last_gp_x = None
        self._sway_target = 0.0
        self._hover_since = None
        self._hover_fired = False
        self._menu = None
        self._tray_init()
        self._place(cfg)
        self._frame = QTimer(self)
        self._frame.timeout.connect(self._tick_frame)
        self._frame.start(16)
        self._hit = QTimer(self)
        self._hit.timeout.connect(self._tick_hit)
        self._hit.start(30)
        if enable_assistant:
            from .assistant import AssistantController
            self.assistant = AssistantController(self, database=assistant_database, parent=self)
            self.assistant.attach_windows()
            self.assistant.changed.connect(self._update_assistant_status)
            self.assistant.shortcuts_changed.connect(self._refresh_shortcut_menu)
            self._refresh_shortcut_menu()

    def _refresh_shortcut_menu(self):
        previous = self.tray.contextMenu()
        self.tray.setContextMenu(self._tray_menu())
        if previous is not None:
            previous.deleteLater()
        self._update_assistant_status()

    def _open_shortcut(self, shortcut_id):
        try:
            self.assistant.open_shortcut(shortcut_id)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "无法打开快捷工具", str(error))

    def _update_assistant_status(self):
        if hasattr(self, "tray"):
            self.tray.setToolTip(self.assistant.tray_status())
        tray_menu = self.tray.contextMenu() if hasattr(self, "tray") else None
        for menu in (tray_menu, self._menu):
            if menu is not None and menu.isVisible() and hasattr(menu, "dnd_action"):
                self._sync_menu(menu)

    def _sync_menu(self, menu):
        count = len(self.assistant.pending_notifications())
        menu.assistant_action.setText(f"KEI 助手（{count} 项待处理）" if count else "KEI 助手")
        manual = self.assistant.settings.get("dnd", False)
        effective = self.assistant.policy.is_dnd()
        menu.dnd_action.setChecked(manual)
        menu.dnd_action.setText("手动勿扰 · 已开启" if manual else
                                "手动勿扰 · 自动勿扰中" if effective else "手动勿扰")
        snapshot = self.assistant.focus_snapshot()
        menu.quick_focus.setEnabled(not snapshot or snapshot.get("state") not in ("running", "paused"))

    def _quick_focus(self, minutes, kind):
        try:
            self.assistant.start_focus(minutes, kind)
        except ValueError as error:
            QMessageBox.warning(self, "无法开始计时", str(error))

    def _apply_size(self):
        self.setFixedSize(*self.scene.size_for(self.s))

    def _place(self, cfg):
        if cfg.get("x") is not None and cfg.get("y") is not None:
            self.move(int(cfg["x"]), int(cfg["y"]))
        else:
            g = self.screen().availableGeometry()
            self.move(g.right() - self.width() - 30, g.bottom() - self.height() + 2)

    def _save_cfg(self):
        config.save({"x": self.x(), "y": self.y(), "scale": self.s})

    def paintEvent(self, _):
        frame = self.scene.sample(time.monotonic(), self.state, self.s)
        painter = QPainter(self)
        try:
            painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
            frame.paint(painter)
        finally:
            painter.end()
        self.displayed_character_frame = frame

    def _dispatch(self, events):
        for event, payload in events:
            if event == "drag_start":
                self._reset_hover()
            elif event == "drag_move":
                gp = QPoint(*map(round, payload["position"]))
                self.move(gp - self._grab_off)
                if self._last_gp_x is not None:
                    dx = gp.x() - self._last_gp_x
                    self._sway_target = max(-8.0, min(8.0, self._sway_target + dx * 0.12))
                self._last_gp_x = gp.x()
                continue
            elif event == "drag_end":
                self._save_cfg()
            if event == "hover" and hasattr(self, "assistant") and self.assistant.policy.is_dnd():
                continue
            self.bus.emit(event, **payload)
            if hasattr(self, "assistant"):
                self.assistant.pointer_event(event, payload.get("region"))

    def _tick_frame(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        self._dispatch(self.gestures.tick(now))
        self.behaviors.tick(now)
        self.reaction_card.sync(self.bubbles.active, now, self.s)
        self._sway_target *= 0.9
        self.state["sway"] += (self._sway_target - self.state["sway"]) * 0.25
        self.update()

    def _region(self, gp):
        frame = self.displayed_character_frame
        return frame.hit_region(QPointF(self.mapFromGlobal(gp))) if frame is not None else None

    def _over_pet(self, gp):
        return self._region(gp) is not None

    def _reset_hover(self):
        self._hover_since = None
        self._hover_fired = False

    def _tick_hit(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        region = self._region(QCursor.pos())
        want = region is not None or self.gestures.pressed
        if want != self._passthrough:
            self._hwnd = int(self.winId())
            set_click_through(self._hwnd, not want)
            self._passthrough = want
            self.setCursor(Qt.PointingHandCursor if want else Qt.ArrowCursor)
        menu_open = self._menu is not None and self._menu.isVisible()
        if region == "head" and not self.gestures.pressed and not menu_open:
            if self._hover_since is None:
                self._hover_since = now
            elif not self._hover_fired and now - self._hover_since >= 1.5:
                self._hover_fired = True
                if not hasattr(self, "assistant") or not self.assistant.policy.is_dnd():
                    self.bus.emit("hover", region="head", now=now)
                    if hasattr(self, "assistant"):
                        self.assistant.pointer_event("hover")
        else:
            self._reset_hover()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        gp = event.globalPosition().toPoint()
        region = self._region(gp)
        if region is None:
            return
        self._reset_hover()
        self._grab_off = gp - self.pos()
        self._last_gp_x = gp.x()
        self._dispatch(self.gestures.press((gp.x(), gp.y()), region=region))

    def mouseMoveEvent(self, event):
        if self.gestures.pressed and event.buttons() & Qt.LeftButton:
            gp = event.globalPosition().toPoint()
            self._dispatch(self.gestures.move((gp.x(), gp.y())))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dispatch(self.gestures.release())

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        gp = event.globalPosition().toPoint()
        region = self._region(gp)
        if region is not None:
            self._grab_off = gp - self.pos()
            self._last_gp_x = gp.x()
            self._dispatch(self.gestures.double_click((gp.x(), gp.y()), region=region))

    def wheelEvent(self, event):
        if self.gestures.pressed:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        old_feet = BUBBLE_BAND + PET_H * self.s
        self.s = max(0.25, min(1.6, self.s * (1.12 if delta > 0 else 1 / 1.12)))
        self._apply_size()
        self.move(self.x(), self.y() + round(old_feet - BUBBLE_BAND - PET_H * self.s))
        self._save_cfg()

    def event(self, event):
        if event.type() == QEvent.UngrabMouse and hasattr(self, "gestures"):
            if self.gestures.pressed:
                self._dispatch(self.gestures.cancel())
        return super().event(event)

    def hideEvent(self, event):
        self.displayed_character_frame = None
        if hasattr(self, "gestures"):
            self._dispatch(self.gestures.cancel())
            self._reset_hover()
        if hasattr(self, "reaction_card"):
            self.reaction_card.hide()
        super().hideEvent(event)

    def contextMenuEvent(self, event):
        self._dispatch(self.gestures.cancel())
        if self._menu is not None:
            self._menu.close()
            self._menu.deleteLater()
        self._menu = self._tray_menu()
        self._menu.popup(event.globalPos())

    def _tray_init(self):
        self.tray = QSystemTrayIcon(QIcon(os.path.join(LAYERS, "tray.png")), self)
        self.tray.setToolTip("KEI · 凯伊桌宠")
        self.tray.setContextMenu(self._tray_menu())
        self.tray.activated.connect(
            lambda reason: self.setVisible(not self.isVisible())
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def _tray_menu(self):
        menu = QMenu(self)
        def update_theme():
            settings = self.assistant.settings if hasattr(self, "assistant") else {}
            dark = resolved_dark(settings.get("assistant_theme", "system"))
            if menu.property("darkTheme") != dark:
                apply_theme(menu, dark)
                t = colors(menu)
                for action in menu.actions():
                    if action.data():
                        action.setIcon(icon(action.data(), t["accent"], 17))

        menu.aboutToShow.connect(update_theme)
        if hasattr(self, "assistant"):
            menu.assistant_action = menu.addAction("KEI 助手", lambda: self.assistant.open_panel())
            menu.assistant_action.setData("halo")
            menu.quick_focus = menu.addMenu("快速专注")
            menu.quick_focus.menuAction().setData("focus")
            menu.quick_focus.addAction("专注 25 分钟", lambda: self._quick_focus(25, "focus"))
            menu.quick_focus.addAction("休息 5 分钟", lambda: self._quick_focus(5, "break"))
            menu.addAction("陪伴对话", lambda: self.assistant.open_panel("陪伴对话")).setData("chat")
            interaction = menu.addMenu("互动")
            interaction.menuAction().setData("gift")
            interaction.addAction("喂食与照顾", lambda: self.assistant.open_interaction("care"))
            interaction.addAction("礼物图鉴", lambda: self.assistant.open_interaction("gifts"))
            interaction.addAction("小游戏", lambda: self.assistant.open_interaction("games"))
            shortcuts = menu.addMenu("快捷工具")
            shortcuts.menuAction().setData("tools")
            shortcuts.addAction("管理快捷工具", lambda: self.assistant.open_panel("快捷工具"))
            entries = self.assistant.shortcuts_list()
            if entries:
                shortcuts.addSeparator()
            for entry in entries:
                action = shortcuts.addAction(entry["name"].replace("&", "&&"))
                action.triggered.connect(lambda checked=False, sid=entry["id"]: self._open_shortcut(sid))
            menu.addSeparator()
            menu.dnd_action = menu.addAction("手动勿扰")
            menu.dnd_action.setCheckable(True)
            menu.dnd_action.triggered.connect(lambda enabled: self.assistant.update_settings({"dnd": enabled}))
            menu.addAction("停止语音", self.assistant.stop_audio).setData("sound")
            menu.addSeparator()
            self._sync_menu(menu)
            menu.aboutToShow.connect(lambda: self._sync_menu(menu))
        expressions = menu.addMenu("原立绘表情")
        for kind, label in LABELS.items():
            action = expressions.addAction(label)
            action.triggered.connect(lambda checked=False, k=kind: self._preview(k))
        expressions.addSeparator()
        expressions.addAction("恢复默认", self._restore_default)
        animations = menu.addMenu("动作预览")
        for name in self.scene.animations:
            clip = self.scene.animations[name]
            action = animations.addAction(clip.label)
            action.triggered.connect(
                lambda checked=False, clip_name=name: self._preview_animation(clip_name))
        if len(self.scene.animations):
            animations.addSeparator()
        animations.addAction("恢复普通待机", self._restore_default)
        display = menu.addMenu("显示与位置")
        display.addAction("显示 / 隐藏桌宠", lambda: self.setVisible(not self.isVisible()))
        display.addAction("重置位置", self._reset_pos)
        menu.addSeparator()
        menu.addAction("退出桌宠", self._quit)
        update_theme()
        return menu

    def _preview(self, kind):
        self._dispatch(self.gestures.cancel())
        self.behaviors.preview(kind)
        if hasattr(self, "assistant"):
            self.assistant.audio.respond(self.bubbles.active, kind)
            self.assistant._last_reaction = self.bubbles.active
        self.show()
        self.reaction_card.sync(self.bubbles.active, time.monotonic(), self.s)
        self.update()

    def _preview_animation(self, name):
        self._dispatch(self.gestures.cancel())
        self.behaviors.preview_animation(name)
        self.show()
        self.update()

    def _restore_default(self):
        self.behaviors.restore_default()
        if hasattr(self, "assistant"):
            self.assistant.stop_audio()
        self.reaction_card.sync(None, time.monotonic(), self.s)
        self.update()

    def _reset_pos(self):
        self._place({"x": None, "y": None})
        self._save_cfg()

    def _quit(self):
        self._save_cfg()
        if hasattr(self, "assistant"):
            self.assistant.shutdown()
        self.tray.hide()
        QApplication.instance().quit()
