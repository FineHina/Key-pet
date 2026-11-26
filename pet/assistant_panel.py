"""Modeless first-stage assistant controls; persistence and scheduling live in the controller."""
import math
import time

from PySide6.QtCore import QDateTime, Qt, QTime
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSlider, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QTimeEdit,
    QVBoxLayout, QWidget,
)


STYLE = """
QDialog, QWidget#notificationWindow { background: #f5f8ff; color: #34334b; }
QWidget { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; font-size: 13px; }
QLabel, QCheckBox, QGroupBox { color: #34334b; }
QGroupBox { border: 1px solid #d6deee; border-radius: 8px; margin-top: 14px; padding: 12px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton { background: #e5ecff; color: #493853; border: 1px solid #c9d4ed; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover { background: #d9e4ff; }
QPushButton:pressed { background: #cbd9f5; }
QPushButton:disabled { color: #9397a5; background: #edf0f5; border-color: #dfe3ed; }
QLineEdit, QSpinBox, QDateTimeEdit, QTimeEdit, QComboBox, QTextEdit, QTableWidget, QListWidget {
    background: white; color: #34334b; border: 1px solid #ccd6e9; border-radius: 4px; padding: 4px;
    selection-background-color: #dce5fa; selection-color: #34334b;
}
QHeaderView::section { background: #eaf0fc; color: #493853; padding: 6px; border: 0; }
QTabBar::tab { padding: 9px 20px; color: #493853; }
QTabBar::tab:selected { background: #e5ecff; }
QTabWidget::pane { border: 1px solid #d6deee; border-radius: 6px; }
QSlider::groove:horizontal { height: 6px; background: #dce4f3; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -5px 0; background: #85719a; border-radius: 8px; }
"""

STATE_LABELS = {"running": "进行中", "paused": "已暂停", "completed": "已完成", "cancelled": "已取消",
                "scheduled": "待提醒", "due": "已到时间", "pending": "待处理", "done": "已完成"}
KIND_LABELS = {"focus": "专注", "break": "休息", "reminder": "提醒"}


def _spin(value=5):
    widget = QSpinBox()
    widget.setRange(1, 1440)
    widget.setValue(value)
    widget.setSuffix(" 分钟")
    return widget


def _date_edit(due_at=None):
    widget = QDateTimeEdit()
    widget.setCalendarPopup(True)
    widget.setDisplayFormat("yyyy-MM-dd HH:mm")
    value = time.time() + 300 if due_at is None else float(due_at)
    widget.setDateTime(QDateTime.fromMSecsSinceEpoch(round(value * 1000)))
    return widget


def _date_text(epoch):
    return QDateTime.fromMSecsSinceEpoch(round(float(epoch) * 1000)).toString("MM-dd HH:mm")


def _button(text, callback):
    button = QPushButton(text)
    button.clicked.connect(callback)
    return button


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    return table


def _fill_table(table, rows):
    selected = table.item(table.currentRow(), 0)
    selected_id = selected.data(Qt.UserRole) if selected else None
    table.blockSignals(True)
    table.setRowCount(len(rows))
    restore_row = -1
    for row, (entity_id, values) in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 0:
                item.setData(Qt.UserRole, entity_id)
            table.setItem(row, column, item)
        if entity_id == selected_id:
            restore_row = row
    table.clearSelection()
    table.setCurrentCell(-1, -1)
    if restore_row >= 0:
        table.selectRow(restore_row)
    table.blockSignals(False)


