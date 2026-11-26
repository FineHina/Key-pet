"""Render original-art reaction cards and retained authored-animation overview."""
import os
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from pet.bubbles import BubbleManager
from pet.expressions import Expressions, LABELS
from pet.reaction_card import ReactionCard
from pet.scene import PetScene

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets/previews"


def pil_image(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                           "raw", "RGBA", rgba.bytesPerLine())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    pet = QWidget()
    pet.setGeometry(600, 300, 250, 400)
    pet.show()
    bubbles = BubbleManager(Expressions(rng=random.Random(7)))
    card = ReactionCard(pet, bubbles.expressions)
    sheet = Image.new("RGB", (4 * 330, 2 * 430), "#edf0f6")
    for i, kind in enumerate(LABELS):
        bubbles.show(kind, 100)
        card.sync(bubbles.active, 101, .42)
        image = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        card.render(image)
        preview = pil_image(image)
        preview.save(OUT / f"original_card_{kind}.png")
        preview.thumbnail((320, 420), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", preview.size, "#edf0f6")
        background.alpha_composite(preview)
        sheet.paste(background.convert("RGB"), ((i % 4) * 330 + 5, (i // 4) * 430 + 5))
    sheet.save(OUT / "original_reactions_overview.png")
    card.hide()
    pet.hide()
    scene = PetScene()
    width, height = scene.size_for(.42)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    scene.paint(painter, 1.3, {}, .42)
    painter.end()
    image.save(str(OUT / "qa_idle.png"))
    overview = Image.new("RGB", (1784, 1280), "#edf0f6")
    for row, name in enumerate(("idle_2", "idle_1", "action_1", "walk_in_place")):
        source = ROOT / "assets/animations" / f"{name}_contact.png"
        if source.exists():
            contact = Image.open(source).convert("RGB")
            contact.thumbnail((1784, 300), Image.Resampling.LANCZOS)
            overview.paste(contact, (0, row * 320))
    overview.save(OUT / "animations_overview.png")
    print(f"Rendered 8 original-art reaction cards, original Q-head idle and authored-animation overview in {OUT}")


if __name__ == "__main__":
    main()
