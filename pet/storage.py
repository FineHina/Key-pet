"""SQLite-only persistence for assistant state, shortcuts and calendar claims."""
from contextlib import contextmanager
import os
import sqlite3


class Storage:
    SCHEMA_VERSION = 4

    def __init__(self, path):
        path = os.fspath(path)
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._savepoint = 0
        try:
            self._migrate()
        except BaseException:
            self.close()
            raise

    def _migrate(self):
        with self.transaction():
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise ValueError(f"Unsupported assistant database schema version: {version}")
            if version == self.SCHEMA_VERSION:
                return
            if version == 0:
                statements = (
                    """CREATE TABLE focus_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL CHECK(kind IN ('focus', 'break')),
                        state TEXT NOT NULL CHECK(state IN ('running', 'paused', 'completed', 'cancelled')),
                        duration REAL NOT NULL CHECK(duration > 0 AND duration <= 86400),
                        remaining REAL NOT NULL CHECK(remaining >= 0),
                        deadline REAL,
                        started_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        completed_at REAL,
                        CHECK((state = 'running' AND deadline IS NOT NULL)
                              OR (state != 'running' AND deadline IS NULL))
                    )""",
                    """CREATE UNIQUE INDEX one_active_focus_session
                        ON focus_sessions ((1)) WHERE state IN ('running', 'paused')""",
                    """CREATE TABLE reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 500),
                        due_at REAL NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('scheduled', 'pending', 'done', 'cancelled')),
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )""",
                    """CREATE INDEX scheduled_reminders_due ON reminders(due_at)
                        WHERE status = 'scheduled'""",
                    """CREATE TABLE notifications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL CHECK(kind IN ('focus', 'break', 'reminder')),
                        entity_id INTEGER NOT NULL,
                        occurrence_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        body TEXT NOT NULL,
                        due_at REAL NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending', 'acknowledged')),
                        presented INTEGER NOT NULL DEFAULT 0 CHECK(presented IN (0, 1)),
                        UNIQUE(kind, occurrence_key)
                    )""",
                    """CREATE INDEX pending_notifications_due ON notifications(due_at, id)
                        WHERE status = 'pending'""",
                )
                for statement in statements:
                    self.conn.execute(statement)
                self.conn.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                self.conn.execute("""CREATE TABLE shortcuts (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
                    type TEXT NOT NULL CHECK(type IN ('app', 'file', 'folder', 'web')),
                    target TEXT NOT NULL CHECK(length(target) > 0),
                    position INTEGER NOT NULL CHECK(position >= 0)
                )""")
                self.conn.execute("""CREATE TABLE calendar_events (
                    local_date TEXT PRIMARY KEY NOT NULL,
                    event_key TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('triggered', 'skipped')),
                    created_at REAL NOT NULL
                )""")
            if version < 3:
                self.conn.execute("""ALTER TABLE reminders ADD COLUMN time_mode TEXT NOT NULL
                    DEFAULT 'absolute' CHECK(time_mode IN ('absolute', 'relative'))""")
                self.conn.execute("""ALTER TABLE reminders ADD COLUMN schedule_revision INTEGER NOT NULL
                    DEFAULT 0 CHECK(schedule_revision >= 0)""")
            if version < 4:
                self._create_interaction_tables()
                self.conn.execute("PRAGMA user_version = 4")

    def _create_interaction_tables(self):
        statements = (
            """CREATE TABLE companion_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                affection INTEGER NOT NULL DEFAULT 0 CHECK(affection BETWEEN 0 AND 100),
                mood TEXT NOT NULL DEFAULT 'calm',
                mood_until REAL NOT NULL DEFAULT 0
            )""",
            "INSERT INTO companion_state (id) VALUES (1)",
            """CREATE TABLE companion_scores (
                event_key TEXT PRIMARY KEY,
                local_date TEXT NOT NULL,
                category TEXT NOT NULL,
                points INTEGER NOT NULL CHECK(points BETWEEN 0 AND 2),
                occurred_at REAL NOT NULL
            )""",
            """CREATE INDEX companion_daily_score ON companion_scores(local_date, category)""",
            """CREATE TABLE gift_inventory (
                gift_id TEXT PRIMARY KEY,
                quantity INTEGER NOT NULL CHECK(quantity >= 0),
                given INTEGER NOT NULL DEFAULT 0 CHECK(given >= 0)
            )""",
            """CREATE TABLE gift_rewards (
                pack TEXT NOT NULL,
                slot INTEGER NOT NULL CHECK(slot BETWEEN 0 AND 2),
                gift_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0, 1)),
                PRIMARY KEY(pack, slot)
            )""",
            """CREATE INDEX gift_rewards_unclaimed ON gift_rewards(created_at) WHERE claimed = 0""",
        )
        for statement in statements:
            self.conn.execute(statement)

    def claim_calendar_event(self, local_date, event_key, outcome, now):
        """Claim at most one automatic calendar event per local date, durably."""
        with self.transaction():
            cursor = self.conn.execute(
                """INSERT INTO calendar_events (local_date, event_key, outcome, created_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(local_date) DO NOTHING""",
                (local_date, event_key, outcome, now))
            return bool(cursor.rowcount)

    @contextmanager
    def transaction(self):
        """Serialize read/modify/write operations; nested calls use savepoints."""
        nested = self.conn.in_transaction
        if nested:
            self._savepoint += 1
            name = f"assistant_{self._savepoint}"
            self.conn.execute(f"SAVEPOINT {name}")
        else:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            if nested:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self.conn.execute(f"RELEASE SAVEPOINT {name}")
            else:
                self.conn.rollback()
            raise
        else:
            if nested:
                self.conn.execute(f"RELEASE SAVEPOINT {name}")
            else:
                self.conn.commit()

    @staticmethod
    def _notification_dict(row, presented=False):
        result = {key: row[key] for key in (
            "id", "kind", "entity_id", "title", "body", "due_at")}
        if presented:
            result["presented"] = bool(row["presented"])
        return result

    def _insert_notification(self, kind, entity_id, occurrence_key, title, body, due_at):
        """Insert inside the state-changing transaction; None means duplicate."""
        if not self.conn.in_transaction:
            with self.transaction():
                return self._insert_notification(
                    kind, entity_id, occurrence_key, title, body, due_at)
        cursor = self.conn.execute(
            """INSERT INTO notifications
                (kind, entity_id, occurrence_key, title, body, due_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, occurrence_key) DO NOTHING""",
            (kind, entity_id, occurrence_key, title, body, due_at))
        if not cursor.rowcount:
            return None
        row = self.conn.execute("SELECT * FROM notifications WHERE id = ?",
                                (cursor.lastrowid,)).fetchone()
        return self._notification_dict(row)

    def pending(self):
        rows = self.conn.execute(
            "SELECT * FROM notifications WHERE status = 'pending' ORDER BY due_at, id")
        return [self._notification_dict(row, presented=True) for row in rows]

    def mark_presented(self, ids):
        ids = list(ids)
        with self.transaction():
            self.conn.executemany(
                "UPDATE notifications SET presented = 1 WHERE id = ? AND status = 'pending'",
                ((identifier,) for identifier in ids))

    def acknowledge(self, notification_id):
        """Resolve a notification, without deleting history; repeated calls are safe."""
        with self.transaction():
            row = self.conn.execute("SELECT * FROM notifications WHERE id = ?",
                                    (notification_id,)).fetchone()
            if row is None:
                raise ValueError("Notification not found")
            if row["status"] == "pending":
                self.conn.execute("UPDATE notifications SET status = 'acknowledged' WHERE id = ?",
                                  (notification_id,))
                if row["kind"] == "reminder":
                    self.conn.execute(
                        """UPDATE reminders SET status = 'done'
                            WHERE id = ? AND status = 'pending' AND due_at = ?""",
                        (row["entity_id"], row["due_at"]))
            return self._notification_dict(row, presented=True)

    def close(self):
        self.conn.close()
