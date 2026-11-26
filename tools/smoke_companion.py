"""Native companion-page check with temporary data, muted audio and fake opener."""
from datetime import datetime
import hashlib
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from pet import config
from pet.window import PetWindow, set_click_through

ROOT = Path(__file__).resolve().parents[1]
paths = [ROOT / "config.json", ROOT / "assets/layers/head.png", ROOT / "assets/layers/body.png"]
paths += list((ROOT / "voices").glob("*.ogg"))
hashes = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths}
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory()
folder = Path(temp.name)
config_patch = patch.object(config, "PATH", str(folder / "config.json"))
config_patch.start()
config.save({"mute": True, "x": 20, "y": 20})
window = PetWindow(assistant_database=folder / "assistant.sqlite3")
window.setWindowFlag(Qt.WindowTransparentForInput, True)
window.show()
window._hit.stop()
window.mousePressEvent = lambda event: None
window.mouseMoveEvent = lambda event: None
window.mouseReleaseEvent = lambda event: None
window.mouseDoubleClickEvent = lambda event: None
set_click_through(int(window.winId()), True)
assistant = window.assistant
opened = []
assistant.shortcuts.opener = lambda kind, target: opened.append((kind, target)) or True
file = folder / "sample.txt"
file.write_text("untouched", encoding="utf-8")
shortcut = assistant.add_shortcut("测试笔记", "file", str(file))
assistant.open_panel("陪伴对话")
reply = assistant.chat("我想看看星空")
assistant.settings.update(birthday_mmdd="12-25", calendar_events_enabled=True)
assistant.calendar.wall_clock = lambda: datetime(2026, 12, 25, 12).timestamp()
event = assistant.trigger_today_event()
assistant.open_shortcut(shortcut["id"])


def finish():
    print("Tabs:", [assistant.panel.tabs.tabText(i) for i in range(assistant.panel.tabs.count())])
    print("Dialogue topic:", reply["topic"], "Chinese:", bool(reply["text"]), "original image:", bool(reply["image"]))
    print("Birthday event:", event["topic"], "audio silent:", assistant.audio.current_id is None)
    print("Only fake opener called:", opened, "file unchanged:", file.read_text(encoding="utf-8") == "untouched")
    print("Source files unchanged:", all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in hashes.items()))
    assistant.shutdown()
    window.tray.hide()
    window.hide()
    app.quit()


QTimer.singleShot(1200, finish)
try:
    app.exec()
finally:
    assistant.shutdown()
    config_patch.stop()
    temp.cleanup()
