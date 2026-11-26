"""Restart-safe focus/break timers, with no UI or notification delivery."""
import math
import sqlite3
import sys
import time


def _minutes(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 < value <= 1440):
        raise ValueError("Minutes must be finite, greater than zero and at most 1440")
    seconds = float(value) * 60
    if seconds <= 0:
        raise ValueError("Duration is too small")
    return seconds


def _continuous_clock():
    """A suspend-inclusive clock distinguishes actual sleep from wall corrections."""
    try:
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_ticks = kernel.GetTickCount64
            get_ticks.argtypes = []
            get_ticks.restype = ctypes.c_ulonglong
            return lambda: get_ticks() / 1000.0
        if hasattr(time, "CLOCK_BOOTTIME"):
            return lambda: time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        pass
    return None


class FocusService:
    """Durations/remaining are seconds; deadlines are epoch seconds.

    In-process elapsed time normally uses max(wall, monotonic). For real clocks,
    a suspend-inclusive OS clock identifies large wall-clock corrections; those
    are rebased rather than ending a timer early. With two injected clocks alone
    a frozen-monotonic sleep and a forward wall correction are indistinguishable,
    so elapsed wall time wins conservatively. Restore uses the persisted wall
    deadline. Reading a snapshot never silently completes a session: tick does.
    """
    def __init__(self, storage, wall_clock=time.time, mono_clock=time.monotonic):
        self.storage = storage
        self.wall_clock = wall_clock
        self.mono_clock = mono_clock
        self._sleep_clock = (_continuous_clock()
                             if wall_clock is time.time and mono_clock is time.monotonic
                             else None)
        self._anchor = None
        with self.storage.transaction():
            row = self._active()
            if row is not None and row["state"] == "running":
                self._sample(row, self.wall_clock(), self.mono_clock())

    def _active(self):
        return self.storage.conn.execute(
            "SELECT * FROM focus_sessions WHERE state IN ('running', 'paused')").fetchone()

    @staticmethod
    def _dict(row):
        return {key: row[key] for key in (
            "id", "kind", "state", "duration", "remaining", "deadline")}

    def _sample(self, row, wall, mono):
        if row["state"] != "running":
            self._anchor = None
            return row
        sleep = self._sleep_clock() if self._sleep_clock is not None else None
        anchor = self._anchor
        if anchor is None or (anchor["id"], anchor["deadline"], anchor["remaining"]) != (
                row["id"], row["deadline"], row["remaining"]):
            raw_remaining = min(row["remaining"], row["deadline"] - wall)
        else:
            elapsed_wall = max(0.0, wall - anchor["wall"])
            elapsed_mono = max(0.0, mono - anchor["mono"])
            elapsed = max(elapsed_wall, elapsed_mono)
            if sleep is not None and anchor["sleep"] is not None:
                elapsed_sleep = max(0.0, sleep - anchor["sleep"])
                physical_elapsed = max(elapsed_mono, elapsed_sleep)
                # Small drift is harmless; a larger wall-only jump is a correction.
                if elapsed_wall > physical_elapsed + 2.0:
                    elapsed = physical_elapsed
                else:
                    elapsed = max(elapsed, elapsed_sleep)
            baseline = (row["deadline"] - anchor["wall"]
                        if row["remaining"] <= 0 else row["remaining"])
            raw_remaining = baseline - elapsed
        remaining = max(0.0, raw_remaining)
        deadline = wall + raw_remaining
        self.storage.conn.execute(
            "UPDATE focus_sessions SET remaining = ?, deadline = ?, updated_at = ? WHERE id = ?",
            (remaining, deadline, wall, row["id"]))
        self._anchor = {"id": row["id"], "wall": wall, "mono": mono, "sleep": sleep,
                        "remaining": remaining, "deadline": deadline}
        return self.storage.conn.execute("SELECT * FROM focus_sessions WHERE id = ?",
                                         (row["id"],)).fetchone()

    def start(self, minutes, kind="focus"):
        duration = _minutes(minutes)
        if kind not in ("focus", "break"):
            raise ValueError("Timer kind must be focus or break")
        wall, mono = self.wall_clock(), self.mono_clock()
        if not math.isfinite(wall + duration) or wall + duration <= wall:
            raise ValueError("Duration is too small or deadline is not finite")
        with self.storage.transaction():
            if self._active() is not None:
                raise ValueError("A focus or break session is already active")
            try:
                cursor = self.storage.conn.execute(
                    """INSERT INTO focus_sessions
                        (kind, state, duration, remaining, deadline, started_at, updated_at)
                        VALUES (?, 'running', ?, ?, ?, ?, ?)""",
                    (kind, duration, duration, wall + duration, wall, wall))
            except sqlite3.IntegrityError as exc:
                raise ValueError("A focus or break session is already active") from exc
            row = self.storage.conn.execute("SELECT * FROM focus_sessions WHERE id = ?",
                                            (cursor.lastrowid,)).fetchone()
            self._anchor = {"id": row["id"], "wall": wall, "mono": mono,
                            "sleep": self._sleep_clock() if self._sleep_clock else None,
                            "remaining": duration, "deadline": wall + duration}
            return self._dict(row)

    def snapshot(self):
        wall, mono = self.wall_clock(), self.mono_clock()
        with self.storage.transaction():
            row = self._active()
            if row is None:
                self._anchor = None
                return None
            return self._dict(self._sample(row, wall, mono))

    def pause(self):
        wall, mono = self.wall_clock(), self.mono_clock()
        with self.storage.transaction():
            row = self._active()
            if row is None:
                raise ValueError("There is no active session to pause")
            if row["state"] == "paused":
                return self._dict(row)
            row = self._sample(row, wall, mono)
            if row["remaining"] <= 0:
                # Expired sessions must generate their notification through tick.
                raise ValueError("Session is due; call tick before pausing")
            self.storage.conn.execute(
                "UPDATE focus_sessions SET state = 'paused', deadline = NULL, updated_at = ? WHERE id = ?",
                (wall, row["id"]))
            self._anchor = None
            result = self._dict(row)
            result.update(state="paused", deadline=None)
            return result

    def resume(self):
        wall, mono = self.wall_clock(), self.mono_clock()
        with self.storage.transaction():
            row = self._active()
            if row is None:
                raise ValueError("There is no active session to resume")
            if row["state"] == "running":
                return self._dict(self._sample(row, wall, mono))
            deadline = wall + row["remaining"]
            self.storage.conn.execute(
                "UPDATE focus_sessions SET state = 'running', deadline = ?, updated_at = ? WHERE id = ?",
                (deadline, wall, row["id"]))
            self._anchor = {"id": row["id"], "wall": wall, "mono": mono,
                            "sleep": self._sleep_clock() if self._sleep_clock else None,
                            "remaining": row["remaining"], "deadline": deadline}
            result = self._dict(row)
            result.update(state="running", deadline=deadline)
            return result

    def cancel(self):
        wall = self.wall_clock()
        with self.storage.transaction():
            row = self._active()
            if row is None:
                return None
            self.storage.conn.execute(
                "UPDATE focus_sessions SET state = 'cancelled', deadline = NULL, updated_at = ? WHERE id = ?",
                (wall, row["id"]))
            self._anchor = None
            result = self._dict(row)
            result.update(state="cancelled", deadline=None)
            return result

    def tick(self):
        wall, mono = self.wall_clock(), self.mono_clock()
        with self.storage.transaction():
            row = self._active()
            if row is None or row["state"] != "running":
                return []
            row = self._sample(row, wall, mono)
            if row["remaining"] > 0:
                return []
            kind = row["kind"]
            title = "专注时间结束" if kind == "focus" else "休息时间结束"
            body = "辛苦啦，休息一下吧。" if kind == "focus" else "休息好了，可以开始下一次专注啦。"
            self.storage.conn.execute(
                """UPDATE focus_sessions SET state = 'completed', remaining = 0,
                    deadline = NULL, completed_at = ?, updated_at = ? WHERE id = ?""",
                (wall, wall, row["id"]))
            notification = self.storage._insert_notification(
                kind, row["id"], str(row["id"]), title, body, row["deadline"])
            self._anchor = None
            return [notification] if notification is not None else []
