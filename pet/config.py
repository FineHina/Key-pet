"""Validated, atomic persistence for position and assistant preferences."""
import json
from datetime import datetime
import math
import os
import re
import tempfile
from collections.abc import Mapping

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "config.json")

DEFAULTS = {
    "x": None,
    "y": None,
    "scale": 0.42,
    "voice_enabled": True,
    "voice_volume": 35,
    "mute": False,
    "dark_angry_voice": False,
    "hover_voice": False,
    "idle_voice": False,
    "assistant_open_voice": True,
    "birthday_mmdd": "",
    "calendar_events_enabled": True,
    "dnd": False,
    "quiet_hours_enabled": False,
    "quiet_start": "23:00",
    "quiet_end": "08:00",
    "fullscreen_dnd": False,
    "focus_minutes": 25,
    "break_minutes": 5,
}

_BOOLEAN_KEYS = {
    "voice_enabled", "mute", "dark_angry_voice", "dnd",
    "hover_voice", "idle_voice", "assistant_open_voice", "calendar_events_enabled",
    "quiet_hours_enabled", "fullscreen_dnd",
}
_TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


def validate(settings):
    """Return a copy with defaults, rejecting invalid known values.

    Unknown fields are preserved for compatibility with other preferences.
    Equal quiet-hour endpoints mean an all-day quiet period.
    """
    if not isinstance(settings, Mapping) or any(not isinstance(k, str) for k in settings):
        raise ValueError("Settings must be an object with string keys")
    cfg = dict(DEFAULTS)
    cfg.update(settings)
    for key in _BOOLEAN_KEYS:
        if type(cfg[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    for key in ("x", "y"):
        if cfg[key] is not None and type(cfg[key]) is not int:
            raise ValueError(f"{key} must be an integer or null")
    for key in ("scale", "focus_minutes", "break_minutes"):
        value = cfg[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{key} must be a finite positive number")
        if key != "scale" and value > 1440:
            raise ValueError(f"{key} must not exceed 1440 minutes")
    if type(cfg["voice_volume"]) is not int or not 0 <= cfg["voice_volume"] <= 100:
        raise ValueError("voice_volume must be an integer between 0 and 100")
    for key in ("quiet_start", "quiet_end"):
        if not isinstance(cfg[key], str) or not _TIME_PATTERN.fullmatch(cfg[key]):
            raise ValueError(f"{key} must use HH:MM (00:00 through 23:59)")
    birthday = cfg["birthday_mmdd"]
    if not isinstance(birthday, str):
        raise ValueError("生日应为空或使用MM-DD格式。")
    if birthday:
        if not re.fullmatch(r"\d{2}-\d{2}", birthday):
            raise ValueError("生日应使用MM-DD格式，不保存年份。")
        try:
            datetime.strptime("2000-" + birthday, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("生日月日无效。") from error
    return cfg


def load():
    """Load validated preferences; only a missing file uses defaults silently."""
    try:
        with open(PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid settings file {PATH}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"Cannot read settings file {PATH}: {exc}") from exc
    try:
        return validate(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid settings file {PATH}: {exc}") from exc


def save(cfg):
    """Merge a partial update into current settings, then atomically replace PATH."""
    if not isinstance(cfg, Mapping) or any(not isinstance(k, str) for k in cfg):
        raise ValueError("Settings update must be an object with string keys")
    merged = load()
    merged.update(cfg)
    merged = validate(merged)
    try:
        payload = json.dumps(merged, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Settings must contain JSON-serializable values: {exc}") from exc
    destination = os.path.abspath(PATH)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=os.path.dirname(destination),
                prefix=".config-", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    except OSError as exc:
        raise OSError(f"Cannot save settings file {PATH}: {exc}") from exc
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return merged
