"""Simulate a drag on the pet and verify position persistence."""
import ctypes
import json
import time
from pathlib import Path
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]

u = ctypes.windll.user32
PX, PY = 1700, 990

# if the tray flyout is open, a click over the pet will dismiss it first
u.SetCursorPos(PX, PY)
time.sleep(0.25)
u.mouse_event(2, 0, 0, 0, 0)     # LEFTDOWN on the pet
# drag to the left-middle of the screen in small steps
for i in range(1, 13):
    x = PX - int(i * 18)
    y = PY - int(i * 14)
    u.SetCursorPos(x, y)
    time.sleep(0.03)
u.mouse_event(4, 0, 0, 0, 0)     # LEFTUP
time.sleep(0.5)

cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
print("config after drag:", cfg)

img = ImageGrab.grab()
w, h = img.size
cx = min(max(0, cfg.get("x") or 0) + 130, max(0, w - 400))
cy = min(max(0, cfg.get("y") or 0), max(0, h - 460))
img.crop((max(0, cx - 130), max(0, cy), min(w, cx + 400), min(h, cy + 460))).save(
    ROOT / "assets" / "raw" / "_qa_drag.png")
print("drag frame saved")
