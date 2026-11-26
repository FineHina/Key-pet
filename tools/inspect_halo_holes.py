from pathlib import Path
from PIL import Image
import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1] / "assets/animations"
for name in ("action_1/frame_0000.png", "action_1/frame_0061.png", "action_1/frame_0122.png",
             "walk_in_place/frame_0027.png"):
    a = np.asarray(Image.open(ROOT / name).convert("RGBA"))
    upper = np.indices(a.shape[:2])[0] < 220
    pink = (upper & (a[:, :, 3] > 10) & (a[:, :, 0].astype(int) - a[:, :, 1].astype(int) > 20)
            & (a[:, :, 0] > 110))
    labels, count = ndimage.label(pink)
    sizes = ndimage.sum(pink, labels, range(1, count + 1))
    main = labels == (int(np.argmax(sizes)) + 1)
    structure = ndimage.generate_binary_structure(2, 2)
    sealed = ndimage.binary_closing(ndimage.binary_dilation(main, structure, iterations=2), structure, iterations=4)
    filled = ndimage.binary_fill_holes(sealed)
    holes = filled & ~ndimage.binary_dilation(main, structure, iterations=2)
    hole_labels, hole_count = ndimage.label(holes)
    comps = []
    for i in range(1, hole_count + 1):
        ys, xs = np.where(hole_labels == i)
        if len(xs) >= 20:
            black = ((a[:, :, 3] > 10) & (a[:, :, :3].max(axis=2) < 60) & (hole_labels == i)).sum()
            comps.append((len(xs), (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), int(black)))
    print(name, "pink", int(main.sum()), "holes", sorted(comps, reverse=True)[:8])
