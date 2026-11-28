"""Offscreen companion-page tests: in-memory fake controller and patched dialogs only.

Run: python tools/test_companion_pages.py
No audio, real targets, configuration files or databases are used.
"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QLineEdit, QMessageBox,
    QTabWidget, QTextEdit, QWidget,
)

from pet.companion_pages import DialoguePage, ShortcutsPage, TOPICS

APP = QApplication.instance() or QApplication([])


class FakeController(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail = None
        self.settings = {"birthday_mmdd": "", "calendar_events_enabled": True}
        self.event = None
        self.reaction = None
        self.shortcuts = [
            {"id": "s1", "name": "工具", "kind": "app", "target": "C:/fake/tool.exe", "position": 0},
            {"id": "s2", "name": "文档", "kind": "file", "target": "C:/fake/note.txt", "position": 1},
            {"id": "s3", "name": "目录", "kind": "folder", "target": "C:/fake", "position": 2},
            {"id": "s4", "name": "网页", "kind": "web", "target": "https://example.invalid", "position": 3},
        ]

    def _record(self, method, *args):
        self.calls.append((method, *args))
        if self.fail == method:
            raise ValueError("模拟输入问题：目标无效或操作暂不可用。")

    def chat(self, text):
        self._record("chat", text)
        self.changed.emit()
        return self.reaction if self.reaction is not None else {
            "text": "本地回应：" + text, "image": "smile", "topic": "support"}

    def dialogue_topic(self, topic):
        self._record("dialogue_topic", topic)
        self.changed.emit()
        return self.reaction if self.reaction is not None else {
            "text": "话题回应：" + topic, "image": "default", "voice_id": None, "topic": topic}

    def today_event(self):
        if self.fail == "today_event":
            raise RuntimeError("无法读取纪念日")
        return dict(self.event) if self.event else None

    def trigger_today_event(self):
        self._record("trigger_today_event")
        self.changed.emit()
        return {"text": "生日快乐！", "image": "happy", "topic": "birthday"}

    def update_settings(self, changes):
        self._record("update_settings", dict(changes))
        self.settings.update(changes)
        self.changed.emit()

    def shortcuts_list(self):
        if self.fail == "shortcuts_list":
            raise RuntimeError("列表暂不可用")
        return [dict(item) for item in self.shortcuts]

    def add_shortcut(self, name, kind, target):
        self._record("add_shortcut", name, kind, target)
        self.shortcuts.append({"id": "new", "name": name, "kind": kind,
                               "target": target, "position": len(self.shortcuts)})
        self.changed.emit()
        return "new"

    def edit_shortcut(self, identifier, name, kind, target):
        self._record("edit_shortcut", identifier, name, kind, target)
        next(item for item in self.shortcuts if item["id"] == identifier).update(
            name=name, kind=kind, target=target)
        self.changed.emit()

    def delete_shortcut(self, identifier):
        self._record("delete_shortcut", identifier)
        self.shortcuts = [item for item in self.shortcuts if item["id"] != identifier]
        self._reposition()
        self.changed.emit()

    def _reposition(self):
        for position, item in enumerate(self.shortcuts):
            item["position"] = position

    def move_shortcut(self, identifier, direction):
        self._record("move_shortcut", identifier, direction)
        row = next(i for i, item in enumerate(self.shortcuts) if item["id"] == identifier)
        other = row + direction
        self.shortcuts[row], self.shortcuts[other] = self.shortcuts[other], self.shortcuts[row]
        self._reposition()
        self.changed.emit()

    def open_shortcut(self, identifier):
        self._record("open_shortcut", identifier)
        self.changed.emit()
        return {"message": "模拟打开；未访问任何目标。"}


class FakeControllerWithShortcutSignal(FakeController):
    shortcuts_changed = Signal()


class PageTests(unittest.TestCase):
    page_type = None

    def setUp(self):
        self.controller = FakeControllerWithShortcutSignal()
        self.page = self.page_type(self.controller)
        self.warning_patch = patch("pet.companion_pages.QMessageBox.warning")
        self.question_patch = patch("pet.companion_pages.QMessageBox.question", return_value=QMessageBox.No)
        self.warning = self.warning_patch.start()
        self.question = self.question_patch.start()

    def tearDown(self):
        self.question_patch.stop()
        self.warning_patch.stop()
        self.page.hide()
        self.page.deleteLater()
        APP.processEvents()

    def assert_input_issue(self):
        self.assertTrue(self.warning.called)
        self.assertTrue(self.warning.call_args.args[2].startswith("输入问题："))


class DialoguePageTests(PageTests):
    page_type = DialoguePage

    def test_public_widgets_topics_limits_and_local_label(self):
        self.assertIsInstance(self.page, QWidget)
        self.assertIsInstance(self.page.history, QTextEdit)
        self.assertTrue(self.page.history.isReadOnly())
        self.assertFalse(self.page.history.acceptRichText())
        self.assertEqual(self.page.history.document().maximumBlockCount(), 60)
        self.assertIsInstance(self.page.input, QLineEdit)
        self.assertEqual(self.page.input.maxLength(), 500)
        self.assertEqual(self.page.birthday_input.maxLength(), 5)
        self.assertTrue(self.page.auto_checkbox.isChecked())
        expected = {"greeting", "encouragement", "rest_advice", "coffee", "stars", "tease", "intro", "support"}
        self.assertEqual(set(self.page.topic_buttons), expected)
        for name in ("history", "input", "send_button", "clear_button", "birthday_input",
                     "auto_checkbox", "save_button", "event_label", "event_button", "status_label"):
            self.assertEqual(getattr(self.page, name).objectName(), name)
        self.assertTrue(any("非 AI" in label.text() for label in self.page.findChildren(
            type(self.page.status_label))))
        self.assertEqual(self.controller.calls, [])

    def test_send_button_and_return_trim_text_clear_only_sent_input(self):
        self.page.input.setText("  你好  ")
        self.page.send_button.click()
        self.assertEqual(self.controller.calls, [("chat", "你好")])
        self.assertEqual(self.page.history.toPlainText(), "你好\n本地回应：你好")
        self.assertEqual(self.page.input.text(), "")
        self.page.input.setText("回车发送")
        QTest.keyClick(self.page.input, Qt.Key_Return)
        self.assertEqual(self.controller.calls[-1], ("chat", "回车发送"))
        self.assertEqual(len(self.page.history.toPlainText().splitlines()), 4)

    def test_empty_input_does_not_call_controller(self):
        self.page.input.setText("   ")
        self.page.send_button.click()
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.page.history.toPlainText(), "")
        self.assertEqual(self.page.input.text(), "   ")
        self.assert_input_issue()

    def test_failed_send_preserves_draft_and_existing_history(self):
        self.page.topic_buttons["greeting"].click()
        old_history = self.page.history.toPlainText()
        self.controller.fail = "chat"
        self.page.input.setText("不要丢失这个草稿")
        self.page.send_button.click()
        self.assertEqual(self.page.input.text(), "不要丢失这个草稿")
        self.assertEqual(self.page.history.toPlainText(), old_history)
        self.assert_input_issue()

    def test_all_topics_call_exact_ids_without_consuming_chat_draft(self):
        self.page.input.setText("尚未发送")
        for topic, title in TOPICS:
            self.page.topic_buttons[topic].click()
            self.assertEqual(self.controller.calls[-1], ("dialogue_topic", topic))
            self.assertIn("话题：" + title, self.page.history.toPlainText())
            self.assertEqual(self.page.topic_buttons[topic].property("topic_id"), topic)
            self.assertEqual(self.page.input.text(), "尚未发送")
        self.assertEqual(len(self.page.history.toPlainText().splitlines()), 16)

    def test_plain_text_response_voice_optional_and_wait_notice(self):
        self.controller.reaction = {"text": "<b>不是 HTML</b>\n第二行\r\n第三行",
                                    "image": "happy", "topic": "support",
                                    "wait_notice": "语音忙，请稍后；文字已显示。"}
        self.page.input.setText("<i>你好</i>")
        self.page.send_button.click()
        self.assertEqual(self.page.history.toPlainText(),
                         "<i>你好</i>\n<b>不是 HTML</b> 第二行 第三行")
        self.assertIn("语音忙", self.page.status_label.text())
        self.controller.reaction = {"text": "仅文字", "waitnotice": "稍后再试"}
        self.page.topic_buttons["support"].click()
        self.assertEqual(self.page.status_label.text(), "稍后再试")
        self.controller.reaction = "纯字符串 mock 回应"
        self.page.topic_buttons["coffee"].click()
        self.assertIn("纯字符串 mock 回应", self.page.history.toPlainText())
        self.assertEqual(self.warning.call_count, 0)

    def test_empty_reaction_does_not_erase_draft(self):
        for reaction in ({"text": ""}, {"text": None}, {"text": 12}):
            self.controller.reaction = reaction
            self.page.input.setText("重试草稿")
            self.page.send_button.click()
            self.assertEqual(self.page.input.text(), "重试草稿")
        self.assertEqual(self.page.history.toPlainText(), "")
        self.assert_input_issue()

    def test_history_retains_latest_30_exchanges_with_60_lines(self):
        self.controller.reaction = {"text": "第一行\n第二行\u2028第三行"}
        for number in range(37):
            self.page.input.setText(f"消息 {number}")
            self.page.send_button.click()
        lines = self.page.history.toPlainText().splitlines()
        self.assertEqual(len(lines), 60)
        self.assertEqual(lines[0], "消息 7")
        self.assertEqual(lines[-2], "消息 36")
        self.assertEqual(lines[-1], "第一行 第二行 第三行")
        self.assertEqual(len(self.page._history), 30)

    def test_history_text_and_input_are_bounded(self):
        self.controller.reaction = {"text": "回" * 9000}
        self.page.input.setText("问" * 900)
        self.assertEqual(len(self.page.input.text()), 500)
        self.page.send_button.click()
        lines = self.page.history.toPlainText().splitlines()
        self.assertEqual(len(lines[0]), 500)
        self.assertEqual(len(lines[1]), 4000)

    def test_clear_is_local_and_keeps_unsent_drafts(self):
        self.page.topic_buttons["greeting"].click()
        self.page.input.setText("未发送")
        self.page.birthday_input.setText("12-31")
        calls = list(self.controller.calls)
        self.page.clear_button.click()
        self.assertTrue(self.page.history.toPlainText())
        self.question.return_value = QMessageBox.Yes
        self.page.clear_button.click()
        self.assertEqual(self.page.history.toPlainText(), "")
        self.assertEqual(list(self.page._history), [])
        self.assertEqual(self.page.input.text(), "未发送")
        self.assertEqual(self.page.birthday_input.text(), "12-31")
        self.assertEqual(self.controller.calls, calls)
        other = DialoguePage(self.controller)
        try:
            self.assertEqual(other.history.toPlainText(), "")
        finally:
            other.deleteLater()

    def test_changed_refresh_preserves_chat_and_dirty_settings_drafts(self):
        self.page.input.setText("草稿 <b>仍在</b>")
        self.page.birthday_input.setText("02-29")
        self.page.auto_checkbox.setChecked(False)
        self.controller.settings.update(birthday_mmdd="11-24", calendar_events_enabled=True)
        for _ in range(6):
            self.controller.changed.emit()
        self.assertEqual(self.page.input.text(), "草稿 <b>仍在</b>")
        self.assertEqual(self.page.birthday_input.text(), "02-29")
        self.assertFalse(self.page.auto_checkbox.isChecked())
        self.assertEqual(self.controller.calls, [])

    def test_clean_settings_fields_follow_controller_changed(self):
        self.controller.settings.update(birthday_mmdd="12-31", calendar_events_enabled=False)
        self.controller.changed.emit()
        self.assertEqual(self.page.birthday_input.text(), "12-31")
        self.assertFalse(self.page.auto_checkbox.isChecked())

    def test_birthday_save_valid_leap_day_and_empty_only_calendar_keys(self):
        for birthday in ("02-29", "12-31", ""):
            self.page.birthday_input.setText(birthday)
            self.page.auto_checkbox.setChecked(False)
            self.page.save_button.click()
            self.assertEqual(self.controller.calls[-1], ("update_settings", {
                "birthday_mmdd": birthday, "calendar_events_enabled": False}))
            self.assertEqual(self.page.birthday_input.text(), birthday)
        self.assertEqual(self.warning.call_count, 0)

    def test_invalid_birthday_does_not_save_or_destroy_draft(self):
        for birthday in ("02-30", "13-01", "00-10", "01-00", "1-01", "ab-cd", "１２-３１"):
            with self.subTest(birthday=birthday):
                self.page.birthday_input.setText(birthday)
                self.page.save_button.click()
                self.assertEqual(self.controller.calls, [])
                self.assertEqual(self.page.birthday_input.text(), birthday)
                self.assert_input_issue()

    def test_failed_settings_save_preserves_drafts(self):
        self.controller.fail = "update_settings"
        self.page.birthday_input.setText("11-24")
        self.page.auto_checkbox.setChecked(False)
        self.page.save_button.click()
        self.assertEqual(self.page.birthday_input.text(), "11-24")
        self.assertFalse(self.page.auto_checkbox.isChecked())
        self.assert_input_issue()

    def test_event_refresh_is_passive_and_manual_trigger_appends_response(self):
        self.assertFalse(self.page.event_button.isEnabled())
        self.page.event_button.click()
        self.assertEqual(self.controller.calls, [])
        self.controller.event = {"name": "<b>生日</b>", "topic": "birthday", "eventkey": "birthday",
                                 "date": "2026-11-24"}
        self.controller.changed.emit()
        self.assertEqual(self.page.event_label.textFormat(), Qt.PlainText)
        self.assertIn("<b>生日</b>", self.page.event_label.text())
        self.assertIn("2026-11-24", self.page.event_label.text())
        self.assertTrue(self.page.event_button.isEnabled())
        self.assertEqual(self.controller.calls, [])
        self.page.input.setText("日历之外的草稿")
        self.page.event_button.click()
        self.assertEqual(self.controller.calls, [("trigger_today_event",)])
        self.assertEqual(self.page.input.text(), "日历之外的草稿")
        self.assertIn("生日快乐！", self.page.history.toPlainText())
        self.controller.event = None
        self.controller.changed.emit()
        self.assertFalse(self.page.event_button.isEnabled())

    def test_event_poll_failure_is_nonmodal_and_can_recover(self):
        self.controller.fail = "today_event"
        self.controller.changed.emit()
        self.assertFalse(self.page.event_button.isEnabled())
        self.assertIn("无法读取", self.page.event_label.text())
        self.warning.assert_not_called()
        self.controller.fail = None
        self.controller.changed.emit()
        self.assertIn("暂无", self.page.event_label.text())

    def test_failed_topic_and_event_add_no_transcript(self):
        self.controller.fail = "dialogue_topic"
        self.page.topic_buttons["stars"].click()
        self.assertEqual(self.page.history.toPlainText(), "")
        self.controller.event = {"name": "生日", "topic": "birthday", "eventkey": "birthday", "date": "11-24"}
        self.controller.changed.emit()
        self.controller.fail = "trigger_today_event"
        self.page.event_button.click()
        self.assertEqual(self.page.history.toPlainText(), "")
        self.assert_input_issue()


class ShortcutsPageTests(PageTests):
    page_type = ShortcutsPage

    def set_draft(self, name="未添加的入口", kind="web", target="https://draft.invalid"):
        self.page.name_input.setText(name)
        self.page.kind_combo.setCurrentIndex(self.page.kind_combo.findData(kind))
        self.page.target_input.setText(target)

    def assert_draft(self, name="未添加的入口", kind="web", target="https://draft.invalid"):
        self.assertEqual(self.page.name_input.text(), name)
        self.assertEqual(self.page.kind_combo.currentData(), kind)
        self.assertEqual(self.page.target_input.text(), target)

    def test_public_widgets_initial_table_kinds_and_no_selection(self):
        self.assertIsInstance(self.page, QWidget)
        self.assertEqual(self.page.name_input.maxLength(), 80)
        self.assertEqual([self.page.kind_combo.itemData(i) for i in range(4)], ["app", "file", "folder", "web"])
        self.assertEqual(self.page.table.rowCount(), 4)
        self.assertFalse(self.page.table.isSortingEnabled())
        self.assertEqual(self.page.table.editTriggers(), QAbstractItemView.NoEditTriggers)
        self.assertIsNone(self.page.selected_id())
        for name in ("name_input", "kind_combo", "target_input", "browse_button", "table",
                     "open_button", "add_button", "edit_button", "delete_button", "up_button", "down_button",
                     "status_label"):
            self.assertEqual(getattr(self.page, name).objectName(), name)
        for button in (self.page.open_button, self.page.edit_button, self.page.delete_button,
                       self.page.up_button, self.page.down_button):
            self.assertFalse(button.isEnabled())
        self.assertEqual(self.controller.calls, [])

    def test_explicit_selection_and_refresh_never_overwrite_add_draft(self):
        self.set_draft()
        self.page.table.selectRow(1)
        self.assert_draft()
        self.assertEqual(self.page.selected_id(), "s2")
        self.controller.shortcuts[1]["name"] = "<b>已更新</b>"
        self.controller.changed.emit()
        self.assert_draft()
        self.assertEqual(self.page.selected_id(), "s2")
        self.assertEqual(self.page.table.item(1, 0).text(), "<b>已更新</b>")
        self.controller.shortcuts.reverse()
        self.controller._reposition()
        self.controller.shortcuts_changed.emit()
        self.assertEqual(self.page.table.currentRow(), 2)
        self.assertEqual(self.page.selected_id(), "s2")
        self.assert_draft()

    def test_list_sorted_by_position_not_input_order(self):
        self.controller.shortcuts.reverse()
        self.controller.changed.emit()
        self.assertEqual([self.page.table.item(row, 0).data(Qt.UserRole) for row in range(4)],
                         ["s1", "s2", "s3", "s4"])

    def test_removed_selection_disables_actions_without_selecting_new_entry(self):
        self.page.table.selectRow(1)
        self.set_draft()
        self.controller.shortcuts = [item for item in self.controller.shortcuts if item["id"] != "s2"]
        self.controller.changed.emit()
        self.assertIsNone(self.page.selected_id())
        self.assertFalse(self.page.open_button.isEnabled())
        self.assertFalse(self.page.edit_button.isEnabled())
        self.assertFalse(self.page.delete_button.isEnabled())
        self.assert_draft()

    def test_optional_shortcuts_signal_absent_still_uses_changed(self):
        controller = FakeController()
        other = ShortcutsPage(controller)
        try:
            controller.shortcuts.clear()
            controller.changed.emit()
            self.assertEqual(other.table.rowCount(), 0)
        finally:
            other.deleteLater()

    def test_add_trimmed_inputs_and_preserve_kind_after_success(self):
        self.set_draft("  名称  ", "web", "  https://example.invalid  ")
        self.page.add_button.click()
        self.assertEqual(self.controller.calls[-1], ("add_shortcut", "名称", "web", "https://example.invalid"))
        self.assertEqual(self.page.name_input.text(), "")
        self.assertEqual(self.page.target_input.text(), "")
        self.assertEqual(self.page.kind_combo.currentData(), "web")
        self.assertEqual(self.page.table.rowCount(), 5)
        self.assertFalse(any(call[0] == "open_shortcut" for call in self.controller.calls))

    def test_name_limit_empty_fields_validation_and_controller_error(self):
        self.set_draft("名" * 120)
        self.assertEqual(len(self.page.name_input.text()), 80)
        for name, target in ((" ", "C:/fake"), ("名字", "   ")):
            self.set_draft(name, "file", target)
            self.page.add_button.click()
            self.assertEqual(self.controller.calls, [])
            self.assert_draft(name, "file", target)
            self.assert_input_issue()
        self.set_draft()
        self.controller.fail = "add_shortcut"
        self.page.add_button.click()
        self.assert_draft()
        self.assert_input_issue()

    def test_browse_app_file_folder_and_cancel_without_real_dialogs(self):
        with patch("pet.companion_pages.QFileDialog.getOpenFileName", return_value=("C:/picked.exe", "")) as file_dialog, \
                patch("pet.companion_pages.QFileDialog.getExistingDirectory", return_value="C:/folder") as folder_dialog:
            self.page.browse_button.click()
            self.assertEqual(self.page.target_input.text(), "C:/picked.exe")
            self.assertIn("*.exe", file_dialog.call_args.args[3])
            self.page.kind_combo.setCurrentIndex(self.page.kind_combo.findData("file"))
            file_dialog.return_value = ("C:/picked.txt", "")
            self.page.browse_button.click()
            self.assertEqual(self.page.target_input.text(), "C:/picked.txt")
            self.assertEqual(file_dialog.call_args.args[3], "所有文件 (*)")
            file_dialog.return_value = ("", "")
            self.page.browse_button.click()
            self.assertEqual(self.page.target_input.text(), "C:/picked.txt")
            self.page.kind_combo.setCurrentIndex(self.page.kind_combo.findData("folder"))
            self.page.browse_button.click()
            self.assertEqual(self.page.target_input.text(), "C:/folder")
            folder_dialog.return_value = ""
            self.page.browse_button.click()
            self.assertEqual(self.page.target_input.text(), "C:/folder")
        self.assertEqual(self.controller.calls, [])

    def test_web_draft_disables_browse_and_never_opens_target_automatically(self):
        self.set_draft()
        with patch("pet.companion_pages.QFileDialog.getOpenFileName") as file_dialog, \
                patch("pet.companion_pages.QFileDialog.getExistingDirectory") as folder_dialog:
            self.assertFalse(self.page.browse_button.isEnabled())
            self.page.browse_button.click()
            self.page.refresh()
            file_dialog.assert_not_called()
            folder_dialog.assert_not_called()
        self.assertEqual(self.controller.calls, [])

    def test_open_calls_selected_id_and_displays_plain_message(self):
        self.set_draft()
        self.page.table.selectRow(3)
        self.page.open_button.click()
        self.assertEqual(self.controller.calls, [("open_shortcut", "s4")])
        self.assertEqual(self.page.status_label.text(), "模拟打开；未访问任何目标。")
        self.assertEqual(self.page.status_label.textFormat(), Qt.PlainText)
        self.assert_draft()
        self.assertEqual(self.page.selected_id(), "s4")

    def test_open_error_keeps_selection_and_add_draft(self):
        self.set_draft()
        self.page.table.selectRow(0)
        self.controller.fail = "open_shortcut"
        self.page.open_button.click()
        self.assertEqual(self.page.selected_id(), "s1")
        self.assert_draft()
        self.assert_input_issue()

    def test_move_uses_exact_id_directions_restores_selection_and_boundaries(self):
        self.set_draft()
        self.page.table.selectRow(0)
        self.assertFalse(self.page.up_button.isEnabled())
        self.page.up_button.click()
        self.assertEqual(self.controller.calls, [])
        self.page.down_button.click()
        self.assertEqual(self.controller.calls[-1], ("move_shortcut", "s1", 1))
        self.assertEqual(self.page.selected_id(), "s1")
        self.assertEqual(self.page.table.currentRow(), 1)
        self.page.up_button.click()
        self.assertEqual(self.controller.calls[-1], ("move_shortcut", "s1", -1))
        self.assertEqual(self.page.table.currentRow(), 0)
        self.page.table.selectRow(3)
        self.assertFalse(self.page.down_button.isEnabled())
        self.assertTrue(self.page.up_button.isEnabled())
        self.assert_draft()

    def test_move_error_is_readable_and_keeps_order(self):
        self.page.table.selectRow(1)
        self.controller.fail = "move_shortcut"
        self.page.up_button.click()
        self.assertEqual(self.page.selected_id(), "s2")
        self.assertEqual(self.page.table.currentRow(), 1)
        self.assert_input_issue()

    def test_delete_requires_confirmation_only_deletes_entry_by_id(self):
        self.set_draft()
        self.page.table.selectRow(1)
        self.page.delete_button.click()
        self.assertEqual(self.controller.calls, [])
        self.assertIn("不会删除目标", self.question.call_args.args[2])
        self.assertEqual(self.question.call_args.args[-1], QMessageBox.No)
        self.question.return_value = QMessageBox.Yes
        self.page.delete_button.click()
        self.assertEqual(self.controller.calls, [("delete_shortcut", "s2")])
        self.assertEqual(self.page.table.rowCount(), 3)
        self.assertIsNone(self.page.selected_id())
        self.assert_draft()
        self.assertIn("目标未改动", self.page.status_label.text())

    def test_delete_error_preserves_selected_entry(self):
        self.question.return_value = QMessageBox.Yes
        self.page.table.selectRow(0)
        self.controller.fail = "delete_shortcut"
        self.page.delete_button.click()
        self.assertEqual(self.page.table.rowCount(), 4)
        self.assertEqual(self.page.selected_id(), "s1")
        self.assert_input_issue()

    def test_edit_modal_populates_selected_entry_and_save_keeps_add_draft(self):
        self.set_draft()
        self.page.table.selectRow(1)
        observed = []

        def edit_dialog(dialog):
            self.assertTrue(dialog.isModal())
            self.assertEqual(dialog.objectName(), "shortcut_edit_dialog")
            self.assertEqual(dialog.name_input.text(), "文档")
            self.assertEqual(dialog.kind_combo.currentData(), "file")
            self.assertEqual(dialog.target_input.text(), "C:/fake/note.txt")
            self.assertEqual(dialog.name_input.maxLength(), 80)
            dialog.name_input.setText("  已编辑  ")
            dialog.kind_combo.setCurrentIndex(dialog.kind_combo.findData("folder"))
            with patch("pet.companion_pages.QFileDialog.getExistingDirectory", return_value="C:/edited"):
                dialog.browse_button.click()
            self.controller.changed.emit()
            self.controller.shortcuts_changed.emit()
            self.assertEqual(dialog.name_input.text(), "  已编辑  ")
            self.assertEqual(dialog.target_input.text(), "C:/edited")
            self.assert_draft()
            dialog.buttons.button(QDialogButtonBox.Ok).click()
            observed.append(dialog.result())
            return dialog.result()

        with patch("pet.companion_pages._ShortcutEditDialog.exec", new=edit_dialog):
            self.page.edit_button.click()
        self.assertEqual(observed, [QDialog.Accepted])
        self.assertEqual(self.controller.calls, [("edit_shortcut", "s2", "已编辑", "folder", "C:/edited")])
        self.assertEqual(self.page.selected_id(), "s2")
        self.assertEqual(self.page.table.item(1, 0).text(), "已编辑")
        self.assert_draft()

    def test_cancel_edit_does_not_call_controller(self):
        self.page.table.selectRow(0)

        def cancel_dialog(dialog):
            dialog.name_input.setText("未保存的修改")
            dialog.buttons.button(QDialogButtonBox.Cancel).click()
            return dialog.result()

        with patch("pet.companion_pages._ShortcutEditDialog.exec", new=cancel_dialog):
            self.page.edit_button.click()
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.page.table.item(0, 0).text(), "工具")

    def test_edit_validation_and_service_error_keep_modal_drafts(self):
        self.set_draft()
        self.page.table.selectRow(2)

        def failed_dialog(dialog):
            dialog.name_input.clear()
            dialog.save_button.click()
            self.assertEqual(self.controller.calls, [])
            self.assert_input_issue()
            dialog.name_input.setText("尚未成功")
            dialog.target_input.setText("C:/draft")
            self.controller.fail = "edit_shortcut"
            dialog.save_button.click()
            self.assertEqual(dialog.result(), QDialog.Rejected)
            self.assertEqual(dialog.name_input.text(), "尚未成功")
            self.assertEqual(dialog.target_input.text(), "C:/draft")
            self.assert_input_issue()
            self.assert_draft()
            return dialog.result()

        with patch("pet.companion_pages._ShortcutEditDialog.exec", new=failed_dialog):
            self.page.edit_button.click()
        self.assertEqual(self.controller.calls[-1], ("edit_shortcut", "s3", "尚未成功", "folder", "C:/draft"))
        self.assertEqual(self.page.selected_id(), "s3")

    def test_failed_list_refresh_is_nonmodal_and_preserves_table_and_draft(self):
        self.set_draft()
        self.page.table.selectRow(1)
        self.controller.fail = "shortcuts_list"
        self.controller.changed.emit()
        self.assertEqual(self.page.table.rowCount(), 4)
        self.assertEqual(self.page.selected_id(), "s2")
        self.assert_draft()
        self.assertIn("无法读取", self.page.status_label.text())
        self.warning.assert_not_called()

    def test_deselection_disables_actions_even_if_current_cell_remains(self):
        self.page.table.selectRow(0)
        self.page.table.clearSelection()
        self.assertIsNone(self.page.selected_id())
        self.assertFalse(self.page.open_button.isEnabled())
        self.assertFalse(self.page.delete_button.isEnabled())


class IntegrationTests(unittest.TestCase):
    def test_pages_can_be_added_to_tabs_and_inherit_parent_style(self):
        controller = FakeController()
        tabs = QTabWidget()
        tabs.setStyleSheet("QLabel { color: #34334b; }")
        dialogue = DialoguePage(controller, tabs)
        shortcuts = ShortcutsPage(controller, tabs)
        try:
            tabs.addTab(dialogue, "聊天 / 纪念日")
            tabs.addTab(shortcuts, "快捷入口")
            self.assertEqual(tabs.count(), 2)
            self.assertEqual(dialogue.styleSheet(), "")
            self.assertEqual(shortcuts.styleSheet(), "")
            self.assertEqual(controller.calls, [])
        finally:
            tabs.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
