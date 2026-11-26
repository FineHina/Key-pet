"""Paired original image/text card tests; no input is sent to the desktop."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget

from pet.bubbles import BubbleManager
from pet.expressions import Expressions, GROUPS
from pet.reaction_card import ReactionCard, card_position

APP = QApplication.instance() or QApplication([])


class ReactionCardTests(unittest.TestCase):
    def setUp(self):
        self.pet = QWidget()
        self.pet.setGeometry(500, 350, 200, 350)
        self.pet.show()
        self.bubbles = BubbleManager(Expressions(rng=random.Random(7)))
        self.card = ReactionCard(self.pet, self.bubbles.expressions)

    def tearDown(self):
        self.card.hide()
        self.pet.hide()
        self.pet.deleteLater()

    def test_image_and_text_are_one_reaction_with_five_second_lifetime(self):
        for kind, ids in GROUPS.items():
            self.bubbles.show(kind, 100)
            active = self.bubbles.active
            self.assertEqual(active["category"], kind)
            self.assertEqual(active["dur"], 5)
            self.assertTrue(active["text"])
            self.assertIn(int(Path(active["image"]).stem.rsplit("_", 1)[1]), ids)
            self.card.sync(active, 104.99)
            self.assertTrue(self.card.isVisible())
            self.assertFalse(self.card.image.isNull())
            self.card.sync(active, 105)
            self.assertFalse(self.card.isVisible())
            self.assertIsNone(self.card.active)

    def test_repeated_reaction_reuses_card_and_replaces_pair(self):
        self.bubbles.show("smile", 0)
        self.card.sync(self.bubbles.active, 0)
        first = self.card.active
        self.bubbles.show("cry", 1)
        self.card.sync(self.bubbles.active, 1)
        self.assertIsNot(self.card.active, first)
        self.assertEqual(self.card.active["category"], "cry")
        self.assertIs(self.card.active, self.bubbles.active)

    def test_card_preserves_aspect_ratio(self):
        self.bubbles.show("happy", 0)
        self.card.sync(self.bubbles.active, 1)
        rect = self.card.image_target()
        self.assertAlmostEqual(rect.width() / rect.height(), self.card.image.width() / self.card.image.height())
        self.assertTrue(self.card._image_rect.contains(rect))

    def test_card_is_smaller_without_reducing_text_size(self):
        self.bubbles.show("happy", 0)
        self.card.sync(self.bubbles.active, 1, .42)
        self.assertEqual(self.card.width(), 240)
        self.assertEqual(self.card.image_target().width(), 108)
        self.assertEqual(self.card.image_target().height(), 108)
        self.assertEqual(self.card._font.pixelSize(), 14)

    def test_card_does_not_take_focus_or_mouse_input(self):
        self.assertEqual(self.card.focusPolicy(), Qt.NoFocus)
        self.assertTrue(self.card.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertTrue(self.card.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(self.card.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(self.card.windowFlags() & Qt.WindowDoesNotAcceptFocus)

    def test_hide_with_pet_and_reposition_without_changing_pet(self):
        self.bubbles.show("blush", 0)
        self.card.sync(self.bubbles.active, 1)
        start = self.card.pos()
        self.pet.move(self.pet.x() - 80, self.pet.y() - 20)
        target_pet_position = self.pet.pos()
        self.card.sync(self.bubbles.active, 2)
        self.assertNotEqual(start, self.card.pos())
        self.assertEqual(target_pet_position, self.pet.pos())
        self.pet.hide()
        self.card.sync(self.bubbles.active, 3)
        self.assertFalse(self.card.isVisible())

    def test_card_is_centered_above_pet(self):
        pet_rect = QRect(800, 500, 200, 300)
        position = card_position(pet_rect, QRect(0, 0, 1920, 1080), (240, 200))
        card = QRect(position.x(), position.y(), 240, 200)
        self.assertLessEqual(abs(card.center().x() - pet_rect.center().x()), 1)
        self.assertEqual(card.bottom(), pet_rect.top() - 13)

    def test_top_edge_uses_below_instead_of_side(self):
        pet_rect = QRect(800, 10, 200, 300)
        position = card_position(pet_rect, QRect(0, 0, 1920, 1080), (240, 200))
        card = QRect(position.x(), position.y(), 240, 200)
        self.assertEqual(card.top(), pet_rect.bottom() + 13)
        self.assertLessEqual(abs(card.center().x() - pet_rect.center().x()), 1)

    def test_above_placement_uses_character_not_invisible_window_margins(self):
        from pet.scene import PetScene
        self.pet.scene = PetScene()
        self.pet.state = {}
        self.bubbles.show("smile", 100)
        self.card.sync(self.bubbles.active, 101, .42)
        bounds = self.card.character_rect(101, .42)
        self.assertGreater(bounds.top(), self.pet.y())
        self.assertLessEqual(abs(self.card.frameGeometry().center().x() - bounds.center().x()), 1)
        self.assertEqual(self.card.frameGeometry().bottom(), bounds.top() - 13)

    def test_position_clamped_at_all_screen_edges_and_negative_screen(self):
        for available in (QRect(0, 0, 1920, 1080), QRect(-1280, -80, 1280, 800)):
            for x in (available.left(), available.right() - 200):
                for y in (available.top(), available.bottom() - 350):
                    position = card_position(QRect(x, y, 200, 350), available, (320, 400))
                    self.assertTrue(available.contains(QRect(position.x(), position.y(), 320, 400)))

    def test_missing_image_still_shows_explanation_and_text(self):
        active = {"category": "cry", "image": None, "text": "呜……", "error": "差分素材缺失",
                  "t0": 0, "dur": 5}
        self.card.sync(active, 1)
        self.assertTrue(self.card.isVisible())
        self.assertIn("呜……", self.card.display_text())
        self.assertIn("差分素材缺失", self.card.display_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
