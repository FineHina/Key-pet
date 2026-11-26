"""Asset pipeline for the desktop pet.

chibi.png (gray background) -> transparent + layers:
  layers/pet.png       composed pet (for alpha hit-test)
  layers/halo.png      pink window halo (rendered behind head, floats/rotates)
  layers/head.png      head + hair above the collar cut line
  layers/body.png      the rest
  layers/blink.png     closed-eyes overlay (drawn over head while blinking)
  layers/eyes.json     eye bounding boxes (full-res coords)
  layers/tray.png      64x64 tray icon (face crop)

Debug outputs (assets/raw/_debug_*.png) for visual verification.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "assets" / "raw" / "chibi.png"
OUT = ROOT / "assets" / "layers"

BG_TOL = 45          # sum-abs distance below which a pixel counts as gray background
CUT_Y = 280          # head/body cut line (collar), feathered
FEATHER = 7          # feather half-width around CUT_Y
HALO_Y_MAX = 160     # halo lives above this row
FACE_BOX = (150, 195, 300, 265)   # x0, y0, x1, y1 — eye search region


def load():
    img = Image.open(RAW).convert("RGBA")
    a = np.array(img).astype(np.int16)
    return img, a


def alpha_from_bg(a):
    """Remove edge-connected gray background, preserving gray character details."""
    bg = np.array([98, 97, 96])
    dist = np.abs(a[:, :, :3] - bg).sum(axis=2)
    gray = dist < BG_TOL
    seeds = np.zeros_like(gray)
    seeds[0, :] = gray[0, :]
    seeds[-1, :] = gray[-1, :]
    seeds[:, 0] = gray[:, 0]
    seeds[:, -1] = gray[:, -1]
    # The window-shaped halo encloses real background holes above the head.
    seeds[:HALO_Y_MAX, :] |= gray[:HALO_Y_MAX, :]
    background = ndimage.binary_propagation(seeds, mask=gray)
    solid = ~background
    # drop tiny noise islands (antialiasing specks far from the character)
    lbl, n = ndimage.label(solid)
    sizes = ndimage.sum(solid, lbl, range(1, n + 1))
    keep = np.isin(lbl, np.where(sizes >= 40)[0] + 1)
    # soften the outer silhouette, not the preserved gray details inside it
    near_bg = keep & ndimage.binary_dilation(~keep)
    alpha = np.where(keep, 255, 0).astype(np.float32)
    edge = np.clip(dist / (BG_TOL * 2.2) * 255, 0, 255)
    alpha[near_bg] = np.minimum(alpha[near_bg], edge[near_bg])
    alpha = ndimage.gaussian_filter(alpha, 0.6)
    return alpha


def feather_cut(alpha):
    """Split alpha at CUT_Y with a soft overlap band (head fades out, body fades in)."""
    h = alpha.shape[0]
    ys = np.arange(h).reshape(-1, 1).astype(np.float32)
    head_w = np.clip((CUT_Y + FEATHER - ys) / (2 * FEATHER), 0, 1)
    body_w = 1 - np.clip((CUT_Y - FEATHER - ys) / (2 * FEATHER), 0, 1)
    return alpha * head_w, alpha * body_w


def halo_mask(a):
    """Pink-ish pixels in the top region = the window-frame halo."""
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    pink = (r - g > 35) & (r - b > 20) & (r > 150)
    region = np.zeros_like(pink)
    region[:HALO_Y_MAX, :] = True
    mask = pink & region
    lbl, n = ndimage.label(mask)
    sizes = ndimage.sum(mask, lbl, range(1, n + 1))
    return np.isin(lbl, np.where(sizes >= 30)[0] + 1)


def find_eyes(a):
    """Locate the two eyes inside FACE_BOX: dark lash pixels + pink iris pixels."""
    x0, y0, x1, y1 = FACE_BOX
    sub = a[y0:y1, x0:x1]
    r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    dark = np.max(sub[:, :, :3], axis=2) < 95
    iris = (r - g > 70) & (r > 140)
    mask = dark | iris
    mask = ndimage.binary_closing(mask, iterations=2)
    lbl, n = ndimage.label(mask)
    if n == 0:
        raise SystemExit("no eye components found — check FACE_BOX")
    sizes = ndimage.sum(mask, lbl, range(1, n + 1))
    comps = [i + 1 for i in range(n) if sizes[i] >= 60]
    boxes = []
    for c in comps:
        ys, xs = np.where(lbl == c)
        bx = [int(x0 + xs.min()), int(y0 + ys.min()), int(x0 + xs.max()), int(y0 + ys.max())]
        if bx[2] - bx[0] >= 15 and bx[3] - bx[1] >= 10:   # real eyes are ~30px wide
            boxes.append(bx)
    boxes.sort()
    # merge components whose boxes overlap (lash + iris of same eye)
    merged = []
    for b_ in boxes:
        if merged and b_[0] <= merged[-1][2] - 4:
            m = merged[-1]
            merged[-1] = [min(m[0], b_[0]), min(m[1], b_[1]), max(m[2], b_[2]), max(m[3], b_[3])]
        else:
            merged.append(b_)
    if len(merged) != 2:
        print("WARN: expected 2 eyes, got", len(merged), merged)
    return merged


def make_blink(a, eyes):
    """Overlay image with closed lids painted over both eyes (full-res canvas)."""
    canvas = np.zeros((a.shape[0], a.shape[1], 4), np.uint8)
    im = Image.fromarray(canvas, "RGBA")
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in eyes:
        w_, h_ = x1 - x0, y1 - y0
        # pad to cover the whole eye incl. outline
        px0, py0, px1, py1 = x0 - 3, y0 - 2, x1 + 3, y1 + 3
        # skin sampled just above the eye
        skin = a[py0 - 6:py0 - 1, (px0 + px1) // 2 - 5:(px0 + px1) // 2 + 5, :3]
        skin = tuple(int(c) for c in np.median(skin.reshape(-1, 3), axis=0))
        d.rounded_rectangle([px0, py0, px1, py1], radius=min(8, h_ // 2), fill=skin + (255,))
        # closed lid: downward lash arc across the lower third
        lash_col = (88, 66, 72, 255)
        y_lid = py1 - h_ * 0.28
        d.line([(px0 + 2, y_lid - 3), ((px0 + px1) // 2, y_lid + 2), (px1 - 2, y_lid - 3)],
               fill=lash_col, width=3, joint="curve")
    return im


def checker_bg(w, h, cell=16):
    bg = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(bg)
    for yy in range(0, h, cell):
        for xx in range(0, w, cell):
            if (xx // cell + yy // cell) % 2:
                d.rectangle([xx, yy, xx + cell - 1, yy + cell - 1], fill=(204, 204, 204))
    return bg


def layer_to_rgba(base, alpha):
    out = base.copy()
    out[:, :, 3] = alpha.astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def main():
    img, a = load()
    h, w = a.shape[:2]
    alpha = alpha_from_bg(a)

    halo = halo_mask(a)
    head_a, body_a = feather_cut(alpha)
    head_a = head_a * (~halo)          # halo is its own layer, painted behind
    body_a = body_a * (~halo)

    eyes = find_eyes(a)
    print("eye boxes:", eyes)
    blink = make_blink(a, eyes)

    # exports
    transparent = layer_to_rgba(a.astype(np.uint8), alpha)
    transparent.save(OUT / "pet.png")

    halo_img = transparent.copy()
    halo_img.putalpha(Image.fromarray((alpha * halo).astype(np.uint8)))
    halo_img.save(OUT / "halo.png")

    layer_to_rgba(a.astype(np.uint8), head_a).save(OUT / "head.png")
    layer_to_rgba(a.astype(np.uint8), body_a).save(OUT / "body.png")
    blink.save(OUT / "blink.png")
    with open(OUT / "eyes.json", "w", encoding="utf-8") as f:
        json.dump({"eyes": eyes, "cut_y": CUT_Y}, f, indent=2)

    face = transparent.crop((150, 140, 300, 290)).resize((64, 64), Image.LANCZOS)
    face.save(OUT / "tray.png")

    # ---- debug renders ----
    # composed over checkerboard
    chk = checker_bg(w, h).convert("RGBA")
    chk.alpha_composite(transparent)
    chk.convert("RGB").save(ROOT / "assets" / "raw" / "_debug_cutout.png")

    # layer separation view: halo in red tint, head in green tint, body in blue tint
    dbg = Image.new("RGB", (w, h), (30, 30, 30))
    dd = ImageDraw.Draw(dbg)
    dd.line([(0, CUT_Y), (w, CUT_Y)], fill=(255, 255, 0), width=1)
    ov = np.array(dbg, np.uint8).copy()
    ov[:, :, 0] = np.maximum(ov[:, :, 0], (alpha * halo * 0.9).astype(np.uint8))
    ov[:, :, 1] = np.maximum(ov[:, :, 1], head_a.astype(np.uint8) * 0.9)
    ov[:, :, 2] = np.maximum(ov[:, :, 2], body_a.astype(np.uint8) * 0.9)
    Image.fromarray(ov).save(ROOT / "assets" / "raw" / "_debug_layers.png")

    # blink preview: head+body+halo with closed eyes over checker
    chk2 = checker_bg(w, h).convert("RGBA")
    comp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    comp.alpha_composite(layer_to_rgba(a.astype(np.uint8), alpha))
    comp.alpha_composite(blink)
    chk2.alpha_composite(comp)
    chk2.crop((120, 150, 330, 300)).resize((420, 300), Image.NEAREST).convert("RGB")\
        .save(ROOT / "assets" / "raw" / "_debug_blink.png")

    print("done. layers in", OUT)


if __name__ == "__main__":
    main()
