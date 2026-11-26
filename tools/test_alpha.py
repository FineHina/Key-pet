"""Regression checks for gray knees, background holes, and layer hit-testing."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_assets", ROOT / "tools" / "prepare_assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class AlphaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = np.array(Image.open(ROOT / "assets" / "raw" / "chibi.png").convert("RGBA"))
        cls.alpha = assets.alpha_from_bg(cls.raw.astype(np.int16))

    def test_both_knees_are_opaque(self):
        for x, y in ((202, 473), (264, 474)):
            with self.subTest(x=x, y=y):
                self.assertEqual(int(self.alpha[y, x]), 255)

    def test_entire_gray_knee_regions_are_preserved(self):
        gray = np.abs(self.raw[:, :, :3].astype(np.int16) - [98, 97, 96]).sum(axis=2) < assets.BG_TOL
        labels, _ = assets.ndimage.label(gray)
        for x, y in ((202, 473), (264, 474)):
            mask = labels == labels[y, x]
            with self.subTest(x=x, y=y):
                self.assertGreater(int(mask.sum()), 100)
                self.assertTrue(np.all(self.alpha[mask] == 255))

    def test_background_and_leg_gap_stay_transparent(self):
        for x, y in ((0, 0), (10, 500), (233, 475), (233, 515)):
            with self.subTest(x=x, y=y):
                self.assertEqual(int(self.alpha[y, x]), 0)

    def test_enclosed_halo_background_stays_transparent(self):
        self.assertEqual(int(self.alpha[90, 230]), 0)

    def test_exported_body_and_hit_map_match(self):
        for name in ("pet", "body"):
            exported = np.array(Image.open(ROOT / "assets" / "layers" / f"{name}.png"))
            with self.subTest(layer=name):
                self.assertEqual(exported.shape, self.raw.shape)
                np.testing.assert_array_equal(exported[:, :, :3], self.raw[:, :, :3])
                for x, y in ((202, 473), (264, 474)):
                    self.assertEqual(int(exported[y, x, 3]), 255)
                for x, y in ((233, 475), (233, 515)):
                    self.assertEqual(int(exported[y, x, 3]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
