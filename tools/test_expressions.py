"""Original-art grouping, source preservation and unchanged Q-head tests."""
import hashlib
from pathlib import Path
import random
import sys
import tempfile
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from pet.expressions import Expressions, GROUPS, SOURCE_ROOT
from pet.scene import PetScene

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


class ExpressionTests(unittest.TestCase):
    def test_exact_original_groups(self):
        self.assertEqual(GROUPS, {
            "default": (1, 2, 3, 4, 5), "smile": (7, 8, 9, 10), "happy": (14, 15),
            "embarrassed": (12,), "dark_angry": (23,), "angry": (24, 25),
            "blush": (26, 27, 28), "cry": (29, 30, 31),
        })
        self.assertEqual(sum(map(len, GROUPS.values())), 21)

    def test_original_selection_never_leaves_group(self):
        source = Expressions(rng=random.Random(7))
        for kind in GROUPS:
            allowed = set(source.paths(kind))
            for _ in range(12):
                path, error = source.choose(kind)
                self.assertIn(path, allowed)
                self.assertIsNone(error)
                self.assertEqual(path.parent, SOURCE_ROOT)
                self.assertFalse(source.image(path).isNull())

    def test_loading_does_not_modify_source_bytes(self):
        source = Expressions(rng=random.Random(1))
        path = source.paths("blush")[0]
        before = hashlib.sha256(path.read_bytes()).digest()
        image = source.image(path)
        self.assertEqual((image.width(), image.height()), (862, 862))
        self.assertTrue(image.hasAlphaChannel())
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())

    def test_missing_files_are_skipped_and_clear_error_is_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Expressions(root=folder, rng=random.Random(1))
            Image.new("RGBA", (20, 30), (200, 100, 160, 255)).save(Path(folder) / "CH0335_spr_08.png")
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                path, error = source.choose("smile")
                self.assertEqual(path.name, "CH0335_spr_08.png")
                self.assertIsNone(error)
                path, error = source.choose("cry")
                self.assertIsNone(path)
                self.assertIn("哭泣", error)

    def test_unknown_category_has_no_silent_fallback(self):
        with self.assertRaises(ValueError):
            Expressions().choose("invalid")

    def test_source_image_cache_is_bounded(self):
        source = Expressions()
        for kind in GROUPS:
            for path in source.paths(kind):
                source.image(path)
        self.assertLessEqual(len(source._images), 8)

    def test_procedural_head_is_original_for_every_reaction(self):
        class Recorder:
            def __init__(self):
                self.images = []
            def save(self):
                pass
            def restore(self):
                pass
            def setWorldTransform(self, *args):
                pass
            def drawImage(self, target, image):
                self.images.append(image)
        scene = PetScene()
        self.assertFalse(hasattr(scene, "expressions"))
        for kind in GROUPS:
            recorder = Recorder()
            scene.paint(recorder, 1, {"expression": kind, "blink": True,
                        "reaction": {"kind": "tilt", "t0": 0, "dur": 2}}, .42)
            self.assertEqual(len(recorder.images), 3)
            self.assertEqual(recorder.images[-1], scene.head)

    def test_hit_testing_preserved_for_moving_original_head(self):
        scene = PetScene()
        state = {"sway": 5, "hop_t0": 0, "hop_big": True}
        for scale in (.25, .42, 1.6):
            transforms = scene.transforms(.25, state, scale)
            head = transforms["head"].map(QPointF(207 * scale, 120 + 237 * scale))
            knee = transforms["body"].map(QPointF(202 * scale, 120 + 473 * scale))
            self.assertEqual(scene.hit_region(head, .25, state, scale), "head")
            self.assertEqual(scene.hit_region(knee, .25, state, scale), "body")


if __name__ == "__main__":
    unittest.main(verbosity=2)
