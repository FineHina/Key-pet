"""Inspect dark/pink components in authored animation halo regions."""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
for name in ("action_1/frame_0061.png", "action_1/frame_0122.png", "action_1/frame_0000.png"):
    path = ROOT / "assets/animations" / name
    a = np.asarray(Image.open(path).convert("RGBA"))
    dark = (a[:, :, 3] > 10) & (a[:, :, :3].max(axis=2) < 40)
    pink = ((a[:, :, 0].astype(int) - a[:, :, 1].astype(int) > 25)
            & (a[:, :, 0] > 120) & (a[:, :, 3] > 10))
    print(name)
    for label, mask in (("dark", dark), ("pink", pink)):
        labels, count = ndimage.label(mask)
        out = []
        for i in range(1, count + 1):
            ys, xs = np.where(labels == i)
            if len(xs) >= 20 and ys.min() < 180:
                out.append((len(xs), (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))))
        print(label, sorted(out, reverse=True)[:10])
