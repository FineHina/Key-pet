"""Speech-bubble lines (JSON, easy to extend) + bubble state manager."""
import json
import os
import random

from .expressions import Expressions

LINES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bubbles.json")

DEFAULT_LINES = {
    "click": ["……", "在的。", "有事？", "嗯。", "指令收到。", "（歪头）"],
    "double_click": ["别戳了。", "……？", "再戳要生气了哦。"],
    "drag": ["哇啊——", "放我下来！", "轻一点。"],
    "idle": ["……", "（盯着你）", "在忙吗？"],
}


class BubbleManager:
    """Holds the currently shown bubble; scene reads `state['bubble']` to paint it."""

    def __init__(self, expressions=None):
        self.expressions = expressions if expressions is not None else Expressions()
        self.lines = dict(DEFAULT_LINES)
        try:
            with open(LINES_PATH, "r", encoding="utf-8") as f:
                self.lines.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.active = None  # {"text","t0","dur"}

    def show(self, kind, now, dur=5.0):
        pool = self.lines.get(kind) or self.lines.get("click") or ["……"]
        category = {"click": "smile", "double_click": "happy", "drag": "embarrassed",
                    "idle": "default"}.get(kind, kind)
        path, error = self.expressions.choose(category)
        text = random.choice(pool)
        self.active = {"text": text, "image": str(path) if path else None,
                       "category": category, "error": error, "t0": now, "dur": dur}

    def clear(self):
        self.active = None

    def tick(self, now):
        if self.active and now > self.active["t0"] + self.active["dur"]:
            self.active = None

    def attach(self, state):
        state["bubble"] = self.active
