"""Read-only source-art selection. These images never replace the Q-style head."""
from collections import OrderedDict
from pathlib import Path
import random
import warnings

from PySide6.QtGui import QImage

LABELS = {
    "default": "默认", "smile": "微笑", "happy": "开心大笑",
    "embarrassed": "不好意思", "dark_angry": "生气黑脸",
    "angry": "生气", "blush": "害羞", "cry": "哭泣",
}
GROUPS = {
    "default": (1, 2, 3, 4, 5), "smile": (7, 8, 9, 10), "happy": (14, 15),
    "embarrassed": (12,), "dark_angry": (23,), "angry": (24, 25),
    "blush": (26, 27, 28), "cry": (29, 30, 31),
}
_REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = _REPO_ROOT / "assets" / "differentials"


class Expressions:
    def __init__(self, root=None, rng=None):
        self.root = Path(root) if root is not None else SOURCE_ROOT
        self.rng = rng if rng is not None else random.Random()
        self._images = OrderedDict()
        self._warned = set()

    def paths(self, kind):
        if kind not in GROUPS:
            raise ValueError(f"unknown expression: {kind}")
        return tuple(self.root / f"CH0335_spr_{index:02d}.png" for index in GROUPS[kind])

    def image(self, path):
        path = Path(path)
        cached = self._images.pop(path, None)
        if cached is not None:
            self._images[path] = cached
            return cached
        image = QImage(str(path))
        if image.isNull():
            if path not in self._warned:
                warnings.warn(f"原立绘差分无法读取：{path}", RuntimeWarning, stacklevel=2)
                self._warned.add(path)
            return image
        self._images[path] = image
        # Only a handful of original 862px images need to remain decoded.
        while len(self._images) > 8:
            self._images.popitem(last=False)
        return image

    def choose(self, kind):
        candidates = list(self.paths(kind))
        self.rng.shuffle(candidates)
        for path in candidates:
            if not self.image(path).isNull():
                return path, None
        return None, f"缺少“{LABELS[kind]}”原立绘，请检查差分素材目录。"
