"""Brief native integration smoke run using temporary DB/config and muted audio."""
import hashlib
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from pet import config
from pet.window import PetWindow, set_click_through

ROOT = Path(__file__).resolve().parents[1]
checks = [ROOT / "config.json", ROOT / "assets/layers/head.png", ROOT / "assets/layers/body.png"]
checks += list((ROOT / "voices").glob("*.ogg"))
original = {path: hashlib.sha256(path.read_bytes()).digest() for path in checks}
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
assistant.open_panel()
assistant.start_focus(.02)
assistant.add_reminder("短时测试提醒", time.time() + 1)
results = {}


def halfway():
    results["pending_count"] = len(assistant.pending_notifications())
    results["focus_completed"] = assistant.focus_snapshot() is None
    results["notification_visible"] = assistant.notification_window.isVisible()
    assistant.update_settings({"dnd": True})
    results["dnd_hides_notification"] = not assistant.notification_window.isVisible()
    assistant.add_reminder("勿扰期间测试", time.time() + .5)
    window.hide()
    QTimer.singleShot(900, finish)


def finish():
    assistant.tick()
    results["hidden_service_pending"] = len(assistant.pending_notifications())
    results["audio_silent"] = assistant.audio.current_id is None
    results["source_files_unchanged"] = all(hashlib.sha256(p.read_bytes()).digest() == h for p, h in original.items())
    print(results)
    assistant.shutdown()
    window.tray.hide()
    window.hide()
    app.quit()


QTimer.singleShot(1800, halfway)
try:
    app.exec()
finally:
    assistant.shutdown()
    config_patch.stop()
    temp.cleanup()
if results.get("pending_count") != 2 or results.get("hidden_service_pending") != 3:
    raise SystemExit(1)
