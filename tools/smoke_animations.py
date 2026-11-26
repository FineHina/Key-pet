"""Native smoke test for all authored clips without desktop input automation."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pet.window import PetWindow

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
window = PetWindow()
window.show()
initial_position = window.pos()
clips = list(window.scene.animations)
results = []
index = 0


def next_clip():
    global index
    if index >= len(clips):
        print("clips:", results)
        print("position unchanged:", window.pos() == initial_position)
        print("cache MiB:", round(window.scene.animations.cache_bytes / 1024 / 1024, 2))
        window.tray.hide()
        window.hide()
        app.quit()
        return
    name = clips[index]
    index += 1
    now = time.monotonic()
    window.behaviors.preview_animation(name, now=now)
    sample = window.scene.current_animation_frame(now + 0.1, window.state)
    alpha = sample[1]
    ys, xs = (alpha > 10).nonzero()
    results.append((name, sample[3], int((alpha > 10).sum()),
                    (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))))
    QTimer.singleShot(350, next_clip)


QTimer.singleShot(100, next_clip)
sys.exit(app.exec())
