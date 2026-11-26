from pathlib import Path
from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
coords = [(236, 265), (237, 265), (245, 264), (246, 264), (247, 264), (248, 264),
          (245, 265), (246, 265), (247, 265), (248, 265)]
for name in ("assets/layers/head.png", "assets/expressions/neutral_head.png",
             "assets/expressions/happy_0_open.png", "assets/expressions/happy_1_open.png",
             "assets/expressions/smile_0_open.png"):
    pixels = np.asarray(Image.open(ROOT / name))
    print(name, [(point, pixels[point[1], point[0], :3].tolist()) for point in coords])
