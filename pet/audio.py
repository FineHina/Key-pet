"""Curated automatic voices and full manual catalog, using unchanged local files."""
from collections import deque
import json
from pathlib import Path
import random
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

ROOT = Path(__file__).resolve().parents[1]


class VoiceLibrary:
    def __init__(self, path=None, rng=None):
        self.path = Path(path) if path is not None else ROOT / "voice_manifest.json"
        self.root = self.path.parent
        self.rng = rng if rng is not None else random.Random()
        self.sources, self.scenes = {}, {}
        self.recent = deque(maxlen=4)
        self.error = None
        try:
            manifest = json.loads(self.path.read_text(encoding="utf-8"))
            if manifest.get("version") != 1:
                raise ValueError("unsupported voice manifest")
            self.sources = manifest["sources"]
            self.scenes = manifest["scenes"]
        except (OSError, ValueError, KeyError) as error:
            self.error = f"语音清单无法加载：{error}"

    def source(self, voice_id):
        entry = self.sources.get(voice_id)
        if entry is None:
            entry = next((item for item in self.sources.values() if item.get("alias") == voice_id), None)
        if not entry:
            return None
        path = (self.root / entry["file"]).resolve()
        if not path.is_relative_to((self.root / "voices").resolve()):
            return None
        if not path.is_file() or not entry.get("decodable", False):
            return None
        return dict(entry, path=path)

    def choose(self, scene, settings, manual=False):
        spec = self.scenes.get(scene)
        if (scene in ("dark_angry", "cry") or not spec or not spec.get("enabled", True)
                or (not manual and not spec.get("automatic", True)
                    and not (scene == "idle" and settings.get("idle_voice", False)))):
            return None
        candidates = [source for voice_id in spec["sources"] if (source := self.source(voice_id))
                      and source.get("text") and (manual or source.get("grade", "A") == "A")]
        if not candidates:
            return None
        fresh = [entry for entry in candidates if entry["id"] not in self.recent]
        if not fresh and self.recent:
            fresh = [entry for entry in candidates if entry["id"] != self.recent[-1]]
        chosen = self.rng.choice(fresh or candidates)
        return dict(chosen, category=spec["category"], cooldown=float(spec.get("cooldown", 3)))

    def mark_played(self, voice_id):
        self.recent.append(voice_id)

    def entries(self):
        return [dict(entry, label=f"{entry.get('alias', voice_id)} · {entry.get('text', entry.get('summary', ''))[:28]}",
                     available=self.source(voice_id) is not None)
                for voice_id, entry in self.sources.items()]


class AudioService(QObject):
    changed = Signal()

    def __init__(self, policy, library=None, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.policy = policy
        self.library = library if library is not None else VoiceLibrary()
        self.clock = clock
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(self._error)
        self.player.mediaStatusChanged.connect(self._status)
        self.current_id = None
        self.current_reaction = None
        self.current_priority = 0
        self.current_until = 0.0
        self._generation = 0
        self.last_started = -float("inf")
        self.last_by_scene = {}
        self.error = self.library.error
        self.apply_settings()

    def apply_settings(self):
        self.output.setVolume(self.policy.settings.get("voice_volume", 35) / 100)
        self.output.setMuted(bool(self.policy.settings.get("mute", False)))
        if not self.policy.can_voice():
            self.stop()

    def stop(self, force=True):
        if not force and self.current_priority > 0 and self.clock() < self.current_until:
            return False
        self._generation += 1
        self.current_id = None
        self.current_reaction = None
        self.current_priority = 0
        self.player.stop()
        self.changed.emit()
        return True

    def _source_matches(self):
        if self.current_id is None:
            return False
        entry = self.library.source(self.current_id)
        return entry is not None and self.player.source() == QUrl.fromLocalFile(str(entry["path"]))

    def _error(self, error, message):
        if self.current_id is not None and not self._source_matches():
            return
        self.error = f"语音播放失败：{message}"
        self.current_id = None
        self.current_reaction = None
        self.current_priority = 0
        self.changed.emit()

    def _status(self, status):
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            if not self._source_matches():
                return
            self.current_id = None
            self.current_reaction = None
            self.current_priority = 0
            self.changed.emit()

    def play(self, entry, reaction_id, scene, manual=False, priority=False):
        now = self.clock()
        if not entry or not self.policy.can_voice():
            return False
        level = 2 if manual else 1 if priority else 0
        if (self.current_id is not None and self.current_priority > level
                and now < self.current_until):
            return False
        if not manual and not priority:
            if now - self.last_started < 3 or now - self.last_by_scene.get(scene, -float("inf")) < entry.get("cooldown", 3):
                return False
        self.player.stop()
        self._generation += 1
        self.error = None
        self.current_id = entry["id"]
        self.current_reaction = reaction_id
        self.current_priority = level
        self.current_until = now + float(entry.get("duration", 0)) + 1.0
        self.output.setVolume(self.policy.settings.get("voice_volume", 35) / 100)
        self.player.setSource(QUrl.fromLocalFile(str(entry["path"])))
        self.player.play()
        self.last_started = now
        self.last_by_scene[scene] = now
        self.library.mark_played(entry["id"])
        self.changed.emit()
        return True

    def audition(self, voice_id):
        entry = self.library.source(voice_id)
        if entry is None:
            raise ValueError("语音文件缺失或无法解码。")
        if not self.policy.can_voice():
            raise ValueError("请先关闭静音/勿扰并启用互动语音，再试听。")
        return self.play(entry, uuid4().hex, "audition", manual=True)

    def respond(self, active, scene, explicit=True, priority=False, manual=False):
        if active is None:
            return
        reaction_id = active.setdefault("id", uuid4().hex)
        if not self.policy.can_react(explicit):
            return
        entry = self.library.choose(scene, self.policy.settings, manual=manual)
        if entry and self.play(entry, reaction_id, scene, manual=manual, priority=priority):
            text = entry.get("text", "")
            if text:
                context = active.get("context_text")
                active["text"] = f"{context}\n{text}" if context else text
            active["voice_id"] = entry["id"]
            active["dur"] = max(active["dur"], float(entry.get("duration", 0)) + .5)

    def shutdown(self):
        self.stop()
        self.player.setSource(QUrl())
