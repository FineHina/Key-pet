"""One persistent, mouse-interactive notification tool; main owns presentation/DND."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .assistant_panel import KIND_LABELS, STYLE, _spin


class NotificationWindow(QWidget):
    open_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.controller = controller
        self.setObjectName("notificationWindow")
        self.setWindowTitle("桌宠提醒")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        # Do not use WindowDoesNotAcceptFocus / TransparentForInput: controls need mouse input.
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(390)
        self.resize(420, 440)
        self._items = []
        layout = QVBoxLayout(self)
        self.queue_label = QLabel("待处理通知")
        self.queue_label.setStyleSheet("color: #635077; font-size: 14px;")
        layout.addWidget(self.queue_label)
        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.PlainText)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 19px; color: #635077; padding: 6px 0;")
        layout.addWidget(self.title_label)
        self.body_label = QLabel()
        self.body_label.setTextFormat(Qt.PlainText)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.body_label)
        self.pending_list = QListWidget()
        self.pending_list.setMaximumHeight(140)
        self.pending_list.currentRowChanged.connect(self._selection_changed)
        layout.addWidget(self.pending_list)
        row = QHBoxLayout()
        self.complete_button = QPushButton("完成")
        self.complete_button.clicked.connect(self._complete)
        self.snooze5_button = QPushButton("稍后 5 分钟")
        self.snooze5_button.clicked.connect(lambda: self._snooze(5))
        self.snooze10_button = QPushButton("稍后 10 分钟")
        self.snooze10_button.clicked.connect(lambda: self._snooze(10))
        for button in (self.complete_button, self.snooze5_button, self.snooze10_button):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.snooze_minutes = _spin(15)
        self.snooze_custom_button = QPushButton("自定义稍后")
        self.snooze_custom_button.clicked.connect(lambda: self._snooze(self.snooze_minutes.value()))
        row.addWidget(self.snooze_minutes)
        row.addWidget(self.snooze_custom_button)
        layout.addLayout(row)
        self.break_minutes = _spin(5)
        self.break_button = QPushButton("开始休息")
        self.break_button.clicked.connect(self._start_break)
        self.break_row = QHBoxLayout()
        self.break_row.addWidget(self.break_minutes)
        self.break_row.addWidget(self.break_button)
        layout.addLayout(self.break_row)
        row = QHBoxLayout()
        self.open_button = QPushButton("打开助手")
        self.open_button.clicked.connect(lambda: self.open_requested.emit())
        self.hide_button = QPushButton("收起")
        self.hide_button.clicked.connect(self.close)
        row.addWidget(self.open_button)
        row.addWidget(self.hide_button)
        layout.addLayout(row)
        note = QLabel("通知会保留，直到完成或稍后提醒。")
        note.setWordWrap(True)
        layout.addWidget(note)
        controller.changed.connect(self._refresh)
        self._selection_changed()

    def sync(self, items):
        """Replace the entire queue, preserve selection, hide if empty; never show/raise.

        Main calls show() only after its DND checks and handles presentation bookkeeping.
        """
        selected = self.current_item()
        selected_id = selected.get("id") if selected else None
        self._items = [dict(item) for item in items]
        self.pending_list.blockSignals(True)
        self.pending_list.clear()
        selected_row = 0
        for row, item in enumerate(self._items):
            entry = QListWidgetItem(f"{KIND_LABELS.get(item.get('kind'), '提醒')} · {item.get('title', '')}")
            entry.setData(Qt.UserRole, item["id"])
            self.pending_list.addItem(entry)
            if item["id"] == selected_id:
                selected_row = row
        if self._items:
            self.pending_list.setCurrentRow(selected_row)
        self.pending_list.blockSignals(False)
        self.queue_label.setText(f"待处理通知 · {len(self._items)} 条")
        self._selection_changed()
        if not self._items:
            self.hide()

    def _refresh(self):
        self.sync(self.controller.pending_notifications())

    def current_item(self):
        row = self.pending_list.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _selection_changed(self, *_):
        item = self.current_item()
        enabled = item is not None
        self.title_label.setText(str(item.get("title", "")) if item else "没有待处理通知")
        self.body_label.setText(str(item.get("body", "")) if item else "")
        self.complete_button.setEnabled(enabled)
        for button in (self.snooze5_button, self.snooze10_button, self.snooze_custom_button):
            button.setEnabled(enabled and item.get("kind") == "reminder")
        focus = item is not None and item.get("kind") == "focus"
        self.break_button.setVisible(focus)
        self.break_minutes.setVisible(focus)
        self.break_button.setEnabled(focus)

    def _call(self, method, *args, **kwargs):
        try:
            method(*args, **kwargs)
        except Exception as error:
            QMessageBox.warning(self, "操作未完成", f"无法完成操作：{error}")
            return False
        self._refresh()
        return True

    def _complete(self):
        item = self.current_item()
        if item:
            self._call(self.controller.complete_notification, item["id"])

    def _snooze(self, minutes):
        item = self.current_item()
        if item:
            self._call(self.controller.snooze_notification, item["id"], minutes)

    def _start_break(self):
        item = self.current_item()
        if item and item.get("kind") == "focus":
            # Resolve the focus alert only if starting the break succeeded.
            if self._call(self.controller.start_focus, self.break_minutes.value(), kind="break"):
                self._call(self.controller.complete_notification, item["id"])

    def closeEvent(self, event):
        self.hide()
        event.ignore()
