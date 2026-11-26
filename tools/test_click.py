"""Simulate a click on the pet and screenshot the reaction (hop + bubble)."""
import ctypes
import time
from pathlib import Path
from PIL import ImageGrab

OUT = Path(__file__).resolve().parents[1] / "assets" / "raw"

u = ctypes.windll.user32
u.SetCursorPos(1700, 990)          # over the pet's torso
time.sleep(0.25)                   # let the hit-test poll enable the window
u.mouse_event(2, 0, 0, 0, 0)       # LEFTDOWN
u.mouse_event(4, 0, 0, 0, 0)       # LEFTUP
time.sleep(0.45)                   # mid-hop + bubble fade-in
ImageGrab.grab().crop((1400, 560, 1920, 1080)).save(OUT / "_qa_click1.png")
time.sleep(1.3)
ImageGrab.grab().crop((1400, 560, 1920, 1080)).save(OUT / "_qa_click2.png")
print("clicked, two frames saved")
