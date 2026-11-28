"""Event bus and clock-driven reactions, independent of Qt.

Register ``on_<event>`` with EventBus; input handlers accept ``region`` and/or
``now`` keywords. Regions are head/body/halo; background input is ignored.
Use one monotonic clock for input, tick and rendering. Expression variants are
zero-based mood metadata, not generated face frames. ``preview`` lasts five
seconds or until restore_default/interaction; hover never replaces it or an
active reaction. Scene reads timed action dicts
``{kind, t0, dur}`` and supplies their smooth animation envelopes.
"""
from collections import deque
from collections.abc import Mapping
import math
import random
import time


from .expressions import GROUPS


EXPRESSION_VARIANTS = {kind: len(ids) for kind, ids in GROUPS.items()}
REGIONS = {"head", "body", "halo"}
MOOD_DURATION = 4.0
ANGER_COOLDOWN = 1.5
PRESS_WINDOW = 2.5


class EventBus:
    def __init__(self):
        self._handlers = {}

    def on(self, event, fn):
        self._handlers.setdefault(event, []).append(fn)

    def emit(self, event, **kw):
        for fn in self._handlers.get(event, []):
            fn(**kw)


class Behaviors:
    def __init__(self, state, bubbles, clock=time.monotonic, rng=None,
                 animation_clips=()):
        self.state = state
        self.bubbles = bubbles
        self.clock = clock
        self.rng = random.Random() if rng is None else rng
        self.animation_clips = self._animation_metadata(animation_clips)
        self.idle_animations = tuple(
            name for name, metadata in self.animation_clips.items()
            if metadata["idle"])
        self.idle_animation_names = self.idle_animations
        self.previewing = False
        self.animation_previewing = False
        self._presses = deque()
        now = self.clock()
        self.next_idle = now + self.rng.uniform(20.0, 45.0)
        for key, value in {
            "expression": "default", "expression_variant": 0,
            "expression_until": None, "idle_action": None, "reaction": None,
            "animation": None,
            "blink": False, "pressed": False, "dragging": False,
            "hop_t0": None, "hop_big": False, "land_t0": None,
        }.items():
            self.state.setdefault(key, value)
        self.state["blink"] = False

    @staticmethod
    def _animation_metadata(animation_clips):
        """Normalize injected clip metadata without importing the renderer."""
        if animation_clips is None:
            return {}
        if isinstance(animation_clips, Mapping):
            entries = animation_clips.items()
        else:
            values = tuple(animation_clips)
            if all(isinstance(value, str) for value in values):
                entries = ((name, {"duration": math.inf, "mode": "loop", "idle": True})
                           for name in values)
            else:
                entries = ((getattr(clip, "name", clip), clip) for clip in values)
        normalized = {}
        for name, metadata in entries:
            if not isinstance(name, str) or not name:
                raise ValueError("animation clip names must be non-empty strings")
            if isinstance(metadata, Mapping):
                duration = metadata.get("duration", metadata.get("total_duration"))
                mode = metadata.get("mode", "once")
                idle = metadata.get("idle", False)
                label = metadata.get("label", name)
            elif isinstance(metadata, (int, float)) and not isinstance(metadata, bool):
                duration, mode, idle, label = metadata, "once", False, name
            else:
                duration = getattr(metadata, "duration", getattr(metadata, "total_duration", None))
                mode = getattr(metadata, "mode", "once")
                idle = getattr(metadata, "idle", False)
                label = getattr(metadata, "label", name)
            if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or math.isnan(duration) or duration <= 0):
                raise ValueError(f"animation clip {name!r} duration must be positive")
            if mode not in ("once", "loop"):
                raise ValueError(f"animation clip {name!r} has invalid mode")
            if not isinstance(idle, bool):
                raise ValueError(f"animation clip {name!r} idle must be boolean")
            normalized[name] = {
                "duration": float(duration), "mode": mode, "idle": idle,
                "label": label if isinstance(label, str) and label else name,
            }
        return normalized

    def _now(self, now):
        return self.clock() if now is None else now

    def _expression(self, category, until):
        self.state.update(
            expression=category,
            expression_variant=self.rng.randrange(EXPRESSION_VARIANTS[category]),
            expression_until=until,
        )

    def _cancel_idle(self):
        self.state["idle_action"] = None
        self.state["blink"] = False

    def _cancel_motion(self):
        self.state.update(hop_t0=None, hop_big=False, land_t0=None)

    def stop_animation(self):
        self.state["animation"] = None
        self.animation_previewing = False

    def start_animation(self, name, now=None, preview=False):
        metadata = self.animation_clips.get(name)
        if metadata is None:
            raise ValueError(f"unknown animation: {name}")
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        self.state["reaction"] = None
        self.state["animation"] = {
            "clip": name, "t0": now, "mode": metadata["mode"],
            "play_once": bool(preview or metadata["idle"]),
        }
        self.animation_previewing = bool(preview)
        self.next_idle = now + self.rng.uniform(20.0, 45.0)
        return self.state["animation"]

    def preview_animation(self, name, now=None):
        now = self._now(now)
        animation = self.start_animation(name, now=now, preview=True)
        self.bubbles.show("idle", now, 5.0)
        return animation

    def _activity(self, now):
        self._expire(now)
        self._cancel_idle()
        self.stop_animation()
        if self.previewing:
            self.previewing = False
            self._expression("default", None)
        self.next_idle = now + self.rng.uniform(20.0, 45.0)

    def _prune_presses(self, now):
        while self._presses and now - self._presses[0] > PRESS_WINDOW:
            self._presses.popleft()

    def _respond(self, category, now, dur, speak=True):
        # A click recognized after its physical press must not undo anger.
        current = self.state["expression"]
        if current == "dark_angry" or (current == "angry" and category != "dark_angry"):
            category = current
        pose = {"smile": "tilt", "happy": "tilt"}.get(category, category)
        self.state["reaction"] = {"kind": pose, "t0": now, "dur": dur}
        self._expression(category, now + MOOD_DURATION)
        if speak:
            self.bubbles.show(category, now, 5.0)

    def play_interaction_pose(self, kind, category, duration, now=None):
        """Apply a one-shot procedural pose without interrupting drag or authored clips."""
        now = self._now(now)
        self._expire(now)
        if (self.state["pressed"] or self.state["dragging"] or self.state["animation"]
                or self.previewing):
            return False
        self._cancel_idle()
        self._cancel_motion()
        self._expression(category, now + duration)
        self.state["reaction"] = {"kind": kind, "t0": now, "dur": duration}
        if kind == "hop":
            self.state["hop_t0"] = now
        self.next_idle = now + self.rng.uniform(20.0, 45.0)
        return True

    def on_press(self, region="body", now=None):
        if region not in REGIONS:
            return
        now = self._now(now)
        self._activity(now)
        self.state["pressed"] = True
        self._cancel_motion()
        self._prune_presses(now)
        self._presses.append(now)
        count = len(self._presses)
        if count >= 4:
            category = "dark_angry" if count >= 7 else "angry"
            self._respond(category, now, 0.7)

    def on_release(self, now=None, region=None):
        now = self._now(now)
        self.state["pressed"] = False
        self.next_idle = now + self.rng.uniform(20.0, 45.0)

    def on_cancel(self, now=None, region=None):
        now = self._now(now)
        self._activity(now)
        self.state.update(pressed=False, dragging=False, reaction=None)
        self._cancel_motion()

    def on_click(self, region="body", now=None):
        if region not in REGIONS:
            return
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        if region == "body":
            self.state.update(hop_t0=now, hop_big=False)
        self._respond("smile", now, 0.55)

    def on_double_click(self, region="body", now=None):
        if region not in REGIONS:
            return
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        if region == "body" and "action_1" in self.animation_clips:
            self.start_animation("action_1", now=now)
            category = self.state["expression"]
            if category not in ("angry", "dark_angry"):
                category = "happy"
            self.bubbles.show(category, now, 5.0)
            return
        self.state.update(hop_t0=now, hop_big=True)
        self._respond("happy", now, 0.8)

    def on_hover(self, region="body", now=None):
        if region not in REGIONS:
            return
        now = self._now(now)
        self._expire(now)
        if (self.previewing or self.state["pressed"] or self.state["dragging"]
                or self.state["reaction"]
                or self.state["expression"] in ("angry", "dark_angry")):
            return
        self._activity(now)
        self._respond("embarrassed", now, 1.0, speak=True)

    def on_long_press(self, region="head", now=None):
        if region != "head":
            return
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        self._respond("blush", now, 1.2)

    def on_drag_start(self, now=None, region=None):
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        self.state["dragging"] = True
        self._respond("embarrassed", now, 1.0)

    def on_drag_end(self, now=None, region=None):
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        self.state.update(dragging=False, pressed=False, land_t0=now)
        self.state["reaction"] = {"kind": "land", "t0": now, "dur": 0.25}

    def preview(self, expression, now=None):
        if expression not in EXPRESSION_VARIANTS:
            raise ValueError(f"unknown expression: {expression}")
        now = self._now(now)
        self._activity(now)
        self._cancel_motion()
        self.state["reaction"] = None
        self.previewing = True
        self._expression(expression, now + 5.0)
        self.bubbles.show(expression, now, 5.0)

    def restore_default(self, now=None):
        now = self._now(now)
        self._activity(now)
        self._presses.clear()
        self._cancel_motion()
        self.stop_animation()
        self.state["reaction"] = None
        self._expression("default", None)
        self.bubbles.clear()

    def _expire(self, now):
        until = self.state["expression_until"]
        if until is not None and now >= until:
            was_preview = self.previewing
            self.previewing = False
            if (not was_preview and self.state["expression"] == "dark_angry"
                    and now < until + ANGER_COOLDOWN):
                self._expression("angry", until + ANGER_COOLDOWN)
            else:
                self._expression("default", None)
        reaction = self.state["reaction"]
        if reaction and now >= reaction["t0"] + reaction["dur"]:
            self.state["reaction"] = None
        for key, dur in (("hop_t0", 0.55), ("land_t0", 0.25)):
            t0 = self.state[key]
            if t0 is not None and now >= t0 + dur:
                self.state[key] = None
        action = self.state["idle_action"]
        if action and now >= action["t0"] + action["dur"]:
            self._cancel_idle()
            self.next_idle = now + self.rng.uniform(20.0, 45.0)
        animation = self.state["animation"]
        if animation:
            metadata = self.animation_clips.get(animation.get("clip"))
            if metadata is None:
                self.stop_animation()
            elif ((metadata["mode"] == "once" or animation.get("play_once"))
                  and now >= animation["t0"] + metadata["duration"]):
                self.stop_animation()
                self.next_idle = now + self.rng.uniform(20.0, 45.0)

    def tick(self, now=None):
        now = self._now(now)
        self._expire(now)
        self._prune_presses(now)
        blocked = (self.state["pressed"] or self.state["dragging"]
                   or self.state["reaction"] or self.previewing
                   or self.animation_previewing or self.state["animation"])
        if blocked:
            self._cancel_idle()
        elif (now >= self.next_idle and self.state["idle_action"] is None
              and self.state["animation"] is None):
            choice = self.rng.choice(("look", "sway") + self.idle_animations)
            if choice in self.animation_clips:
                self.start_animation(choice, now=now)
            else:
                kind = choice
                dur = self.rng.uniform(2.0, 4.0)
                self.state["idle_action"] = {"kind": kind, "t0": now, "dur": dur}
        # Authored video may blink; behavior never generates eye-closure frames.
        self.state["blink"] = False
        self.bubbles.tick(now)
        self.bubbles.attach(self.state)
