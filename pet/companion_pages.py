"""Standalone companion tabs; controller owns reactions, settings and shortcut I/O.

Integration: tabs.addTab(DialoguePage(controller), "聊天 / 纪念日") and
 tabs.addTab(ShortcutsPage(controller), "快捷入口"). No panel import is required.
"""
from collections import deque
from datetime import date, datetime
import re

from PySide6.QtCore import QEvent, QFileInfo, QRectF, Signal, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFileIconProvider, QFormLayout, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from .character_art import CharacterArt
from .ui_theme import CONTROL_GAP, PAGE_MARGIN, card, colors, icon, label, scroll_body


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


class _DialogueHistory(QTextEdit):
    """Draw rounded message backgrounds while retaining selectable transcript text."""

    replay_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.voices = {}
        self.timestamps = {}
        self.replay_targets = []
        self.document().setDocumentMargin(27)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            for rect, voice in self.replay_targets:
                if rect.contains(event.position()):
                    self.replay_requested.emit(voice)
                    return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        self.replay_targets.clear()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        t = colors(self.window())
        block = self.document().begin()
        while block.isValid():
            lines = block.layout()
            count = lines.lineCount()
            if block.text() and count:
                first = self.cursorRect(QTextCursor(block))
                last_line = lines.lineAt(count - 1)
                top = first.top() - 4
                bottom = first.top() + last_line.y() + last_line.height() + 4
                if bottom >= 0 and top < self.viewport().height():
                    extent = max(lines.lineAt(index).naturalTextWidth() for index in range(count))
                    fmt = block.blockFormat()
                    right_aligned = bool(fmt.alignment() & Qt.AlignRight)
                    margin = self.document().documentMargin()
                    left = (self.viewport().width() - margin - fmt.rightMargin() - extent
                            if right_aligned else margin + fmt.leftMargin())
                    painter.setBrush(QColor(t["bubble" if right_aligned else "panel"]))
                    painter.drawRoundedRect(QRectF(left - 7, top, extent + 14, bottom - top), 8, 8)
                    stamp = self.timestamps.get(block.blockNumber())
                    if stamp:
                        painter.setPen(QColor(t["muted"]))
                        painter.drawText(QRectF(0, top - 21, self.viewport().width(), 16), Qt.AlignCenter, stamp)
                        painter.setPen(Qt.NoPen)
                    voice = self.voices.get(block.blockNumber())
                    if voice:
                        replay = QRectF(min(left + extent + 13, self.viewport().width() - 28), top, 24, 23)
                        painter.setBrush(QColor(t["panel"]))
                        painter.drawRoundedRect(replay, 7, 7)
                        painter.setPen(QColor(t["accent"]))
                        painter.drawText(replay, Qt.AlignCenter, "▶")
                        painter.setPen(Qt.NoPen)
                        self.replay_targets.append((replay, voice))
            block = block.next()
        painter.end()
        super().paintEvent(event)


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
        layout.setContentsMargins(PAGE_MARGIN, 16, PAGE_MARGIN, 16)
        layout.setSpacing(CONTROL_GAP)
        identity, identity_box = card(margin=9)
        identity_row = QHBoxLayout()
        identity_row.addWidget(CharacterArt("avatar", 42))
        name = QVBoxLayout()
        name.setSpacing(0)
        name.addWidget(label("日常对话 · KEI", "heading"))
        name.addWidget(label("本地话题 · 非 AI", "muted"))
        identity_row.addLayout(name, 1)
        if hasattr(controller, "open_interaction"):
            identity_row.addWidget(_button("互动", "interaction", lambda: controller.open_interaction()))
        identity_row.addWidget(_button("纪念日", "calendar", self._open_calendar))
        more = QPushButton("更多")
        self.replay_menu = QMenu("语音重播", more)
        self.replay_menu.setEnabled(False)
        menu = QMenu(more)
        if hasattr(controller, "audition"):
            menu.addMenu(self.replay_menu)
        self.clear_button = _button("清空记录", "clear_button", self._confirm_clear)
        menu.addAction("清空记录…", self.clear_button.click)
        more.setMenu(menu)
        identity_row.addWidget(more)
        identity_box.addLayout(identity_row)
        layout.addWidget(identity)
        self.transcript = QStackedWidget()
        self.history = _DialogueHistory()
        self.history.setObjectName("history")
        self.history.setAccessibleName("陪伴对话记录")
        self.history.setReadOnly(True)
        self.history.setAcceptRichText(False)
        self.history.document().setMaximumBlockCount(60)
        self.history.setMinimumHeight(80)
        if hasattr(controller, "audition"):
            self.history.replay_requested.connect(self._replay)
        self.transcript.addWidget(self.history)
        empty, empty_box = card(margin=5)
        empty_box.addStretch()
        empty_box.addWidget(CharacterArt("chat", 150, 137), alignment=Qt.AlignCenter)
        empty_hint = label("选择一个话题，或输入想聊的内容。", "muted")
        empty_hint.setWordWrap(False)
        empty_hint.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(empty_hint)
        empty_box.addStretch()
        self.transcript.addWidget(empty)
        self.transcript.setCurrentWidget(empty)
        layout.addWidget(self.transcript, 1)
        topics = QGridLayout()
        topics.setSpacing(5)
        self.topic_buttons = {}
        for index, (topic, title) in enumerate(TOPICS):
            button = _button(title, "topic_" + topic,
                             lambda checked=False, t=topic: self._topic(t))
            button.setProperty("topic_id", topic)
            self.topic_buttons[topic] = button
            topics.addWidget(button, index // 4, index % 4)
        layout.addLayout(topics)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setObjectName("input")
        self.input.setMaxLength(500)
        self.input.setPlaceholderText("说点什么吧（最多 500 字，回车发送）")
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        self.send_button = _button("发送", "send_button", self._send)
        self.send_button.setProperty("role", "primary")
        row.addWidget(self.send_button)
        layout.addLayout(row)
        footer = QHBoxLayout()
        self.status_label = _label()
        self.status_label.setObjectName("status_label")
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        footer.addWidget(self.status_label, 1)
        self.count_label = label("0 / 500", "muted")
        self.input.textChanged.connect(lambda text: self.count_label.setText(f"{len(text)} / 500"))
        footer.addWidget(self.count_label)
        layout.addLayout(footer)
        self.calendar_dialog = QDialog(self)
        self.calendar_dialog.setWindowTitle("KEI · 生日与纪念日")
        self.calendar_dialog.setMinimumWidth(380)
        form = QFormLayout(self.calendar_dialog)
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
        close = QPushButton("关闭")
        close.clicked.connect(self.calendar_dialog.close)
        form.addRow(close)
        self._load_settings()
        controller.changed.connect(self.refresh)
        self.refresh()

    def _open_calendar(self):
        self.calendar_dialog.setModal(True)
        self.calendar_dialog.show()

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
        voice_id = reaction.get("voice_id") if isinstance(reaction, dict) else None
        self._history.append((_line(user_text, 500), _line(text), voice_id, datetime.now()))
        self._render_history()
        self.transcript.setCurrentWidget(self.history)
        self.replay_menu.clear()
        for _, reply, voice_id, _ in reversed(self._history):
            if voice_id and hasattr(self.controller, "audition"):
                self.replay_menu.addAction(reply[:34] + ("…" if len(reply) > 34 else ""),
                                           lambda checked=False, i=voice_id: self._replay(i))
        self.replay_menu.setEnabled(bool(self.replay_menu.actions()))
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.status_label.setText(str(notice) if notice else "")
        return True

    def _render_history(self):
        bar = self.history.verticalScrollBar()
        position = bar.value()
        previous = self.history.textCursor()
        anchor, caret = previous.anchor(), previous.position()
        self.history.clear()
        cursor = QTextCursor(self.history.document())
        t = colors(self.window())
        first = True
        last_stamp = None
        self.history.voices.clear()
        self.history.timestamps.clear()
        for user, reply, voice, moment in self._history:
            for is_user, text in ((True, user), (False, reply)):
                timestamp = is_user and (last_stamp is None or (moment - last_stamp).total_seconds() >= 300)
                block = QTextBlockFormat()
                block.setAlignment(Qt.AlignRight if is_user else Qt.AlignLeft)
                block.setLeftMargin(56 if is_user else 11)
                block.setRightMargin(11 if is_user else 56)
                block.setLineHeight(120, QTextBlockFormat.ProportionalHeight.value)
                block.setTopMargin(29 if timestamp else 12)
                block.setBottomMargin(12)
                style = QTextCharFormat()
                style.setForeground(QColor(t["text"]))
                if first:
                    cursor.setBlockFormat(block)
                    first = False
                else:
                    cursor.insertBlock(block)
                if timestamp:
                    self.history.timestamps[cursor.blockNumber()] = moment.strftime("%H:%M")
                    last_stamp = moment
                if voice and not is_user:
                    self.history.voices[cursor.blockNumber()] = voice
                cursor.insertText(text, style)
        end = self.history.document().characterCount() - 1
        cursor.setPosition(min(anchor, end))
        cursor.setPosition(min(caret, end), QTextCursor.KeepAnchor)
        self.history.setTextCursor(cursor)
        bar.setValue(position)

    def _replay(self, voice_id):
        try:
            self.controller.audition(voice_id)
        except Exception as error:
            _warn(self, error)

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

    def _confirm_clear(self):
        if self._history and QMessageBox.question(
                self, "清空对话", "清空本次对话记录？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._clear()

    def _clear(self):
        self._history.clear()
        self._render_history()
        self.transcript.setCurrentIndex(1)
        self.replay_menu.clear()
        self.replay_menu.setEnabled(False)
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
        self.setWindowTitle("KEI · 编辑快捷入口")
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
        self.save_button.setProperty("role", "primary")
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
        self._shortcuts = None
        self._target_errors = {}
        self._target_icons = {}
        self._system_icons = QFileIconProvider()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = scroll_body(outer)
        layout.addWidget(label("快捷工作台", "title"))
        switcher = QHBoxLayout()
        self.views = QStackedWidget()
        view_buttons = QButtonGroup(self)
        for index, title in enumerate(("网格", "管理列表")):
            button = QPushButton(title)
            button.setProperty("role", "segment")
            button.setCheckable(True)
            view_buttons.addButton(button, index)
            switcher.addWidget(button)
        view_buttons.button(0).setChecked(True)
        view_buttons.idClicked.connect(self.views.setCurrentIndex)
        switcher.addStretch()
        add_entry = _button("＋ 添加入口", "open_add_dialog", self._open_add_dialog)
        add_entry.hide()
        self.views.currentChanged.connect(lambda index: add_entry.setVisible(index == 1))
        switcher.addWidget(add_entry)
        layout.addLayout(switcher)
        self.add_dialog = QDialog(self)
        self.add_dialog.setWindowTitle("KEI · 添加快捷入口")
        self.add_dialog.setModal(True)
        self.add_dialog.setMinimumWidth(410)
        dialog_layout = QVBoxLayout(self.add_dialog)
        form = _shortcut_fields(self)
        dialog_layout.addLayout(form)
        self.add_button = _button("添加", "add_button", self._add)
        self.add_button.setProperty("role", "primary")
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(_button("取消", "cancel_add", self.add_dialog.reject))
        actions.addWidget(self.add_button)
        dialog_layout.addLayout(actions)
        self.grid = QListWidget()
        self.grid.setProperty("role", "launcherGrid")
        self.grid.setAccessibleName("快捷工具启动器")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setMovement(QListView.Static)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setUniformItemSizes(True)
        self.grid.setWrapping(True)
        self.grid.setGridSize(QSize(132, 90))
        self.grid.setIconSize(QSize(29, 29))
        self.grid.setMinimumHeight(184)
        self.grid.setWordWrap(False)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.itemClicked.connect(self._activate_tile)
        self.grid.setMouseTracking(True)
        self.grid.viewport().installEventFilter(self)
        self.hover_more = QPushButton("⋯", self.grid.viewport())
        self.hover_more.setAccessibleName("快捷入口更多操作")
        self.hover_more.setFixedSize(28, 25)
        self.hover_more.hide()
        self.hover_more.clicked.connect(lambda: self._grid_menu(self.hover_more.pos()))
        self.grid.itemEntered.connect(self._show_tile_menu)
        self.grid.verticalScrollBar().valueChanged.connect(self.hover_more.hide)
        for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            shortcut = QShortcut(QKeySequence(key), self.grid)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.activated.connect(lambda: self._activate_tile(self.grid.currentItem()))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._grid_menu)
        self.views.addWidget(self.grid)
        manager = QWidget()
        manager_layout = QVBoxLayout(manager)
        manager_layout.setContentsMargins(0, 0, 0, 0)
        self.views.addWidget(manager)
        layout.addWidget(self.views, 1)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("table")
        self.table.setHorizontalHeaderLabels(["名称", "类型", "目标"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 130)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        manager_layout.addWidget(self.table, 1)
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
        check = _button("检查路径", "check_targets", lambda: self._check_targets(report=True))
        row.addWidget(check)
        self.delete_button.setProperty("role", "danger")
        manager_layout.addLayout(row)
        manager_layout.addWidget(label("删除入口仅移除快捷方式。", "muted"))
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
        if shortcuts == self._shortcuts:
            return
        self._shortcuts = shortcuts
        self._check_targets()
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
        self._show_target_status()
        self._selection_changed()

    def _open_add_dialog(self):
        self.add_dialog.show()
        self.name_input.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.grid.viewport() and event.type() in (QEvent.Leave, QEvent.Hide, QEvent.Resize):
            self.hover_more.hide()
        return super().eventFilter(watched, event)

    def _show_tile_menu(self, item):
        if item.data(Qt.UserRole) is None:
            self.hover_more.hide()
            return
        rect = self.grid.visualItemRect(item)
        self.hover_more.move(rect.right() - self.hover_more.width() - 3, rect.top() + 4)
        self.hover_more.show()
        self.hover_more.raise_()

    def _check_targets(self, report=False):
        errors = {}
        local_icons = {}
        check = getattr(self.controller, "check_shortcut", None)
        if check:
            for item in self._shortcuts:
                try:
                    target = check(item["id"])
                except (ValueError, OSError) as error:
                    errors[item["id"]] = str(error)
                else:
                    if item["kind"] != "web":
                        local_icons[item["id"]] = self._system_icons.icon(QFileInfo(target))
        self._target_errors = errors
        self._target_icons = local_icons
        self._populate_grid()
        if report:
            self._show_target_status()
            self.status_label.setText(f"已检查 {len(self._shortcuts)} 个入口，{len(errors)} 个失效。"
                                      if errors else f"已检查 {len(self._shortcuts)} 个入口。")

    def _show_target_status(self):
        for row, shortcut in enumerate(self._shortcuts):
            item = self.table.item(row, 2)
            error = self._target_errors.get(shortcut["id"])
            item.setText(("失效 · " if error else "") + shortcut["target"])
            item.setToolTip(error or shortcut["target"])

    def _populate_grid(self):
        selected = self.grid.currentItem()
        identifier = selected.data(Qt.UserRole) if selected else None
        self.hover_more.hide()
        self.grid.clear()
        for shortcut in self._shortcuts:
            error = self._target_errors.get(shortcut["id"])
            tile = QListWidgetItem(("失效 · " if error else "") + shortcut["name"], self.grid)
            tile.setSizeHint(QSize(124, 82))
            tile.setData(Qt.UserRole, shortcut["id"])
            tile.setData(Qt.UserRole + 1, shortcut["kind"])
            tile.setToolTip(f"{shortcut['name']}\n{error or shortcut['target']}")
            if identifier == shortcut["id"]:
                self.grid.setCurrentItem(tile)
        tile = QListWidgetItem("添加入口", self.grid)
        tile.setSizeHint(QSize(124, 82))
        tile.setData(Qt.UserRole + 1, "add")
        self.update_icons()

    def update_icons(self):
        t = colors(self.window())
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            identifier = item.data(Qt.UserRole)
            system_icon = self._target_icons.get(identifier)
            if system_icon and not system_icon.isNull():
                item.setIcon(system_icon)
            else:
                tint = t["rose"] if identifier in self._target_errors else t["accent"]
                item.setIcon(icon(item.data(Qt.UserRole + 1), tint, 29))

    def _activate_tile(self, item):
        if item is None:
            return
        identifier = item.data(Qt.UserRole)
        if identifier is None:
            self._open_add_dialog()
            return
        self.table.selectRow(self.grid.row(item))
        if identifier in self._target_errors:
            self._edit()
        else:
            self._open()

    def _grid_menu(self, point):
        item = self.grid.itemAt(point)
        if item is None or item.data(Qt.UserRole) is None:
            return
        self.grid.setCurrentItem(item)
        self.table.selectRow(self.grid.row(item))
        menu = QMenu(self.grid)
        for button in (self.open_button, self.edit_button, self.up_button, self.down_button, self.delete_button):
            action = menu.addAction(button.text(), button.click)
            if button is self.edit_button and item.data(Qt.UserRole) in self._target_errors:
                action.setText("修复入口…")
            action.setEnabled(button.isEnabled())
        menu.exec(self.grid.viewport().mapToGlobal(point))
        menu.deleteLater()

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
            self.add_dialog.accept()

    def _open(self):
        shortcut = self._selected()
        if not shortcut:
            return
        success, result = self._call(self.controller.open_shortcut, shortcut["id"])
        if success:
            self.status_label.setText(str(result.get("message", "已打开快捷入口。"))
                                      if isinstance(result, dict) else "已打开快捷入口。")
        elif hasattr(self.controller, "check_shortcut"):
            self._check_targets()
            self._show_target_status()

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
