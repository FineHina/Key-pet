"""Runtime animation manifest, sampling, cache, scene paint, and hit tests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from pet.animations import AnimationLibrary
from pet.scene import BUBBLE_BAND, PetScene

APP = QApplication.instance() or QApplication([])


class Recorder:
    def __init__(self):
        self.images = []
        self.paths = []
        self.texts = []

    def save(self):
        pass

    def restore(self):
        pass

    def setWorldTransform(self, *args):
        pass

    def drawImage(self, target, image):
        self.images.append(image)

    def setOpacity(self, *args):
        pass

    def setFont(self, *args):
        pass

    def setPen(self, *args):
        pass

    def setBrush(self, *args):
        pass

    def drawPath(self, path):
        self.paths.append(path)

    def drawText(self, *args):
        self.texts.append(args)


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._frame("frame0.png", QColor(240, 20, 20, 255), (20, 30, 45, 55))
        self._frame("frame1.png", QColor(20, 240, 20, 255), (80, 320, 105, 345))
        self._mask("head.png", (20, 30, 35, 45))
        self.manifest = {
            "version": 1,
            "canvas": [446, 602],
            "clips": {
                "wave": {
                    "label": "挥手", "mode": "once", "idle": False,
                    "anchor": [223, 602],
                    "frames": [
                        {"file": "frame0.png", "duration": 0.1, "head_mask": "head.png"},
                        {"file": "frame1.png", "duration": 0.2},
                    ],
                },
                "breathe": {
                    "label": "呼吸", "mode": "loop", "idle": True,
                    "anchor": [223, 602],
                    "frames": [
                        {"file": "frame0.png", "duration": 0.1},
                        {"file": "frame1.png", "duration": 0.2},
                    ],
                },
            },
        }
        self.path = self.root / "manifest.json"
        self._write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def _write_manifest(self):
        self.path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")

    def _frame(self, name, color, rect):
        image = QImage(446, 602, QImage.Format.Format_RGBA8888)
        image.fill(0)
        x0, y0, x1, y1 = rect
        for y in range(y0, y1):
            for x in range(x0, x1):
                image.setPixelColor(x, y, color)
        self.assertTrue(image.save(str(self.root / name), "PNG"))

    def _mask(self, name, rect):
        image = QImage(446, 602, QImage.Format.Format_Grayscale8)
        image.fill(0)
        x0, y0, x1, y1 = rect
        for y in range(y0, y1):
            for x in range(x0, x1):
                image.setPixelColor(x, y, QColor(255, 255, 255))
        self.assertTrue(image.save(str(self.root / name), "PNG"))

    def test_manifest_metadata_and_frame_boundaries(self):
        library = AnimationLibrary(self.path)
        self.assertEqual(tuple(library), ("wave", "breathe"))
        self.assertEqual(tuple(library.clips), ("wave", "breathe"))
        self.assertEqual(library.idle_names, ("breathe",))
        clip = library["wave"]
        first = clip.sample(0)
        self.assertEqual(first[3:], (0, False))
        self.assertEqual(clip.sample(0.099)[3:], (0, False))
        self.assertEqual(clip.sample(0.1)[3:], (1, False))
        self.assertEqual(clip.sample(0.299)[3:], (1, False))
        self.assertEqual(clip.sample(0.3)[3:], (1, True))
        self.assertEqual(library["breathe"].sample(0.3)[3:], (0, False))
        self.assertAlmostEqual(clip.duration, 0.3)
        self.assertEqual(first[1].shape, (602, 446))
        self.assertEqual(first[2].shape, (602, 446))

    def test_loading_is_lazy_and_lru_is_bounded(self):
        library = AnimationLibrary(self.path, cache_limit=3_000_000)
        self.assertEqual(library.cache_bytes, 0)
        image0 = library["wave"].sample(0)[0]
        self.assertGreater(library.cache_bytes, 0)
        self.assertLessEqual(library.cache_bytes, library.cache_limit)
        library["wave"].sample(0.15)
        self.assertLessEqual(library.cache_bytes, library.cache_limit)
        image0_again = library["wave"].sample(0)[0]
        self.assertFalse(image0_again.isNull())
        self.assertLessEqual(library.cache_bytes, library.cache_limit)
        self.assertEqual(image0.size(), image0_again.size())

    def test_invalid_durations_and_decoded_dimensions_fail(self):
        self.manifest["clips"]["wave"]["frames"][0]["duration"] = 0
        self._write_manifest()
        with self.assertRaisesRegex(ValueError, "duration"):
            AnimationLibrary(self.path)
        self.manifest["clips"]["wave"]["frames"][0]["duration"] = 0.1
        bad = QImage(2, 2, QImage.Format.Format_RGBA8888)
        bad.fill(QColor("red"))
        self.assertTrue(bad.save(str(self.root / "frame0.png"), "PNG"))
        self._write_manifest()
        with self.assertRaisesRegex(ValueError, "446x602"):
            AnimationLibrary(self.path)["wave"].sample(0)

    def test_explicit_missing_manifest_fails(self):
        with self.assertRaises(FileNotFoundError):
            AnimationLibrary(self.root / "missing.json")

    def test_scene_frame_hit_regions_and_single_image_then_bubble(self):
        scene = PetScene(animation_manifest=self.path)
        state = {"animation": {"clip": "wave", "t0": 10}, "sway": 0,
                 "dragging": False,
                 "bubble": {"text": "你好", "t0": 10, "dur": 2.4}}
        sample = scene.current_animation_frame(10, state)
        self.assertEqual(sample[3:], (0, False))
        self.assertEqual(scene.hit_region(QPointF(25, BUBBLE_BAND + 35), 10, state, 1), "head")
        self.assertEqual(scene.hit_region(QPointF(40, BUBBLE_BAND + 50), 10, state, 1), "body")
        self.assertIsNone(scene.hit_region(QPointF(200, BUBBLE_BAND + 200), 10, state, 1))
        # The second frame has no head mask; CUT_Y is the fallback classifier.
        self.assertEqual(scene.hit_region(QPointF(85, BUBBLE_BAND + 325), 10.15, state, 1), "body")
        recorder = Recorder()
        scene.paint(recorder, 10.05, state, 1)
        self.assertEqual(len(recorder.images), 1)
        # Text is now rendered only in the original-art card, never twice over the pet.
        self.assertEqual(len(recorder.texts), 0)
        self.assertEqual(len(recorder.paths), 0)
        # During an actual drag, authored frames may use only whole-pet sway.
        state["dragging"] = True
        state["sway"] = 7
        recorder = Recorder()
        scene.paint(recorder, 10.05, state, 1)
        self.assertEqual(len(recorder.images), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
