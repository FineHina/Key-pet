"""Inspect connected mouth-colored components in generated expression frames."""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
for name in (
    "assets/layers/head.png", "assets/expressions/happy_0_open.png",
    "assets/expressions/smile_0_open.png", "assets/expressions/blush_0_open.png",
):
    pixels = np.asarray(Image.open(ROOT / name).convert("RGBA")).astype(np.int16)
    r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    region = np.zeros(r.shape, dtype=bool)
    region[248:279, 235:286] = True
    colored = region & (r - b > 8) & (r - g > 3) & (r < 235)
    labels, count = ndimage.label(colored)
    print(name)
    for component in range(1, count + 1):
        ys, xs = np.where(labels == component)
        if len(xs) >= 2:
            print(" ", len(xs), (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                  "median", np.median(pixels[ys, xs, :3], axis=0).astype(int).tolist())
