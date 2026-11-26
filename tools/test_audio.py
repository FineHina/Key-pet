"""Audio routing tests without audible playback or workspace state writes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from pet.audio import AudioService, VoiceLibrary
from pet.config import DEFAULTS
from pet.notification_policy import NotificationPolicy

APP = QApplication.instance() or QApplication([])


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(DEFAULTS, voice_enabled=True, mute=False, dnd=False)
        self.policy = NotificationPolicy(self.settings, fullscreen_detector=lambda: False)
        self.now = 100.0
        self.audio = AudioService(self.policy, clock=lambda: self.now)
        self.play = patch.object(self.audio.player, "play")
        self.stop = patch.object(self.audio.player, "stop")
        self.source = patch.object(self.audio.player, "setSource")
        self.play_mock = self.play.start()
        self.stop_mock = self.stop.start()
        self.source.start()

    def tearDown(self):
        self.audio.shutdown()
        self.source.stop()
        self.stop.stop()
        self.play.stop()
        self.audio.deleteLater()

    def test_one_voice_rate_limit_and_no_backlog(self):
        entry = self.audio.library.choose("smile", self.settings)
        self.assertTrue(self.audio.play(entry, "r1", "smile"))
        self.now += .1
        self.assertFalse(self.audio.play(entry, "r2", "smile"))
        self.assertEqual(self.audio.current_reaction, "r1")
        self.assertEqual(self.play_mock.call_count, 1)
        self.now += 3.1
        self.assertTrue(self.audio.play(entry, "r3", "smile"))
        self.assertEqual(self.audio.current_reaction, "r3")
        self.assertEqual(self.play_mock.call_count, 2)

    def test_drag_has_longer_cooldown(self):
        entry = self.audio.library.choose("drag", self.settings)
        self.assertEqual(entry["cooldown"], 12)
        self.assertTrue(self.audio.play(entry, "r1", "drag"))
        self.now += 4
        self.assertFalse(self.audio.play(entry, "r2", "drag"))

    def test_played_voice_pairs_csv_translation_and_long_card(self):
        entry = self.audio.library.choose("happy", self.settings)
        self.audio.library.choose = lambda scene, settings, **kwargs: dict(entry, duration=8.2)
        reaction = {"text": "旧台词", "category": "happy", "t0": 100, "dur": 5}
        self.audio.respond(reaction, "happy")
        self.assertEqual(reaction["id"], self.audio.current_reaction)
        self.assertEqual(reaction["voice_id"], entry["id"])
        self.assertEqual(reaction["text"], entry["text"])
        self.assertNotIn("未试听核验", reaction["text"])
        self.assertNotIn("素材清单译文", reaction["text"])
        self.assertAlmostEqual(reaction["dur"], 8.7)

    def test_mute_and_dnd_stop_and_keep_text(self):
        entry = self.audio.library.choose("smile", self.settings)
        self.audio.play(entry, "r1", "smile")
        self.settings["dnd"] = True
        self.audio.apply_settings()
        self.assertIsNone(self.audio.current_reaction)
        reaction = {"text": "无声回应", "t0": 100, "dur": 5}
        self.audio.respond(reaction, "smile", explicit=True)
        self.assertEqual(reaction["text"], "无声回应")
        self.assertNotIn("voice_id", reaction)
        self.assertEqual(self.play_mock.call_count, 1)

    def test_missing_and_cry_are_silent_without_fabrication(self):
        self.assertIsNone(self.audio.library.source("not_a_real_source"))
        reaction = {"text": "呜……", "t0": 100, "dur": 5}
        self.audio.respond(reaction, "cry")
        self.assertEqual(self.play_mock.call_count, 0)
        self.assertEqual(reaction["text"], "呜……")
        self.assertIsNone(self.audio.library.choose("dark_angry", self.settings))

    def test_notice_priority_cannot_be_stopped_by_ordinary_pointer(self):
        entry = self.audio.library.choose("focus_complete", self.settings)
        self.assertTrue(self.audio.play(entry, "notice", "focus_complete", priority=True))
        self.assertFalse(self.audio.stop(force=False))
        self.now += .5
        ordinary = self.audio.library.choose("smile", self.settings)
        self.assertFalse(self.audio.play(ordinary, "touch", "smile"))
        self.assertEqual(self.audio.current_reaction, "notice")
        self.audio.stop()
        self.assertIsNone(self.audio.current_reaction)

    def test_expired_priority_does_not_lock_out_future_interactions(self):
        entry = self.audio.library.choose("focus_complete", self.settings)
        self.audio.play(entry, "notice", "focus_complete", priority=True)
        self.now = self.audio.current_until + 1
        ordinary = self.audio.library.choose("smile", self.settings)
        self.assertTrue(self.audio.play(ordinary, "touch", "smile"))
        self.assertEqual(self.audio.current_reaction, "touch")

    def test_catalog_includes_all_sources_and_recent_choice_avoids_repeating(self):
        library = self.audio.library
        self.assertEqual(len(library.entries()), len(library.sources))
        first = library.choose("happy", self.settings)
        library.mark_played(first["id"])
        second = library.choose("happy", self.settings)
        self.assertNotEqual(first["id"], second["id"])
        for scene in ("dark_angry", "cry"):
            self.assertIsNone(library.choose(scene, dict(self.settings, dark_angry_voice=True)))

    def test_audition_respects_dnd_and_can_stop(self):
        voice_id = "ch0335_exskill_2"
        self.assertTrue(self.audio.audition(voice_id))
        self.audio.stop()
        self.assertIsNone(self.audio.current_id)
        self.settings["mute"] = True
        with self.assertRaises(ValueError):
            self.audio.audition(voice_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
