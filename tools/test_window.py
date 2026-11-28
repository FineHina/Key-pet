"""Targeted Qt input tests; does not click the live desktop or save configuration."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QTransform
from PySide6.QtWidgets import QApplication

from pet.behaviors import Behaviors
from pet.character_renderer import CharacterFrame, CharacterLayer, image_alpha
from pet.window import PetWindow

APP = QApplication.instance() or QApplication([])


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.saved = patch("pet.window.config.save")
        self.save = self.saved.start()
        self.tray = patch.object(PetWindow, "_tray_init", lambda self: None)
        self.tray.start()
        self.win = PetWindow(enable_assistant=False)
        self.win._frame.stop()
        self.win._hit.stop()
        self.now = 1000.0
        self.win.gestures.clock = lambda: self.now
        self.win.behaviors = Behaviors(self.win.state, self.win.bubbles, clock=lambda: self.now)
        self.win.bus._handlers.clear()
        self.seen = []
        for name in ("press", "release", "click", "double_click", "long_press", "hover", "drag_start", "drag_end", "cancel"):
            self.win.bus.on(name, getattr(self.win.behaviors, "on_" + name))
            self.win.bus.on(name, lambda event=name, **payload: self.seen.append(event))
        self.point = self.win.scene.transforms(self.now, self.win.state, self.win.s)["head"].map(
            QPointF(207 * self.win.s, 120 + 237 * self.win.s))
        self.win._region = lambda gp, now=None: "head"

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.tray.stop()
        self.saved.stop()

    def send(self, event_type, button=Qt.LeftButton, buttons=Qt.NoButton, point=None):
        lp = self.point if point is None else point
        gp = QPointF(self.win.mapToGlobal(lp.toPoint()))
        event = QMouseEvent(event_type, lp, gp, button, buttons, Qt.NoModifier)
        QApplication.sendEvent(self.win, event)

    def test_single_click_is_deferred(self):
        self.send(QEvent.MouseButtonPress, buttons=Qt.LeftButton)
        self.now += 0.05
        self.send(QEvent.MouseButtonRelease)
        self.assertNotIn("click", self.seen)
        self.now += self.win.gestures.double_interval + 0.02
        self.win._dispatch(self.win.gestures.tick(self.now))
        self.assertEqual(self.seen.count("click"), 1)
        self.assertEqual(self.win.state["expression"], "smile")
        self.assertEqual(self.win.bubbles.active["category"], "smile")
        self.assertIn("CH0335_spr_", self.win.bubbles.active["image"])
        self.assertEqual(self.win.bubbles.active["dur"], 5)

    def test_native_double_sequence_does_not_emit_single(self):
        self.send(QEvent.MouseButtonPress, buttons=Qt.LeftButton)
        self.now += 0.05
        self.send(QEvent.MouseButtonRelease)
        self.now += 0.05
        self.send(QEvent.MouseButtonDblClick, buttons=Qt.LeftButton)
        self.now += 0.05
        self.send(QEvent.MouseButtonRelease)
        self.now += self.win.gestures.double_interval + 0.02
        self.win._dispatch(self.win.gestures.tick(self.now))
        self.assertNotIn("click", self.seen)
        self.assertEqual(self.seen.count("double_click"), 1)
        self.assertEqual(self.win.state["expression"], "happy")

    def test_right_double_click_does_not_change_mood(self):
        self.send(QEvent.MouseButtonDblClick, button=Qt.RightButton, buttons=Qt.RightButton)
        self.assertNotIn("double_click", self.seen)
        self.assertEqual(self.win.state["expression"], "default")

    def test_long_press_consumes_release_click(self):
        self.send(QEvent.MouseButtonPress, buttons=Qt.LeftButton)
        self.now += 0.8
        self.win._dispatch(self.win.gestures.tick(self.now))
        self.send(QEvent.MouseButtonRelease)
        self.now += 1
        self.win._dispatch(self.win.gestures.tick(self.now))
        self.assertIn("long_press", self.seen)
        self.assertNotIn("click", self.seen)
        self.assertEqual(self.win.state["expression"], "blush")

    def test_drag_cancels_long_press_and_saves_only_on_drop(self):
        self.send(QEvent.MouseButtonPress, buttons=Qt.LeftButton)
        self.now += 0.05
        self.send(QEvent.MouseMove, button=Qt.NoButton, buttons=Qt.LeftButton,
                  point=self.point + QPointF(30, 0))
        self.now += 0.8
        self.win._dispatch(self.win.gestures.tick(self.now))
        self.send(QEvent.MouseButtonRelease)
        self.assertIn("drag_start", self.seen)
        self.assertIn("drag_end", self.seen)
        self.assertNotIn("long_press", self.seen)
        self.assertNotIn("click", self.seen)
        self.save.assert_called_once()
        self.assertFalse(self.win.state["dragging"])

    def test_menu_includes_animation_preview_and_does_not_move_or_save(self):
        metadata = {
            "wave": {"duration": 1.0, "mode": "once", "idle": False, "label": "挥手"},
            "walk": {"duration": 2.0, "mode": "loop", "idle": False, "label": "原地走路"},
        }
        self.win.behaviors = Behaviors(
            self.win.state, self.win.bubbles, clock=lambda: self.now,
            animation_clips=metadata)
        self.win.scene.animations.clips = {
            name: type("Clip", (), {"name": name, "label": data["label"],
                                    "sample": lambda self, elapsed: None})()
            for name, data in metadata.items()
        }
        self.win.bus._handlers.clear()
        for name in ("press", "release", "click", "double_click", "long_press", "hover",
                     "drag_start", "drag_end", "cancel"):
            self.win.bus.on(name, getattr(self.win.behaviors, "on_" + name))
        before = self.win.pos()
        menu = self.win._tray_menu()
        animations = next(action.menu() for action in menu.actions()
                          if action.text() == "动作预览")
        labels = [action.text() for action in animations.actions()]
        self.assertIn("挥手", labels)
        self.assertIn("原地走路", labels)
        self.assertIn("恢复普通待机", labels)
        next(action for action in animations.actions() if action.text() == "原地走路").trigger()
        self.assertEqual(self.win.state["animation"]["clip"], "walk")
        self.assertEqual(self.win.pos(), before)
        self.save.assert_not_called()
        with patch("pet.window.time.monotonic", return_value=self.now + 0.5):
            self.win._tick_frame()
        self.assertEqual(self.win.pos(), before)
        self.save.assert_not_called()
        next(action for action in animations.actions()
             if action.text() == "恢复普通待机").trigger()
        self.assertIsNone(self.win.state["animation"])
        self.assertEqual(self.win.pos(), before)
        self.save.assert_not_called()

    def test_hit_testing_waits_for_the_new_frame_to_be_painted(self):
        image = QImage(20, 20, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.white)
        layer = CharacterLayer(image, image_alpha(image), QTransform(), region="head")
        first = CharacterFrame((layer,), QRectF(20, 130, 20, 20))
        second = CharacterFrame((layer,), QRectF(60, 130, 20, 20))
        with patch.object(self.win.scene, "sample", return_value=first) as sample:
            del self.win._region
            self.win.show()
            APP.processEvents()
            old_point = self.win.mapToGlobal(QPoint(25, 135))
            new_point = self.win.mapToGlobal(QPoint(65, 135))
            self.assertIs(self.win.displayed_character_frame, first)
            sample.return_value = second
            self.assertEqual(self.win._region(old_point), "head")
            self.assertIsNone(self.win._region(new_point))
            self.win.repaint()
            self.assertIs(self.win.displayed_character_frame, second)
            self.assertIsNone(self.win._region(old_point))
            self.assertEqual(self.win._region(new_point), "head")
            self.win.hide()
            self.assertIsNone(self.win.displayed_character_frame)

    def test_menu_includes_all_expression_categories(self):
        menu = self.win._tray_menu()
        submenu = menu.actions()[0].menu()
        self.assertEqual(menu.actions()[0].text(), "原立绘表情")
        self.assertNotIn("表情预览", [action.text() for action in menu.actions()])
        labels = [a.text() for a in submenu.actions()]
        for label in ("默认", "微笑", "开心大笑", "不好意思", "生气黑脸", "生气", "害羞", "哭泣", "恢复默认"):
            self.assertIn(label, labels)
        submenu.actions()[7].trigger()
        self.assertEqual(self.win.state["expression"], "cry")
        self.save.assert_not_called()

    def test_body_action_and_original_image_text_coexist(self):
        self.win.behaviors = Behaviors(
            self.win.state, self.win.bubbles, clock=lambda: self.now,
            animation_clips=self.win.scene.animations.metadata)
        self.win.behaviors.on_double_click("body", now=self.now)
        self.assertEqual(self.win.state["animation"]["clip"], "action_1")
        active = self.win.bubbles.active
        self.assertEqual(active["category"], "happy")
        self.assertTrue(active["text"])
        self.assertTrue(active["image"].endswith(("CH0335_spr_14.png", "CH0335_spr_15.png")))
        self.win.show()
        self.win.reaction_card.sync(active, self.now + .1, self.win.s)
        self.assertTrue(self.win.reaction_card.isVisible())
        self.assertEqual(self.win.state["animation"]["clip"], "action_1")
        self.win.hide()
        self.assertFalse(self.win.reaction_card.isVisible())
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
