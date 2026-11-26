"""Standalone companion tabs; controller owns reactions, settings and shortcut I/O.

Integration: tabs.addTab(DialoguePage(controller), "聊天 / 纪念日") and
 tabs.addTab(ShortcutsPage(controller), "快捷入口"). No panel import is required.
"""
from collections import deque
from datetime import date
import re

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)


TOPICS = (
    ("greeting", "问候"), ("encouragement", "鼓励"),
    ("rest_advice", "休息建议"), ("coffee", "咖啡"),
    ("stars", "星空"), ("tease", "逗趣"),
    ("intro", "自我介绍"), ("support", "陪伴"),
)
SHORTCUT_KINDS = (("app", "应用"), ("file", "文件"),
                  ("folder", "文件夹"), ("web", "网页"))


def _label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


def _button(text, name, callback):
    button = QPushButton(text)
    button.setObjectName(name)
    button.clicked.connect(callback)
    return button


def _line(text, limit=4000):
    # One user line and one bot line per entry, including multiline responses.
    return " ".join(str(text).splitlines())[:limit]


def _warn(parent, error, input_issue=False):
    prefix = "输入问题：" if input_issue or isinstance(error, ValueError) else "操作未完成："
    message = prefix + str(error)
    parent.status_label.setText(message)
    QMessageBox.warning(parent, "请检查输入" if prefix == "输入问题：" else "操作未完成", message)


def _shortcut_fields(owner):
    """Shared add/edit fields; browsing never opens or modifies a target."""
    form = QFormLayout()
    owner.name_input = QLineEdit()
    owner.name_input.setObjectName("name_input")
    owner.name_input.setMaxLength(80)
    owner.name_input.setPlaceholderText("入口名称（最多 80 字）")
    form.addRow("名称", owner.name_input)
    owner.kind_combo = QComboBox()
    owner.kind_combo.setObjectName("kind_combo")
    for kind, title in SHORTCUT_KINDS:
        owner.kind_combo.addItem(title, kind)
    form.addRow("类型", owner.kind_combo)
    owner.target_input = QLineEdit()
    owner.target_input.setObjectName("target_input")
    owner.target_input.setPlaceholderText("应用 / 文件 / 文件夹路径，或网页地址")
    row = QHBoxLayout()
    row.addWidget(owner.target_input)
    owner.browse_button = _button("浏览…", "browse_button", lambda: _browse(owner))
    row.addWidget(owner.browse_button)
    form.addRow("目标", row)
    owner.kind_combo.currentIndexChanged.connect(
        lambda: owner.browse_button.setEnabled(owner.kind_combo.currentData() != "web"))
    return form


def _browse(owner):
    kind = owner.kind_combo.currentData()
    if kind == "web":
        return
    try:
        if kind == "folder":
            target = QFileDialog.getExistingDirectory(owner, "选择文件夹", owner.target_input.text())
        else:
            file_filter = "应用 (*.exe);;所有文件 (*)" if kind == "app" else "所有文件 (*)"
            target, _ = QFileDialog.getOpenFileName(
                owner, "选择应用" if kind == "app" else "选择文件",
                owner.target_input.text(), file_filter)
        if target:
            owner.target_input.setText(target)
    except Exception as error:
        _warn(owner, error)


def _shortcut_values(owner):
    name = owner.name_input.text().strip()
    kind = owner.kind_combo.currentData()
    target = owner.target_input.text().strip()
    if not name:
        raise ValueError("请填写快捷入口名称。")
    if not target:
        raise ValueError("请填写目标路径或网页地址。")
    return name, kind, target


