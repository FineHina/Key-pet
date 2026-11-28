"""Application-owned timer, local services, notification policy and audio routing."""
from datetime import datetime
from pathlib import Path
from sqlite3 import Error as DatabaseError
import time
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from . import config
from .audio import AudioService
from .calendar_events import CalendarEvents, STARTUP_VOICES
from .dialogue import DialogueService, TOPICS
from .shortcuts import ShortcutsService
from .focus import FocusService
from .interaction import GIFTS, InteractionService
from .interaction_feedback import CUES, Cue, milestone
from .notification_policy import NotificationPolicy
from .reminders import ReminderService
from .storage import Storage

ROOT = Path(__file__).resolve().parents[1]


class AssistantController(QObject):
    changed = Signal()
    shortcuts_changed = Signal()
    interaction_changed = Signal()
    interaction_voice = Signal(str)

    def __init__(self, pet, database=None, parent=None, wall_clock=time.time,
                 mono_clock=time.monotonic, start_timer=True):
        super().__init__(parent)
        self.pet = pet
        self.settings = config.load()
        self.wall_clock = wall_clock
        self.mono_clock = mono_clock
        self.storage = Storage(database if database is not None else ROOT / "data/assistant.sqlite3")
        self.interaction = InteractionService(self.storage, wall_clock=wall_clock)
        self.interaction_window = None
        self.interaction_error = None
        self.focus = FocusService(self.storage, wall_clock=wall_clock, mono_clock=mono_clock)
        self.reminders = ReminderService(self.storage, wall_clock=wall_clock)
        self.policy = NotificationPolicy(self.settings, wall_clock=wall_clock)
        self.audio = AudioService(self.policy, parent=self, clock=mono_clock)
        self._interaction_voice_id = None
        self._viewed_rewards = set()
        self.audio.changed.connect(self._sync_interaction_subtitle)
        self.shortcuts = ShortcutsService(self.storage)
        self.dialogue = DialogueService(self.audio.library)
        self.calendar = CalendarEvents(self.storage, self.settings, wall_clock=wall_clock)
        self._calendar_ready_at = self.mono_clock() + 5
        self._startup_greeted = False
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
        self.panel.theme_changed.connect(self.notification_window.set_theme)
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
        if self.panel.isMinimized():
            self.panel.setWindowState(self.panel.windowState() & ~Qt.WindowMinimized)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        if not was_visible and self.settings.get("assistant_open_voice", True):
            self._feature_reaction("assistant_open", "default", "")

    def tick(self):
        if self._closed:
            return
        # Commit completion and its one-time affection credit together.
        focus_milestone = None
        with self.storage.transaction():
            focus_notifications = self.focus.tick()
            for item in focus_notifications:
                if item["kind"] == "focus":
                    earned = self.interaction.focus_completed(item["entity_id"])
                    if earned:
                        after = self.interaction.status()[0]
                        focus_milestone = milestone(after - earned, after)
        notifications = focus_notifications + self.reminders.tick()
        if focus_notifications:
            self.interaction_changed.emit()
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
        if focus_milestone and not dnd:
            # The existing completion notification owns audio and its caption.
            self._apply_interaction_pose(CUES[focus_milestone[0]], self.mono_clock())
        self.pet.bubbles.tick(self.mono_clock())
        self._calendar_tick()
        self._idle_voice_tick()
        self.changed.emit()

    def play_startup_greeting(self):
        """Choose a single greeting after the desktop pet has become visible."""
        if self._closed or self._startup_greeted:
            return
        self._startup_greeted = True
        blocked = self._automatic_voice_blocked()
        event = self.calendar.claim(blocked=blocked)
        if blocked:
            return
        if event:
            self._apply_interaction_pose(Cue("nod", event["category"], 0.9), self.mono_clock())
            self._show_calendar(event, explicit=False, startup=True)
            return
        hour = datetime.fromtimestamp(self.wall_clock()).hour
        if 5 <= hour < 12:
            greeting = "早上好"
        elif 12 <= hour < 18:
            greeting = "下午好"
        else:
            greeting = "晚上好" if hour >= 18 else "你好"
        self._apply_interaction_pose(Cue("nod", "smile", 0.9), self.mono_clock())
        self._feature_reaction("greeting", "smile", f"{greeting}，欢迎回来。", explicit=False)

    def _automatic_voice_blocked(self):
        return bool(
            not self.policy.can_voice() or not self.pet.isVisible()
            or self.focus.snapshot() is not None or self.storage.pending()
            or self.audio.current_id is not None or self.pet.bubbles.active is not None
            or self.pet.state.get("pressed") or self.pet.state.get("dragging")
            or self.pet.state.get("reaction") is not None
        )

    def _calendar_tick(self):
        if self.mono_clock() < self._calendar_ready_at:
            return
        event = self.calendar.claim(blocked=self._automatic_voice_blocked())
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

    def pointer_event(self, event, region=None):
        crossed = None
        if event == "click" and region == "head":
            earned = self.interaction.touch()
            if earned:
                after = self.interaction.status()[0]
                self.interaction_changed.emit()
                crossed = milestone(after - earned, after)
        elif event == "press" and self.pet.state.get("expression") in ("angry", "dark_angry"):
            self.interaction.ask_for_quiet()
            self.interaction_changed.emit()
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
        if crossed:
            self._interaction_feedback(*crossed)
            return
        active = self.pet.bubbles.active
        if active is not None and active is not self._last_reaction:
            if event == "hover" and not self.settings.get("hover_voice", False):
                self._last_reaction = active
                return
            scene = "drag" if event == "drag_start" else "click" if event == "click" else active["category"]
            self.audio.respond(active, scene, explicit=event != "hover")
            self._last_reaction = active

    def grant_startup_gift(self):
        try:
            gift = self.interaction.grant_daily_gift()
        except DatabaseError as error:
            self.interaction_error = f"今日礼物暂未入库：{error}"
            return None
        if gift:
            self.interaction_changed.emit()
        return gift

    def open_interaction(self, section="care"):
        if self.interaction_window is None:
            from .interaction_window import InteractionWindow
            from .ui_theme import resolved_dark
            self.interaction_window = InteractionWindow(self)
            self.interaction_window.set_theme(resolved_dark(self.settings.get("assistant_theme", "system")))
            if self.panel:
                self.panel.theme_changed.connect(self.interaction_window.set_theme)
            if self.interaction_error:
                self.interaction_window.feedback.setText(self.interaction_error)
        self.interaction_window.show_section(section)

    def _sync_interaction_subtitle(self):
        if self._interaction_voice_id and self.audio.current_reaction != self._interaction_voice_id:
            self._interaction_voice_id = None
            self.interaction_voice.emit("")

    def _apply_interaction_pose(self, cue, now):
        behaviors = getattr(self.pet, "behaviors", None)
        return bool(behaviors and self.pet.isVisible() and behaviors.play_interaction_pose(
            cue.pose, cue.expression, cue.seconds, now))

    def _interaction_voice_entry(self, cue, now, replay):
        library = self.audio.library
        available = []
        for voice_id in cue.voices:
            entry = library.source(voice_id)
            if entry and entry.get("text") and entry.get("grade") == "A":
                available.append(entry)
        for entry in available:
            if entry["id"] not in library.recent:
                return entry
        last = library.recent[-1] if library.recent else None
        for entry in available:
            if entry["id"] != last:
                return entry
        if available and (replay or now - self.audio.last_started >= 8):
            return available[0]
        return None

    def _interaction_feedback(self, event, text, *, previous_points=None, replay=False):
        if self._closed or self.policy.is_dnd():
            return False
        if previous_points is not None:
            crossed = milestone(previous_points, self.interaction.status()[0])
            if crossed:
                event, caption = crossed
                text = f"{text} · {caption}"
        cue = CUES[event]
        now = self.mono_clock()
        busy = self.audio.current_id is not None and now < self.audio.current_until
        if ((busy and self.audio.current_priority > 0)
                or (self.notification_window and self.notification_window.isVisible())):
            return False
        animated = self._apply_interaction_pose(cue, now)
        if busy:
            return False

        entry = self._interaction_voice_entry(cue, now, replay) if self.policy.can_voice() else None
        reaction_id = uuid4().hex
        played = bool(entry and self.audio.play(entry, reaction_id, f"interaction_{event}"))
        if animated:
            self.pet.bubbles.show(cue.expression, now, 5.0)
            active = self.pet.bubbles.active
            active.update(id=reaction_id, context_text=text, text=text)
            if played:
                active.update(text=f"{text}\n{entry['text']}", voice_id=entry["id"],
                              dur=max(5.0, float(entry["duration"]) + 0.5))
            self._last_reaction = active
        if played:
            self._interaction_voice_id = reaction_id
            self.interaction_voice.emit(entry["text"])
        return played

    def care_for_key(self, action):
        result = self.interaction.care(action)
        self.interaction_changed.emit()
        points = self.interaction.status()[0]
        self._interaction_feedback("rest" if action == "rest" else "food", result[0],
                                   previous_points=points - result[1])
        return result

    def give_kei_gift(self, gift_id):
        result = self.interaction.give(gift_id)
        self.interaction_changed.emit()
        points = self.interaction.status()[0]
        self._interaction_feedback("gift", result[0], previous_points=points - result[1])
        return result

    def replay_interaction_milestone(self):
        if self.interaction.status()[0] < 70:
            return False
        return self._interaction_feedback("bonded_replay", "默契纪念语音", replay=True)

    def view_interaction_reward(self, pack=None):
        if pack is None:
            gift = self.interaction.today_gift()
            if gift is None:
                return
            day = datetime.fromtimestamp(self.wall_clock()).date()
            pack = f"daily:{day.isoformat()}"
            text = f"今日礼物 · {GIFTS[gift].name}"
        else:
            if not self.interaction.reward_cards(pack):
                return
            text = "这一场的礼物牌"
        if pack not in self._viewed_rewards:
            self._viewed_rewards.add(pack)
            self._interaction_feedback("reward", text)

    def finish_interaction_game(self, kind, match_id, *, outcome=None, summary="游戏完成"):
        pack, points, created = self.interaction.settle_game(kind, match_id)
        if created:
            self.interaction_changed.emit()
            result_cue = outcome if kind == "rps" and outcome in ("player_win", "kei_win") else (
                "draw" if kind == "rps" else kind)
            total = self.interaction.status()[0]
            self._interaction_feedback(result_cue, summary, previous_points=total - points)
        return pack, points

    def reveal_gift(self, pack, slot):
        result = self.interaction.reveal(pack, slot)
        self.interaction_changed.emit()
        return result

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

    def add_reminder(self, title, due_at, time_mode="absolute"):
        result = self.reminders.add(title, due_at, time_mode)
        self.changed.emit()
        return result

    def edit_reminder(self, reminder_id, title, due_at=None, *, time_mode=None, rearm=False):
        result = self.reminders.edit(reminder_id, title, due_at, time_mode=time_mode, rearm=rearm)
        self.changed.emit()
        return result

    def delete_reminder(self, reminder_id):
        self.reminders.delete(reminder_id)
        self.changed.emit()

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

    def _show_calendar(self, event, explicit=True, startup=False):
        library = self.audio.library
        entry = library.source(STARTUP_VOICES[event["event_key"]]) if startup else None
        if not entry or not entry.get("text") or entry.get("grade") not in ("A", "B"):
            entry = library.choose(event["topic"], self.settings, manual=True)
            if entry and entry.get("grade") not in ("A", "B"):
                entry = None
        if startup and entry is None:
            entry = library.choose("greeting", self.settings)
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

    def check_shortcut(self, shortcut_id):
        return self.shortcuts.check(shortcut_id)

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
        parts = ["KEI Assistant"]
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
        if self.interaction_window:
            self.interaction_window.close()
        if self.panel:
            self.panel.hide()
        if self.notification_window:
            self.notification_window.hide()
        self.storage.close()
