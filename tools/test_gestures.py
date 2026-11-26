"""Qt-free gesture tests. Run: python -I tools/test_gestures.py."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.gestures import Gestures


def names(events):
    return [name for name, _ in events]


class GesturesTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.gesture = Gestures(clock=lambda: self.now)

    def test_single_click_is_deferred_and_release_is_immediate(self):
        self.assertEqual(names(self.gesture.press((1, 2), "body", 0)), ["press"])
        self.assertTrue(self.gesture.pressed)
        self.assertEqual(self.gesture.press_position, (1, 2))
        self.assertEqual(names(self.gesture.release(0.1)), ["release"])
        self.assertFalse(self.gesture.pressed)
        self.assertEqual(self.gesture.tick(0.399), [])
        self.assertEqual(self.gesture.tick(0.4), [("click", {"region": "body", "now": 0.4})])
        self.assertEqual(self.gesture.tick(1), [])

    def test_qt_double_is_exclusive_and_counts_second_press_once(self):
        events = self.gesture.press((0, 0), "head", 0)
        events += self.gesture.release(0.05)
        events += self.gesture.double_click((0, 0), "head", 0.1)
        events += self.gesture.release(0.15)
        events += self.gesture.tick(1)
        self.assertEqual(names(events), ["press", "release", "press", "double_click", "release"])
        self.assertEqual(self.gesture.tick(2), [])

    def test_adapter_press_then_double_does_not_duplicate_press(self):
        events = self.gesture.press((0, 0), "body", 0)
        events += self.gesture.release(0.05)
        events += self.gesture.press((0, 0), "body", 0.1)
        events += self.gesture.double_click((0, 0), "body", 0.1)
        events += self.gesture.double_click((0, 0), "body", 0.1)
        events += self.gesture.release(0.2)
        events += self.gesture.tick(1)
        self.assertEqual(names(events).count("press"), 2)
        self.assertEqual(names(events).count("double_click"), 1)
        self.assertNotIn("click", names(events))

    def test_queued_rapid_singles_are_not_lost(self):
        events = []
        for now in (0, 0.1, 0.2, 0.3):
            events += self.gesture.press((0, 0), "body", now)
            events += self.gesture.release(now + 0.01)
        events += self.gesture.tick(1)
        self.assertEqual(names(events).count("press"), 4)
        self.assertEqual(names(events).count("release"), 4)
        self.assertEqual(names(events).count("click"), 4)
        self.assertNotIn("double_click", names(events))

    def test_double_cancels_only_matching_pending_single(self):
        events = []
        for now in (0, 0.1):
            events += self.gesture.press((0, 0), "body", now)
            events += self.gesture.release(now + 0.01)
        events += self.gesture.double_click((0, 0), "body", 0.2)
        events += self.gesture.release(0.21)
        events += self.gesture.tick(1)
        self.assertEqual(names(events).count("click"), 1)
        self.assertEqual(names(events).count("double_click"), 1)
        self.assertEqual(names(events).count("press"), 3)

    def test_region_or_distance_mismatch_cannot_consume_single(self):
        for position, region in (((0, 0), "halo"), ((20, 0), "body")):
            self.gesture.cancel()
            events = self.gesture.press((0, 0), "body", 0)
            events += self.gesture.release(0.01)
            events += self.gesture.double_click(position, region, 0.1)
            events += self.gesture.release(0.11)
            events += self.gesture.tick(1)
            self.assertNotIn("double_click", names(events))
            self.assertEqual(names(events).count("click"), 2)

    def test_expired_double_is_two_singles(self):
        events = self.gesture.press((0, 0), "body", 0)
        events += self.gesture.release(0.1)
        events += self.gesture.double_click((0, 0), "body", 0.5)
        events += self.gesture.release(0.6)
        events += self.gesture.tick(1)
        self.assertEqual(names(events).count("click"), 2)
        self.assertNotIn("double_click", names(events))

    def test_long_press_only_head_only_tick_consumes_release(self):
        self.gesture.press((0, 0), "head", 0)
        self.assertEqual(self.gesture.tick(0.699), [])
        self.assertEqual(names(self.gesture.tick(0.7)), ["long_press"])
        self.assertEqual(self.gesture.tick(1), [])
        self.assertEqual(self.gesture.move((20, 0), 1), [])
        self.assertEqual(names(self.gesture.release(1.1)), ["release"])
        self.assertEqual(self.gesture.tick(2), [])
        for region in ("body", "halo"):
            self.gesture.press((0, 0), region, 3)
            self.assertEqual(self.gesture.tick(4), [])
            events = self.gesture.release(4.1) + self.gesture.tick(5)
            self.assertEqual(names(events), ["release", "click"])

    def test_release_never_synthesizes_long_press(self):
        self.gesture.press((0, 0), "head", 0)
        events = self.gesture.release(1) + self.gesture.tick(2)
        self.assertEqual(names(events), ["release", "click"])

    def test_drag_cancels_long_press_and_click_and_emits_position(self):
        self.gesture.press((0, 0), "head", 0)
        self.assertEqual(self.gesture.move((6, 0), 0.1), [])
        events = self.gesture.move((7, 0), 0.2)
        self.assertEqual(names(events), ["drag_start", "drag_move"])
        self.assertEqual(events[-1][1], {"region": "head", "now": 0.2, "position": (7, 0)})
        self.assertTrue(self.gesture.dragging)
        self.assertEqual(self.gesture.tick(0.7), [])
        self.assertEqual(names(self.gesture.move((8, 0), 0.8)), ["drag_move"])
        self.assertEqual(names(self.gesture.release(1)), ["release", "drag_end"])
        self.assertFalse(self.gesture.dragging)
        self.assertFalse(self.gesture.pressed)
        self.assertEqual(self.gesture.tick(2), [])

    def test_consumed_double_cannot_turn_into_drag_or_long_press(self):
        self.gesture.press((0, 0), "head", 0)
        self.gesture.release(0.01)
        self.gesture.double_click((0, 0), "head", 0.1)
        self.assertEqual(self.gesture.move((20, 0), 0.2), [])
        self.assertEqual(self.gesture.tick(1), [])
        self.assertEqual(names(self.gesture.release(1.1)), ["release"])

    def test_cancel_drops_pending_and_held_state(self):
        self.gesture.press((0, 0), "body", 0)
        self.gesture.release(0.01)
        self.gesture.press((0, 0), "head", 0.1)
        self.gesture.move((10, 0), 0.2)
        self.now = 0.3
        self.assertEqual(self.gesture.cancel(), [("cancel", {"region": "head", "now": 0.3})])
        self.assertFalse(self.gesture.pressed)
        self.assertFalse(self.gesture.dragging)
        self.assertIsNone(self.gesture.press_position)
        self.assertEqual(self.gesture.release(0.4), [])
        self.assertEqual(self.gesture.tick(1), [])
        events = self.gesture.press((0, 0), "body", 2) + self.gesture.release(2.1) + self.gesture.tick(3)
        self.assertEqual(names(events), ["press", "release", "click"])

    def test_idle_moves_and_releases_do_nothing(self):
        self.assertEqual(self.gesture.move((0, 0), 0), [])
        self.assertEqual(self.gesture.release(0), [])
        self.assertEqual(self.gesture.tick(0), [])

    def test_configurable_intervals_and_injected_clock(self):
        gesture = Gestures(double_interval=0.25, drag_distance=10, long_press_interval=1,
                           clock=lambda: self.now)
        gesture.press((0, 0), "body")
        self.now = 0.1
        gesture.release()
        self.now = 0.25
        self.assertEqual(names(gesture.tick()), ["click"])
        self.now = 1
        gesture.press((0, 0), "head")
        self.assertEqual(gesture.move((10, 0)), [])
        self.now = 2
        self.assertEqual(names(gesture.tick()), ["long_press"])
        for kwargs in ({"double_interval": -1}, {"drag_distance": -1}, {"long_press_interval": 0}):
            with self.assertRaises(ValueError):
                Gestures(**kwargs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
