"""Controller integration and original voice pairing with local temporary data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from pet import config
from pet.assistant import AssistantController
from pet.audio import VoiceLibrary
from pet.window import PetWindow

APP = QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.value = 1700000000.0
    def __call__(self):
        return self.value


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.config_patch = patch.object(config, "PATH", str(self.path / "config.json"))
        self.config_patch.start()
        config.save({"mute": True})
        self.tray_patch = patch.object(PetWindow, "_tray_init", lambda self: None)
        self.tray_patch.start()
        self.pet = PetWindow(enable_assistant=False)
        self.clock = Clock()
        self.controller = AssistantController(self.pet, self.path / "assistant.sqlite3",
                                              wall_clock=self.clock, mono_clock=self.clock, start_timer=False)
        self.controller.attach_windows()

    def tearDown(self):
        self.controller.shutdown()
        self.pet.hide()
        self.pet.deleteLater()
        self.tray_patch.stop()
        self.config_patch.stop()
        self.temp.cleanup()

    def test_focus_completes_once_while_pet_hidden(self):
        self.controller.start_focus(.01)
        self.pet.hide()
        self.clock.value += 1
        self.controller.tick()
        self.assertIsNone(self.controller.focus_snapshot())
        self.assertEqual(len(self.controller.pending_notifications()), 1)
        self.controller.tick()
        self.assertEqual(len(self.controller.pending_notifications()), 1)

    def test_dnd_persists_reminder_and_does_not_replay_when_disabled(self):
        self.controller.update_settings({"dnd": True})
        self.controller.add_reminder("测试事项", self.clock.value + 1)
        self.clock.value += 2
        self.controller.tick()
        self.assertEqual(len(self.controller.pending_notifications()), 1)
        self.assertFalse(self.controller.notification_window.isVisible())
        self.controller.update_settings({"dnd": False})
        self.controller.tick()
        self.assertFalse(self.controller.notification_window.isVisible())
        self.assertEqual(len(self.controller.pending_notifications()), 1)

    def test_reminder_snooze_complete_and_cancel(self):
        row = self.controller.add_reminder("测试", self.clock.value + 1)
        self.clock.value += 2
        self.controller.tick()
        notification = self.controller.pending_notifications()[0]
        self.controller.snooze_notification(notification["id"], 5)
        self.assertFalse(self.controller.pending_notifications())
        self.clock.value += 301
        self.controller.tick()
        due = self.controller.pending_notifications()[0]
        self.controller.complete_notification(due["id"])
        self.assertFalse(self.controller.pending_notifications())
        self.assertEqual(self.controller.reminders_list()[0]["status"], "done")
        second = self.controller.add_reminder("取消", self.clock.value + 20)
        self.controller.cancel_reminder(second["id"])
        self.assertEqual(self.controller.reminders_list()[-1]["status"], "cancelled")

    def test_voice_inventory_exact_ids_and_quiet_categories(self):
        library = VoiceLibrary()
        self.assertEqual(len(library.sources), 152)
        self.assertFalse(library.scenes["cry"]["sources"])
        self.assertIsNone(library.choose("dark_angry", self.controller.settings))
        for scene in ("smile", "happy", "blush", "drag", "focus_complete"):
            source = library.choose(scene, self.controller.settings)
            self.assertTrue(source["path"].is_file())
            self.assertGreater(source["duration"], 0)
            self.assertTrue(source["text"])
        self.assertEqual(library.scenes["focus_start"]["sources"],
                         ["ch0335_login_2_1", "ch0335_carrier_monolog_1", "ch0335_eventmission_login_1_1"])

    def test_muted_interaction_retains_original_card_without_player(self):
        self.pet.bubbles.show("smile", self.clock.value)
        self.controller.pointer_event("click")
        self.assertIn("CH0335_spr_", self.pet.bubbles.active["image"])
        self.assertIsNone(self.controller.audio.current_id)
        self.assertEqual(self.pet.bubbles.active["dur"], 5)

    def test_settings_preserve_geometry_and_dnd_stops_current_audio(self):
        config.save({"x": 123, "y": 456, "scale": .42})
        self.controller.update_settings({"voice_volume": 20, "dnd": True})
        loaded = config.load()
        self.assertEqual((loaded["x"], loaded["y"]), (123, 456))
        self.assertEqual(loaded["voice_volume"], 20)
        self.assertFalse(self.controller.policy.can_voice())

    def test_break_uses_rest_pool_and_reminder_completion_is_once(self):
        with patch.object(self.controller, "_feature_reaction") as react:
            self.controller.start_focus(5, kind="break")
            self.assertEqual(react.call_args.args[0], "break_start")
            self.controller.cancel_focus()
            self.controller.add_reminder("完成测试", self.clock.value + 1)
            self.clock.value += 2
            self.controller.tick()
            notification = self.controller.pending_notifications()[0]
            react.reset_mock()
            self.controller.complete_notification(notification["id"])
            self.assertEqual(react.call_count, 1)
            self.assertEqual(react.call_args.args[0], "reminder_complete")
            self.controller.complete_notification(notification["id"])
            self.assertEqual(react.call_count, 1)

    def test_hover_voice_default_off_and_manual_topic_is_explicit(self):
        with patch.object(self.controller.audio, "respond") as respond:
            self.pet.bubbles.show("embarrassed", self.clock.value)
            self.controller.pointer_event("hover")
            respond.assert_not_called()
        self.controller.settings.update(mute=True, dnd=False)
        reply = self.controller.speak_topic("encouragement")
        self.assertTrue(reply["text"])
        self.assertEqual(reply["topic"], "encouragement")
        self.assertTrue(reply["image"])
        self.assertIsNone(self.controller.audio.current_id)
        with self.assertRaises(ValueError):
            self.controller.speak_topic("invented")

    def test_priority_voice_keeps_matching_card_during_pointer_reaction(self):
        protected = {"id": "notice", "text": "专注完成", "category": "happy", "t0": 1, "dur": 5}
        self.controller._protected_reaction = protected
        self.controller.audio.current_reaction = "notice"
        self.controller.audio.current_priority = 1
        self.controller.audio.current_until = self.clock.value + 10
        self.pet.bubbles.show("smile", self.clock.value)
        self.controller.pointer_event("click")
        self.assertIs(self.pet.bubbles.active, protected)
        self.controller.stop_audio()
        self.controller.pointer_event("release")
        self.assertIsNone(self.controller._protected_reaction)

    def test_dialogue_under_dnd_has_same_chinese_image_without_audio(self):
        self.controller.settings.update(dnd=True, mute=True)
        reply = self.controller.chat("我想看看星空")
        self.assertEqual(reply["topic"], "stars")
        self.assertTrue(reply["text"])
        self.assertIn("CH0335_spr_", reply["image"])
        self.assertIsNone(self.controller.audio.current_id)
        self.assertEqual(reply["text"], self.pet.bubbles.active["text"])

    def test_calendar_dnd_skip_persists_after_disable_and_restart(self):
        from datetime import datetime
        self.clock.value = datetime(2026, 12, 25, 12).timestamp()
        self.controller.settings.update(dnd=True, mute=False)
        self.controller._calendar_ready_at = 0
        self.controller.tick()
        row = self.controller.storage.conn.execute("SELECT * FROM calendar_events").fetchone()
        self.assertEqual(row["outcome"], "skipped")
        self.controller.settings["dnd"] = False
        with patch.object(self.controller, "_show_calendar") as show:
            self.controller.tick()
            show.assert_not_called()

    def test_shortcuts_execute_only_explicit_open_and_delete_keeps_target(self):
        target = self.path / "note.txt"
        target.write_text("keep", encoding="utf-8")
        opened = []
        self.controller.shortcuts.opener = lambda kind, path: opened.append((kind, path)) or True
        item = self.controller.add_shortcut("笔记", "file", str(target))
        self.controller.tick()
        self.assertFalse(opened)
        self.controller.chat("请帮我打开笔记")
        self.assertFalse(opened)
        result = self.controller.open_shortcut(item["id"])
        self.assertIn("已发起打开", result["message"])
        self.assertEqual(len(opened), 1)
        self.controller.delete_shortcut(item["id"])
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.controller.shortcuts_list())

    def test_assistant_has_two_companion_tabs(self):
        names = [self.controller.panel.tabs.tabText(i) for i in range(self.controller.panel.tabs.count())]
        self.assertIn("陪伴对话", names)
        self.assertIn("快捷工具", names)

    def test_single_instance_lock_blocks_duplicate(self):
        from PySide6.QtCore import QLockFile
        path = str(self.path / "instance.lock")
        first, second = QLockFile(path), QLockFile(path)
        first.setStaleLockTime(0)
        second.setStaleLockTime(0)
        self.assertTrue(first.tryLock(0))
        self.assertFalse(second.tryLock(0))
        first.unlock()
        self.assertTrue(second.tryLock(0))
        second.unlock()

    def test_panel_close_keeps_services_and_notifications_unresolved(self):
        self.controller.open_panel()
        self.controller.panel.close()
        self.assertFalse(self.controller._closed)
        self.controller.add_reminder("留下", self.clock.value + 1)
        self.clock.value += 2
        self.controller.tick()
        self.controller.notification_window.close()
        self.assertEqual(len(self.controller.pending_notifications()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
