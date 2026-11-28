"""Controller integration and original voice pairing with local temporary data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import datetime
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
from pet.interaction_feedback import CUES
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

    def test_interaction_voices_resolve_and_food_pairs_same_subtitle_and_pose(self):
        library = self.controller.audio.library
        for cue in CUES.values():
            for voice_id in cue.voices:
                entry = library.source(voice_id)
                self.assertTrue(entry and entry["text"], voice_id)
                self.assertEqual(entry["grade"], "A")
        self.pet.show()
        self.pet._frame.stop()
        self.controller.settings.update(mute=False, dnd=False, fullscreen_dnd=False)
        with patch.object(self.controller.audio, "play", return_value=True) as play:
            self.controller.care_for_key("milk_tea_hot")
        entry, identifier, scene = play.call_args.args
        self.assertEqual((entry["id"], scene), ("ch0335_cafe_monolog_2", "interaction_food"))
        self.assertEqual(self.pet.state["reaction"]["kind"], "curious")
        card = self.pet.bubbles.active
        self.assertEqual(card["id"], identifier)
        self.assertIn("热奶茶", card["text"])
        self.assertIn(entry["text"], card["text"])

    def test_ordinary_gift_plays_the_original_gesture_and_returns_to_idle(self):
        scene = self.pet.scene
        self.pet.show()
        self.pet._frame.stop()
        self.pet._hit.stop()
        gift = self.controller.grant_startup_gift()
        self.controller.give_kei_gift(gift)

        reaction = self.pet.state["reaction"]
        self.assertEqual(reaction["kind"], "receive")
        halfway = self.clock.value + reaction["dur"] / 2
        holding = scene.sample(halfway, self.pet.state, 1)
        idle = scene.sample(halfway, {}, 1)
        self.assertEqual(len(holding.layers), len(idle.layers))
        self.assertNotEqual(holding.layers[-1].transform, idle.layers[-1].transform)
        after = self.clock.value + reaction["dur"] + .2
        self.pet.behaviors.tick(after)
        self.assertIsNone(self.pet.state["reaction"])
        recovered = scene.sample(after, self.pet.state, 1)
        self.assertEqual(len(recovered.layers), len(idle.layers))
        self.assertEqual([layer.transform for layer in recovered.layers],
                         [layer.transform for layer in scene.sample(after, {}, 1).layers])
        self.assertEqual(self.controller.interaction.inventory()[gift], (0, 1))

    def test_affection_milestone_replaces_ordinary_gift_voice(self):
        self.controller.settings.update(mute=False, dnd=False, fullscreen_dnd=False)
        gift = self.controller.grant_startup_gift()
        self.controller.storage.conn.execute("UPDATE companion_state SET affection = 69 WHERE id = 1")
        with patch.object(self.controller.audio, "play", return_value=True) as play:
            self.controller.give_kei_gift(gift)
        play.assert_called_once()
        self.assertEqual(play.call_args.args[0]["id"], "ch0335_battle_buffed_1")
        self.assertEqual(self.controller.interaction.inventory()[gift], (0, 1))
        self.assertEqual(self.controller.interaction.status()[0], 71)

    def test_game_result_and_reward_view_each_play_at_most_once(self):
        self.controller.settings.update(mute=False, dnd=False, fullscreen_dnd=False)
        with patch.object(self.controller.audio, "play", return_value=True) as play:
            pack, _ = self.controller.finish_interaction_game("rps", "win", outcome="kei_win")
            self.controller.finish_interaction_game("rps", "win", outcome="kei_win")
            self.controller.finish_interaction_game("rps", "draw", outcome="draw")
            self.controller.view_interaction_reward(pack)
            self.controller.view_interaction_reward(pack)
            self.controller.reveal_gift(pack, 0)
        self.assertEqual([call.args[0]["id"] for call in play.call_args_list],
                         ["ch0335_battle_victory_1", "ch0335_eventmission_get_1_1"])

    def test_interaction_respects_muting_dnd_and_does_not_replace_priority_card(self):
        self.pet.show()
        self.pet._frame.stop()
        self.controller.settings.update(mute=True, dnd=False, fullscreen_dnd=False)
        with patch.object(self.controller.audio, "play") as play:
            self.controller.care_for_key("rest")
            self.assertEqual(self.pet.state["reaction"]["kind"], "relax")
            self.controller.settings["dnd"] = True
            self.controller.care_for_key("cake")
            self.assertEqual(self.pet.state["reaction"]["kind"], "relax")
            self.controller.settings.update(mute=False, dnd=False)
            self.controller.audio.current_id = "priority"
            self.controller.audio.current_until = self.clock.value + 10
            self.controller.audio.current_priority = 1
            self.pet.bubbles.show("smile", self.clock.value)
            notice = self.pet.bubbles.active
            self.controller.care_for_key("milk_tea_cold")
            self.assertIs(self.pet.bubbles.active, notice)
            self.assertEqual(self.pet.state["reaction"]["kind"], "relax")
            play.assert_not_called()

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

    def test_startup_greeting_runs_once_and_uses_original_greeting_pool(self):
        self.clock.value = datetime(2026, 11, 27, 9).timestamp()
        self.controller.settings.update(mute=False, dnd=False, fullscreen_dnd=False)
        self.pet.show()
        with patch.object(self.controller.audio, "play", return_value=True) as play:
            self.controller.play_startup_greeting()
            self.controller.play_startup_greeting()
        play.assert_called_once()
        voice, reaction_id, scene = play.call_args.args
        self.assertEqual(scene, "greeting")
        self.assertIn(voice["id"], self.controller.audio.library.scenes["greeting"]["sources"])
        self.assertIn("早上好", self.pet.bubbles.active["text"])
        self.assertIn(voice["text"], self.pet.bubbles.active["text"])
        self.assertEqual(self.pet.bubbles.active["id"], reaction_id)

    def test_startup_uses_the_right_date_voice_then_falls_back_on_same_day(self):
        self.controller.settings.update(mute=False, dnd=False, fullscreen_dnd=False)
        self.pet.show()
        dates = (
            (2027, 1, 1, "", "ch0335_season_newyear_1"),
            (2027, 10, 31, "", "ch0335_season_halloween_1"),
            (2027, 12, 25, "", "ch0335_season_xmas_1"),
            (2028, 1, 1, "01-01", "ch0335_season_birthday_player_7"),
        )
        for year, month, day, birthday, voice_id in dates:
            with self.subTest(voice_id=voice_id):
                self.clock.value = datetime(year, month, day, 12).timestamp()
                self.controller.settings["birthday_mmdd"] = birthday
                self.controller._startup_greeted = False
                self.pet.bubbles.clear()
                self.pet.behaviors.restore_default()
                with patch.object(self.controller.audio, "play", return_value=True) as play:
                    self.controller.play_startup_greeting()
                    self.controller._calendar_tick()
                play.assert_called_once()
                self.assertEqual(play.call_args.args[0]["id"], voice_id)
        self.controller._startup_greeted = False
        self.pet.behaviors.restore_default()
        with patch.object(self.controller.audio, "play", return_value=True) as play:
            self.controller.play_startup_greeting()
        self.assertEqual(play.call_args.args[2], "greeting")

    def test_startup_dnd_skips_without_late_playback(self):
        self.clock.value = datetime(2027, 12, 25, 9).timestamp()
        self.controller.settings.update(mute=False, dnd=True, fullscreen_dnd=False)
        self.pet.show()
        with patch.object(self.controller.audio, "play") as play:
            self.controller.play_startup_greeting()
            self.controller.settings["dnd"] = False
            self.clock.value += 10
            self.controller.tick()
            self.controller.play_startup_greeting()
        play.assert_not_called()
        row = self.controller.storage.conn.execute("SELECT outcome FROM calendar_events").fetchone()
        self.assertEqual(row["outcome"], "skipped")

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
