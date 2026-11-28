"""Interaction regressions use isolated SQLite databases and deterministic clocks."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import random
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.focus import FocusService
from pet.interaction import GIFTS, InteractionService
from pet.mini_games import GuessNumber, HaloMemory, RockPaperScissors
from pet.storage import Storage


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "assistant.sqlite3"
        self.clock = datetime(2026, 11, 26, 12).timestamp()
        self.storage = Storage(self.path)
        self.addCleanup(lambda: self.storage.close())
        self.service = self.new_service()

    def new_service(self):
        return InteractionService(self.storage, lambda: self.clock, rng=random.Random(12))

    def restart(self):
        self.storage.close()
        self.storage = Storage(self.path)
        self.service = self.new_service()

    def test_daily_gift_once_per_local_day_after_restart(self):
        gift = self.service.grant_daily_gift()
        self.assertIn(gift, GIFTS)
        self.assertEqual(self.service.inventory(), {gift: (1, 0)})
        self.restart()
        self.assertIsNone(self.service.grant_daily_gift())
        self.assertEqual(self.service.today_gift(), gift)
        self.clock = datetime(2026, 11, 27, 0, 1).timestamp()
        self.assertIsNone(self.service.today_gift())
        self.assertIn(self.service.grant_daily_gift(), GIFTS)
        self.assertEqual(sum(count for count, _ in self.service.inventory().values()), 2)

    def test_every_match_generates_three_reopenable_cards_and_flip_is_idempotent(self):
        pack, points = self.service.finish_game("number", "first")
        self.assertEqual(points, 2)
        self.assertEqual(self.service.reward_cards(pack), [(0, None), (1, None), (2, None)])
        self.assertEqual(self.service.inventory(), {})
        first = self.service.reveal(pack, 0)
        self.restart()
        self.assertEqual(self.service.finish_game("number", "first"), (pack, 0))
        self.assertEqual(self.service.reveal(pack, 0), first)
        self.assertEqual(self.service.inventory(), {first: (1, 0)})
        self.assertEqual(self.service.pending_packs(), [(pack, 2)])
        for slot in (1, 2):
            self.assertIn(self.service.reveal(pack, slot), GIFTS)
        self.assertEqual(sum(count for count, _ in self.service.inventory().values()), 3)
        self.assertEqual(self.service.pending_packs(), [])
        self.assertEqual(self.service.finish_game("memory", "second")[1], 2)
        third, points = self.service.finish_game("rps", "third")
        self.assertEqual(points, 0)
        self.assertEqual(len(self.service.reward_cards(third)), 3)

    def test_no_cooldown_care_and_gifts_share_score_limit_but_still_change_mood(self):
        for action in ("cake", "milk_tea_cold"):
            self.assertEqual(self.service.care(action)[1], 2)
        self.assertEqual(self.service.care("milk_tea_hot")[1], 0)
        self.assertEqual(self.service.status(), (4, "初识", "happy"))
        self.assertEqual(self.service.care("rest")[1], 0)
        self.assertEqual(self.service.status()[2], "relaxed")
        gift = self.service.grant_daily_gift()
        self.assertEqual(self.service.give(gift)[1], 0)
        self.assertEqual(self.service.inventory()[gift], (0, 1))
        self.assertEqual(self.service.status()[2], "surprised")
        with self.assertRaises(ValueError):
            self.service.give(gift)
        pack, _ = self.service.finish_game("rps", "gifts")
        for slot in (0, 1, 2):
            item = self.service.reveal(pack, slot)
            self.service.give(item)
        self.assertEqual(sum(given for _, given in self.service.inventory().values()), 4)
        self.restart()
        self.assertEqual(self.service.status()[2], "surprised")
        self.clock += 1201
        self.assertEqual(self.service.status()[2], "calm")

    def test_head_interactions_and_qualified_focus_count_only_once_without_losing_points(self):
        self.assertEqual(self.service.touch(), 1)
        self.assertEqual(self.service.touch(), 0)
        self.clock += 300
        self.assertEqual(self.service.touch(), 1)
        self.service.ask_for_quiet()
        self.assertEqual(self.service.status()[2], "quiet")
        self.clock += 31
        self.assertEqual(self.service.status()[2], "calm")
        focus = FocusService(self.storage, lambda: self.clock, lambda: self.clock)
        session = focus.start(10)
        self.assertEqual(self.service.focus_completed(session["id"]), 0)
        self.clock += 601
        self.assertEqual(len(focus.tick()), 1)
        self.assertEqual(self.service.focus_completed(session["id"]), 2)
        self.restart()
        self.assertEqual(self.service.focus_completed(session["id"]), 0)
        self.assertEqual(self.service.status()[0], 4)

    def test_v3_migration_preserves_existing_reminder_entries(self):
        self.storage.close()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            for table in ("companion_state", "companion_scores", "gift_inventory", "gift_rewards"):
                conn.execute(f"DROP TABLE {table}")
            conn.execute("PRAGMA user_version = 3")
            conn.execute("""INSERT INTO reminders (title,due_at,status,created_at,updated_at,time_mode)
                            VALUES ('现有提醒',1500,'scheduled',1000,1000,'relative')""")
        self.storage = Storage(self.path)
        self.assertEqual(self.storage.conn.execute("PRAGMA user_version").fetchone()[0], 4)
        row = self.storage.conn.execute("SELECT title, time_mode FROM reminders").fetchone()
        self.assertEqual(tuple(row), ("现有提醒", "relative"))
        self.assertEqual(self.new_service().status(), (0, "初识", "calm"))


class MiniGameTests(unittest.TestCase):
    def test_rps_preselects_and_locks_three_picks(self):
        game = RockPaperScissors(random.Random(2))
        move = game.opponent
        self.assertEqual(game.play(move)[1], "平局")
        with self.assertRaises(ValueError):
            game.play(move)
        for _ in range(2):
            game.next_round()
            self.assertEqual(game.play((game.opponent + 2) % 3)[1], "你赢了")
        self.assertTrue(game.done)
        self.assertIn("2 胜", game.summary())

    def test_number_hints_stable_target_and_no_duplicate_count(self):
        game = GuessNumber(random.Random(12))
        secret = game.secret
        self.assertEqual(game.guess(secret - 1), "猜小了")
        self.assertEqual(game.guess(secret + 1), "猜大了")
        self.assertEqual(game.guess(secret - 1), "猜小了")
        self.assertFalse(game.done)
        self.assertEqual(game.guess(secret), "猜中了")
        self.assertTrue(game.done)
        self.assertEqual(len(game.guesses), 3)

    def test_memory_retry_repeats_same_sequence_and_finishes_at_five_steps(self):
        game = HaloMemory(random.Random(12))
        sequence = game.sequence
        self.assertEqual(game.pick((sequence[0] + 1) % 4), "retry")
        self.assertEqual(game.sequence, sequence)
        for length in (3, 4, 5):
            self.assertEqual(len(game.sequence), length)
            for symbol in game.sequence:
                game.pick(symbol)
        self.assertTrue(game.done)


if __name__ == "__main__":
    unittest.main()
