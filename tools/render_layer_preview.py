"""Render each layer over a checkerboard for visual verification."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
LAY = ROOT / "assets" / "layers"


def checker(w, h, cell=16):
    bg = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(bg)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if (x // cell + y // cell) % 2:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(200, 200, 200))
    return bg.convert("RGBA")


pet = Image.open(LAY / "pet.png")
w, h = pet.size

# all three layers stacked exactly as the app will draw them (idle pose)
comp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
for name in ("halo", "body", "head"):   # halo behind, then body, head on top
    comp.alpha_composite(Image.open(LAY / (name + ".png")))
bg = checker(w, h)
bg.alpha_composite(comp)
bg.save(ROOT / "assets" / "raw" / "_debug_stack.png")

# head layer alone + blink overlay (what a blink looks like)
head = Image.open(LAY + r"\head.png").copy()
head.alpha_composite(Image.open(LAY + r"\blink.png"))
b2 = checker(w, h)
b2.alpha_composite(head)
b2.crop((120, 150, 330, 300)).resize((630, 450), Image.NEAREST).convert("RGB")\
    .save(ROOT / "assets" / "raw" / "_debug_blink.png")
print("saved _debug_stack.png / _debug_blink.png")
