"""Native original-art card smoke test; no desktop input or persistence writes."""
import hashlib
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pet.window import PetWindow

ROOT = Path(__file__).resolve().parents[1]
paths = [ROOT / name for name in ("config.json", "assets/layers/head.png", "assets/layers/body.png")]
paths += sorted((ROOT / "assets" / "differentials").glob("CH0335_spr_*.png"))
before = {p: hashlib.sha256(p.read_bytes()).digest() for p in paths}
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
from PySide6.QtCore import Qt
from pet.window import set_click_through

window = PetWindow()
window.setWindowFlag(Qt.WindowTransparentForInput, True)
window._hit.stop()
window._save_cfg = lambda: None
window.move(20, 20)
window.show()
set_click_through(int(window.winId()), True)
window.setAttribute(Qt.WA_TransparentForMouseEvents)
window.mousePressEvent = lambda event: None
window.mouseMoveEvent = lambda event: None
window.mouseReleaseEvent = lambda event: None
window.mouseDoubleClickEvent = lambda event: None
initial_position = None
active = None


def begin():
    global initial_position, active
    initial_position = window.pos()
    window._preview("blush")
    active = window.bubbles.active
    print("Original source image:", active["image"])
    print("Reaction text:", active["text"])
    print("Native card visible:", window.reaction_card.isVisible(), "duration:", active["dur"])
    print("Source image dimensions:", window.reaction_card.image.size().toTuple())
    QTimer.singleShot(5600, finish)


def finish():
    print("Card hidden after five seconds:", not window.reaction_card.isVisible())
    print("Window position unchanged:", window.pos() == initial_position)
    changed = [str(p) for p, digest in before.items() if hashlib.sha256(p.read_bytes()).digest() != digest]
    print("Changed files:", changed)
    print("Final state:", window.bubbles.active, "pressed:", window.gestures.pressed)
    print("Position before/after:", initial_position.toTuple(), window.pos().toTuple())
    window.tray.hide()
    window.hide()
    app.quit()


QTimer.singleShot(250, begin)
sys.exit(app.exec())
