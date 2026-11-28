"""Headless deterministic service tests. Run: python -I tools/test_assistant_services.py."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet import config
from pet.focus import FocusService
from pet.notification_policy import NotificationPolicy, detect_fullscreen
from pet.reminders import ReminderService
from pet.storage import Storage


class Clock:
    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "assistant.db"
        self.storage = Storage(self.path)
        self.wall = Clock()
        self.mono = Clock(100)
        self.focus = FocusService(self.storage, self.wall, self.mono)
        self.reminders = ReminderService(self.storage, self.wall)

    def tearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def advance(self, seconds):
        self.wall.advance(seconds)
        self.mono.advance(seconds)

    def restart(self):
        self.storage.close()
        self.storage = Storage(self.path)
        self.focus = FocusService(self.storage, self.wall, self.mono)
        self.reminders = ReminderService(self.storage, self.wall)

    def count(self, table):
        return self.storage.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_migration_and_schema_reopen(self):
        self.assertEqual(self.storage.conn.execute("PRAGMA user_version").fetchone()[0], 4)
        self.assertEqual(self.count("focus_sessions"), 0)
        self.restart()
        self.assertEqual(self.storage.conn.execute("PRAGMA user_version").fetchone()[0], 4)
        self.assertEqual(self.storage.pending(), [])

    def test_future_schema_rejected(self):
        path = Path(self.temp.name) / "future.db"
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA user_version = 5")
        finally:
            conn.close()
        with self.assertRaisesRegex(ValueError, "schema version"):
            Storage(path)

    def test_one_active_database_constraint_running_and_paused(self):
        self.focus.start(1)
        other = Storage(self.path)
        try:
            duplicate = FocusService(other, self.wall, self.mono)
            with self.assertRaisesRegex(ValueError, "already active"):
                duplicate.start(1, "break")
            self.focus.pause()
            with self.assertRaises(sqlite3.IntegrityError):
                with other.transaction():
                    other.conn.execute(
                        """INSERT INTO focus_sessions
                            (kind,state,duration,remaining,deadline,started_at,updated_at)
                            VALUES ('focus','paused',60,60,NULL,1000,1000)""")
            with self.assertRaises(ValueError):
                duplicate.start(1)
        finally:
            other.close()
        self.assertEqual(self.count("focus_sessions"), 1)

    def test_start_snapshot_pause_resume_cancel(self):
        started = self.focus.start(2)
        self.assertEqual(set(started), {"id", "kind", "state", "duration", "remaining", "deadline"})
        self.assertEqual((started["kind"], started["duration"], started["remaining"]), ("focus", 120, 120))
        self.advance(30)
        self.assertEqual(self.focus.snapshot()["remaining"], 90)
        paused = self.focus.pause()
        self.assertEqual((paused["state"], paused["remaining"], paused["deadline"]), ("paused", 90, None))
        self.advance(600)
        self.assertEqual(self.focus.snapshot()["remaining"], 90)
        self.assertEqual(self.focus.tick(), [])
        self.assertEqual(self.focus.pause(), paused)
        resumed = self.focus.resume()
        self.assertEqual(resumed["deadline"], self.wall.now + 90)
        self.advance(10)
        self.assertEqual(self.focus.resume()["remaining"], 80)
        cancelled = self.focus.cancel()
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIsNone(self.focus.snapshot())
        self.advance(1000)
        self.assertEqual(self.focus.tick(), [])
        self.assertEqual(self.storage.pending(), [])
        self.assertIsNone(self.focus.cancel())
        self.assertEqual(self.count("focus_sessions"), 1)
        self.assertEqual(self.focus.start(5, "break")["kind"], "break")

    def test_invalid_focus_durations_and_kind_do_not_create_sessions(self):
        for value in (0, -1, 1440.01, float("nan"), float("inf"), "25", None, True, 5e-324):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.focus.start(value)
        with self.assertRaises(ValueError):
            self.focus.start(1, "unknown")
        self.assertEqual(self.count("focus_sessions"), 0)
        self.assertEqual(self.focus.start(1440)["duration"], 86400)

    def test_absent_pause_resume_raise(self):
        for method in (self.focus.pause, self.focus.resume):
            with self.assertRaises(ValueError):
                method()
        self.assertIsNone(self.focus.snapshot())

    def test_expiration_is_explicit_tick_once_and_survives_restart(self):
        session = self.focus.start(1)
        self.advance(60)
        self.assertEqual(self.focus.snapshot()["remaining"], 0)
        self.assertEqual(self.count("notifications"), 0)
        self.assertEqual(self.focus.snapshot()["state"], "running")
        with self.assertRaises(ValueError):
            self.focus.pause()
        new = self.focus.tick()
        self.assertEqual(len(new), 1)
        self.assertEqual(set(new[0]), {"id", "kind", "entity_id", "title", "body", "due_at"})
        self.assertEqual((new[0]["kind"], new[0]["entity_id"], new[0]["due_at"]),
                         ("focus", session["id"], session["deadline"]))
        self.assertIsNone(self.focus.snapshot())
        self.assertEqual(self.focus.tick(), [])
        self.restart()
        self.assertEqual(self.focus.tick(), [])
        self.assertEqual(self.count("notifications"), 1)
        row = self.storage.conn.execute("SELECT state FROM focus_sessions").fetchone()
        self.assertEqual(row["state"], "completed")
        self.assertFalse(self.storage.pending()[0]["presented"])

    def test_overdue_snapshot_does_not_drift_notification_due(self):
        session = self.focus.start(1)
        self.advance(70)
        self.focus.snapshot()
        self.advance(20)
        self.focus.snapshot()
        self.advance(30)
        self.assertEqual(self.focus.tick()[0]["due_at"], session["deadline"])

    def test_running_restores_deadline_and_generates_overdue_on_tick(self):
        session = self.focus.start(2)
        self.advance(30)
        self.restart()
        self.assertEqual(self.focus.snapshot()["remaining"], 90)
        self.assertEqual(self.focus.snapshot()["deadline"], session["deadline"])
        self.advance(200)
        self.restart()
        self.assertEqual(self.count("notifications"), 0)
        self.assertEqual(self.focus.snapshot()["remaining"], 0)
        self.assertEqual(self.focus.tick()[0]["due_at"], session["deadline"])
        self.assertEqual(self.focus.tick(), [])

    def test_paused_restores_without_elapsed_time(self):
        self.focus.start(2)
        self.advance(25)
        self.focus.pause()
        self.advance(10000)
        self.restart()
        state = self.focus.snapshot()
        self.assertEqual((state["state"], state["remaining"], state["deadline"]), ("paused", 95, None))
        self.focus.resume()
        self.advance(95)
        self.assertEqual(len(self.focus.tick()), 1)

    def test_wall_backward_monotonic_still_counts(self):
        self.focus.start(2)
        self.wall.advance(-3600)
        self.mono.advance(30)
        state = self.focus.snapshot()
        self.assertEqual(state["remaining"], 90)
        self.assertEqual(state["deadline"], self.wall.now + 90)
        self.advance(90)
        self.assertEqual(len(self.focus.tick()), 1)

    def test_suspend_wall_elapsed_wins_when_monotonic_frozen(self):
        self.focus.start(1)
        self.wall.advance(80)
        self.assertEqual(len(self.focus.tick()), 1)
        self.assertEqual(self.focus.tick(), [])

    def test_sleep_clock_distinguishes_forward_wall_correction(self):
        sleep = Clock(50)
        self.focus._sleep_clock = sleep
        self.focus.start(2)
        self.wall.advance(3600)
        self.mono.advance(10)
        sleep.advance(10)
        state = self.focus.snapshot()
        self.assertEqual(state["remaining"], 110)
        self.assertEqual(state["deadline"], self.wall.now + 110)
        self.assertEqual(self.focus.tick(), [])
        self.wall.advance(110)
        sleep.advance(110)
        self.assertEqual(len(self.focus.tick()), 1)

    def test_break_notification_ack_keeps_history(self):
        session = self.focus.start(1, "break")
        self.advance(60)
        notification = self.focus.tick()[0]
        self.assertEqual(notification["kind"], "break")
        self.storage.acknowledge(notification["id"])
        self.storage.acknowledge(notification["id"])
        self.assertEqual(self.storage.pending(), [])
        self.assertEqual(self.count("focus_sessions"), 1)
        self.assertEqual(self.count("notifications"), 1)
        row = self.storage.conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (session["id"],)).fetchone()
        self.assertEqual(row["state"], "completed")

    def test_focus_completion_and_insert_roll_back_together(self):
        self.focus.start(1)
        self.advance(60)
        with patch.object(self.storage, "_insert_notification", side_effect=RuntimeError("insertion failed")):
            with self.assertRaises(RuntimeError):
                self.focus.tick()
        self.assertEqual(self.count("notifications"), 0)
        self.assertEqual(self.focus.snapshot()["state"], "running")
        self.assertEqual(len(self.focus.tick()), 1)

    def test_notifications_dedupe_pending_presented_and_acknowledged(self):
        item = self.reminders.add("喝水", self.wall.now + 10)
        self.advance(10)
        notification = self.reminders.tick()[0]
        self.storage.mark_presented([notification["id"], notification["id"], 999])
        self.assertTrue(self.storage.pending()[0]["presented"])
        self.restart()
        self.assertTrue(self.storage.pending()[0]["presented"])
        row = self.storage.conn.execute("SELECT * FROM notifications").fetchone()
        with self.storage.transaction():
            duplicate = self.storage._insert_notification(
                row["kind"], row["entity_id"], row["occurrence_key"], row["title"], row["body"], row["due_at"])
        self.assertIsNone(duplicate)
        self.storage.acknowledge(notification["id"])
        self.storage.acknowledge(notification["id"])
        self.assertEqual(self.reminders.list_all()[0]["status"], "done")
        self.assertEqual(self.storage.pending(), [])
        self.assertEqual(self.count("reminders"), 1)
        self.assertEqual(self.count("notifications"), 1)
        self.assertEqual(self.reminders.list_all()[0]["id"], item["id"])
        with self.assertRaises(ValueError):
            self.storage.acknowledge(999)

    def test_reminder_add_edit_list_validation(self):
        due = self.wall.now + 60
        item = self.reminders.add("  复习  ", due)
        self.assertEqual(item, {"id": item["id"], "title": "复习", "due_at": due, "status": "scheduled", "time_mode": "absolute"})
        edited = self.reminders.edit(item["id"], "吃饭", due + 120)
        self.assertEqual(edited["title"], "吃饭")
        self.assertEqual(self.reminders.list_all(), [edited])
        for title in ("", " \t\n", "x" * 501, None, 4):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.reminders.add(title, due)
        for invalid in (self.wall.now, self.wall.now - 1, float("nan"), float("inf"), "1100", True):
            with self.subTest(due=invalid), self.assertRaises(ValueError):
                self.reminders.add("test", invalid)
        self.assertEqual(self.count("reminders"), 1)
        self.assertEqual(len(self.reminders.add("x" * 500, due)["title"]), 500)

    def test_pending_edits_replace_only_the_previous_occurrence(self):
        item = self.reminders.add("喝水", self.wall.now + 30, time_mode="relative")
        self.advance(40)
        self.restart()
        old, = self.reminders.tick()
        edited = self.reminders.edit(item["id"], "编辑描述")
        self.assertEqual(edited["status"], "pending")
        self.assertEqual(self.storage.pending()[0]["id"], old["id"])
        self.assertEqual(self.storage.pending()[0]["body"], "编辑描述")
        self.reminders.edit(item["id"], "新计划", self.wall.now + 30, time_mode="absolute")
        self.assertEqual(self.storage.pending(), [])
        self.restart()
        self.assertEqual(self.reminders.tick(), [])
        self.advance(30)
        new, = self.reminders.tick()
        self.assertNotEqual(new["id"], old["id"])
        self.storage.acknowledge(old["id"])
        self.assertEqual(self.storage.pending()[0]["id"], new["id"])
        self.assertEqual(self.reminders.tick(), [])

    def test_edit_historical_reminders_keep_status_unless_rearmed_and_delete_scopes(self):
        done = self.reminders.add("已完成", self.wall.now + 10)
        cancelled = self.reminders.add("已取消", self.wall.now + 10)
        self.reminders.cancel(cancelled["id"])
        self.advance(10)
        old, = self.reminders.tick()
        self.storage.acknowledge(old["id"])
        self.assertEqual(self.reminders.edit(done["id"], "历史修订", self.wall.now - 60)["status"], "done")
        self.assertEqual(self.reminders.edit(cancelled["id"], "取消修订")["status"], "cancelled")
        self.assertEqual(self.reminders.tick(), [])
        with self.assertRaises(ValueError):
            self.reminders.edit(done["id"], "不能重启", self.wall.now - 1, rearm=True)
        due = self.wall.now + 20
        self.assertEqual(self.reminders.edit(done["id"], "重新提醒", due, rearm=True)["status"], "scheduled")
        self.restart()
        self.advance(20)
        new, = self.reminders.tick()
        self.storage._insert_notification("focus", done["id"], "unrelated", "focus", "focus", due)
        self.reminders.delete(done["id"])
        with self.assertRaises(ValueError):
            self.reminders.delete(done["id"])
        self.restart()
        self.assertEqual([row["id"] for row in self.reminders.list_all()], [cancelled["id"]])
        self.assertEqual([row["kind"] for row in self.storage.pending()], ["focus"])
        self.assertEqual(self.count("notifications"), 1)

    def test_cancel_and_complete_scheduled_or_pending_reminders(self):
        a = self.reminders.add("cancel", self.wall.now + 10)
        b = self.reminders.add("complete", self.wall.now + 10)
        self.assertEqual(self.reminders.cancel(a["id"])["status"], "cancelled")
        self.assertEqual(self.reminders.complete(b["id"])["status"], "done")
        self.advance(10)
        self.assertEqual(self.reminders.tick(), [])
        c = self.reminders.add("pending cancel", self.wall.now + 10)
        d = self.reminders.add("pending complete", self.wall.now + 10)
        self.advance(10)
        self.assertEqual(len(self.reminders.tick()), 2)
        self.reminders.cancel(c["id"])
        self.reminders.complete(d["id"])
        self.assertEqual(self.storage.pending(), [])
        self.assertEqual(self.reminders.cancel(c["id"])["status"], "cancelled")
        self.assertEqual(self.reminders.complete(d["id"])["status"], "done")
        self.assertEqual(self.count("reminders"), 4)
        self.assertEqual(self.count("notifications"), 2)

    def test_snooze_new_occurrence_dedup_and_old_ack_safe(self):
        item = self.reminders.add("喝水", self.wall.now + 10)
        self.advance(10)
        first = self.reminders.tick()[0]
        self.storage.mark_presented([first["id"]])
        snoozed = self.reminders.snooze(first["id"], 5)
        self.assertEqual((snoozed["status"], snoozed["due_at"]), ("scheduled", self.wall.now + 300))
        self.assertEqual(self.storage.pending(), [])
        self.storage.acknowledge(first["id"])
        self.assertEqual(self.reminders.list_all()[0]["status"], "scheduled")
        with self.assertRaises(ValueError):
            self.reminders.snooze(first["id"], 5)
        self.restart()
        self.advance(299)
        self.assertEqual(self.reminders.tick(), [])
        self.advance(1)
        second = self.reminders.tick()[0]
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["entity_id"], item["id"])
        self.assertEqual(self.reminders.tick(), [])
        self.storage.acknowledge(first["id"])
        self.assertEqual(self.reminders.list_all()[0]["status"], "pending")
        self.storage.acknowledge(second["id"])
        self.assertEqual(self.reminders.list_all()[0]["status"], "done")
        self.assertEqual(self.count("notifications"), 2)
        keys = [row[0] for row in self.storage.conn.execute("SELECT occurrence_key FROM notifications")]
        self.assertEqual(len(set(keys)), 2)
        self.assertTrue(all(key.startswith(f"{item['id']}:") for key in keys))

    def test_snooze_validation_leaves_pending(self):
        item = self.reminders.add("test", self.wall.now + 1)
        self.advance(1)
        notification = self.reminders.tick()[0]
        for minutes in (0, -1, 1441, float("nan"), float("inf"), True, "5"):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                self.reminders.snooze(notification["id"], minutes)
        self.assertEqual(self.reminders.list_all()[0]["status"], "pending")
        self.assertEqual(len(self.storage.pending()), 1)
        self.reminders.complete(item["id"])
        with self.assertRaises(ValueError):
            self.reminders.snooze(notification["id"], 5)

    def test_reminder_insert_failure_rolls_back_pending_state(self):
        self.reminders.add("test", self.wall.now + 1)
        self.advance(1)
        with patch.object(self.storage, "_insert_notification", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                self.reminders.tick()
        self.assertEqual(self.reminders.list_all()[0]["status"], "scheduled")
        self.assertEqual(self.storage.pending(), [])
        self.assertEqual(len(self.reminders.tick()), 1)

    def test_pending_sort_and_nested_transaction_rollback(self):
        self.reminders.add("later", self.wall.now + 20)
        self.reminders.add("earlier", self.wall.now + 10)
        self.advance(20)
        self.assertEqual([item["body"] for item in self.reminders.tick()], ["earlier", "later"])
        self.assertEqual([item["body"] for item in self.storage.pending()], ["earlier", "later"])
        with self.assertRaises(RuntimeError):
            with self.storage.transaction():
                self.storage.mark_presented([item["id"] for item in self.storage.pending()])
                raise RuntimeError("rollback")
        self.assertTrue(all(not item["presented"] for item in self.storage.pending()))


class PolicyTests(unittest.TestCase):
    @staticmethod
    def clock_at(hour, minute=0):
        return Clock(datetime(2026, 11, 24, hour, minute).timestamp())

    def test_defaults_manual_dnd_voice_and_explicit_reactions(self):
        settings = dict(config.DEFAULTS)
        policy = NotificationPolicy(settings, lambda: False, self.clock_at(12))
        self.assertEqual(policy.reasons(), [])
        self.assertTrue(policy.can_notify())
        self.assertTrue(policy.can_voice())
        self.assertTrue(policy.can_react(False))
        settings["dnd"] = True
        self.assertEqual(policy.reasons(), ["手动免打扰"])
        self.assertTrue(policy.is_dnd())
        self.assertFalse(policy.can_notify())
        self.assertFalse(policy.can_voice())
        self.assertFalse(policy.can_react(False))
        self.assertTrue(policy.can_react())
        settings["dnd"] = False
        settings["mute"] = True
        self.assertTrue(policy.can_notify())
        self.assertFalse(policy.can_voice())
        settings["mute"] = False
        settings["voice_enabled"] = False
        self.assertFalse(policy.can_voice())

    def test_quiet_hours_cross_midnight_boundaries(self):
        for hour, minute, expected in ((22, 59, False), (23, 0, True), (23, 59, True),
                                        (0, 0, True), (7, 59, True), (8, 0, False), (12, 0, False)):
            with self.subTest(hour=hour, minute=minute):
                settings = {"quiet_hours_enabled": True, "quiet_start": "23:00", "quiet_end": "08:00"}
                policy = NotificationPolicy(settings, lambda: False, self.clock_at(hour, minute))
                self.assertEqual(policy.is_dnd(), expected)
                self.assertEqual(policy.reasons(), ["夜间免打扰"] if expected else [])

    def test_daytime_and_equal_endpoints_all_day(self):
        for hour, expected in ((8, False), (9, True), (16, True), (17, False), (23, False)):
            policy = NotificationPolicy({"quiet_hours_enabled": True, "quiet_start": "09:00", "quiet_end": "17:00"},
                                        lambda: False, self.clock_at(hour))
            self.assertEqual(policy.is_dnd(), expected)
        for hour in (0, 8, 12, 23):
            policy = NotificationPolicy({"quiet_hours_enabled": True, "quiet_start": "08:00", "quiet_end": "08:00"},
                                        lambda: False, self.clock_at(hour))
            self.assertTrue(policy.is_dnd())

    def test_combined_fullscreen_reasons_and_failure_safe(self):
        policy = NotificationPolicy({"dnd": True, "quiet_hours_enabled": True, "fullscreen_dnd": True},
                                    lambda: True, self.clock_at(23))
        self.assertEqual(policy.reasons(), ["手动免打扰", "夜间免打扰", "全屏免打扰"])
        def failure():
            raise OSError("API failure")
        policy = NotificationPolicy({"fullscreen_dnd": True}, failure, self.clock_at(12))
        self.assertFalse(policy.is_dnd())
        policy = NotificationPolicy({}, lambda: self.fail("disabled detector was called"), self.clock_at(12))
        self.assertFalse(policy.is_dnd())
        with patch("pet.notification_policy.sys.platform", "linux"):
            self.assertFalse(detect_fullscreen())
        with patch("ctypes.WinDLL", side_effect=OSError("unavailable"), create=True):
            self.assertFalse(detect_fullscreen())

    def test_invalid_policy_settings_rejected(self):
        for settings in ({"quiet_start": "24:00"}, {"quiet_end": "8:00"}, {"dnd": "false"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                NotificationPolicy(settings)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.json"
        self.override = patch.object(config, "PATH", str(self.path))
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def test_defaults_and_partial_merge_preserve_position_unknowns(self):
        self.assertEqual(config.load(), config.DEFAULTS)
        config.save({"x": 321, "y": 654, "scale": 0.6, "future_setting": {"enabled": True}})
        config.save({"mute": True, "voice_volume": 40})
        loaded = config.load()
        self.assertEqual((loaded["x"], loaded["y"], loaded["scale"]), (321, 654, 0.6))
        self.assertEqual(loaded["future_setting"], {"enabled": True})
        self.assertTrue(loaded["mute"])
        config.save({"x": -100, "y": 0, "scale": 0.42})
        self.assertTrue(config.load()["mute"])
        self.assertEqual(config.load()["voice_volume"], 40)
        self.assertEqual(list(Path(self.temp.name).glob(".config-*.tmp")), [])

    def test_invalid_type_and_range_do_not_replace_file(self):
        config.save({"x": 1, "y": 2})
        before = self.path.read_bytes()
        invalid = ({"x": True}, {"y": 1.5}, {"scale": 0}, {"scale": float("inf")},
                   {"voice_enabled": 1}, {"voice_volume": 101}, {"voice_volume": 35.5},
                   {"focus_minutes": float("nan")}, {"break_minutes": 1441},
                   {"quiet_start": "25:00"}, {"quiet_end": "8:00"}, {"mute": "false"})
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValueError):
                config.save(update)
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            config.save({"unknown": object()})
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_load_and_save_fail_clearly(self):
        for content in ("{broken", "[]", '{"voice_volume": "loud"}'):
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid settings file"):
                config.load()
            with self.assertRaises(ValueError):
                config.save({"mute": True})
            self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_atomic_replace_failure_preserves_old_and_cleans_temp(self):
        config.save({"x": 123})
        before = self.path.read_bytes()
        with patch("pet.config.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(OSError, "Cannot save settings file"):
                config.save({"mute": True})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(Path(self.temp.name).glob(".config-*.tmp")), [])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["x"], 123)


if __name__ == "__main__":
    unittest.main(verbosity=2)
