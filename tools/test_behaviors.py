"""Deterministic behavior tests. Run: python -I tools/test_behaviors.py."""
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

# -I omits cwd; insert only this test's known, trusted project directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.behaviors import Behaviors, EventBus, EXPRESSION_VARIANTS
from pet.gestures import Gestures


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Bubbles:
    def __init__(self):
        self.calls = []
        self.active = None
        self.ticks = []

    def show(self, category, now, dur=2.4):
        self.calls.append((category, now, dur))
        self.active = {"category": category, "t0": now, "dur": dur}

    def clear(self):
        self.active = None

    def tick(self, now):
        self.ticks.append(now)
        if self.active and now >= self.active["t0"] + self.active["dur"]:
            self.active = None

    def attach(self, state):
        state["bubble"] = self.active


class BehaviorsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.bubbles = Bubbles()
        self.state = {}
        self.pet = Behaviors(self.state, self.bubbles, self.clock, random.Random(7))

    def test_initial_state_and_categories(self):
        self.assertEqual(self.state["expression"], "default")
        self.assertEqual(self.state["expression_variant"], 0)
        self.assertIsNone(self.state["idle_action"])
        self.assertIsNone(self.state["reaction"])
        self.assertFalse(self.state["pressed"])
        self.assertGreaterEqual(self.pet.next_idle, 20)
        self.assertLessEqual(self.pet.next_idle, 45)
        for category, variants in EXPRESSION_VARIANTS.items():
            self.pet.preview(category, now=1)
            self.assertIn(self.state["expression_variant"], range(variants))
            self.assertEqual(self.bubbles.calls[-1], (category, 1, 5.0))
            self.assertEqual(self.state["expression"], category)
            self.assertEqual(self.state["expression_until"], 6.0)
        self.assertEqual(EXPRESSION_VARIANTS, {
            "default": 5, "smile": 4, "happy": 2, "embarrassed": 1,
            "dark_angry": 1, "angry": 2, "blush": 3, "cry": 3,
        })
        self.assertEqual(sum(EXPRESSION_VARIANTS.values()), 21)
        with self.assertRaises(ValueError):
            self.pet.preview("not-a-category")

    def test_explicit_now_never_reads_clock_and_bubbles_share_time(self):
        self.pet.clock = lambda: self.fail("explicit now must not read clock")
        self.pet.on_press(now=10)
        self.pet.on_release(now=10.1)
        self.pet.on_click(now=10.5)
        self.pet.tick(10.6)
        self.assertEqual(self.state["hop_t0"], 10.5)
        self.assertEqual(self.bubbles.calls[-1][1], 10.5)
        self.assertEqual(self.bubbles.ticks[-1], 10.6)
        self.assertIs(self.state["bubble"], self.bubbles.active)

    def test_all_optional_now_handlers_use_injected_clock(self):
        self.clock.now = 12
        self.pet.on_press()
        self.pet.on_release()
        self.pet.on_click()
        self.assertEqual(self.state["reaction"]["t0"], 12)
        self.pet.on_double_click()
        self.pet.on_long_press()
        self.pet.on_drag_start()
        self.pet.on_drag_end()
        self.pet.on_cancel()
        self.pet.preview("cry")
        self.assertEqual(self.state["expression_until"], 17)
        self.pet.restore_default()
        self.pet.on_hover()
        self.pet.tick()
        self.assertEqual(self.bubbles.ticks[-1], 12)

    def test_same_seed_is_deterministic(self):
        other_state = {}
        other = Behaviors(other_state, Bubbles(), self.clock, random.Random(7))
        for handler, kwargs in (("on_click", {"now": 1}),
                                ("preview", {"expression": "cry", "now": 2}),
                                ("restore_default", {"now": 3}),
                                ("tick", {"now": 60})):
            getattr(self.pet, handler)(**kwargs)
            getattr(other, handler)(**kwargs)
            self.assertEqual(self.state, other_state)
            self.assertEqual(self.pet.next_idle, other.next_idle)

    def test_click_double_and_region_reactions(self):
        for region in ("head", "body", "halo"):
            self.pet.restore_default(now=0)
            self.pet.on_click(region, now=1)
            self.assertEqual(self.state["expression"], "smile")
            self.assertEqual(self.state["reaction"]["kind"], "tilt")
            self.assertFalse(self.state["hop_big"])
            self.assertEqual(self.state["hop_t0"], 1 if region == "body" else None)
            self.pet.tick(1.55)
            self.assertIsNone(self.state["reaction"])
            self.assertIsNone(self.state["hop_t0"])
            self.pet.on_double_click(region, now=2)
            self.assertTrue(self.state["hop_big"])
            self.assertEqual(self.state["expression"], "happy")
        self.pet.on_long_press("head", now=3)
        self.assertEqual(self.state["expression"], "blush")
        self.assertIsNone(self.state["hop_t0"])

    def test_body_double_click_starts_authored_action_when_available(self):
        state = {}
        pet = Behaviors(
            state, self.bubbles, self.clock, random.Random(7),
            animation_clips={"action_1": {"duration": 10.75, "mode": "once", "idle": False}})
        pet.on_double_click("body", now=2)
        self.assertEqual(state["animation"]["clip"], "action_1")
        self.assertFalse(state["animation"]["play_once"])
        self.assertIsNone(state["hop_t0"])
        self.assertEqual(self.bubbles.calls[-1], ("happy", 2, 5.0))
        pet.on_press("body", now=2.1)
        self.assertIsNone(state["animation"])

    def test_body_double_authored_action_preserves_anger_priority(self):
        state = {}
        pet = Behaviors(
            state, self.bubbles, self.clock, random.Random(7),
            animation_clips={"action_1": {"duration": 10.75, "mode": "once", "idle": False}})
        for index in range(7):
            now = index * 0.2
            pet.on_press("body", now=now)
            pet.on_release(now=now + 0.01)
            pet.on_double_click("body", now=now + 0.02)
            self.assertEqual(state["animation"]["clip"], "action_1")
            if index >= 3:
                category = "dark_angry" if index >= 6 else "angry"
                self.assertEqual(state["expression"], category)
                self.assertEqual(self.bubbles.calls[-1], (category, now + 0.02, 5.0))

    def test_interactions_dispatch_paired_five_second_cards(self):
        cases = [
            ("on_long_press", {"region": "head"}, "blush"),
            ("on_drag_start", {}, "embarrassed"),
            ("preview", {"expression": "cry"}, "cry"),
        ]
        for region in ("head", "body", "halo"):
            cases.extend((
                ("on_click", {"region": region}, "smile"),
                ("on_double_click", {"region": region}, "happy"),
                ("on_hover", {"region": region}, "embarrassed"),
            ))
        for handler, kwargs, category in cases:
            with self.subTest(handler=handler, kwargs=kwargs):
                bubbles = Bubbles()
                state = {}
                pet = Behaviors(state, bubbles, self.clock, random.Random(7))
                getattr(pet, handler)(now=1, **kwargs)
                self.assertEqual(state["expression"], category)
                self.assertEqual(bubbles.calls, [(category, 1, 5.0)])
                pet.tick(5.99)
                self.assertEqual(state["bubble"]["category"], category)
                pet.tick(6)
                self.assertIsNone(state["bubble"])
                self.assertEqual(len(bubbles.calls), 1)

    def test_mood_expiry_does_not_clear_or_redispatch_card(self):
        self.pet.on_click(now=1)
        self.pet.tick(5)
        self.assertEqual(self.state["expression"], "default")
        self.assertEqual(self.state["bubble"]["category"], "smile")
        self.assertEqual(self.bubbles.calls, [("smile", 1, 5.0)])
        self.pet.tick(6)
        self.assertIsNone(self.state["bubble"])
        self.assertEqual(self.bubbles.calls, [("smile", 1, 5.0)])

    def test_physical_input_immediately_interrupts_animation(self):
        for handler in ("on_press", "on_drag_start", "on_cancel"):
            with self.subTest(handler=handler):
                state = {}
                pet = Behaviors(
                    state, Bubbles(), self.clock, random.Random(7),
                    animation_clips={"action_1": {"duration": 10.75, "mode": "once", "idle": False}})
                pet.start_animation("action_1", now=1)
                getattr(pet, handler)(now=1.01)
                self.assertIsNone(state["animation"])
                self.assertFalse(pet.animation_previewing)
                self.assertFalse(state["blink"])

    def test_background_input_has_no_effect(self):
        before = self.state.copy()
        for handler in ("on_press", "on_click", "on_double_click", "on_hover", "on_long_press"):
            getattr(self.pet, handler)(region="background", now=1)
        self.assertEqual(self.state, before)
        self.assertEqual(self.bubbles.calls, [])

    def test_hover_speaks_without_replacing_active_reaction(self):
        self.pet.on_click("head", now=1)
        before = self.state.copy()
        self.pet.on_hover("halo", now=1.1)
        self.assertEqual(self.state, before)
        self.assertEqual(len(self.bubbles.calls), 1)
        self.pet.on_hover("head", now=2)
        self.assertEqual(self.state["expression"], "embarrassed")
        self.assertEqual(self.state["reaction"]["kind"], "embarrassed")
        self.assertEqual(self.bubbles.calls[-1], ("embarrassed", 2, 5.0))
        self.assertEqual(len(self.bubbles.calls), 2)

    def test_preview_priorities_and_five_second_expiry(self):
        self.pet.preview("dark_angry", now=0)
        self.pet.on_hover("body", now=4.9)
        self.pet.tick(4.9)
        self.assertEqual(self.state["expression"], "dark_angry")
        self.assertIsNone(self.state["idle_action"])
        self.assertIsNotNone(self.bubbles.active)
        self.assertEqual(self.bubbles.calls, [("dark_angry", 0, 5.0)])
        self.pet.tick(5)
        self.assertEqual(self.state["expression"], "default")
        self.assertFalse(self.pet.previewing)
        self.assertIsNone(self.bubbles.active)
        self.pet.preview("cry", now=9)
        self.pet.on_press(now=10)
        self.assertFalse(self.pet.previewing)
        self.assertEqual(self.state["expression"], "default")
        self.pet.on_release(now=10)
        self.pet.restore_default(now=11)
        self.assertIsNone(self.bubbles.active)

    def test_anger_counts_only_physical_presses_and_has_priority(self):
        for index in range(7):
            now = index * 0.2
            self.pet.on_press(now=now)
            self.pet.on_release(now=now + 0.01)
            self.pet.on_double_click(now=now + 0.02)
            if index == 3:
                self.assertEqual(self.state["expression"], "angry")
        self.assertEqual(len(self.pet._presses), 7)
        self.assertEqual(self.state["expression"], "dark_angry")
        self.assertEqual(self.state["reaction"]["kind"], "dark_angry")
        self.pet.on_hover("head", now=2.5)
        self.assertEqual(self.state["expression"], "dark_angry")
        # Recognition events alone do not add presses.
        fresh = Behaviors({}, Bubbles(), self.clock, random.Random(7))
        for index in range(10):
            fresh.on_click(now=index * 0.1)
        self.assertEqual(len(fresh._presses), 0)
        self.assertEqual(fresh.state["expression"], "smile")

    def test_sliding_press_window_and_mood_expiry(self):
        for now in (0, 0.2, 0.4, 3.0):
            self.pet.on_press(now=now)
            self.pet.on_release(now=now)
        self.assertEqual(len(self.pet._presses), 1)
        self.assertEqual(self.state["expression"], "default")
        self.pet.restore_default(now=4)
        for index in range(7):
            self.pet.on_press(now=5 + index * 0.2)
            self.pet.on_release(now=5 + index * 0.2)
        until = self.state["expression_until"]
        calls = list(self.bubbles.calls)
        card = self.bubbles.active
        self.pet.tick(until)
        self.assertEqual(self.state["expression"], "angry")
        self.assertEqual(self.state["expression_until"], until + 1.5)
        self.assertIs(self.bubbles.active, card)
        self.assertEqual(self.bubbles.calls, calls)
        self.pet.tick(until + 1.5)
        self.assertEqual(self.state["expression"], "default")
        self.assertIsNone(self.bubbles.active)
        self.assertEqual(self.bubbles.calls, calls)
        self.pet.on_click(now=15)
        self.pet.tick(19)
        self.assertEqual(self.state["expression"], "default")

    def test_sparse_tick_skips_expired_anger_cooldown(self):
        for index in range(7):
            self.pet.on_press(now=index * 0.1)
            self.pet.on_release(now=index * 0.1)
        self.pet.tick(10)
        self.assertEqual(self.state["expression"], "default")

    def test_idle_actions_have_bounded_durations_and_no_dialog(self):
        for kind in ("look", "sway"):
            self.pet.restore_default(now=0)
            self.pet.next_idle = 20
            with patch.object(self.pet.rng, "choice", return_value=kind) as choose:
                self.pet.tick(20)
            choose.assert_called_once_with(("look", "sway"))
            action = self.state["idle_action"]
            self.assertEqual(action["kind"], kind)
            self.assertGreaterEqual(action["dur"], 2)
            self.assertLessEqual(action["dur"], 4)
            self.assertFalse(self.state["blink"])
            self.assertEqual(self.state["expression"], "default")
            self.assertIsNone(self.state["expression_until"])
            self.assertEqual(self.bubbles.calls, [])
            self.pet.tick(20 + action["dur"])
            self.assertIsNone(self.state["idle_action"])
            self.assertEqual(self.state["expression"], "default")

    def test_blink_compatibility_state_is_always_false(self):
        state = {"blink": True}
        pet = Behaviors(state, self.bubbles, self.clock, random.Random(7))
        self.assertFalse(state["blink"])
        self.assertFalse(hasattr(pet, "next_blink"))
        self.assertFalse(hasattr(pet, "blink_until"))
        for now in (2, 2.05, 10, 100):
            pet.tick(now)
            self.assertFalse(state["blink"], now)
        state["blink"] = True
        pet.on_press(now=100.05)
        self.assertFalse(state["blink"])

    def test_canceling_sway_preserves_interaction_mood(self):
        self.pet.on_click("head", now=1)
        self.pet.tick(1.6)
        self.pet.next_idle = 2
        with patch.object(self.pet.rng, "choice", return_value="sway"):
            self.pet.tick(2)
        self.assertEqual(self.state["idle_action"]["kind"], "sway")
        self.pet.on_press(now=2.1)
        self.assertIsNone(self.state["idle_action"])
        self.assertEqual(self.state["expression"], "smile")
        self.assertEqual(self.state["expression_until"], 5)
        self.assertEqual(self.bubbles.calls, [("smile", 1, 5.0)])

    def test_idle_suppressed_by_held_drag_reaction_and_preview(self):
        for blocker in ("pressed", "dragging", "reaction", "preview"):
            self.pet.restore_default(now=0)
            self.state.update(pressed=False, dragging=False)
            if blocker == "preview":
                self.pet.preview("cry", now=30)
            elif blocker == "reaction":
                self.pet.on_click(now=30)
            else:
                self.state[blocker] = True
            self.pet.next_idle = 20
            self.pet.tick(30.1)
            self.assertIsNone(self.state["idle_action"], blocker)

    def test_press_release_cancel_and_drag_clear_idle_and_motion(self):
        self.state.update(idle_action={"kind": "look", "t0": 0, "dur": 3}, blink=True,
                          hop_t0=0, land_t0=0)
        self.pet.on_press(now=1)
        self.assertIsNone(self.state["idle_action"])
        self.assertFalse(self.state["blink"])
        self.assertIsNone(self.state["hop_t0"])
        self.assertIsNone(self.state["land_t0"])
        self.pet.tick(100)
        self.assertIsNone(self.state["idle_action"])
        self.pet.on_release(now=100)
        self.assertFalse(self.state["pressed"])
        self.assertGreaterEqual(self.pet.next_idle, 120)
        self.pet.on_click(now=101)
        self.pet.on_drag_start(now=101.1)
        self.assertTrue(self.state["dragging"])
        self.assertIsNone(self.state["hop_t0"])
        self.pet.on_drag_end(now=102)
        self.assertFalse(self.state["dragging"])
        self.assertEqual(self.state["land_t0"], 102)
        self.pet.on_cancel(now=102.1)
        self.assertIsNone(self.state["land_t0"])
        self.assertIsNone(self.state["reaction"])
        self.assertFalse(self.state["pressed"])

    def test_animation_clip_names_can_be_injected_as_a_list(self):
        state = {}
        pet = Behaviors(state, self.bubbles, self.clock, random.Random(7),
                        animation_clips=["breathe"])
        self.assertEqual(pet.idle_animations, ("breathe",))
        pet.start_animation("breathe", now=1)
        pet.tick(100)
        self.assertEqual(state["animation"]["clip"], "breathe")

    def test_animation_preview_interrupt_expiry_and_physical_cancel(self):
        state = {}
        pet = Behaviors(
            state, self.bubbles, self.clock, random.Random(7),
            animation_clips={
                "wave": {"duration": 1.25, "mode": "once", "idle": False},
                "breathe": {"duration": 0.5, "mode": "loop", "idle": True},
            })
        pet.preview("cry", now=1)
        pet.preview_animation("wave", now=2)
        self.assertFalse(pet.previewing)
        self.assertTrue(pet.animation_previewing)
        self.assertEqual(state["expression"], "default")
        self.assertEqual(state["animation"], {"clip": "wave", "t0": 2, "mode": "once",
                                              "play_once": True})
        self.assertEqual(self.bubbles.calls[-1], ("idle", 2, 5.0))
        pet.tick(3.24)
        self.assertIsNotNone(state["animation"])
        pet.tick(3.25)
        self.assertIsNone(state["animation"])
        self.assertFalse(pet.animation_previewing)
        pet.preview_animation("wave", now=4)
        pet.on_press(now=4.1)
        self.assertIsNone(state["animation"])
        self.assertFalse(pet.animation_previewing)
        with self.assertRaises(ValueError):
            pet.preview_animation("missing", now=5)

    def test_idle_can_select_authored_clip_and_loop_blocks_rescheduling(self):
        state = {}
        pet = Behaviors(
            state, self.bubbles, self.clock, random.Random(7),
            animation_clips={
                "breathe": {"duration": 0.5, "mode": "loop", "idle": True},
                "walk": {"duration": 0.8, "mode": "once", "idle": False},
            })
        pet.next_idle = 20
        with patch.object(pet.rng, "choice", return_value="breathe") as choose:
            pet.tick(20)
        choose.assert_called_once_with(("look", "sway", "breathe"))
        self.assertEqual(state["animation"]["clip"], "breathe")
        self.assertIsNone(state["idle_action"])
        pet.tick(100)
        self.assertIsNone(state["animation"])
        # A manual preview of a loop also plays one complete cycle.
        pet.preview_animation("breathe", now=100)
        self.assertTrue(state["animation"]["play_once"])
        pet.tick(100.5)
        self.assertIsNone(state["animation"])
        # Authored walk is visual-only: behavior never changes window position.
        pet.start_animation("walk", now=101)
        self.assertEqual(set(state["animation"]), {"clip", "t0", "mode", "play_once"})
        pet.tick(101.8)
        self.assertIsNone(state["animation"])

    def test_event_bus_accepts_gesture_payloads_and_counts_qt_double(self):
        bus = EventBus()
        for event in ("press", "release", "cancel", "click", "double_click", "long_press", "drag_start", "drag_end"):
            bus.on(event, getattr(self.pet, "on_" + event))
        gesture = Gestures(clock=self.clock)
        def dispatch(events):
            for name, payload in events:
                bus.emit(name, **payload)
        dispatch(gesture.press((0, 0), "body", 0))
        dispatch(gesture.release(0.05))
        dispatch(gesture.double_click((0, 0), "body", 0.1))
        dispatch(gesture.release(0.15))
        dispatch(gesture.tick(1))
        self.assertEqual(len(self.pet._presses), 2)
        self.assertEqual(len(self.bubbles.calls), 1)
        self.assertFalse(self.state["pressed"])
        dispatch(gesture.press((0, 0), "body", 2))
        dispatch(gesture.move((20, 0), 2.1))
        dispatch(gesture.release(2.2))
        self.assertFalse(self.state["dragging"])
        dispatch(gesture.press((0, 0), "head", 3))
        dispatch(gesture.tick(3.7))
        dispatch(gesture.release(3.8))
        dispatch(gesture.cancel())
        self.assertFalse(self.state["pressed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
