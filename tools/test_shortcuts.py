"""Headless shortcut/migration tests. Run: python -I tools/test_shortcuts.py."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet.shortcuts import ShortcutsService
from pet.storage import Storage


# An independent copy of the actual legacy schema, not a v2 database relabelled v1.
_V1_SCHEMA = """
CREATE TABLE focus_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('focus', 'break')),
    state TEXT NOT NULL CHECK(state IN ('running', 'paused', 'completed', 'cancelled')),
    duration REAL NOT NULL CHECK(duration > 0 AND duration <= 86400),
    remaining REAL NOT NULL CHECK(remaining >= 0), deadline REAL,
    started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
    CHECK((state = 'running' AND deadline IS NOT NULL)
          OR (state != 'running' AND deadline IS NULL))
);
CREATE UNIQUE INDEX one_active_focus_session
    ON focus_sessions ((1)) WHERE state IN ('running', 'paused');
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 500),
    due_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('scheduled', 'pending', 'done', 'cancelled')),
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX scheduled_reminders_due ON reminders(due_at) WHERE status = 'scheduled';
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('focus', 'break', 'reminder')),
    entity_id INTEGER NOT NULL, occurrence_key TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, due_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'acknowledged')),
    presented INTEGER NOT NULL DEFAULT 0 CHECK(presented IN (0, 1)),
    UNIQUE(kind, occurrence_key)
);
CREATE INDEX pending_notifications_due ON notifications(due_at, id) WHERE status = 'pending';
PRAGMA user_version = 1;
"""


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "assistant.db"
        self.storage = Storage(self.path)
        self.opener = Mock(return_value=True)
        self.service = ShortcutsService(self.storage, self.opener)
        self.document = self.root / "notes with spaces.txt"
        self.document.write_bytes(b"keep these document bytes")
        self.app = self.root / "dummy application.EXE"
        self.app.write_bytes(b"MZ dummy fixture, never executed")
        self.folder = self.root / "folder with spaces"
        self.folder.mkdir()
        self.web = "https://example.invalid/path?q=test#section"

    def tearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def add_document(self, name="Notes"):
        return self.service.add(name, "file", str(self.document))

    def test_all_kinds_add_edit_and_restart(self):
        items = [self.service.add("  Application  ", "app", str(self.app)),
                 self.add_document(),
                 self.service.add("Folder", "folder", str(self.folder)),
                 self.service.add("Web", "web", self.web)]
        self.assertEqual([item["position"] for item in items], [0, 1, 2, 3])
        self.assertEqual(items[0]["name"], "Application")
        for item in items:
            self.assertEqual(set(item), {"id", "name", "kind", "target", "position"})
        edited = self.service.edit(items[1]["id"], "Renamed", "web", "http://example.invalid/")
        self.assertEqual((edited["id"], edited["position"]), (items[1]["id"], 1))
        items[1] = edited
        self.assertEqual(self.service.list_all(), items)
        self.storage.close()
        self.storage = Storage(self.path)
        self.service = ShortcutsService(self.storage, self.opener)
        self.assertEqual(self.service.list_all(), items)
        self.opener.assert_not_called()

    def test_name_kind_target_validation_is_nonmutating(self):
        item = self.add_document()
        for name in ("", " \t\n", "x" * 81, None, 123):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.add(name, "file", str(self.document))
        for kind in ("APP", "script", None, [], 1):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.service.edit(item["id"], "name", kind, str(self.document))
        for target in ("", " \t", None, 123, [], "https://example.invalid/\x00"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.service.add("name", "web", target)
        self.assertEqual(self.service.list_all(), [item])
        self.assertEqual(len(self.service.add("x" * 80, "web", self.web)["name"]), 80)
        self.opener.assert_not_called()

    def test_web_protocol_credentials_and_malformed_urls_rejected(self):
        targets = ("javascript:alert(1)", "data:text/html,test", "file:///C:/test.txt",
                   "ftp://example.invalid/", "mailto:user@example.invalid", "about:blank",
                   "//example.invalid/", "example.invalid", "https:///path", "http:",
                   "https://user:pass@example.invalid/", "https://user@example.invalid/",
                   "https://@example.invalid/", "https://example.invalid:bad/",
                   "https://example.invalid:65536/", "https://[broken/",
                   "https://example.invalid\\@evil.invalid/", "https://example.invalid/\npath",
                   "https://exa mple.invalid/", "https://user%40example.invalid/",
                   "https://example.invalid/%0a\x7f", "https://example.invalid/\tpath")
        for target in targets:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.service.add("Bad", "web", target)
        self.assertEqual(self.service.list_all(), [])
        for target in ("http://example.invalid/", "HTTPS://example.invalid:443/a%20b",
                       "https://[::1]:443/", self.web):
            self.assertEqual(self.service.add("Safe", "web", target)["target"], target)
        self.opener.assert_not_called()

    def test_local_paths_must_exist_be_absolute_and_match_kind(self):
        for kind, target in (("file", "notes.txt"), ("app", "dummy.exe"),
                             ("folder", "relative/folder"),
                             ("file", str(self.root / "missing.txt")),
                             ("folder", str(self.document)), ("app", str(self.folder)),
                             ("file", str(self.folder)), ("app", str(self.document)),
                             ("app", str(self.app) + " --dangerous"),
                             ("app", '"' + str(self.app) + '"'),
                             ("file", "file:///C:/notes.txt"),
                             ("file", str(self.document) + ":hidden")):
            with self.subTest(kind=kind, target=target), self.assertRaises(ValueError):
                self.service.add("Bad", kind, target)
        self.assertEqual(self.service.list_all(), [])
        self.opener.assert_not_called()

    def test_unc_network_and_device_paths_rejected_without_launch(self):
        for target in (r"\\server\share\file.txt", "//server/share/file.txt",
                       r"\\?\C:\file.exe", r"\\.\C:\file.exe", r"\??\C:\file.exe"):
            for kind in ("app", "file", "folder"):
                with self.subTest(target=target, kind=kind), self.assertRaises(ValueError):
                    self.service.add("Bad", kind, target)
        self.opener.assert_not_called()

    def test_scripts_and_executables_cannot_masquerade_as_files(self):
        for extension in ("exe", "com", "bat", "cmd", "ps1", "vbs", "js", "lnk", "url",
                          "scr", "py", "sh", "jar", "hta", "cpl", "msi", "reg", "wsf"):
            path = self.root / ("unsafe." + extension.upper())
            path.write_bytes(b"harmless dummy fixture")
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                self.service.add("Bad", "file", str(path))
        for index, content in enumerate((b"MZ executable", b"\x7fELF executable",
                                         b"#!/bin/sh\nexit", b"\xef\xbb\xbf#!/usr/bin/python\n",
                                         b"<?php echo 'script';")):
            path = self.root / f"disguised-{index}.txt"
            path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.service.add("Bad", "file", str(path))
        self.assertEqual(self.service.list_all(), [])
        self.opener.assert_not_called()

    def test_open_only_requests_fake_opener_and_reports_acceptance(self):
        for kind, target in (("app", str(self.app)), ("file", str(self.document)),
                             ("folder", str(self.folder)), ("web", self.web)):
            item = self.service.add(kind, kind, target)
            self.assertEqual(self.service.open(item["id"]), dict(item, status="success"))
            self.opener.assert_called_with(kind, item["target"])
        self.assertEqual(self.opener.call_count, 4)
        self.opener.return_value = False
        with self.assertRaisesRegex(ValueError, "not accepted"):
            self.service.open(item["id"])
        self.opener.side_effect = OSError("request failed")
        with self.assertRaisesRegex(ValueError, "request failed"):
            self.service.open(item["id"])

    def test_open_revalidates_removed_and_tampered_targets(self):
        item = self.add_document()
        self.document.unlink()
        with self.assertRaises(ValueError):
            self.service.open(item["id"])
        self.storage.conn.execute("UPDATE shortcuts SET type = 'web', target = ? WHERE id = ?",
                                  ("javascript:alert(1)", item["id"]))
        with self.assertRaises(ValueError):
            self.service.open(item["id"])
        self.opener.assert_not_called()

    def test_delete_only_configuration_preserves_file_bytes_and_folder(self):
        before_document, before_app = self.document.read_bytes(), self.app.read_bytes()
        nested = self.folder / "keep.txt"
        nested.write_bytes(b"keep folder contents")
        for kind, target in (("app", self.app), ("file", self.document), ("folder", self.folder)):
            item = self.service.add("Delete config", kind, str(target))
            self.assertIsNone(self.service.delete(item["id"]))
            self.assertTrue(target.exists())
        self.assertEqual(self.document.read_bytes(), before_document)
        self.assertEqual(self.app.read_bytes(), before_app)
        self.assertEqual(nested.read_bytes(), b"keep folder contents")
        self.assertEqual(self.service.list_all(), [])
        self.opener.assert_not_called()

    def test_reorder_swaps_neighbors_handles_gaps_and_survives_restart(self):
        a, b, c, d = [self.add_document(name) for name in ("A", "B", "C", "D")]
        self.assertEqual(self.service.reorder(a["id"], -1), self.service.list_all())
        self.assertEqual(self.service.reorder(d["id"], 1), self.service.list_all())
        self.service.delete(b["id"])
        moved = self.service.reorder(c["id"], -1)
        self.assertEqual([item["id"] for item in moved], [c["id"], a["id"], d["id"]])
        self.assertEqual([item["position"] for item in moved], [0, 2, 3])
        self.service.reorder(a["id"], 1)
        expected = self.service.list_all()
        self.assertEqual([item["id"] for item in expected], [c["id"], d["id"], a["id"]])
        self.storage.close()
        self.storage = Storage(self.path)
        self.service = ShortcutsService(self.storage, self.opener)
        self.assertEqual(self.service.list_all(), expected)
        self.assertEqual(self.add_document("E")["position"], 4)
        self.opener.assert_not_called()

    def test_reorder_both_updates_roll_back_on_failure(self):
        a, b = self.add_document("A"), self.add_document("B")
        before = self.service.list_all()
        self.storage.conn.execute(f"""CREATE TRIGGER reject_second_swap
            BEFORE UPDATE OF position ON shortcuts WHEN NEW.id = {b['id']}
            BEGIN SELECT RAISE(ABORT, 'swap failed'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.reorder(a["id"], 1)
        self.assertEqual(self.service.list_all(), before)

    def test_unknown_ids_and_invalid_directions_raise(self):
        item = self.add_document()
        for id in (999, True, "1", None, -1):
            for method in (self.service.open, self.service.delete,
                           lambda id: self.service.edit(id, "New", "file", str(self.document)),
                           lambda id: self.service.reorder(id, 1)):
                with self.subTest(id=id, method=method), self.assertRaises(ValueError):
                    method(id)
        for direction in (0, 2, -2, True, False, 1.0, "1", None):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                self.service.reorder(item["id"], direction)
        self.assertEqual(self.service.list_all(), [item])
        self.opener.assert_not_called()


class MigrationAndCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "assistant.db"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def legacy_snapshot(conn):
        columns = "id, title, due_at, status, created_at, updated_at"
        return {table: conn.execute(f"SELECT {columns if table == 'reminders' else '*'} "
                                    f"FROM {table} ORDER BY rowid").fetchall()
                for table in ("focus_sessions", "reminders", "notifications", "sqlite_sequence")}

    def test_actual_v1_migration_preserves_all_legacy_rows_and_schema(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(_V1_SCHEMA)
            conn.execute("""INSERT INTO focus_sessions VALUES
                (7, 'focus', 'paused', 120, 90, NULL, 1000, 1030, NULL),
                (8, 'break', 'completed', 60, 0, NULL, 900, 960, 960),
                (9, 'focus', 'cancelled', 60, 40, NULL, 800, 820, NULL)""")
            conn.execute("""INSERT INTO reminders VALUES
                (10, 'scheduled', 2000, 'scheduled', 1000, 1000),
                (11, 'pending', 950, 'pending', 900, 960),
                (12, 'done', 800, 'done', 700, 810),
                (13, 'cancelled', 900, 'cancelled', 800, 850)""")
            conn.execute("""INSERT INTO notifications VALUES
                (14, 'reminder', 11, '11:950', 'Reminder', 'pending', 950, 'pending', 1),
                (15, 'break', 8, '8:960', 'Break', 'done', 960, 'acknowledged', 1),
                (16, 'focus', 6, '6:900', 'Focus', 'pending', 900, 'pending', 0)""")
            conn.commit()
            before = self.legacy_snapshot(conn)
            schema = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'shortcuts'").fetchone())
        finally:
            conn.close()
        storage = Storage(self.path)
        try:
            self.assertEqual(Storage.SCHEMA_VERSION, 4)
            self.assertEqual(storage.conn.execute("PRAGMA user_version").fetchone()[0], 4)
            after = {table: [tuple(row) for row in rows]
                     for table, rows in self.legacy_snapshot(storage.conn).items()}
            self.assertEqual(after, before)
            modes = storage.conn.execute("SELECT time_mode, schedule_revision FROM reminders").fetchall()
            self.assertTrue(all(tuple(row) == ("absolute", 0) for row in modes))
            for name, sql in schema:
                if name == "reminders":
                    continue
                self.assertEqual(storage.conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0], sql)
            self.assertEqual(storage.conn.execute("SELECT count(*) FROM shortcuts").fetchone()[0], 0)
            self.assertEqual(storage.conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0], 0)
            self.assertEqual([row["id"] for row in storage.pending()], [16, 14])
            storage.close()
            storage = Storage(self.path)
            self.assertEqual({table: [tuple(row) for row in rows]
                              for table, rows in self.legacy_snapshot(storage.conn).items()}, before)
        finally:
            storage.close()

    def test_fresh_v4_schema_and_constraints(self):
        storage = Storage(self.path)
        try:
            self.assertEqual(storage.conn.execute("PRAGMA user_version").fetchone()[0], 4)
            for table in ("focus_sessions", "reminders", "notifications", "shortcuts", "calendar_events"):
                self.assertEqual(storage.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            for values in (("Bad", "script", "x", 0), ("", "web", "https://example.invalid", 0)):
                with self.assertRaises(sqlite3.IntegrityError):
                    storage.conn.execute("INSERT INTO shortcuts (name,type,target,position) VALUES (?,?,?,?)",
                                         values)
            with self.assertRaises(sqlite3.IntegrityError):
                storage.claim_calendar_event("2026-11-24", "birthday", "unknown", 1000)
            self.assertEqual(storage.conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0], 0)
        finally:
            storage.close()

    def test_calendar_one_claim_per_date_total_preserves_first_outcome_and_restart(self):
        storage = Storage(self.path)
        try:
            self.assertTrue(storage.claim_calendar_event("2026-11-24", "birthday", "skipped", 1000))
            self.assertFalse(storage.claim_calendar_event("2026-11-24", "birthday", "triggered", 1001))
            self.assertFalse(storage.claim_calendar_event("2026-11-24", "holiday", "triggered", 1002))
            row = storage.conn.execute("SELECT * FROM calendar_events").fetchone()
            self.assertEqual(tuple(row), ("2026-11-24", "birthday", "skipped", 1000.0))
            storage.close()
            storage = Storage(self.path)
            self.assertFalse(storage.claim_calendar_event("2026-11-24", "season", "triggered", 1003))
            self.assertTrue(storage.claim_calendar_event("2026-11-25", "birthday", "triggered", 2000))
            self.assertEqual(storage.conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0], 2)
        finally:
            storage.close()

    def test_calendar_claim_nested_transaction_rolls_back(self):
        storage = Storage(self.path)
        try:
            with self.assertRaises(RuntimeError):
                with storage.transaction():
                    self.assertTrue(storage.claim_calendar_event("2026-11-24", "holiday", "triggered", 1000))
                    raise RuntimeError("rollback")
            self.assertTrue(storage.claim_calendar_event("2026-11-24", "birthday", "skipped", 1001))
        finally:
            storage.close()

    def test_calendar_concurrent_connections_only_one_claim_wins(self):
        Storage(self.path).close()
        barrier = threading.Barrier(2)
        results, errors = [], []

        def claim(key):
            storage = None
            try:
                storage = Storage(self.path)
                barrier.wait(timeout=5)
                results.append(storage.claim_calendar_event("2026-11-24", key, "triggered", 1000))
            except Exception as exc:
                errors.append(exc)
            finally:
                if storage is not None:
                    storage.close()

        threads = [threading.Thread(target=claim, args=(key,)) for key in ("birthday", "holiday")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
