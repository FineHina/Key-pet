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
        return {key: row[key] for key in ("id", "title", "due_at", "status")}

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

    def add(self, title, due_at):
        now = self.wall_clock()
        title, due_at = self._title(title), self._due(due_at, now)
        with self.storage.transaction():
            cursor = self.storage.conn.execute(
                """INSERT INTO reminders (title, due_at, status, created_at, updated_at)
                    VALUES (?, ?, 'scheduled', ?, ?)""", (title, due_at, now, now))
            return self._dict(self._get(cursor.lastrowid))

    def edit(self, reminder_id, title, due_at):
        now = self.wall_clock()
        title, due_at = self._title(title), self._due(due_at, now)
        with self.storage.transaction():
            row = self._get(reminder_id)
            if row["status"] != "scheduled":
                raise ValueError("Only scheduled reminders can be edited")
            self.storage.conn.execute(
                "UPDATE reminders SET title = ?, due_at = ?, updated_at = ? WHERE id = ?",
                (title, due_at, now, reminder_id))
            return self._dict(self._get(reminder_id))

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
            self.storage.conn.execute(
                """UPDATE notifications SET status = 'acknowledged'
                    WHERE kind = 'reminder' AND entity_id = ? AND status = 'pending'""",
                (reminder_id,))
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
                "UPDATE reminders SET status = 'scheduled', due_at = ?, updated_at = ? WHERE id = ?",
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
                notification = self.storage._insert_notification(
                    "reminder", row["id"], key, "提醒", row["title"], row["due_at"])
                if notification is not None:
                    result.append(notification)
        return result
