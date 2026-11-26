"""Asset-level verification for generated animation clips."""
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "assets/animations"
manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
print("clip frames corner_alpha center_x_range feet_y_range bad_frames")
for name, clip in manifest["clips"].items():
    centers, feet, corners, bad = [], [], [], []
    for frame in clip["frames"]:
        pixels = np.asarray(Image.open(ROOT / frame["file"]).convert("RGBA"))
        if pixels.shape != (602, 446, 4):
            bad.append(frame["file"])
            continue
        ys, xs = np.where(pixels[:, :, 3] > 10)
        centers.append((xs.min() + xs.max()) / 2)
        feet.append(int(ys.max()))
        corners.extend((int(pixels[0, 0, 3]), int(pixels[0, -1, 3]),
                        int(pixels[-1, 0, 3]), int(pixels[-1, -1, 3])))
    print(name, len(clip["frames"]), max(corners),
          [round(min(centers), 1), round(max(centers), 1)],
          [min(feet), max(feet)], len(bad))
