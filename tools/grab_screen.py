"""Full-screen grab, cropped to the bottom-right where the pet spawns."""
from pathlib import Path
from PIL import ImageGrab

OUT = Path(__file__).resolve().parents[1] / "assets" / "raw"

img = ImageGrab.grab()
w, h = img.size
img.save(OUT / "_qa_live_full.png")
img.crop((max(0, w - 520), max(0, h - 520), w, h)).save(OUT / "_qa_live_crop.png")
print("screen size:", w, h)