class AssistantPanel(QDialog):
    """Call show()/raise_() from main; close hides and stops only preview audio."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("桌宠助手")
        self.setModal(False)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setStyleSheet(STYLE)
        self.resize(720, 720)
        self.setMinimumSize(600, 560)
        self._reminders = []
        self._pending = []
        self._voices = []
        self._all_voices = []
        layout = QVBoxLayout(self)
        heading = QLabel("一起专注，也记得休息")
        heading.setStyleSheet("font-size: 19px; color: #635077; padding: 5px;")
        layout.addWidget(heading)
        self.live_status = QLabel()
        self.live_status.setWordWrap(True)
        layout.addWidget(self.live_status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_focus()
        self._build_reminders()
        self._build_sound()
        if hasattr(controller, "chat") and hasattr(controller, "shortcuts_list"):
            from .companion_pages import DialoguePage, ShortcutsPage
            self.dialogue_page = DialoguePage(controller, self)
            self.shortcuts_page = ShortcutsPage(controller, self)
            self.tabs.addTab(self.dialogue_page, "陪伴对话")
            self.tabs.addTab(self.shortcuts_page, "快捷工具")
        layout.addWidget(_button("收起", self.close), alignment=Qt.AlignRight)
        self._load_settings()
        controller.changed.connect(self.refresh)
        self.refresh()

    def _page(self, title):
        page = QWidget()
        self.tabs.addTab(page, title)
        return QVBoxLayout(page)

    def _build_focus(self):
        layout = self._page("专注")
        self.focus_status = QLabel("尚未开始专注")
        self.focus_status.setStyleSheet("font-size: 16px; color: #635077;")
        self.countdown = QLabel("00:00")
        self.countdown.setAlignment(Qt.AlignCenter)
        self.countdown.setStyleSheet("font-size: 42px; color: #635077; padding: 15px;")
        layout.addWidget(self.focus_status)
        layout.addWidget(self.countdown)
        self.start_buttons = []
        for title, kind, presets, default in (("开始专注", "focus", (25, 45, 60), 25),
                                               ("开始休息", "break", (5, 10), 5)):
            group = QGroupBox(title)
            group_layout = QVBoxLayout(group)
            row = QHBoxLayout()
            for minutes in presets:
                button = _button(f"{minutes} 分钟", lambda checked=False, m=minutes, k=kind: self._start(m, k))
                self.start_buttons.append(button)
                row.addWidget(button)
            group_layout.addLayout(row)
            row = QHBoxLayout()
            spin = _spin(default)
            setattr(self, "focus_minutes" if kind == "focus" else "break_minutes", spin)
            row.addWidget(spin)
            button = _button("开始自定义", lambda checked=False, s=spin, k=kind: self._start(s.value(), k))
            self.start_buttons.append(button)
            row.addWidget(button)
            group_layout.addLayout(row)
            layout.addWidget(group)
        row = QHBoxLayout()
        self.pause_button = _button("暂停", lambda: self._call(self.controller.pause_focus))
        self.resume_button = _button("继续", lambda: self._call(self.controller.resume_focus))
        self.cancel_focus_button = _button("取消", lambda: self._call(self.controller.cancel_focus))
        for button in (self.pause_button, self.resume_button, self.cancel_focus_button):
            row.addWidget(button)
        layout.addLayout(row)
        note = QLabel("计时在后台继续；收起面板不会取消专注或休息。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def _build_reminders(self):
        layout = self._page("提醒")
        group = QGroupBox("新增提醒")
        form = QFormLayout(group)
        self.reminder_title = QLineEdit()
        self.reminder_title.setPlaceholderText("例如：喝水、会议、取快递")
        form.addRow("内容", self.reminder_title)
        self.reminder_mode = QComboBox()
        self.reminder_mode.addItems(["若干分钟后", "指定日期时间"])
        form.addRow("提醒方式", self.reminder_mode)
        self.reminder_minutes = _spin(10)
        self.reminder_datetime = _date_edit()
        form.addRow("多久以后", self.reminder_minutes)
        form.addRow("日期时间", self.reminder_datetime)
        self.reminder_mode.currentIndexChanged.connect(self._reminder_mode_changed)
        self._reminder_mode_changed()
        self.add_reminder_button = _button("添加提醒", self._add_reminder)
        form.addRow(self.add_reminder_button)
        layout.addWidget(group)
        self.reminders_table = _table(["内容", "时间", "状态"])
        self.reminders_table.itemSelectionChanged.connect(self._reminder_selection_changed)
        layout.addWidget(self.reminders_table)
        row = QHBoxLayout()
        self.edit_reminder_button = _button("编辑", self._edit_reminder)
        self.cancel_reminder_button = _button("取消提醒", self._cancel_reminder)
        row.addWidget(self.edit_reminder_button)
        row.addWidget(self.cancel_reminder_button)
        row.addStretch()
        layout.addLayout(row)
        group = QGroupBox("待处理通知（收起不会清除）")
        box = QVBoxLayout(group)
        self.pending_table = _table(["通知", "类型"])
        self.pending_table.setMinimumHeight(85)
        self.pending_table.setMaximumHeight(150)
        self.pending_table.itemSelectionChanged.connect(self._pending_selection_changed)
        box.addWidget(self.pending_table)
        row = QHBoxLayout()
        self.complete_button = _button("完成", self._complete_pending)
        self.snooze5_button = _button("稍后 5 分钟", lambda: self._snooze_pending(5))
        self.snooze10_button = _button("稍后 10 分钟", lambda: self._snooze_pending(10))
        for button in (self.complete_button, self.snooze5_button, self.snooze10_button):
            row.addWidget(button)
        box.addLayout(row)
        row = QHBoxLayout()
        self.snooze_minutes = _spin(15)
        self.snooze_custom_button = _button("自定义稍后", lambda: self._snooze_pending(self.snooze_minutes.value()))
        row.addWidget(self.snooze_minutes)
        row.addWidget(self.snooze_custom_button)
        box.addLayout(row)
        layout.addWidget(group)

    def _build_sound(self):
        layout = self._page("声音 / 勿扰")
        group = QGroupBox("声音与安静时间")
        form = QFormLayout(group)
        self.setting_checks = {}
        for key, label in (("voice_enabled", "启用语音"), ("mute", "静音"),
                           ("hover_voice", "悬停时播放语音（默认关闭）"),
                           ("idle_voice", "低频待机独白（默认关闭）"),
                           ("assistant_open_voice", "打开助手时语音回应"),
                           ("dnd", "手动勿扰"),
                           ("quiet_hours_enabled", "启用安静时段"), ("fullscreen_dnd", "全屏时勿扰")):
            checkbox = QCheckBox(label)
            self.setting_checks[key] = checkbox
            form.addRow(checkbox)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume_label = QLabel()
        self.volume.valueChanged.connect(lambda value: self.volume_label.setText(f"{value}%"))
        row = QHBoxLayout()
        row.addWidget(self.volume)
        row.addWidget(self.volume_label)
        form.addRow("语音音量", row)
        self.quiet_start = QTimeEdit()
        self.quiet_end = QTimeEdit()
        for edit in (self.quiet_start, self.quiet_end):
            edit.setDisplayFormat("HH:mm")
        form.addRow("安静开始", self.quiet_start)
        form.addRow("安静结束", self.quiet_end)
        self.apply_button = _button("应用设置", self._apply_settings)
        form.addRow(self.apply_button)
        layout.addWidget(group)
        group = QGroupBox("全量语音鉴赏")
        box = QVBoxLayout(group)
        filters = QHBoxLayout()
        self.voice_category = QComboBox()
        self.voice_category.addItem("全部分类", "")
        self.voice_grade = QComboBox()
        self.voice_grade.addItems(["全部等级", "A", "B", "C"])
        self.voice_search = QLineEdit()
        self.voice_search.setPlaceholderText("搜索中文、原编号或VO别名")
        filters.addWidget(self.voice_category)
        filters.addWidget(self.voice_grade)
        filters.addWidget(self.voice_search)
        box.addLayout(filters)
        self.voice_category.currentIndexChanged.connect(self._filter_voices)
        self.voice_grade.currentIndexChanged.connect(self._filter_voices)
        self.voice_search.textChanged.connect(self._filter_voices)
        self.voice_combo = QComboBox()
        self.voice_combo.currentIndexChanged.connect(self._voice_selection_changed)
        box.addWidget(self.voice_combo)
        self.voice_text = QTextEdit()
        self.voice_text.setReadOnly(True)
        self.voice_text.setMaximumHeight(130)
        box.addWidget(self.voice_text)
        row = QHBoxLayout()
        self.audition_button = _button("试听", self._audition)
        self.stop_button = _button("停止", lambda: self._call(self.controller.stop_audio))
        row.addWidget(self.audition_button)
        row.addWidget(self.stop_button)
        box.addLayout(row)
        layout.addWidget(group)
        topics = QHBoxLayout()
        for scene, label in (("greeting", "问候"), ("encouragement", "鼓励"), ("rest_advice", "休息建议")):
            button = _button(label, lambda checked=False, s=scene: self._call(self.controller.speak_topic, s))
            button.setEnabled(hasattr(self.controller, "speak_topic"))
            topics.addWidget(button)
        layout.addLayout(topics)
        note = QLabel("A级精选用于日常事件；B/C级可手动试听，剧情和无线电不进入普通随机池。\n设置点击“应用设置”后保存；黑脸和哭泣自动静音。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def _call(self, method, *args, **kwargs):
        try:
            method(*args, **kwargs)
        except Exception as error:
            QMessageBox.warning(self, "操作未完成", f"无法完成操作：{error}")
            return False
        self.refresh()
        return True

    def _start(self, minutes, kind):
        return self._call(self.controller.start_focus, minutes, kind=kind)

    def _reminder_mode_changed(self, *_):
        relative = self.reminder_mode.currentIndex() == 0
        self.reminder_minutes.setEnabled(relative)
        self.reminder_datetime.setEnabled(not relative)

    def _add_reminder(self):
        due = (time.time() + self.reminder_minutes.value() * 60 if self.reminder_mode.currentIndex() == 0
               else self.reminder_datetime.dateTime().toMSecsSinceEpoch() / 1000)
        if self._save_reminder(self.reminder_title.text(), due):
            self.reminder_title.clear()

    def _save_reminder(self, title, due_at, reminder_id=None):
        title = title.strip()
        if not title or due_at <= time.time():
            QMessageBox.warning(self, "请检查提醒", "请填写提醒内容，并选择未来的时间。")
            return False
        if reminder_id is None:
            return self._call(self.controller.add_reminder, title, due_at)
        return self._call(self.controller.edit_reminder, reminder_id, title, due_at)

    @staticmethod
    def _selected_id(table):
        item = table.item(table.currentRow(), 0)
        return item.data(Qt.UserRole) if item else None

    def _reminder_selection_changed(self):
        selected = self._selected_id(self.reminders_table)
        reminder = next((item for item in self._reminders if item["id"] == selected), None)
        editable = reminder is not None and reminder.get("status") == "scheduled"
        self.edit_reminder_button.setEnabled(editable)
        self.cancel_reminder_button.setEnabled(reminder is not None and reminder.get("status") in ("scheduled", "pending"))

    def _cancel_reminder(self):
        reminder_id = self._selected_id(self.reminders_table)
        if reminder_id is not None:
            self._call(self.controller.cancel_reminder, reminder_id)

    def _edit_reminder(self):
        selected = self._selected_id(self.reminders_table)
        reminder = next((item for item in self._reminders if item["id"] == selected), None)
        if reminder is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑提醒")
        form = QFormLayout(dialog)
        title = QLineEdit(str(reminder["title"]))
        title.setObjectName("editReminderTitle")
        due = _date_edit(reminder["due_at"])
        due.setObjectName("editReminderDue")
        form.addRow("内容", title)
        form.addRow("日期时间", due)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(lambda: dialog.accept() if self._save_reminder(
            title.text(), due.dateTime().toMSecsSinceEpoch() / 1000, selected) else None)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        dialog.exec()

    def _pending_selection_changed(self):
        selected = self._selected_id(self.pending_table)
        item = next((item for item in self._pending if item["id"] == selected), None)
        self.complete_button.setEnabled(item is not None)
        for button in (self.snooze5_button, self.snooze10_button, self.snooze_custom_button):
            button.setEnabled(item is not None and item.get("kind") == "reminder")

    def _complete_pending(self):
        notification_id = self._selected_id(self.pending_table)
        if notification_id is not None:
            self._call(self.controller.complete_notification, notification_id)

    def _snooze_pending(self, minutes):
        notification_id = self._selected_id(self.pending_table)
        if notification_id is not None:
            self._call(self.controller.snooze_notification, notification_id, minutes)

    def _load_settings(self):
        settings = self.controller.settings
        defaults = {"voice_enabled": True, "fullscreen_dnd": True}
        for key, checkbox in self.setting_checks.items():
            checkbox.setChecked(bool(settings.get(key, defaults.get(key, False))))
        self.volume.setValue(int(settings.get("voice_volume", 70)))
        self.volume_label.setText(f"{self.volume.value()}%")
        for key, default in (("quiet_start", "22:00"), ("quiet_end", "08:00")):
            value = QTime.fromString(str(settings.get(key, default)), "HH:mm")
            getattr(self, key).setTime(value if value.isValid() else QTime.fromString(default, "HH:mm"))

    def _apply_settings(self):
        settings = {key: checkbox.isChecked() for key, checkbox in self.setting_checks.items()}
        settings.update(voice_volume=self.volume.value(), quiet_start=self.quiet_start.time().toString("HH:mm"),
                        quiet_end=self.quiet_end.time().toString("HH:mm"))
        if self._call(self.controller.update_settings, settings):
            self._load_settings()

    def _voice_selection_changed(self, *_):
        voice_id = self.voice_combo.currentData()
        voice = next((item for item in self._voices if item["id"] == voice_id), None)
        available = voice is not None and bool(voice.get("available"))
        self.audition_button.setEnabled(available)
        self.stop_button.setEnabled(available)
        if voice is None:
            self.voice_text.setPlainText("没有可用的语音条目。")
            return
        self.voice_text.setPlainText(
            f"中文：\n{voice.get('text', '')}\n\n"
            f"分类：{voice.get('voice_category', '未分类')} · 等级：{voice.get('grade', 'C')}\n"
            f"来源：{voice.get('source') or voice.get('file') or voice['id']}\n时长：{voice.get('duration', '未知')} 秒"
            + ("\n音频文件缺失，无法试听。" if not available else ""))

    def _filter_voices(self, *_):
        if not hasattr(self, "voice_combo"):
            return
        category = self.voice_category.currentData()
        grade = self.voice_grade.currentText()
        search = self.voice_search.text().strip().casefold()
        selected = self.voice_combo.currentData()
        self._voices = [entry for entry in self._all_voices
                        if (not category or entry.get("voice_category", "未分类") == category)
                        and (grade == "全部等级" or entry.get("grade", "C") == grade)
                        and (not search or search in " ".join(str(entry.get(key, "")) for key in
                             ("id", "alias", "text", "summary", "voice_category")).casefold())]
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for entry in self._voices:
            self.voice_combo.addItem(str(entry.get("label", entry["id"])), entry["id"])
        index = self.voice_combo.findData(selected)
        if index >= 0:
            self.voice_combo.setCurrentIndex(index)
        self.voice_combo.blockSignals(False)
        self._voice_selection_changed()

    def _audition(self):
        if self.audition_button.isEnabled():
            self._call(self.controller.audition, self.voice_combo.currentData())

    def refresh(self):
        """Refresh live data only: never reset unsaved settings or reminder form input."""
        status = ""
        if hasattr(self.controller, "policy"):
            reasons = self.controller.policy.reasons()
            status = "勿扰中：" + "、".join(reasons) if reasons else "通知正常；静音仅影响声音。"
        if hasattr(self.controller, "audio") and self.controller.audio.error:
            status += "\n" + self.controller.audio.error
        self.live_status.setText(status)
        snapshot = self.controller.focus_snapshot()
        state = snapshot.get("state") if snapshot else None
        active = state in ("running", "paused")
        if snapshot:
            seconds = max(0, math.ceil(float(snapshot.get("remaining", 0))))
            self.countdown.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")
            self.focus_status.setText(f"{KIND_LABELS.get(snapshot.get('kind'), '专注')} · {STATE_LABELS.get(state, state or '')}")
        else:
            self.countdown.setText("00:00")
            self.focus_status.setText("尚未开始专注")
        for button in self.start_buttons:
            button.setEnabled(not active)
        self.pause_button.setEnabled(state == "running")
        self.resume_button.setEnabled(state == "paused")
        self.cancel_focus_button.setEnabled(active)
        self._reminders = list(self.controller.reminders_list())
        self._pending = list(self.controller.pending_notifications())
        _fill_table(self.reminders_table, [(item["id"], (item["title"], _date_text(item["due_at"]),
                     STATE_LABELS.get(item.get("status"), item.get("status", "")))) for item in self._reminders])
        _fill_table(self.pending_table, [(item["id"], (item["title"], KIND_LABELS.get(item.get("kind"), "提醒")))
                                        for item in self._pending])
        self._reminder_selection_changed()
        self._pending_selection_changed()
        voices = list(self.controller.voice_entries())
        if voices != self._all_voices:
            self._all_voices = voices
            selected_category = self.voice_category.currentData()
            self.voice_category.blockSignals(True)
            self.voice_category.clear()
            self.voice_category.addItem("全部分类", "")
            for category in sorted({entry.get("voice_category", "未分类") for entry in voices}):
                self.voice_category.addItem(category, category)
            index = self.voice_category.findData(selected_category)
            if index >= 0:
                self.voice_category.setCurrentIndex(index)
            self.voice_category.blockSignals(False)
            self._filter_voices()
        self._voice_selection_changed()

    def closeEvent(self, event):
        # Do not stop background focus/reminders or delete this reusable panel.
        try:
            self.controller.stop_audio()
        except Exception:
            pass
        self.hide()
        event.ignore()

    def reject(self):
        # Escape must have the same hide-only behavior as the title-bar close button.
        self.close()