class DialoguePage(QWidget):
    """Local, non-AI dialogue with at most 30 exchanges / 60 plain-text lines.

    Public widgets: topic_buttons (topic ID -> QPushButton), history, input,
    send_button, clear_button, birthday_input, auto_checkbox, save_button,
    event_label, event_button, status_label. Refresh never sends a message.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._history = deque(maxlen=30)
        self._event = None
        self._settings_snapshot = {}
        layout = QVBoxLayout(self)
        layout.addWidget(_label("本地话题陪伴（非 AI），不联网推理；聊天记录仅保留在本页内存中。"))
        topics = QGridLayout()
        self.topic_buttons = {}
        for index, (topic, title) in enumerate(TOPICS):
            button = _button(title, "topic_" + topic,
                             lambda checked=False, t=topic: self._topic(t))
            button.setProperty("topic_id", topic)
            self.topic_buttons[topic] = button
            topics.addWidget(button, index // 4, index % 4)
        layout.addLayout(topics)
        self.history = QTextEdit()
        self.history.setObjectName("history")
        self.history.setReadOnly(True)
        self.history.setAcceptRichText(False)
        self.history.document().setMaximumBlockCount(60)
        layout.addWidget(self.history, 1)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setObjectName("input")
        self.input.setMaxLength(500)
        self.input.setPlaceholderText("说点什么吧（最多 500 字，回车发送）")
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        self.send_button = _button("发送", "send_button", self._send)
        self.clear_button = _button("清空记录", "clear_button", self._clear)
        row.addWidget(self.send_button)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        group = QGroupBox("生日与今日纪念")
        form = QFormLayout(group)
        self.birthday_input = QLineEdit()
        self.birthday_input.setObjectName("birthday_input")
        self.birthday_input.setMaxLength(5)
        self.birthday_input.setPlaceholderText("MM-DD（可留空，例如 02-29）")
        form.addRow("你的生日", self.birthday_input)
        self.auto_checkbox = QCheckBox("自动纪念日回应")
        self.auto_checkbox.setObjectName("auto_checkbox")
        form.addRow(self.auto_checkbox)
        self.save_button = _button("保存纪念日设置", "save_button", self._save_settings)
        form.addRow(self.save_button)
        self.event_label = _label()
        self.event_label.setObjectName("event_label")
        form.addRow("今日", self.event_label)
        self.event_button = _button("今日纪念回应", "event_button", self._trigger_event)
        form.addRow(self.event_button)
        layout.addWidget(group)
        self.status_label = _label()
        self.status_label.setObjectName("status_label")
        layout.addWidget(self.status_label)
        self._load_settings()
        controller.changed.connect(self.refresh)
        self.refresh()

    def _load_settings(self):
        settings = self.controller.settings
        self.birthday_input.setText(settings.get("birthday_mmdd", ""))
        self.auto_checkbox.setChecked(settings.get("calendar_events_enabled", True))
        self._settings_snapshot = {"birthday_mmdd": self.birthday_input.text(),
                                   "calendar_events_enabled": self.auto_checkbox.isChecked()}

    def refresh(self, *_):
        # Sync only clean fields. Unsent chat and settings drafts survive timer ticks.
        settings = self.controller.settings
        if self.birthday_input.text() == self._settings_snapshot["birthday_mmdd"]:
            value = settings.get("birthday_mmdd", "")
            self.birthday_input.setText(value)
            self._settings_snapshot["birthday_mmdd"] = value
        if self.auto_checkbox.isChecked() == self._settings_snapshot["calendar_events_enabled"]:
            value = settings.get("calendar_events_enabled", True)
            self.auto_checkbox.setChecked(value)
            self._settings_snapshot["calendar_events_enabled"] = value
        try:
            self._event = self.controller.today_event()
            if self._event:
                self.event_label.setText(f"{self._event['name']} · {self._event.get('date', '')}")
            else:
                self.event_label.setText("今天暂无特别纪念日。")
            self.event_button.setEnabled(bool(self._event))
        except Exception as error:
            self._event = None
            self.event_button.setEnabled(False)
            self.event_label.setText(f"无法读取今日纪念：{error}")

    def _respond(self, user_text, method, *args):
        try:
            reaction = method(*args)
            # A text-only controller is fine; voice/image routing stays in controller.
            if isinstance(reaction, dict):
                text = reaction.get("text", "")
                notice = reaction.get("wait_notice", reaction.get("waitnotice", ""))
            else:
                text, notice = reaction, ""
            if not isinstance(text, str) or not text.strip():
                raise ValueError("暂时没有可显示的回应，请稍后再试。")
        except Exception as error:
            _warn(self, error)
            return False
        self._history.append((_line(user_text, 500), _line(text)))
        self.history.setPlainText("\n".join(
            f"你：{user}\n凯伊：{bot}" for user, bot in self._history))
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.status_label.setText(str(notice) if notice else "本地回应；语音是否播放由助手控制。")
        return True

    def _send(self):
        text = self.input.text().strip()
        if not text:
            _warn(self, "请先输入聊天内容。", input_issue=True)
            return
        if self._respond(text, self.controller.chat, text):
            self.input.clear()

    def _topic(self, topic):
        title = dict(TOPICS)[topic]
        self._respond("话题：" + title, self.controller.dialogue_topic, topic)

    def _clear(self):
        self._history.clear()
        self.history.clear()
        self.status_label.clear()

    def _save_settings(self):
        birthday = self.birthday_input.text().strip()
        try:
            if birthday:
                if not re.fullmatch(r"[0-9]{2}-[0-9]{2}", birthday):
                    raise ValueError("生日请使用 MM-DD 格式，也可以留空。")
                try:
                    date(2000, int(birthday[:2]), int(birthday[3:]))
                except ValueError:
                    raise ValueError("请输入有效的生日日期，例如 02-29 或 12-31。") from None
            self.controller.update_settings({
                "birthday_mmdd": birthday,
                "calendar_events_enabled": self.auto_checkbox.isChecked(),
            })
        except Exception as error:
            _warn(self, error)
            return
        self._load_settings()
        self.refresh()
        self.status_label.setText("纪念日设置已保存。")

    def _trigger_event(self):
        if self._event:
            self._respond("今日纪念：" + str(self._event["name"]),
                          self.controller.trigger_today_event)


class _ShortcutEditDialog(QDialog):
    """Explicit edit only; controller refreshes do not touch these draft widgets."""

    def __init__(self, shortcut, on_save, parent=None):
        super().__init__(parent)
        self.setObjectName("shortcut_edit_dialog")
        self.setWindowTitle("编辑快捷入口")
        self.setModal(True)
        self.resize(520, 230)
        self._on_save = on_save
        layout = QVBoxLayout(self)
        layout.addLayout(_shortcut_fields(self))
        self.name_input.setText(shortcut["name"])
        self.kind_combo.setCurrentIndex(self.kind_combo.findData(shortcut["kind"]))
        self.target_input.setText(shortcut["target"])
        self.status_label = _label()
        layout.addWidget(self.status_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.Ok)
        self.save_button.setText("保存")
        self.save_button.setObjectName("save_button")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _save(self):
        try:
            values = _shortcut_values(self)
            self._on_save(*values)
        except Exception as error:
            _warn(self, error)
            return
        self.accept()


class ShortcutsPage(QWidget):
    """Plain-text shortcut list, separate add draft and explicit modal editing.

    Public widgets: name_input, kind_combo (data: app/file/folder/web),
    target_input, browse_button, table, open_button, add_button, edit_button,
    delete_button, up_button, down_button, status_label. selected_id() returns
    the current controller ID, also stored in column 0's Qt.UserRole.
    move_shortcut direction is -1 (up) or +1 (down). Only Open calls open_shortcut.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._shortcuts = []
        layout = QVBoxLayout(self)
        layout.addWidget(_label("快捷入口只保存名称和目标。删除入口不会删除目标文件；网页不会自动打开。"))
        group = QGroupBox("新增快捷入口")
        form = _shortcut_fields(self)
        group.setLayout(form)
        self.add_button = _button("添加", "add_button", self._add)
        form.addRow(self.add_button)
        layout.addWidget(group)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("table")
        self.table.setHorizontalHeaderLabels(["名称", "类型", "目标"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        for name, title, callback in (
            ("open_button", "打开", self._open), ("edit_button", "编辑", self._edit),
            ("delete_button", "删除入口", self._delete),
            ("up_button", "上移", lambda: self._move(-1)),
            ("down_button", "下移", lambda: self._move(1)),
        ):
            button = _button(title, name, callback)
            setattr(self, name, button)
            row.addWidget(button)
        layout.addLayout(row)
        self.status_label = _label()
        self.status_label.setObjectName("status_label")
        layout.addWidget(self.status_label)
        controller.changed.connect(self.refresh)
        signal = getattr(controller, "shortcuts_changed", None)
        if signal is not None:
            signal.connect(self.refresh)
        self.refresh()

    def selected_id(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.UserRole) if item and item.isSelected() else None

    def _selected(self):
        identifier = self.selected_id()
        return next((item for item in self._shortcuts if item["id"] == identifier), None)

    def refresh(self, *_):
        identifier = self.selected_id()
        try:
            shortcuts = sorted(self.controller.shortcuts_list(), key=lambda item: item["position"])
        except Exception as error:
            # Periodic refresh must not open modal warnings or discard drafts.
            self.status_label.setText(f"无法读取快捷入口：{error}")
            return
        self._shortcuts = shortcuts
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(shortcuts))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        restore_row = -1
        titles = dict(SHORTCUT_KINDS)
        for row, shortcut in enumerate(shortcuts):
            values = (shortcut["name"], titles.get(shortcut["kind"], shortcut["kind"]), shortcut["target"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, shortcut["id"])
                self.table.setItem(row, column, item)
            if shortcut["id"] == identifier:
                restore_row = row
        if restore_row >= 0:
            self.table.selectRow(restore_row)
        del blocker
        self._selection_changed()

    def _selection_changed(self):
        selected = self._selected() is not None
        for button in (self.open_button, self.edit_button, self.delete_button):
            button.setEnabled(selected)
        row = self.table.currentRow()
        self.up_button.setEnabled(selected and row > 0)
        self.down_button.setEnabled(selected and row < len(self._shortcuts) - 1)

    def _call(self, method, *args):
        try:
            result = method(*args)
        except Exception as error:
            _warn(self, error)
            return False, None
        self.refresh()
        return True, result

    def _add(self):
        try:
            values = _shortcut_values(self)
        except Exception as error:
            _warn(self, error)
            return
        success, _ = self._call(self.controller.add_shortcut, *values)
        if success:
            self.name_input.clear()
            self.target_input.clear()
            self.status_label.setText("快捷入口已添加。")

    def _open(self):
        shortcut = self._selected()
        if not shortcut:
            return
        success, result = self._call(self.controller.open_shortcut, shortcut["id"])
        if success:
            self.status_label.setText(str(result.get("message", "已打开快捷入口。"))
                                      if isinstance(result, dict) else "已打开快捷入口。")

    def _edit(self):
        shortcut = self._selected()
        if not shortcut:
            return
        identifier = shortcut["id"]

        def save(name, kind, target):
            self.controller.edit_shortcut(identifier, name, kind, target)
            self.refresh()
            self.status_label.setText("快捷入口已修改。")

        dialog = _ShortcutEditDialog(dict(shortcut), save, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _delete(self):
        shortcut = self._selected()
        if not shortcut:
            return
        answer = QMessageBox.question(
            self, "删除快捷入口", f"仅删除入口“{shortcut['name']}”？\n不会删除目标文件或文件夹。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        success, _ = self._call(self.controller.delete_shortcut, shortcut["id"])
        if success:
            self.status_label.setText("已删除入口，目标未改动。")

    def _move(self, direction):
        shortcut = self._selected()
        if not shortcut:
            return
        row = self.table.currentRow()
        if (direction == -1 and row == 0) or (direction == 1 and row == len(self._shortcuts) - 1):
            return
        success, _ = self._call(self.controller.move_shortcut, shortcut["id"], direction)
        if success:
            self.status_label.setText("入口顺序已更新。")
