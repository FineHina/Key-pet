"""Modeless assistant views; persistence and scheduling live in the controller."""
import math
import time

from PySide6.QtCore import QDateTime, QEasingCurve, QPropertyAnimation, QSignalBlocker, Qt, QTime, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFormLayout, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView,
    QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSlider, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QTimeEdit,
    QVBoxLayout, QWidget,
)

from .character_art import CharacterArt, brand_icon
from .ui_theme import CONTROL_GAP, apply_theme, apply_titlebar, card, colors, icon, label, resolved_dark, scroll_body
from .ui_widgets import KeiHalo, PageSections, StatusLight, TimerRing, ToggleSwitch, WrappingTitle, disclosure


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


def _reminder_form(owner, reminder=None):
    form = QFormLayout()
    owner.reminder_title = QLineEdit(reminder["title"] if reminder else "")
    owner.reminder_title.setObjectName("editReminderTitle" if reminder else "reminderTitle")
    owner.reminder_title.setPlaceholderText("填写提醒内容")
    owner.reminder_title.setMaxLength(500)
    form.addRow("内容", owner.reminder_title)
    owner.reminder_mode = QComboBox()
    owner.reminder_mode.addItem("若干分钟后", "relative")
    owner.reminder_mode.addItem("指定日期时间", "absolute")
    form.addRow("提醒方式", owner.reminder_mode)
    remaining = math.ceil((reminder["due_at"] - time.time()) / 60) if reminder else 10
    owner.reminder_minutes = _spin(max(1, min(1440, remaining)))
    owner.reminder_datetime = _date_edit(reminder["due_at"] if reminder else None)
    owner.reminder_datetime.setObjectName("editReminderDue" if reminder else "reminderDue")
    form.addRow("多久以后", owner.reminder_minutes)
    form.addRow("日期时间", owner.reminder_datetime)

    def sync_mode():
        relative = owner.reminder_mode.currentData() == "relative"
        owner.reminder_minutes.setEnabled(relative)
        owner.reminder_datetime.setEnabled(not relative)

    owner.reminder_mode.currentIndexChanged.connect(sync_mode)
    owner.reminder_mode.setCurrentIndex(int(bool(reminder and reminder.get("time_mode") != "relative")))
    sync_mode()
    return form


def _form_due(owner):
    if owner.reminder_mode.currentData() == "relative":
        return time.time() + owner.reminder_minutes.value() * 60
    return owner.reminder_datetime.dateTime().toMSecsSinceEpoch() / 1000



def _button(text, callback):
    button = QPushButton(text)
    button.clicked.connect(callback)
    return button


