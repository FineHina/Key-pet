"""Offscreen widget tests: fake controller only, no storage, audio, or desktop input."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QDateTime, QObject, Qt, QTime, Signal
from PySide6.QtWidgets import QApplication, QDateTimeEdit, QDialog, QDialogButtonBox, QLineEdit

from pet.assistant_panel import AssistantPanel
from pet.notification_window import NotificationWindow

APP = QApplication.instance() or QApplication([])


def notification(identifier="n1", kind="reminder"):
    return {"id": identifier, "kind": kind, "title": f"通知 {identifier}", "body": "请休息一下。",
            "due_at": time.time(), "entity_id": "r1", "presented": False}


class FakeController(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []
        self.focus = None
        self.reminders = []
        self.pending = []
        self.settings = {"voice_enabled": True, "voice_volume": 70, "mute": False,
                         "dark_angry_voice": False, "dnd": False, "quiet_hours_enabled": True,
                         "quiet_start": "22:00", "quiet_end": "08:00", "fullscreen_dnd": True}
        self.voices = [{"id": "happy_01", "label": "开心", "file": "happy_01.wav", "text": "一起加油！",
                        "duration": 2.5, "available": True},
                       {"id": "missing_02", "label": "缺失语音", "source": "提供的 CSV", "text": "早上好。",
                        "duration": None, "available": False}]
        self.fail = None

    def _record(self, name, *args):
        if self.fail == name:
            raise ValueError("已有计时，请先取消；或检查输入。")
        self.calls.append((name, *args))

    def focus_snapshot(self):
        return self.focus

    def start_focus(self, minutes, kind="focus"):
        self._record("start_focus", minutes, kind)
        if self.focus and self.focus["state"] in ("running", "paused"):
            raise ValueError("已有计时正在进行。")
        self.focus = {"kind": kind, "state": "running", "remaining": minutes * 60}
        self.changed.emit()

    def pause_focus(self):
        self._record("pause_focus")
        self.focus["state"] = "paused"
        self.changed.emit()

    def resume_focus(self):
        self._record("resume_focus")
        self.focus["state"] = "running"
        self.changed.emit()

    def cancel_focus(self):
        self._record("cancel_focus")
        self.focus = None
        self.changed.emit()

    def reminders_list(self):
        return self.reminders

    def add_reminder(self, title, due_at):
        self._record("add_reminder", title, due_at)
        self.reminders.append({"id": "r-new", "title": title, "due_at": due_at, "status": "scheduled"})
        self.changed.emit()

    def edit_reminder(self, identifier, title, due_at):
        self._record("edit_reminder", identifier, title, due_at)
        for item in self.reminders:
            if item["id"] == identifier:
                item.update(title=title, due_at=due_at, status="scheduled")
        self.changed.emit()

    def cancel_reminder(self, identifier):
        self._record("cancel_reminder", identifier)
        for item in self.reminders:
            if item["id"] == identifier:
                item["status"] = "cancelled"
        self.changed.emit()

    def pending_notifications(self):
        return self.pending

    def complete_notification(self, identifier):
        self._record("complete_notification", identifier)
        self.pending = [item for item in self.pending if item["id"] != identifier]
        self.changed.emit()

    def snooze_notification(self, identifier, minutes):
        self._record("snooze_notification", identifier, minutes)
        self.pending = [item for item in self.pending if item["id"] != identifier]
        self.changed.emit()

    def update_settings(self, settings):
        self._record("update_settings", dict(settings))
        self.settings.update(settings)
        self.changed.emit()

    def voice_entries(self):
        return self.voices

    def audition(self, voice_id):
        self._record("audition", voice_id)

    def stop_audio(self):
        self._record("stop_audio")


class AssistantPanelTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.panel = AssistantPanel(self.controller)
        self.warning_patch = patch("pet.assistant_panel.QMessageBox.warning")
        self.warning = self.warning_patch.start()

    def tearDown(self):
        self.warning_patch.stop()
        self.panel.hide()
        self.panel.deleteLater()
        APP.processEvents()

    def test_modeless_tabs_and_minute_ranges(self):
        self.assertFalse(self.panel.isModal())
        self.assertEqual(self.panel.tabs.count(), 3)
        self.assertFalse(self.panel.testAttribute(Qt.WA_QuitOnClose))
        for spin in (self.panel.focus_minutes, self.panel.break_minutes, self.panel.reminder_minutes,
                     self.panel.snooze_minutes):
            self.assertEqual((spin.minimum(), spin.maximum()), (1, 1440))

    def test_all_focus_presets_and_custom_durations(self):
        for button, minutes, kind in zip(self.panel.start_buttons, (25, 45, 60, 25, 5, 10, 5),
                                         ("focus", "focus", "focus", "focus", "break", "break", "break")):
            button.click()
            self.assertIn(("start_focus", minutes, kind), self.controller.calls)
            self.panel.cancel_focus_button.click()
        self.panel.focus_minutes.setValue(1440)
        self.panel.start_buttons[3].click()
        self.assertIn(("start_focus", 1440, "focus"), self.controller.calls)

    def test_focus_state_and_countdown_follow_changed(self):
        self.assertFalse(self.panel.pause_button.isEnabled())
        self.panel.start_buttons[0].click()
        self.assertEqual(self.panel.countdown.text(), "25:00")
        self.assertFalse(self.panel.start_buttons[0].isEnabled())
        self.panel.pause_button.click()
        self.assertIn("已暂停", self.panel.focus_status.text())
        self.assertTrue(self.panel.resume_button.isEnabled())
        self.panel.resume_button.click()
        self.assertTrue(self.panel.pause_button.isEnabled())
        self.controller.focus["remaining"] = 61.2
        self.controller.changed.emit()
        self.assertEqual(self.panel.countdown.text(), "01:02")
        self.panel.cancel_focus_button.click()
        self.assertEqual(self.panel.countdown.text(), "00:00")
        self.assertTrue(self.panel.start_buttons[0].isEnabled())

    def test_completed_and_cancelled_snapshots_disable_controls(self):
        for state in ("completed", "cancelled"):
            self.controller.focus = {"kind": "break", "state": state, "remaining": -1}
            self.controller.changed.emit()
            self.assertEqual(self.panel.countdown.text(), "00:00")
            self.assertFalse(self.panel.cancel_focus_button.isEnabled())
            self.assertTrue(self.panel.start_buttons[0].isEnabled())

    def test_active_creation_error_is_readable_and_keeps_state(self):
        self.controller.start_focus(25)
        self.panel._start(45, "focus")
        self.warning.assert_called_once()
        self.assertIn("已有计时", self.warning.call_args.args[2])
        self.assertEqual(self.controller.focus["remaining"], 1500)

    def test_refresh_preserves_all_unsaved_inputs(self):
        self.panel.reminder_title.setText("尚未保存的提醒")
        self.panel.reminder_minutes.setValue(33)
        self.panel.reminder_mode.setCurrentIndex(1)
        target = QDateTime.currentDateTime().addDays(3)
        self.panel.reminder_datetime.setDateTime(target)
        self.panel.focus_minutes.setValue(71)
        self.panel.volume.setValue(23)
        self.panel.setting_checks["mute"].setChecked(True)
        self.panel.quiet_start.setTime(QTime(21, 14))
        self.controller.settings.update(voice_volume=88, mute=False, quiet_start="23:00")
        self.controller.changed.emit()
        self.assertEqual(self.panel.reminder_title.text(), "尚未保存的提醒")
        self.assertEqual(self.panel.reminder_minutes.value(), 33)
        self.assertEqual(self.panel.reminder_mode.currentIndex(), 1)
        self.assertEqual(self.panel.reminder_datetime.dateTime(), target)
        self.assertEqual(self.panel.focus_minutes.value(), 71)
        self.assertEqual(self.panel.volume.value(), 23)
        self.assertTrue(self.panel.setting_checks["mute"].isChecked())
        self.assertEqual(self.panel.quiet_start.time(), QTime(21, 14))

    def test_relative_and_absolute_reminder_creation(self):
        self.panel.reminder_title.setText("  喝水  ")
        self.panel.reminder_minutes.setValue(12)
        with patch("pet.assistant_panel.time.time", return_value=1000):
            self.panel.add_reminder_button.click()
        self.assertIn(("add_reminder", "喝水", 1720), self.controller.calls)
        self.assertEqual(self.panel.reminder_title.text(), "")
        self.panel.reminder_mode.setCurrentIndex(1)
        self.assertFalse(self.panel.reminder_minutes.isEnabled())
        self.assertTrue(self.panel.reminder_datetime.isEnabled())
        self.panel.reminder_title.setText("会议")
        target = QDateTime.currentDateTime().addSecs(3600)
        self.panel.reminder_datetime.setDateTime(target)
        self.panel.add_reminder_button.click()
        self.assertIn(("add_reminder", "会议", target.toMSecsSinceEpoch() / 1000), self.controller.calls)

    def test_invalid_reminder_never_calls_controller(self):
        self.panel.add_reminder_button.click()
        self.assertEqual(self.controller.calls, [])
        self.panel.reminder_title.setText("过去的时间")
        self.panel.reminder_mode.setCurrentIndex(1)
        self.panel.reminder_datetime.setDateTime(QDateTime.currentDateTime().addSecs(-60))
        self.panel.add_reminder_button.click()
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.warning.call_count, 2)
        self.assertEqual(self.panel.reminder_title.text(), "过去的时间")

    def test_failed_add_preserves_form(self):
        self.controller.fail = "add_reminder"
        self.panel.reminder_title.setText("保留输入")
        self.panel.add_reminder_button.click()
        self.assertEqual(self.panel.reminder_title.text(), "保留输入")
        self.warning.assert_called_once()

    def test_reminder_selection_uses_id_and_survives_reordering(self):
        self.controller.reminders = [{"id": identifier, "title": "同名", "due_at": time.time() + 500,
                                      "status": status} for identifier, status in
                                     (("r1", "scheduled"), ("r2", "pending"), ("r3", "done"), ("r4", "cancelled"))]
        self.controller.changed.emit()
        self.panel.reminders_table.selectRow(1)
        self.controller.reminders.reverse()
        self.controller.changed.emit()
        self.assertEqual(self.panel._selected_id(self.panel.reminders_table), "r2")
        self.panel.cancel_reminder_button.click()
        self.assertIn(("cancel_reminder", "r2"), self.controller.calls)
        self.assertFalse(self.panel.cancel_reminder_button.isEnabled())
        self.panel.reminders_table.selectRow(1)  # done after reversing
        self.assertFalse(self.panel.edit_reminder_button.isEnabled())
        self.assertEqual(self.panel.reminders_table.item(1, 2).text(), "已完成")

    def test_edit_dialog_saves_selected_id_without_real_dialog(self):
        self.controller.reminders = [{"id": "r7", "title": "旧标题", "due_at": time.time() + 500, "status": "scheduled"}]
        self.controller.changed.emit()
        self.panel.reminders_table.selectRow(0)

        def fake_exec(dialog):
            dialog.findChild(QLineEdit, "editReminderTitle").setText("新标题")
            dialog.findChild(QDateTimeEdit, "editReminderDue").setDateTime(QDateTime.currentDateTime().addDays(1))
            dialog.findChild(QDialogButtonBox).accepted.emit()
            return QDialog.Accepted

        with patch.object(QDialog, "exec", fake_exec):
            self.panel.edit_reminder_button.click()
        self.assertEqual(self.controller.calls[-1][:3], ("edit_reminder", "r7", "新标题"))

    def test_pending_actions_and_reminder_only_snooze(self):
        for button_name, minutes in (("snooze5_button", 5), ("snooze10_button", 10), ("snooze_custom_button", 17)):
            self.controller.pending = [notification()]
            self.controller.changed.emit()
            self.panel.pending_table.selectRow(0)
            self.panel.snooze_minutes.setValue(17)
            getattr(self.panel, button_name).click()
            self.assertIn(("snooze_notification", "n1", minutes), self.controller.calls)
        self.controller.pending = [notification("focus", "focus")]
        self.controller.changed.emit()
        self.panel.pending_table.selectRow(0)
        self.assertFalse(self.panel.snooze5_button.isEnabled())
        self.panel.complete_button.click()
        self.assertIn(("complete_notification", "focus"), self.controller.calls)
        self.assertEqual(self.panel.pending_table.rowCount(), 0)

    def test_apply_writes_exact_settings_and_failure_keeps_edits(self):
        self.assertEqual(self.panel.volume.value(), 70)
        self.panel.volume.setValue(31)
        self.panel.setting_checks["dnd"].setChecked(True)
        self.panel.quiet_start.setTime(QTime(23, 17))
        self.panel.quiet_end.setTime(QTime(7, 32))
        self.panel.apply_button.click()
        saved = self.controller.calls[-1][1]
        self.assertEqual(saved["voice_volume"], 31)
        self.assertTrue(saved["dnd"])
        self.assertEqual(saved["quiet_start"], "23:17")
        self.assertEqual(saved["quiet_end"], "07:32")
        self.assertEqual(set(saved), set(self.panel.setting_checks) | {"voice_volume", "quiet_start", "quiet_end"})
        self.assertNotIn("dark_angry_voice", saved)
        self.assertIn("hover_voice", saved)
        self.assertIn("idle_voice", saved)
        self.controller.fail = "update_settings"
        self.panel.volume.setValue(42)
        self.panel.apply_button.click()
        self.assertEqual(self.panel.volume.value(), 42)
        self.warning.assert_called_once()

    def test_voice_metadata_preview_controls_and_selection(self):
        self.assertIn("一起加油", self.panel.voice_text.toPlainText())
        self.assertNotIn("未核验", self.panel.voice_text.toPlainText())
        self.assertIn("中文：", self.panel.voice_text.toPlainText())
        self.assertIn("happy_01.wav", self.panel.voice_text.toPlainText())
        self.panel.audition_button.click()
        self.panel.stop_button.click()
        self.assertIn(("audition", "happy_01"), self.controller.calls)
        self.assertIn(("stop_audio",), self.controller.calls)
        self.panel.voice_combo.setCurrentIndex(1)
        self.assertFalse(self.panel.audition_button.isEnabled())
        self.assertFalse(self.panel.stop_button.isEnabled())
        self.assertIn("缺失", self.panel.voice_text.toPlainText())
        self.controller.voices.reverse()
        self.controller.changed.emit()
        self.assertEqual(self.panel.voice_combo.currentData(), "missing_02")

    def test_catalog_filters_category_grade_and_chinese_search(self):
        self.controller.voices = [
            {"id": "one", "label": "微笑", "text": "你好老师", "voice_category": "微笑",
             "grade": "A", "available": True},
            {"id": "two", "label": "剧情", "text": "星光", "voice_category": "剧情",
             "grade": "C", "available": True},
        ]
        self.controller.changed.emit()
        self.assertEqual(self.panel.voice_combo.count(), 2)
        self.panel.voice_grade.setCurrentText("C")
        self.assertEqual(self.panel.voice_combo.currentData(), "two")
        self.panel.voice_grade.setCurrentText("全部等级")
        self.panel.voice_search.setText("老师")
        self.assertEqual(self.panel.voice_combo.count(), 1)
        self.assertEqual(self.panel.voice_combo.currentData(), "one")
        self.panel.voice_search.clear()
        self.panel.voice_category.setCurrentIndex(self.panel.voice_category.findData("剧情"))
        self.assertEqual(self.panel.voice_combo.currentData(), "two")

    def test_empty_voice_list_disables_controls(self):
        self.controller.voices = []
        self.controller.changed.emit()
        self.assertFalse(self.panel.audition_button.isEnabled())
        self.assertFalse(self.panel.stop_button.isEnabled())
        self.assertIn("没有可用", self.panel.voice_text.toPlainText())

    def test_close_and_escape_hide_only_stop_preview_not_background(self):
        self.controller.start_focus(25)
        for close in (self.panel.close, self.panel.reject):
            self.panel.show()  # Offscreen only; no actual desktop window/input.
            close()
            self.assertFalse(self.panel.isVisible())
            self.assertEqual(self.controller.focus["state"], "running")
        self.assertEqual(self.controller.calls.count(("stop_audio",)), 2)
        self.assertNotIn(("cancel_focus",), self.controller.calls)


class NotificationWindowTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.window = NotificationWindow(self.controller)
        self.warning_patch = patch("pet.notification_window.QMessageBox.warning")
        self.warning = self.warning_patch.start()

    def tearDown(self):
        self.warning_patch.stop()
        self.window.hide()
        self.window.deleteLater()
        APP.processEvents()

    def test_tool_non_activating_but_mouse_interactive(self):
        self.assertEqual(self.window.windowFlags() & Qt.WindowType_Mask, Qt.Tool)
        self.assertTrue(self.window.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertFalse(self.window.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertFalse(self.window.windowFlags() & Qt.WindowTransparentForInput)
        self.assertFalse(self.window.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertFalse(self.window.testAttribute(Qt.WA_QuitOnClose))

    def test_notification_text_is_plain_not_html(self):
        item = notification()
        item.update(title="<b>提醒原文</b>", body="<img src='http://example.invalid/image'>")
        self.window.sync([item])
        self.assertEqual(self.window.title_label.textFormat(), Qt.PlainText)
        self.assertEqual(self.window.body_label.textFormat(), Qt.PlainText)
        self.assertEqual(self.window.title_label.text(), item["title"])
        self.assertEqual(self.window.body_label.text(), item["body"])

    def test_sync_never_presents_and_empty_hides(self):
        self.window.sync([notification()])
        self.assertFalse(self.window.isVisible())
        self.window.show()
        self.assertTrue(self.window.isVisible())
        self.window.sync([])
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.controller.calls, [])

    def test_queue_updates_same_window_and_preserves_selected_id(self):
        identity = id(self.window)
        first, second = notification("n1"), notification("n2", "focus")
        self.window.sync([first, second])
        self.assertEqual(self.window.title_label.text(), first["title"])
        self.window.pending_list.setCurrentRow(1)
        self.assertEqual(self.window.body_label.text(), second["body"])
        self.window.sync([second, first, notification("n3")])
        self.assertEqual(id(self.window), identity)
        self.assertEqual(self.window.pending_list.count(), 3)
        self.assertEqual(self.window.current_item()["id"], "n2")
        self.assertIn("3 条", self.window.queue_label.text())
        self.assertTrue(self.window.break_button.isEnabled())
        self.assertFalse(self.window.snooze5_button.isEnabled())

    def test_selected_complete_and_all_snooze_controls(self):
        for button_name, minutes in (("snooze5_button", 5), ("snooze10_button", 10), ("snooze_custom_button", 18)):
            self.controller.pending = [notification("n1"), notification("n2")]
            self.window.sync(self.controller.pending)
            self.window.pending_list.setCurrentRow(1)
            self.window.snooze_minutes.setValue(18)
            getattr(self.window, button_name).click()
            self.assertIn(("snooze_notification", "n2", minutes), self.controller.calls)
            self.assertEqual(self.window.current_item()["id"], "n1")
        self.window.complete_button.click()
        self.assertIn(("complete_notification", "n1"), self.controller.calls)
        self.assertFalse(self.window.complete_button.isEnabled())

    def test_start_break_then_resolve_focus_notification(self):
        self.controller.pending = [notification("nf", "focus")]
        self.window.sync(self.controller.pending)
        self.window.break_minutes.setValue(10)
        self.window.break_button.click()
        self.assertEqual(self.controller.calls, [("start_focus", 10, "break"), ("complete_notification", "nf")])
        self.assertEqual(self.controller.focus["kind"], "break")
        self.assertEqual(self.window.pending_list.count(), 0)

    def test_failed_break_does_not_resolve_notification(self):
        self.controller.pending = [notification("nf", "focus")]
        self.controller.fail = "start_focus"
        self.window.sync(self.controller.pending)
        self.window.break_button.click()
        self.warning.assert_called_once()
        self.assertEqual(self.controller.pending[0]["id"], "nf")
        self.assertEqual(self.controller.calls, [])

    def test_errors_keep_pending_and_open_signal_close_do_not_resolve(self):
        self.controller.pending = [notification()]
        self.window.sync(self.controller.pending)
        self.controller.fail = "complete_notification"
        self.window.complete_button.click()
        self.warning.assert_called_once()
        self.assertEqual(len(self.controller.pending), 1)
        opened = []
        self.window.open_requested.connect(lambda: opened.append(True))
        self.window.open_button.click()
        self.assertEqual(opened, [True])
        self.window.show()
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(len(self.controller.pending), 1)
        self.assertEqual(self.controller.calls, [])
        self.controller.changed.emit()
        self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
