"""Application-owned timer, local services, notification policy and audio routing."""
from pathlib import Path
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal

from . import config
from .audio import AudioService
from .calendar_events import CalendarEvents
from .dialogue import DialogueService, TOPICS
from .shortcuts import ShortcutsService
from .focus import FocusService
from .notification_policy import NotificationPolicy
from .reminders import ReminderService
from .storage import Storage

ROOT = Path(__file__).resolve().parents[1]


class AssistantController(QObject):
    changed = Signal()
    shortcuts_changed = Signal()

    def __init__(self, pet, database=None, parent=None, wall_clock=time.time,
                 mono_clock=time.monotonic, start_timer=True):
        super().__init__(parent)
        self.pet = pet
        self.settings = config.load()
        self.wall_clock = wall_clock
        self.mono_clock = mono_clock
        self.storage = Storage(database if database is not None else ROOT / "data/assistant.sqlite3")
        self.focus = FocusService(self.storage, wall_clock=wall_clock, mono_clock=mono_clock)
        self.reminders = ReminderService(self.storage, wall_clock=wall_clock)
        self.policy = NotificationPolicy(self.settings, wall_clock=wall_clock)
        self.audio = AudioService(self.policy, parent=self, clock=mono_clock)
        self.shortcuts = ShortcutsService(self.storage)
        self.dialogue = DialogueService(self.audio.library)
        self.calendar = CalendarEvents(self.storage, self.settings, wall_clock=wall_clock)
        self._calendar_ready_at = self.mono_clock() + 5
        self.panel = None
        self.notification_window = None
        self._dnd = self.policy.is_dnd()
        self._last_reaction = None
        self._protected_reaction = None
        self._next_idle_voice = self.mono_clock() + 180
        self._closed = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        if start_timer:
            self.timer.start(250)

    def attach_windows(self):
        from .assistant_panel import AssistantPanel
        from .notification_window import NotificationWindow
        self.panel = AssistantPanel(self)
        self.notification_window = NotificationWindow(self)
        self.notification_window.open_requested.connect(self.open_panel)

    def open_panel(self, tab=None):
        if self.panel is None:
            self.attach_windows()
        was_visible = self.panel.isVisible()
        self.panel.refresh()
        if tab is not None:
            for index in range(self.panel.tabs.count()):
                if self.panel.tabs.tabText(index) == tab:
                    self.panel.tabs.setCurrentIndex(index)
                    break
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        if not was_visible and self.settings.get("assistant_open_voice", True):
            self._feature_reaction("assistant_open", "default", "")

    def tick(self):
        if self._closed:
            return
        notifications = self.focus.tick() + self.reminders.tick()
        dnd = self.policy.is_dnd()
        if dnd != self._dnd:
            self._dnd = dnd
            if dnd:
                self.audio.stop()
                if self.notification_window:
                    self.notification_window.hide()
        pending = self.storage.pending()
        if not dnd:
            self.storage.mark_presented([item["id"] for item in pending if not item["presented"]])
        if self.notification_window:
            self.notification_window.sync(pending)
            if notifications and not dnd:
                self.storage.mark_presented([item["id"] for item in notifications])
                self.notification_window.show()
                self._notify_completed(notifications)
        self.pet.bubbles.tick(self.mono_clock())
        self._calendar_tick()
        self._idle_voice_tick()
        self.changed.emit()

    def _calendar_tick(self):
        if self.mono_clock() < self._calendar_ready_at:
            return
        blocked = (not self.policy.can_voice() or not self.pet.isVisible()
                   or self.focus.snapshot() is not None or bool(self.storage.pending())
                   or self.audio.current_id is not None or self.pet.bubbles.active is not None
                   or self.pet.state.get("pressed") or self.pet.state.get("dragging")
                   or self.pet.state.get("reaction") is not None)
        event = self.calendar.claim(blocked=bool(blocked))
        if event is not None:
            self._show_calendar(event, explicit=False)

    def _idle_voice_tick(self):
        now = self.mono_clock()
        if now < self._next_idle_voice:
            return
        self._next_idle_voice = now + 180
        if (not self.settings.get("idle_voice", False) or not self.policy.can_voice()
                or not self.pet.isVisible() or self.focus.snapshot() is not None
                or self.audio.current_id is not None or self.storage.pending()
                or self.pet.state.get("pressed") or self.pet.state.get("dragging")
                or self.pet.state.get("reaction") or self.pet.bubbles.active is not None):
            return
        self._feature_reaction("idle", "default", "", explicit=False)

    def _notify_completed(self, notifications):
        # One batch, one voice; ordinary reminders have no fabricated character line.
        completed = next((item for item in notifications if item["kind"] in ("focus", "break")), None)
        if completed and self.pet.isVisible():
            scene = "focus_complete" if completed["kind"] == "focus" else "break_complete"
            self._feature_reaction(scene, "smile", completed["body"], explicit=False, priority=True)

    def _feature_reaction(self, scene, category, text, explicit=True, priority=False):
        if not self.policy.can_react(explicit):
            return
        now = self.mono_clock()
        self.pet.bubbles.show(category, now, 5)
        self.pet.bubbles.active["text"] = text
        self.pet.bubbles.active["context_text"] = text
        self.audio.respond(self.pet.bubbles.active, scene, explicit, priority)
        self._last_reaction = self.pet.bubbles.active
        if priority and self.audio.current_reaction == self.pet.bubbles.active.get("id"):
            self._protected_reaction = self.pet.bubbles.active

    def pointer_event(self, event):
        if (self._protected_reaction is not None
                and self.audio.current_priority > 0
                and self.mono_clock() < self.audio.current_until
                and self.audio.current_reaction == self._protected_reaction.get("id")):
            self.pet.bubbles.active = self._protected_reaction
            self._last_reaction = self._protected_reaction
            return
        self._protected_reaction = None
        if event in ("press", "cancel", "drag_start"):
            self.audio.stop(force=False)
            self._next_idle_voice = self.mono_clock() + 180
        if event in ("hover",) and self.policy.is_dnd():
            self.pet.bubbles.clear()
            return
        active = self.pet.bubbles.active
        if active is not None and active is not self._last_reaction:
            if event == "hover" and not self.settings.get("hover_voice", False):
                self._last_reaction = active
                return
            scene = "drag" if event == "drag_start" else "click" if event == "click" else active["category"]
            self.audio.respond(active, scene, explicit=event != "hover")
            self._last_reaction = active

    def focus_snapshot(self):
        return self.focus.snapshot()

    def start_focus(self, minutes, kind="focus"):
        result = self.focus.start(minutes, kind=kind)
        self.settings["focus_minutes" if kind == "focus" else "break_minutes"] = minutes
        config.save({"focus_minutes" if kind == "focus" else "break_minutes": minutes})
        scene = "focus_start" if kind == "focus" else "break_start"
        self._feature_reaction(scene, "default", "专注开始。" if kind == "focus" else "开始休息。")
        self.changed.emit()
        return result

    def pause_focus(self):
        self.focus.pause()
        self._feature_reaction("focus_pause", "default", "计时已暂停。")
        self.changed.emit()

    def resume_focus(self):
        self.focus.resume()
        self._feature_reaction("focus_resume", "default", "继续计时。")
        self.changed.emit()

    def cancel_focus(self):
        self.focus.cancel()
        self.audio.stop()
        self.changed.emit()

    def reminders_list(self):
        return self.reminders.list_all()

    def add_reminder(self, title, due_at):
        result = self.reminders.add(title, due_at)
        self.changed.emit()
        return result

    def edit_reminder(self, reminder_id, title, due_at):
        result = self.reminders.edit(reminder_id, title, due_at)
        self.changed.emit()
        return result

    def cancel_reminder(self, reminder_id):
        self.reminders.cancel(reminder_id)
        self.changed.emit()

    def pending_notifications(self):
        return self.storage.pending()

    def complete_notification(self, notification_id):
        pending = next((item for item in self.storage.pending() if item["id"] == notification_id), None)
        self.storage.acknowledge(notification_id)
        if pending and pending["kind"] == "reminder":
            self._feature_reaction("reminder_complete", "happy", "事项已完成。")
        self.changed.emit()
        self.tick()

    def snooze_notification(self, notification_id, minutes):
        self.reminders.snooze(notification_id, minutes)
        self.changed.emit()
        self.tick()

    def update_settings(self, changes):
        config.save(changes)
        self.settings.clear()
        self.settings.update(config.load())
        self.audio.apply_settings()
        if self.policy.is_dnd() and self.notification_window:
            self.notification_window.hide()
        self._dnd = self.policy.is_dnd()
        self.changed.emit()

    def voice_entries(self):
        return self.audio.library.entries()

    def _show_dialogue_reply(self, reply, user="", explicit=True):
        if (self.audio.current_priority == 1 and self.audio.current_id is not None
                and self.mono_clock() < self.audio.current_until):
            return {"text": "先听完这条提醒吧，稍后再聊。", "topic": reply.get("topic"), "image": None}
        # A new explicit reply replaces an old manual line too. The guard above
        # is the sole exception for a still-playing important notification.
        self.audio.stop()
        now = self.mono_clock()
        self.pet.bubbles.show(reply["category"], now, 5)
        active = self.pet.bubbles.active
        active["text"] = reply["text"]
        entry = reply.get("voice")
        if entry and self.policy.can_voice():
            reaction_id = active.setdefault("id", uuid4().hex)
            if self.audio.play(entry, reaction_id, reply.get("topic") or "dialogue", manual=explicit):
                active["voice_id"] = entry["id"]
                active["dur"] = max(5, float(entry["duration"]) + .5)
        result = dict(active, topic=reply.get("topic"))
        self._last_reaction = active
        if user:
            self.dialogue.remember(user, result)
        self.changed.emit()
        return result

    def chat(self, text):
        reply = self.dialogue.message(text)
        return self._show_dialogue_reply(reply, user=text)

    def dialogue_topic(self, topic):
        return self._show_dialogue_reply(self.dialogue.topic(topic), user=TOPICS[topic]["name"])

    def speak_topic(self, scene):
        if scene not in TOPICS:
            raise ValueError("未知语音话题。")
        return self.dialogue_topic(scene)

    def today_event(self):
        return self.calendar.today()

    def _show_calendar(self, event, explicit=True):
        entry = self.audio.library.choose(event["topic"], self.settings, manual=True)
        if entry and entry.get("grade") not in ("A", "B"):
            entry = None
        text = event["text"] + ("\n" + entry["text"] if entry else "")
        return self._show_dialogue_reply({"text": text, "category": event["category"],
                                         "voice": entry, "topic": event["topic"]}, explicit=explicit)

    def trigger_today_event(self):
        event = self.calendar.today()
        if event is None:
            return {"text": "今天没有日期彩蛋，可以选一个话题聊聊。", "topic": None, "image": None}
        return self._show_calendar(event)

    def shortcuts_list(self):
        return self.shortcuts.list_all()

    def add_shortcut(self, name, kind, target):
        result = self.shortcuts.add(name, kind, target)
        self.shortcuts_changed.emit()
        self.changed.emit()
        return result

    def edit_shortcut(self, shortcut_id, name, kind, target):
        result = self.shortcuts.edit(shortcut_id, name, kind, target)
        self.shortcuts_changed.emit()
        self.changed.emit()
        return result

    def delete_shortcut(self, shortcut_id):
        self.shortcuts.delete(shortcut_id)
        self.shortcuts_changed.emit()
        self.changed.emit()

    def move_shortcut(self, shortcut_id, direction):
        self.shortcuts.reorder(shortcut_id, direction)
        self.shortcuts_changed.emit()
        self.changed.emit()

    def open_shortcut(self, shortcut_id):
        result = self.shortcuts.open(shortcut_id)
        self._feature_reaction("shortcut_open", "default", f"已发起打开：{result['name']}")
        return dict(result, message=f"已发起打开：{result['name']}")

    def audition(self, voice_id):
        return self.audio.audition(voice_id)

    def stop_audio(self):
        self.audio.stop()

    def tray_status(self):
        pending = len(self.storage.pending())
        focus = self.focus.snapshot()
        parts = ["凯伊 · 桌宠助手"]
        if focus and focus["state"] in ("running", "paused"):
            seconds = max(0, round(focus["remaining"]))
            parts.append(f"{'休息' if focus['kind'] == 'break' else '专注'} {seconds // 60:02d}:{seconds % 60:02d}")
        if pending:
            parts.append(f"待处理提醒 {pending}")
        if self.policy.is_dnd():
            parts.append("勿扰中")
        return "\n".join(parts)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self.audio.shutdown()
        if self.panel:
            self.panel.hide()
        if self.notification_window:
            self.notification_window.hide()
        self.storage.close()
