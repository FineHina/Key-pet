"""Qt-free pointer recognizer. Supply positions as (x, y) tuples.

All methods return [(name, payload), ...]; dispatch with bus.emit(name, **payload).
Payloads contain region/now; drag_move also contains position. Configure
``double_interval`` from the system interval in seconds. Forward Qt's normal
press/release/double-click/release sequence, not a synthetic extra press.
Single clicks wait until the first press's double-click deadline. Only the
matching pending first click is consumed by a double; other singles stay queued.
Long press is head-only and emitted by tick, never release. Cancel discards all
pending input and emits cancel. Use the same monotonic clock as Behaviors.
"""
import math
import time


class Gestures:
    def __init__(self, double_interval=0.4, drag_distance=6.0,
                 long_press_interval=0.7, clock=time.monotonic):
        if double_interval < 0 or drag_distance < 0 or long_press_interval <= 0:
            raise ValueError("gesture intervals/distances must be nonnegative; long press positive")
        self.double_interval = double_interval
        self.drag_distance = drag_distance
        self.long_press_interval = long_press_interval
        self.clock = clock
        self.pressed = False
        self.dragging = False
        self.press_position = None
        self._region = "body"
        self._press_now = None
        self._consumed = False
        self._long_possible = False
        self._sequence = 0
        self._candidate = None
        self._pending = []

    def _now(self, now):
        return self.clock() if now is None else now

    def _event(self, name, now, region=None, **extra):
        return name, {"region": self._region if region is None else region,
                      "now": now, **extra}

    def _due_clicks(self, now, inclusive=True):
        events, waiting = [], []
        for click in self._pending:
            due = now >= click["deadline"] if inclusive else now > click["deadline"]
            if due:
                events.append(self._event("click", now, click["region"]))
            else:
                waiting.append(click)
        self._pending = waiting
        return events

    def press(self, position, region="body", now=None):
        now = self._now(now)
        # Duplicate delivery of the same physical press cannot count twice.
        if self.pressed:
            return []
        events = self._due_clicks(now, inclusive=False)
        self._candidate = None
        if self._pending:
            previous = self._pending[-1]
            if (previous["sequence"] == self._sequence and previous["region"] == region
                    and now <= previous["deadline"]
                    and math.dist(position, previous["position"]) <= self.drag_distance):
                self._candidate = previous["sequence"]
        self._sequence += 1
        self.pressed = True
        self.dragging = False
        self.press_position = tuple(position)
        self._press_now = now
        self._region = region
        self._consumed = False
        self._long_possible = region == "head"
        events.append(self._event("press", now))
        return events

    def double_click(self, position, region="body", now=None):
        now = self._now(now)
        # Qt sends its double-click callback *instead* of the second press.
        # Also tolerate adapters that already delivered that second press.
        events = [] if self.pressed else self.press(position, region, now)
        if self._consumed or self.dragging or self._region != region:
            return events
        match = next((click for click in self._pending
                      if click["sequence"] == self._candidate), None)
        if (match is None or now > match["deadline"]
                or math.dist(position, match["position"]) > self.drag_distance):
            return events
        self._pending.remove(match)
        self._candidate = None
        self._long_possible = False
        self._consumed = True
        events.append(self._event("double_click", now))
        return events

    def move(self, position, now=None):
        now = self._now(now)
        if not self.pressed or self._consumed:
            return []
        events = []
        if not self.dragging and math.dist(position, self.press_position) > self.drag_distance:
            self._long_possible = False
            self.dragging = True
            events.append(self._event("drag_start", now))
        if self.dragging:
            events.append(self._event("drag_move", now, position=tuple(position)))
        return events

    def release(self, now=None):
        now = self._now(now)
        if not self.pressed:
            return []
        events = [self._event("release", now)]
        if self.dragging:
            events.append(self._event("drag_end", now))
        elif not self._consumed:
            self._pending.append({
                "sequence": self._sequence, "region": self._region,
                "position": self.press_position,
                "deadline": self._press_now + self.double_interval,
            })
        self.pressed = False
        self.dragging = False
        self.press_position = None
        self._long_possible = False
        self._consumed = False
        self._candidate = None
        return events

    def tick(self, now=None):
        now = self._now(now)
        events = self._due_clicks(now)
        if (self.pressed and self._long_possible and not self.dragging
                and not self._consumed
                and now >= self._press_now + self.long_press_interval):
            self._long_possible = False
            self._consumed = True
            events.append(self._event("long_press", now))
        return events

    def cancel(self):
        now = self.clock()
        events = [self._event("cancel", now)]
        self._pending.clear()
        self.pressed = False
        self.dragging = False
        self.press_position = None
        self._press_now = None
        self._candidate = None
        self._long_possible = False
        self._consumed = False
        return events
