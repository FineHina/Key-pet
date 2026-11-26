"""Inspect the chibi image: size, background color, grid overlay for coordinate picking."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "assets" / "raw"
RAW = ROOT / "chibi.png"
OUT = ROOT / "_debug_grid.png"

img = Image.open(RAW).convert("RGBA")
a = np.array(img)
h, w = a.shape[:2]
print(f"size: {w}x{h}")

# background estimate: median of the 2px border ring
border = np.concatenate([a[:2, :, :3].reshape(-1, 3), a[-2:, :, :3].reshape(-1, 3),
                         a[:, :2, :3].reshape(-1, 3), a[:, -2:, :3].reshape(-1, 3)])
bg = np.median(border, axis=0)
print("border median color:", bg)
print("corners:", a[0, 0, :3], a[0, -1, :3], a[-1, 0, :3], a[-1, -1, :3])
print("alpha channel min/max:", a[:, :, 3].min(), a[:, :, 3].max())

# grid overlay every 50 px with labels
g = img.copy().convert("RGB")
d = ImageDraw.Draw(g)
for x in range(0, w, 50):
    d.line([(x, 0), (x, h)], fill=(255, 0, 0, 255), width=1)
    d.text((x + 2, 2), str(x), fill=(255, 0, 0))
for y in range(0, h, 50):
    d.line([(0, y), (w, y)], fill=(255, 0, 0, 255), width=1)
    d.text((2, y + 2), str(y), fill=(255, 0, 0))
g.save(OUT)
print("grid overlay saved:", OUT)
