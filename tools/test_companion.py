"""Local dialogue/calendar tests with fake dates and temporary storage."""
from datetime import datetime
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pet.audio import VoiceLibrary
from pet.calendar_events import CalendarEvents
from pet.config import validate
from pet.dialogue import DialogueService, TOPICS
from pet.storage import Storage


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "db.sqlite3")
        self.now = datetime(2026, 12, 25, 12).timestamp()
        self.settings = {"birthday_mmdd": "", "calendar_events_enabled": True}
        self.calendar = CalendarEvents(self.storage, self.settings, lambda: self.now)
        self.dialogue = DialogueService(VoiceLibrary(rng=random.Random(7)))

    def tearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def test_local_keywords_and_unknown_have_no_side_effects(self):
        for text, topic in (("你好", "greeting"), ("我好累想休息", "rest_advice"),
                            ("说说星空", "stars"), ("咖啡味道", "coffee"),
                            ("鼓励我学习", "encouragement"), ("讲个吐槽", "tease")):
            self.assertEqual(self.dialogue.route(text), topic)
        reply = self.dialogue.message("帮我打开浏览器并创建提醒")
        self.assertIsNone(reply["topic"])
        self.assertIsNone(reply["voice"])
        self.assertIn("本地", reply["text"])

    def test_topics_have_compatible_subtitles_and_no_archive_sources(self):
        for topic in TOPICS:
            for _ in range(5):
                reply = self.dialogue.topic(topic)
                self.assertTrue(reply["text"])
                voice = reply["voice"]
                self.assertIsNotNone(voice)
                self.assertIn(voice["grade"], ("A", "B"))
                self.assertEqual(reply["text"], voice["text"])
                self.assertNotIn("_radio", voice["id"])

    def test_session_history_is_bounded_and_clearable(self):
        for index in range(60):
            self.dialogue.remember(str(index), {"text": "回应", "topic": "greeting"})
        self.assertEqual(len(self.dialogue.history), 30)
        self.dialogue.clear()
        self.assertFalse(self.dialogue.history)

    def test_each_date_claimed_once_and_restart_keeps_claim(self):
        event = self.calendar.claim()
        self.assertEqual(event["event_key"], "xmas")
        self.assertIsNone(self.calendar.claim())
        second = CalendarEvents(self.storage, self.settings, lambda: self.now)
        self.assertIsNone(second.claim())

    def test_blocked_today_never_replays_when_unblocked(self):
        self.assertIsNone(self.calendar.claim(blocked=True))
        self.assertIsNone(self.calendar.claim(blocked=False))
        self.now = datetime(2027, 1, 1, 12).timestamp()
        self.assertEqual(self.calendar.claim()["event_key"], "newyear")

    def test_birthday_precedes_holiday_and_leap_date(self):
        self.settings["birthday_mmdd"] = "12-25"
        self.assertEqual(self.calendar.today()["event_key"], "birthday")
        self.calendar.claim()
        self.settings["birthday_mmdd"] = ""
        self.assertIsNone(self.calendar.claim())
        self.settings["birthday_mmdd"] = "02-29"
        self.now = datetime(2028, 2, 29, 12).timestamp()
        self.assertEqual(self.calendar.today()["event_key"], "birthday")
        self.now = datetime(2027, 2, 28, 12).timestamp()
        self.assertIsNone(self.calendar.today())

    def test_halloween_and_no_missed_date_replay(self):
        self.now = datetime(2026, 10, 31, 12).timestamp()
        self.assertEqual(self.calendar.today()["event_key"], "halloween")
        self.now = datetime(2026, 11, 1, 12).timestamp()
        self.assertIsNone(self.calendar.claim())

    def test_birthday_settings_validate_no_birth_year(self):
        self.assertEqual(validate({"birthday_mmdd": "02-29"})["birthday_mmdd"], "02-29")
        for value in ("02-30", "2026-01-01", "1-1", None):
            with self.assertRaises(ValueError):
                validate({"birthday_mmdd": value})


if __name__ == "__main__":
    unittest.main(verbosity=2)