def _segments(titles, stack):
    row = QHBoxLayout()
    buttons = QButtonGroup(stack)
    for index, title in enumerate(titles):
        button = QPushButton(title)
        button.setProperty("role", "segment")
        button.setCheckable(True)
        row.addWidget(button)
        buttons.addButton(button, index)
    buttons.idClicked.connect(stack.setCurrentIndex)
    stack.currentChanged.connect(lambda index: buttons.button(index).setChecked(True))
    buttons.button(0).setChecked(True)
    return row, buttons


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

    theme_changed = Signal(bool)

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.controller = controller
        self.setWindowTitle("KEI Assistant")
        self.setWindowIcon(brand_icon())
        self.setModal(False)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.resize(820, 640)
        self.setMinimumSize(660, 520)
        self._reminders = None
        self._pending = []
        self._voices = []
        self._all_voices = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(CONTROL_GAP)
        shell = QHBoxLayout()
        shell.setSpacing(CONTROL_GAP)
        sidebar, rail = card("sidebar", 10)
        sidebar.setFixedWidth(156)
        brand = QHBoxLayout()
        brand.setSpacing(3)
        self.halo = KeiHalo()
        brand.addWidget(self.halo)
        wordmark = label("KEI Assistant", "brand")
        wordmark.setWordWrap(False)
        brand.addWidget(wordmark, 1)
        rail.addLayout(brand)
        caption = label("凯伊辅助终端", "muted")
        caption.setContentsMargins(6, 0, 0, 8)
        rail.addWidget(caption)
        self.navigation = QButtonGroup(self)
        shell.addWidget(sidebar)
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        shell.addWidget(self.tabs, 1)
        layout.addLayout(shell, 1)
        self._build_focus()
        self._build_reminders()
        self._build_sound()
        if hasattr(controller, "chat") and hasattr(controller, "shortcuts_list"):
            from .companion_pages import DialoguePage, ShortcutsPage
            self.dialogue_page = DialoguePage(controller, self)
            self.shortcuts_page = ShortcutsPage(controller, self)
            self.tabs.addTab(self.dialogue_page, "陪伴对话")
            self.tabs.addTab(self.shortcuts_page, "快捷工具")
        names = ("专注计时", "事项提醒", "声音设置", "陪伴对话", "快捷工具")
        for index in range(self.tabs.count()):
            button = QPushButton(names[index])
            button.setProperty("role", "nav")
            button.setCheckable(True)
            rail.addWidget(button)
            self.navigation.addButton(button, index)
        self.navigation.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._page_changed)
        self._page_animation = QPropertyAnimation(self)
        self._page_animation.setPropertyName(b"opacity")
        self._page_animation.setDuration(165)
        self._page_animation.setStartValue(0.72)
        self._page_animation.setEndValue(1.0)
        self._page_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._page_animation.finished.connect(self._clear_page_animation)
        self._animated_page = None
        self._tracks_screen = False
        self.navigation.button(0).setChecked(True)
        rail.addStretch()
        self.theme_choice = QComboBox()
        self.theme_choice.setAccessibleName("界面主题")
        for title, value in (("跟随系统", "system"), ("浅色主题", "light"), ("深色主题", "dark")):
            self.theme_choice.addItem(title, value)
        rail.addWidget(self.theme_choice)
        self.theme_choice.currentIndexChanged.connect(self._save_theme)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._sync_theme)
        footer = QHBoxLayout()
        self.status_light = StatusLight()
        footer.addWidget(self.status_light)
        self.error_art = CharacterArt("error", 32)
        self.error_art.hide()
        footer.addWidget(self.error_art)
        self.live_status = label(role="muted")
        footer.addWidget(self.live_status, 1)
        footer.addWidget(_button("收起", self.close))
        layout.addLayout(footer)
        self._load_settings()
        controller.changed.connect(self.refresh)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "focus_art"):
            compact = event.size().height() < 570
            self.timer_ring.set_compact(compact)
            edge = 58 if compact else 78
            self.focus_art.setFixedSize(edge, edge)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._tracks_screen:
            self.windowHandle().screenChanged.connect(self._fit_screen)
            self._tracks_screen = True
        self._fit_screen(self.screen())
        apply_titlebar(self)
        self.halo.animate()

    def _fit_screen(self, screen):
        if screen is None or self.isMaximized() or self.isMinimized():
            return
        available = screen.availableGeometry()
        margins = self.windowHandle().frameMargins()
        width = max(1, available.width() - margins.left() - margins.right())
        height = max(1, available.height() - margins.top() - margins.bottom())
        self.setMinimumSize(min(660, width), min(520, height))
        self.resize(min(width, self.width()), min(height, self.height()))
        frame = self.frameGeometry()
        self.move(
            max(available.left(), min(frame.left(), available.right() - frame.width() + 1)),
            max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)),
        )

    def _page_changed(self, index):
        if index < 0:
            return
        self.navigation.button(index).setChecked(True)
        self._update_navigation_icons()
        self._clear_page_animation()
        if self.isVisible():
            self.halo.animate()
            page = self.tabs.widget(index)
            effect = QGraphicsOpacityEffect(page)
            effect.setOpacity(0.72)
            page.setGraphicsEffect(effect)
            self._animated_page = page
            self._page_animation.setTargetObject(effect)
            self._page_animation.start()

    def _clear_page_animation(self):
        if self._animated_page is not None:
            self._page_animation.stop()
            self._page_animation.setTargetObject(None)
            self._animated_page.setGraphicsEffect(None)
            self._animated_page = None

    def _update_navigation_icons(self):
        t = colors(self)
        for index, name in enumerate(("focus", "reminder", "sound", "chat", "tools")[:self.tabs.count()]):
            button = self.navigation.button(index)
            button.setIcon(icon(name, t["on_accent" if button.isChecked() else "accent"], 19))

    def _save_theme(self, *_):
        self._call(self.controller.update_settings, {"assistant_theme": self.theme_choice.currentData()})
        self._sync_theme()

    def _sync_theme(self, *_):
        preference = self.controller.settings.get("assistant_theme", "system")
        with QSignalBlocker(self.theme_choice):
            self.theme_choice.setCurrentIndex(max(0, self.theme_choice.findData(preference)))
        dark = resolved_dark(preference)
        if self.property("darkTheme") == dark:
            return
        apply_theme(self, dark)
        apply_titlebar(self)
        self._update_navigation_icons()
        if hasattr(self, "dialogue_page"):
            self.dialogue_page._render_history()
            self.shortcuts_page.update_icons()
        self.theme_changed.emit(dark)

    def _page(self, title):
        page = QWidget()
        self.tabs.addTab(page, title)
        layout = scroll_body(QVBoxLayout(page))
        page.layout().setContentsMargins(0, 0, 0, 0)
        return layout

    def _build_focus(self):
        layout = self._page("专注")
        layout.addWidget(label("专注空间", "title"))
        surface, box = card()
        self.focus_modes = QStackedWidget()
        modes, self.focus_mode_buttons = _segments(("专注模式", "休息模式"), self.focus_modes)
        self.focus_modes.currentChanged.connect(self._focus_mode_changed)
        box.addLayout(modes)
        self.focus_status = label(role="muted")
        self.focus_status.setAlignment(Qt.AlignCenter)
        self.countdown = label("25:00", "countdown")
        self.countdown.setAlignment(Qt.AlignCenter)
        self.countdown.setWordWrap(False)
        self.timer_ring = TimerRing(self.countdown, self.focus_status)
        box.addWidget(self.timer_ring, alignment=Qt.AlignHCenter)
        self.start_buttons = []
        self.presets = {}
        for title, kind, minutes_set, default in (("开始专注", "focus", (25, 45, 60), 25),
                                                   ("开始休息", "break", (5, 10), 5)):
            group = QWidget()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            row = QHBoxLayout()
            choices = QButtonGroup(group)
            for minutes in (*minutes_set, 0):
                button = QPushButton(f"{minutes} 分钟" if minutes else "自定义")
                button.setProperty("role", "segment")
                button.setCheckable(True)
                choices.addButton(button, minutes)
                row.addWidget(button)
            self.presets[kind] = choices
            group_layout.addLayout(row)
            row = QHBoxLayout()
            saved = round(self.controller.settings.get(f"{kind}_minutes", default))
            spin = _spin(saved)
            spin.setAccessibleName(f"{title}的自定义时长")
            setattr(self, "focus_minutes" if kind == "focus" else "break_minutes", spin)
            row.addWidget(spin)
            button = _button(title, lambda checked=False, s=spin, k=kind, c=choices:
                             self._start(c.checkedId() or s.value(), k))
            button.setProperty("role", "primary")
            self.start_buttons.append(button)
            row.addWidget(button, 1)
            group_layout.addLayout(row)
            initial = saved if saved in minutes_set else 0
            choices.button(initial).setChecked(True)
            spin.setVisible(initial == 0)
            choices.idClicked.connect(lambda index, field=spin: self._select_duration(index, field))
            spin.valueChanged.connect(self._focus_mode_changed)
            self.focus_modes.addWidget(group)
        box.addWidget(self.focus_modes)
        self.focus_controls = QWidget()
        row = QHBoxLayout(self.focus_controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.pause_button = _button("暂停", lambda: self._call(self.controller.pause_focus))
        self.pause_button.setProperty("role", "primary")
        self.resume_button = _button("继续", lambda: self._call(self.controller.resume_focus))
        self.resume_button.setProperty("role", "primary")
        self.cancel_focus_button = _button("取消计时", lambda: self._call(self.controller.cancel_focus))
        for button in (self.pause_button, self.resume_button, self.cancel_focus_button):
            row.addWidget(button)
        box.addWidget(self.focus_controls)
        layout.addWidget(surface)
        tip, tip_box = card(margin=10)
        tip_row = QHBoxLayout()
        self.focus_art = CharacterArt("ready", 78)
        tip_row.addWidget(self.focus_art)
        self.focus_hint = label(role="muted")
        tip_row.addWidget(self.focus_hint, 1)
        tip_box.addLayout(tip_row)
        layout.addWidget(tip)
        layout.addStretch()

    def _select_duration(self, index, field):
        field.setVisible(index == 0)
        if index == 0:
            field.setFocus()
        self._focus_mode_changed()

    def _focus_mode_changed(self, *_):
        if self.countdown.property("phase") == "complete":
            self._last_focus = None
        self._sync_focus()

    def _sync_focus(self, *_):
        if not hasattr(self, "cancel_focus_button"):
            return
        snapshot = self.controller.focus_snapshot() or {}
        if snapshot:
            self._last_focus = snapshot
        elif getattr(self, "_last_focus", None):
            last = self._last_focus
            completed = any(item.get("kind") == last.get("kind") and item.get("entity_id") == last.get("id")
                            for item in self.controller.pending_notifications())
            snapshot = {"state": "completed", "kind": last["kind"], "remaining": 0} if completed else {}
            if not completed:
                self._last_focus = None
        state = snapshot.get("state")
        active = state in ("running", "paused")
        rest = self.focus_modes.currentIndex() == 1
        if active or state == "completed":
            rest = snapshot.get("kind") == "break"
            mode = int(rest)
            if self.focus_modes.currentIndex() != mode:
                self.focus_modes.blockSignals(True)
                self.focus_modes.setCurrentIndex(mode)
                self.focus_modes.blockSignals(False)
                self.focus_mode_buttons.button(mode).setChecked(True)
        kind_id = "break" if rest else "focus"
        field = self.break_minutes if rest else self.focus_minutes
        selected = self.presets[kind_id].checkedId() or field.value()
        remaining = float(snapshot.get("remaining", selected * 60))
        seconds = max(0, math.ceil(remaining))
        phase = ("paused" if state == "paused" else "rest" if active and rest else
                 "focus" if active else "complete" if state == "completed" else "ready")
        self.countdown.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")
        long_duration = seconds >= 6000
        if (self.countdown.property("phase"), self.countdown.property("longDurationStyle")) != (phase, long_duration):
            self.countdown.setProperty("phase", phase)
            self.countdown.setProperty("longDurationStyle", long_duration)
            self.countdown.style().unpolish(self.countdown)
            self.countdown.style().polish(self.countdown)
        kind = "休息" if rest else "专注"
        self.focus_status.setText(f"{kind} · {STATE_LABELS.get(state, '等待开始')}")
        duration = float(snapshot.get("duration", 0)) or max(seconds, 1)
        token = {"paused": "pink", "rest": "green", "complete": "green"}.get(phase, "accent")
        self.timer_ring.set_progress(remaining / duration if active else float(phase == "complete"), token)
        quiet = hasattr(self.controller, "policy") and self.controller.policy.is_dnd()
        self.focus_art.set_state("quiet" if quiet else phase)
        prompts = {
            "ready": "选好时长后开始。收起或最小化窗口，计时会在后台继续。",
            "focus": "专注正在计时，收起窗口后会在后台继续。",
            "rest": "休息正在计时。",
            "paused": "计时已暂停，继续后从剩余时长开始。",
            "complete": "本轮计时已结束。",
        }
        self.focus_hint.setText("勿扰模式已生效。 " + prompts[phase] if quiet else prompts[phase])
        self.focus_modes.setVisible(not active)
        self.focus_controls.setVisible(active)
        for button in self.focus_mode_buttons.buttons():
            button.setEnabled(not active)
        for button in self.start_buttons:
            button.setEnabled(not active)
        self.pause_button.setVisible(state == "running")
        self.pause_button.setEnabled(state == "running")
        self.resume_button.setVisible(state == "paused")
        self.resume_button.setEnabled(state == "paused")
        self.cancel_focus_button.setEnabled(active)
        return snapshot

    def _build_reminders(self):
        layout = self._page("提醒")
        header = QHBoxLayout()
        header.addWidget(label("我的提醒", "title"), 1)
        layout.addLayout(header)
        stats = QHBoxLayout()
        self.reminder_counts = {}
        for key, title in (("scheduled", "待提醒"), ("pending", "已到期"), ("done", "已完成")):
            tile, box = card("tile", 10)
            box.addWidget(label(title, "muted"))
            self.reminder_counts[key] = label("0", "metric")
            box.addWidget(self.reminder_counts[key])
            stats.addWidget(tile, 1)
        layout.addLayout(stats)
        new_reminder_form, box = card()
        form = _reminder_form(self)
        box.addLayout(form)
        self.add_reminder_button = _button("添加提醒", self._add_reminder)
        self.add_reminder_button.setProperty("role", "primary")
        self.reminder_title.returnPressed.connect(self._add_reminder)
        form.addRow(self.add_reminder_button)
        self.new_reminder = QPushButton("＋ 新建提醒")
        self.new_reminder.setCheckable(True)
        self.new_reminder.toggled.connect(new_reminder_form.setVisible)
        self.new_reminder.toggled.connect(lambda checked: self.reminder_title.setFocus() if checked else None)
        header.addWidget(self.new_reminder)
        new_reminder_form.hide()
        layout.addWidget(new_reminder_form)
        self.reminder_cards = QVBoxLayout()
        self.reminder_cards.setSpacing(CONTROL_GAP)
        layout.addLayout(self.reminder_cards)
        group, box = card()
        self.reminders_table = _table(["内容", "时间", "状态"])
        self.reminders_table.setMinimumHeight(132)
        self.reminders_table.itemSelectionChanged.connect(self._reminder_selection_changed)
        filters = QHBoxLayout()
        self.reminder_query = QLineEdit()
        self.reminder_query.setPlaceholderText("搜索提醒内容")
        self.reminder_filter = QComboBox()
        self.reminder_filter.addItem("全部状态", None)
        for status in ("scheduled", "pending", "done", "cancelled"):
            self.reminder_filter.addItem(STATE_LABELS[status], status)
        filters.addWidget(self.reminder_query, 1)
        filters.addWidget(self.reminder_filter)
        box.addLayout(filters)
        box.addWidget(self.reminders_table)
        self.reminder_empty = label("没有匹配的记录", "muted")
        box.addWidget(self.reminder_empty)
        self.reminder_query.textChanged.connect(self._refresh_reminder_history)
        self.reminder_filter.currentIndexChanged.connect(self._refresh_reminder_history)
        self.reminders_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.reminders_table.customContextMenuRequested.connect(self._reminder_history_menu)
        self.reminders_table.cellDoubleClicked.connect(lambda *_: self._edit_reminder())
        row = QHBoxLayout()
        self.edit_reminder_button = _button("编辑", lambda: self._edit_reminder())
        self.cancel_reminder_button = _button("取消提醒", self._cancel_reminder)
        row.addWidget(self.edit_reminder_button)
        row.addWidget(self.cancel_reminder_button)
        self.delete_reminder_button = _button("删除", lambda: self._delete_reminder())
        self.delete_reminder_button.setProperty("role", "danger")
        row.addWidget(self.delete_reminder_button)
        row.addStretch()
        box.addLayout(row)
        disclosure("全部提醒与历史", group, layout)
        group, box = card()
        self.pending_table = _table(["通知", "类型"])
        self.pending_table.setMinimumHeight(110)
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
        disclosure("待处理通知", group, layout)
        layout.addStretch()

    def _refresh_reminder_cards(self):
        while self.reminder_cards.count():
            widget = self.reminder_cards.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        for key, count in self.reminder_counts.items():
            count.setText(str(sum(item["status"] == key for item in self._reminders)))
        reminders_by_id = {item["id"]: item for item in self._reminders}
        notified = {item.get("entity_id") for item in self._pending if item.get("kind") == "reminder"}
        active = [item for item in self._reminders
                  if item["status"] == "scheduled" or (item["status"] == "pending" and item["id"] not in notified)]
        if not (self._pending or active):
            tile, box = card()
            box.addWidget(CharacterArt("success" if self.reminder_counts["done"].text() != "0"
                                       else "workbench", 54), alignment=Qt.AlignHCenter)
            box.addWidget(label("暂无待办提醒", "muted"), alignment=Qt.AlignHCenter)
            self.reminder_cards.addWidget(tile)
        for item in (*self._pending, *active):
            notification = "kind" in item
            tile, box = card(margin=12)
            row = QHBoxLayout()
            if notification:
                state = {"focus": "complete", "break": "sleepy"}.get(item["kind"], "reminder")
                row.addWidget(CharacterArt(state, 56))
            text = QVBoxLayout()
            title = item.get("body") if notification and item["kind"] == "reminder" else item["title"]
            text.addWidget(WrappingTitle(str(title or item["title"])))
            if notification:
                meta = ("已到期" if item["kind"] == "reminder" else
                        f"待处理 · {KIND_LABELS.get(item['kind'], '通知')}")
                if "due_at" in item:
                    meta += f" · {_date_text(item['due_at'])}"
            else:
                meta = _date_text(item["due_at"])
            text.addWidget(label(meta, "muted"))
            row.addLayout(text, 1)
            actions = QMenu(tile)
            if notification:
                actions.addAction("完成", lambda checked=False, i=item["id"]:
                                  self._call(self.controller.complete_notification, i))
                if item["kind"] == "reminder":
                    for minutes in (5, 10):
                        actions.addAction(f"稍后 {minutes} 分钟", lambda checked=False, i=item["id"], m=minutes:
                                          self._call(self.controller.snooze_notification, i, m))
                    actions.addAction("自定义稍后…", lambda checked=False, i=item["id"]:
                                      self._custom_snooze(i))
                    reminder = reminders_by_id.get(item.get("entity_id"))
                    if reminder:
                        actions.addSeparator()
                        self._reminder_actions(actions, reminder)
            else:
                self._reminder_actions(actions, item)
            quick_action = actions.actions()[0]
            row.addWidget(_button(quick_action.text(), quick_action.trigger))
            more = QPushButton("更多")
            more.setMenu(actions)
            row.addWidget(more)
            box.addLayout(row)
            self.reminder_cards.addWidget(tile)

    def _custom_snooze(self, notification_id):
        minutes, accepted = QInputDialog.getInt(self, "稍后提醒", "分钟", self.snooze_minutes.value(), 1, 1440)
        if accepted:
            self._call(self.controller.snooze_notification, notification_id, minutes)

    def _build_sound(self):
        layout = self._page("声音 / 勿扰")
        layout.addWidget(label("声音与勿扰", "title"))
        sections = PageSections()
        sections.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        tabs, _ = _segments(("声音设置", "语音档案馆"), sections)
        layout.addLayout(tabs)
        layout.addWidget(sections, alignment=Qt.AlignTop)
        layout.addStretch()
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        sections.addWidget(settings)
        group, form = card()
        settings_layout.addWidget(group)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setAccessibleName("语音音量")
        self.volume_label = label(role="muted")
        self.volume.valueChanged.connect(lambda value: self.volume_label.setText(f"{value}%"))
        row = QHBoxLayout()
        row.addWidget(label("语音音量", "heading"))
        row.addStretch()
        row.addWidget(self.volume_label)
        form.addLayout(row)
        form.addWidget(self.volume)
        self.setting_checks = {}
        for key, title in (("voice_enabled", "启用语音"), ("mute", "静音"),
                           ("hover_voice", "悬停时播放语音"),
                           ("idle_voice", "低频待机独白"),
                           ("assistant_open_voice", "打开助手时语音回应"),
                           ("dnd", "手动勿扰"), ("fullscreen_dnd", "全屏时勿扰"),
                           ("quiet_hours_enabled", "启用安静时段")):
            if key == "hover_voice":
                form.addWidget(label("互动语音", "heading"))
            if key == "dnd":
                quiet = QHBoxLayout()
                quiet.addWidget(CharacterArt("quiet", 44))
                quiet.addWidget(label("勿扰规则", "heading"), 1)
                form.addLayout(quiet)
            checkbox = ToggleSwitch(title)
            self.setting_checks[key] = checkbox
            form.addWidget(checkbox)
        self.quiet_start = QTimeEdit()
        self.quiet_end = QTimeEdit()
        row = QHBoxLayout()
        row.addWidget(label("安静时段"))
        for title, edit in (("开始", self.quiet_start), ("结束", self.quiet_end)):
            edit.setAccessibleName("安静时段" + title)
            edit.setDisplayFormat("HH:mm")
            row.addWidget(edit, 1)
        form.addLayout(row)
        self.apply_button = _button("应用设置", self._apply_settings)
        self.apply_button.setProperty("role", "primary")
        form.addWidget(self.apply_button)
        settings_layout.addStretch()
        archive, box = card()
        sections.addWidget(archive)
        filters = QHBoxLayout()
        self.voice_category = QComboBox()
        self.voice_category.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.voice_category.setMinimumContentsLength(5)
        self.voice_category.addItem("全部分类", "")
        self.voice_grade = QComboBox()
        self.voice_grade.addItems(["全部等级", "A", "B", "C"])
        filters.addWidget(self.voice_category, 1)
        filters.addWidget(self.voice_grade)
        box.addLayout(filters)
        self.voice_search = QLineEdit()
        self.voice_search.setAccessibleName("搜索语音")
        self.voice_search.setPlaceholderText("搜索中文、原编号或 VO 别名")
        box.addWidget(self.voice_search)
        self.voice_category.currentIndexChanged.connect(self._filter_voices)
        self.voice_grade.currentIndexChanged.connect(self._filter_voices)
        self.voice_search.textChanged.connect(self._filter_voices)
        self.voice_list = QListWidget()
        self.voice_list.setAccessibleName("匹配的语音条目")
        self.voice_list.setProperty("role", "catalog")
        self.voice_list.setFixedHeight(118)
        self.voice_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.voice_list.currentRowChanged.connect(self._voice_selection_changed)
        self.voice_list.itemActivated.connect(lambda _: self._audition())
        player = QHBoxLayout()
        player.addWidget(CharacterArt("archive", 78))
        voice_info = QVBoxLayout()
        voice_info.addWidget(label("语音档案馆", "heading"))
        self.voice_metadata = label(role="muted")
        voice_info.addWidget(self.voice_metadata)
        self.voice_caption = label(role="heading")
        self.voice_caption.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.voice_caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        voice_info.addWidget(self.voice_caption)
        player.addLayout(voice_info, 1)
        box.addLayout(player)
        self.voice_progress = QProgressBar()
        self.voice_progress.setAccessibleName("试听进度")
        self.voice_progress.setTextVisible(False)
        self.voice_progress.setFixedHeight(5)
        box.addWidget(self.voice_progress)
        self.voice_time = label(role="muted")
        box.addWidget(self.voice_time)
        row = QHBoxLayout()
        self.previous_voice = _button("上一条", lambda: self._step_voice(-1))
        self.next_voice = _button("下一条", lambda: self._step_voice(1))
        self.audition_button = _button("播放", self._audition)
        self.audition_button.setProperty("role", "primary")
        self.stop_button = _button("停止", lambda: self._call(self.controller.stop_audio))
        for button in (self.previous_voice, self.audition_button, self.stop_button, self.next_voice):
            row.addWidget(button)
        box.addLayout(row)
        box.addWidget(label("语音列表", "muted"))
        box.addWidget(self.voice_list)
        self.voice_text = QTextEdit()
        self.voice_text.setReadOnly(True)
        self.voice_text.setFixedHeight(110)
        disclosure("语音详情", self.voice_text, box)
        topics = QHBoxLayout()
        for scene, title in (("greeting", "问候"), ("encouragement", "鼓励"), ("rest_advice", "休息建议")):
            button = _button(title, lambda checked=False, s=scene: self._call(self.controller.speak_topic, s))
            button.setEnabled(hasattr(self.controller, "speak_topic"))
            topics.addWidget(button)
        box.addLayout(topics)
        box.addWidget(label("A级精选用于日常事件。B/C 级可手动试听。", "muted"))

    def _call(self, method, *args, **kwargs):
        try:
            method(*args, **kwargs)
        except Exception as error:
            self.error_art.show()
            QMessageBox.warning(self, "操作未完成", f"无法完成操作：{error}")
            return False
        self.error_art.hide()
        self.refresh()
        return True

    def _start(self, minutes, kind):
        return self._call(self.controller.start_focus, minutes, kind=kind)

    def _add_reminder(self):
        title = self.reminder_title.text().strip()
        due_at = _form_due(self)
        if not title or due_at <= time.time():
            QMessageBox.warning(self, "请检查提醒", "请填写提醒内容，并选择未来的时间。")
            return
        if self._call(self.controller.add_reminder, title, due_at,
                      time_mode=self.reminder_mode.currentData()):
            self.reminder_title.clear()
            self.new_reminder.setChecked(False)

    @staticmethod
    def _selected_id(table):
        item = table.item(table.currentRow(), 0)
        return item.data(Qt.UserRole) if item and item.isSelected() else None

    def _reminder_record(self, reminder_id=None):
        if reminder_id is None:
            reminder_id = self._selected_id(self.reminders_table)
        return next((item for item in (self._reminders or ()) if item["id"] == reminder_id), None)

    def _refresh_reminder_history(self, *_):
        query = self.reminder_query.text().strip().casefold()
        status = self.reminder_filter.currentData()
        rows = [(item["id"], (item["title"], _date_text(item["due_at"]),
                 STATE_LABELS.get(item.get("status"), item.get("status", ""))))
                for item in (self._reminders or ())
                if query in item["title"].casefold() and (not status or item["status"] == status)]
        _fill_table(self.reminders_table, rows)
        self.reminder_empty.setVisible(not rows)
        self._reminder_selection_changed()

    def _reminder_selection_changed(self):
        reminder = self._reminder_record()
        self.edit_reminder_button.setEnabled(reminder is not None)
        self.delete_reminder_button.setEnabled(reminder is not None)
        self.cancel_reminder_button.setEnabled(reminder is not None and reminder["status"] in ("scheduled", "pending"))

    def _reminder_actions(self, menu, reminder):
        identifier = reminder["id"]
        menu.addAction("编辑", lambda: self._edit_reminder(identifier))
        if reminder["status"] in ("scheduled", "pending"):
            menu.addAction("取消提醒", lambda: self._call(self.controller.cancel_reminder, identifier))
        menu.addAction("删除…", lambda: self._delete_reminder(identifier)).setIcon(
            icon("delete", colors(self)["rose"], 17))

    def _reminder_history_menu(self, point):
        item = self.reminders_table.itemAt(point)
        if item:
            self.reminders_table.selectRow(item.row())
        reminder = self._reminder_record()
        if reminder:
            menu = QMenu(self)
            self._reminder_actions(menu, reminder)
            menu.exec(self.reminders_table.viewport().mapToGlobal(point))
            menu.deleteLater()

    def _cancel_reminder(self):
        reminder = self._reminder_record()
        if reminder:
            self._call(self.controller.cancel_reminder, reminder["id"])

    def _delete_reminder(self, reminder_id=None):
        reminder = self._reminder_record(reminder_id)
        if not reminder:
            return
        dialog = QMessageBox(QMessageBox.Warning, "删除提醒", "删除这条提醒及它的通知记录？",
                             QMessageBox.Yes | QMessageBox.No, self)
        dialog.setTextFormat(Qt.PlainText)
        dialog.setInformativeText(reminder["title"])
        dialog.setDefaultButton(QMessageBox.No)
        dialog.button(QMessageBox.Yes).setText("删除")
        dialog.button(QMessageBox.Yes).setProperty("role", "danger")
        dialog.button(QMessageBox.No).setText("保留")
        if dialog.exec() == QMessageBox.Yes:
            self._call(self.controller.delete_reminder, reminder["id"])
        dialog.deleteLater()

    def _edit_reminder(self, reminder_id=None):
        reminder = self._reminder_record(reminder_id)
        if reminder is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("KEI · 编辑提醒")
        dialog.setMinimumWidth(390)
        outer = QVBoxLayout(dialog)
        form = _reminder_form(dialog, reminder)
        outer.addLayout(form)
        status = reminder["status"]
        historical = status in ("done", "cancelled")
        form.addRow("当前状态", label(STATE_LABELS.get(status, status)))
        disposition = QComboBox()
        disposition.addItem("仅更新记录，保持状态", False)
        disposition.addItem("重新启用并安排提醒", True)
        if historical:
            form.addRow("保存方式", disposition)
        else:
            outer.addWidget(label("修改时间将按新的计划提醒。", "muted"))
        minutes = dialog.reminder_minutes.value()
        date_time = dialog.reminder_datetime.dateTime()
        mode = dialog.reminder_mode.currentData()

        def save():
            title = dialog.reminder_title.text().strip()
            choice = dialog.reminder_mode.currentData()
            changed_time = (dialog.reminder_minutes.value() != minutes if choice == "relative"
                            else dialog.reminder_datetime.dateTime() != date_time)
            rearm = bool(disposition.currentData())
            due = _form_due(dialog) if (changed_time or choice != mode or rearm) else None
            if not title or (due is not None and (rearm or not historical) and due <= time.time()):
                QMessageBox.warning(dialog, "请检查提醒", "请填写内容。需要安排提醒时，请选择未来的时间。")
                return
            if self._call(self.controller.edit_reminder, reminder["id"], title, due,
                          time_mode=choice, rearm=rearm):
                dialog.accept()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

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

    def _load_settings(self, preserve_draft=False):
        settings = self.controller.settings
        defaults = {"voice_enabled": True, "fullscreen_dnd": True}
        draft = self._sound_settings()
        preferences = {key: settings.get(key) for key in draft}
        if preserve_draft and preferences == self._sound_preferences:
            return
        saved = {}
        for key, checkbox in self.setting_checks.items():
            saved[key] = bool(settings.get(key, defaults.get(key, False)))
            if not preserve_draft or draft[key] == self._loaded_sound[key]:
                checkbox.setChecked(saved[key])
        saved["voice_volume"] = int(settings.get("voice_volume", 70))
        if not preserve_draft or draft["voice_volume"] == self._loaded_sound["voice_volume"]:
            self.volume.setValue(saved["voice_volume"])
        self.volume_label.setText(f"{self.volume.value()}%")
        for key, default in (("quiet_start", "22:00"), ("quiet_end", "08:00")):
            value = QTime.fromString(str(settings.get(key, default)), "HH:mm")
            if not value.isValid():
                value = QTime.fromString(default, "HH:mm")
            saved[key] = value.toString("HH:mm")
            if not preserve_draft or draft[key] == self._loaded_sound[key]:
                getattr(self, key).setTime(value)
        self._loaded_sound = saved
        self._sound_preferences = preferences

    def _sound_settings(self):
        settings = {key: checkbox.isChecked() for key, checkbox in self.setting_checks.items()}
        settings.update(voice_volume=self.volume.value(), quiet_start=self.quiet_start.time().toString("HH:mm"),
                        quiet_end=self.quiet_end.time().toString("HH:mm"))
        return settings

    def _apply_settings(self):
        if self._call(self.controller.update_settings, self._sound_settings()):
            self._load_settings()

    def _selected_voice_id(self):
        item = self.voice_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _step_voice(self, direction):
        self.voice_list.setCurrentRow(self.voice_list.currentRow() + direction)

    def _voice_selection_changed(self, *_):
        voice_id = self._selected_voice_id()
        voice = next((item for item in self._voices if item["id"] == voice_id), None)
        available = voice is not None and bool(voice.get("available"))
        self.audition_button.setEnabled(available)
        self.stop_button.setEnabled(available)
        index = self.voice_list.currentRow()
        self.previous_voice.setEnabled(index > 0)
        self.next_voice.setEnabled(0 <= index < self.voice_list.count() - 1)
        self._selected_voice = voice
        self._refresh_voice_progress()
        self.voice_metadata.setText(
            f"{voice.get('voice_category', '未分类')}  ·  {voice.get('grade', 'C')} 级" if voice else "")
        self.voice_caption.setText(
            str(voice.get("text") or voice.get("summary") or voice_id) if voice else "没有匹配的语音")
        if voice is None:
            self.voice_text.setPlainText("没有可用的语音条目。")
            return
        self.voice_text.setPlainText(
            f"中文：\n{voice.get('text', '')}\n\n"
            f"分类：{voice.get('voice_category', '未分类')} · 等级：{voice.get('grade', 'C')}\n"
            f"原编号：{voice['id']}\n原文件：{voice.get('file') or voice['id']}"
            f"\n路径：{voice.get('path') or voice.get('file') or voice['id']}"
            f"\n来源：{voice.get('source') or '语音清单'}"
            f"\n时长：{voice.get('duration', '未知')} 秒"
            + ("\n音频文件缺失，无法试听。" if not available else ""))

    def _refresh_voice_progress(self):
        voice = getattr(self, "_selected_voice", None)
        audio = getattr(self.controller, "audio", None)
        active = voice and audio and audio.current_id == voice["id"]
        playing = active and audio.player.isPlaying()
        self.audition_button.setText("重播" if playing else "播放")
        if audio:
            self.stop_button.setEnabled(audio.current_id is not None or bool(voice and voice.get("available")))
        duration = int(float(voice.get("duration") or 0) * 1000) if voice else 0
        if active and audio.player.duration() > 0:
            duration = audio.player.duration()
        position = audio.player.position() if active else 0
        self.voice_progress.setRange(0, max(duration, 1))
        self.voice_progress.setValue(min(position, duration))

        def stamp(milliseconds):
            seconds = milliseconds // 1000
            return f"{seconds // 60:02d}:{seconds % 60:02d}"

        state = ("播放中" if playing else "加载中" if active else
                 "待播放" if voice and voice.get("available") else "文件缺失")
        self.voice_time.setText(f"{state}  ·  {stamp(position)} / {stamp(duration)}" if voice else "")

    def _filter_voices(self, *_):
        if not hasattr(self, "voice_list") or self._all_voices is None:
            return
        category = self.voice_category.currentData()
        grade = self.voice_grade.currentText()
        search = self.voice_search.text().strip().casefold()
        selected = self._selected_voice_id()
        self._voices = [entry for entry in self._all_voices
                        if (not category or entry.get("voice_category", "未分类") == category)
                        and (grade == "全部等级" or entry.get("grade", "C") == grade)
                        and (not search or search in " ".join(str(entry.get(key, "")) for key in
                             ("id", "alias", "text", "summary", "voice_category", "file", "source")).casefold())]
        with QSignalBlocker(self.voice_list):
            self.voice_list.clear()
            selection = 0
            for row, entry in enumerate(self._voices):
                text = " ".join(str(entry.get("text") or entry.get("summary") or entry["id"]).split())
                item = QListWidgetItem(f"{entry.get('grade', 'C')} · {text}", self.voice_list)
                item.setData(Qt.UserRole, entry["id"])
                item.setToolTip(text)
                if entry["id"] == selected:
                    selection = row
            self.voice_list.setCurrentRow(selection)
        self._voice_selection_changed()

    def _audition(self):
        if self.audition_button.isEnabled():
            self._call(self.controller.audition, self._selected_voice_id())

    def refresh(self):
        """Refresh live data only: never reset unsaved settings or reminder form input."""
        self._sync_theme()
        self._load_settings(preserve_draft=True)
        snapshot = self._sync_focus()
        reminders = list(self.controller.reminders_list())
        pending = list(self.controller.pending_notifications())
        reasons = self.controller.policy.reasons() if hasattr(self.controller, "policy") else ()
        if reasons:
            status, token = "勿扰中 · " + "、".join(reasons), "faint"
        elif pending:
            status, token = f"{len(pending)} 项待处理通知", "pink"
        elif snapshot.get("state") in ("running", "paused"):
            kind = KIND_LABELS.get(snapshot.get("kind"), "专注")
            state = STATE_LABELS[snapshot["state"]]
            status, token = f"{kind}{state}", "accent"
        else:
            status, token = "系统就绪", "green"
        if getattr(self.controller, "audio", None) and self.controller.audio.error:
            status += "\n" + self.controller.audio.error
        self.live_status.setText(status)
        self.status_light.set_status(token)
        if (reminders, pending) != (self._reminders, self._pending):
            self._reminders, self._pending = reminders, pending
            self._refresh_reminder_history()
            _fill_table(self.pending_table, [(item["id"], (item["title"], KIND_LABELS.get(item.get("kind"), "提醒")))
                                            for item in pending])
            self._refresh_reminder_cards()
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
        self._refresh_voice_progress()

    def closeEvent(self, event):
        # Do not stop background focus/reminders or delete this reusable panel.
        try:
            self.controller.stop_audio()
        except Exception:
            pass
        self._clear_page_animation()
        self.hide()
        event.ignore()

    def reject(self):
        # Escape must have the same hide-only behavior as the title-bar close button.
        self.close()
