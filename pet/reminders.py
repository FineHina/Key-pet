"""One-shot reminders with durable notification occurrences and snoozing."""
import math
import time

from .focus import _minutes


class ReminderService:
    def __init__(self, storage, wall_clock=time.time):
        self.storage = storage
        self.wall_clock = wall_clock

    @staticmethod
    def _dict(row):
        return {key: row[key] for key in ("id", "title", "due_at", "status", "time_mode")}

    @staticmethod
    def _title(title):
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError("Reminder title must be nonempty and at most 500 characters")
        return title.strip()

    @staticmethod
    def _due(due_at, now):
        if (isinstance(due_at, bool) or not isinstance(due_at, (int, float))
                or not math.isfinite(due_at) or due_at <= now):
            raise ValueError("Reminder due_at must be a finite future timestamp")
        return float(due_at)

    def _get(self, reminder_id):
        row = self.storage.conn.execute("SELECT * FROM reminders WHERE id = ?",
                                        (reminder_id,)).fetchone()
        if row is None:
            raise ValueError("Reminder not found")
        return row

    @staticmethod
    def _time_mode(mode):
        if mode not in ("absolute", "relative"):
            raise ValueError("提醒方式无效。")
        return mode

    def add(self, title, due_at, time_mode="absolute"):
        now = self.wall_clock()
        title, due_at = self._title(title), self._due(due_at, now)
        mode = self._time_mode(time_mode)
        with self.storage.transaction():
            cursor = self.storage.conn.execute(
                """INSERT INTO reminders (title, due_at, status, created_at, updated_at, time_mode)
                    VALUES (?, ?, 'scheduled', ?, ?, ?)""", (title, due_at, now, now, mode))
            return self._dict(self._get(cursor.lastrowid))

    def edit(self, reminder_id, title, due_at=None, *, time_mode=None, rearm=False):
        now = self.wall_clock()
        title = self._title(title)
        with self.storage.transaction():
            row = self._get(reminder_id)
            status = row["status"]
            mode = row["time_mode"] if time_mode is None else self._time_mode(time_mode)
            if due_at is None:
                due_at = row["due_at"]
            if (isinstance(due_at, bool) or not isinstance(due_at, (int, float))
                    or not math.isfinite(due_at)):
                raise ValueError("请选择有效的提醒时间。")
            time_changed = due_at != row["due_at"]
            reschedule = rearm or (time_changed and status in ("scheduled", "pending"))
            if reschedule:
                due_at = self._due(due_at, now)
                status = "scheduled"
                self._acknowledge_pending(reminder_id)
            elif status == "pending":
                self.storage.conn.execute(
                    """UPDATE notifications SET body = ? WHERE kind = 'reminder'
                        AND entity_id = ? AND status = 'pending'""", (title, reminder_id))
            self.storage.conn.execute(
                """UPDATE reminders SET title = ?, due_at = ?, updated_at = ?, status = ?,
                    time_mode = ?, schedule_revision = schedule_revision + ? WHERE id = ?""",
                (title, due_at, now, status, mode, int(reschedule), reminder_id))
            return self._dict(self._get(reminder_id))

    def delete(self, reminder_id):
        with self.storage.transaction():
            self._get(reminder_id)
            self.storage.conn.execute(
                "DELETE FROM notifications WHERE kind = 'reminder' AND entity_id = ?", (reminder_id,))
            self.storage.conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def _acknowledge_pending(self, reminder_id):
        self.storage.conn.execute(
            """UPDATE notifications SET status = 'acknowledged'
                WHERE kind = 'reminder' AND entity_id = ? AND status = 'pending'""", (reminder_id,))

    def _resolve(self, reminder_id, status):
        now = self.wall_clock()
        with self.storage.transaction():
            row = self._get(reminder_id)
            if row["status"] in ("done", "cancelled"):
                if row["status"] == status:
                    return self._dict(row)
                raise ValueError("Reminder is already resolved")
            self.storage.conn.execute(
                "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, reminder_id))
            self._acknowledge_pending(reminder_id)
            return self._dict(self._get(reminder_id))

    def cancel(self, reminder_id):
        return self._resolve(reminder_id, "cancelled")

    def complete(self, reminder_id):
        return self._resolve(reminder_id, "done")

    def snooze(self, notification_id, minutes):
        now = self.wall_clock()
        due_at = self._due(now + _minutes(minutes), now)
        with self.storage.transaction():
            notification = self.storage.conn.execute("SELECT * FROM notifications WHERE id = ?",
                                                     (notification_id,)).fetchone()
            if (notification is None or notification["kind"] != "reminder"
                    or notification["status"] != "pending"):
                raise ValueError("Snooze requires a pending reminder notification")
            row = self._get(notification["entity_id"])
            if row["status"] != "pending" or row["due_at"] != notification["due_at"]:
                raise ValueError("This reminder occurrence is no longer pending")
            self.storage.conn.execute(
                "UPDATE notifications SET status = 'acknowledged' WHERE id = ?", (notification_id,))
            self.storage.conn.execute(
                """UPDATE reminders SET status = 'scheduled', due_at = ?, updated_at = ?,
                    time_mode = 'relative', schedule_revision = schedule_revision + 1 WHERE id = ?""",
                (due_at, now, row["id"]))
            return self._dict(self._get(row["id"]))

    def list_all(self):
        return [self._dict(row) for row in self.storage.conn.execute(
            "SELECT * FROM reminders ORDER BY due_at, id")]

    def tick(self):
        now = self.wall_clock()
        result = []
        with self.storage.transaction():
            rows = self.storage.conn.execute(
                """SELECT * FROM reminders WHERE status = 'scheduled' AND due_at <= ?
                    ORDER BY due_at, id""", (now,)).fetchall()
            for row in rows:
                self.storage.conn.execute(
                    "UPDATE reminders SET status = 'pending', updated_at = ? WHERE id = ?",
                    (now, row["id"]))
                # repr of a float round-trips, preventing subsecond key collisions.
                key = f"{row['id']}:{repr(float(row['due_at']))}"
                if row["schedule_revision"]:
                    key += f":{row['schedule_revision']}"
                notification = self.storage._insert_notification(
                    "reminder", row["id"], key, "提醒", row["title"], row["due_at"])
                if notification is not None:
                    result.append(notification)
        return result
