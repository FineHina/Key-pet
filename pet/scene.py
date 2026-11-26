"""PetScene: layered rendering + procedural animation.

Draws halo (behind) / body / one selected head / bubble, driven purely by
(timestamp, state dict) so it can be painted into any QPainter (live window or
offscreen QA renders).

Full-res pet canvas is 446x602; display scale `s` maps it to the window.
Window layout:  [ BUBBLE_BAND | pet canvas | right margin ]
"""
import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QTransform

from .animations import AnimationLibrary

LAYERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "layers")

PET_W, PET_H = 446, 602
CUT_Y = 280                      # neck pivot row (full-res)
BUBBLE_BAND = 120                # top margin reserved for speech bubbles
RIGHT_MARGIN = 90

TWO_PI = 2 * math.pi


def _load_image(name):
    img = QImage(os.path.join(LAYERS, name))
    if img.isNull():
        raise SystemExit(f"missing asset: {name} — run tools/prepare_assets.py first")
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


class PetScene:
    def __init__(self, animation_manifest=None):
        self.halo = _load_image("halo.png")
        self.body = _load_image("body.png")
        self.head = _load_image("head.png")
        self.animations = AnimationLibrary(animation_manifest)
        self._layer_alpha = {}
        for name, image in (("head", self.head), ("body", self.body), ("halo", self.halo)):
            alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
            self._layer_alpha[name] = np.frombuffer(alpha.constBits(), dtype=np.uint8).reshape(
                alpha.height(), alpha.bytesPerLine())[:, :alpha.width()].copy()

        # halo pivot = its own visual center (full-res coords)
        a8 = self.halo.convertToFormat(QImage.Format.Format_Alpha8)
        arr = np.frombuffer(a8.constBits(), dtype=np.uint8).reshape(a8.height(), a8.bytesPerLine())
        ys, xs = np.nonzero(arr[:, :a8.width()] > 8)
        if len(xs):
            self.halo_c = QPointF(xs.mean(), ys.mean())
        else:
            self.halo_c = QPointF(210, 95)

    # ---------- layout ----------
    def size_for(self, s):
        return (int(PET_W * s) + RIGHT_MARGIN, int(PET_H * s) + BUBBLE_BAND)

    def pet_origin(self, s):
        return 0, BUBBLE_BAND

    # ---------- procedural pose ----------
    def _pose(self, t, st):
        """Idle motion offsets: (body_sy, head_dy, head_ang_deg, halo_dy, halo_ang_deg)."""
        breath = math.sin(TWO_PI * t / 3.4)
        body_sy = 1.0 + 0.006 * breath
        head_dy = 2.2 * math.sin(TWO_PI * t / 3.4 + 0.35)
        ang = 1.6 * math.sin(TWO_PI * t / 5.8 + 1.1)
        # idle flavor accent: an extra one-off head-tilt bump
        if st.get("accent_t0") is not None:
            p = (t - st["accent_t0"]) / 0.9
            if 0 <= p <= 1:
                ang += 2.6 * math.sin(math.pi * p)
        halo_dy = 3.0 * math.sin(TWO_PI * t / 2.9)
        halo_ang = 3.5 * math.sin(TWO_PI * t / 7.3 + 0.5)
        return body_sy, head_dy, ang, halo_dy, halo_ang

    def _action_pose(self, t, st):
        head_dy = head_ang = lean = gaze = 0.0
        dozing = False
        for key in ("idle_action", "reaction"):
            action = st.get(key)
            if not action:
                continue
            progress = (t - action["t0"]) / action["dur"]
            if not 0 <= progress <= 1:
                continue
            envelope = math.sin(math.pi * progress) ** 2
            kind = action["kind"]
            if kind in ("look", "tilt", "embarrassed"):
                motion = math.sin(TWO_PI * progress) if kind == "look" else 1
                head_ang += (5 if kind == "look" else 4) * envelope * motion
                gaze += 2 * envelope * motion
            elif kind == "doze":
                head_dy -= 3 * envelope
                head_ang += 3 * envelope
                dozing = 0.16 < progress < 0.85
            elif kind in ("sway", "blush"):
                head_ang += 3 * envelope * math.sin(TWO_PI * progress * 2)
                lean += 1.5 * envelope * math.sin(TWO_PI * progress)
            elif kind in ("shake", "angry", "dark_angry"):
                head_ang += 4 * envelope * math.sin(TWO_PI * progress * 3)
        return head_dy, head_ang, lean, gaze, dozing

    def transforms(self, t, st, s):
        ox, oy = self.pet_origin(s)
        anchor = QPointF(ox + PET_W * s / 2, oy + PET_H * s)
        neck = QPointF(ox + PET_W * s / 2, oy + CUT_Y * s)
        body_sy, head_dy, head_ang, halo_dy, halo_ang = self._pose(t, st)
        action_dy, action_ang, lean, _, _ = self._action_pose(t, st)
        hop_dy, hop_sx, hop_sy = self._hop_offset(t, st)
        land_sx, land_sy = self._land_squash(t, st)
        if st.get("dragging"):
            body_sy, hop_dy, hop_sx, hop_sy = 1.0, 0.0, 1.0, 1.0
            land_sx, land_sy = 1.0, 1.0
        whole = QTransform()
        whole.translate(anchor.x(), anchor.y())
        whole.rotate(st.get("sway", 0.0) + lean)
        whole.scale(hop_sx * land_sx, hop_sy * land_sy)
        whole.translate(0, hop_dy)
        whole.translate(-anchor.x(), -anchor.y())
        hc = QPointF(ox + self.halo_c.x() * s, oy + self.halo_c.y() * s)
        halo = QTransform()
        halo.translate(hc.x(), hc.y() + halo_dy)
        halo.rotate(halo_ang)
        halo.translate(-hc.x(), -hc.y())
        body = QTransform()
        body.translate(anchor.x(), anchor.y())
        body.scale(1.0, body_sy)
        body.translate(-anchor.x(), -anchor.y())
        head = QTransform()
        head.translate(neck.x(), neck.y())
        head.rotate(head_ang + action_ang)
        head.translate(-neck.x(), -neck.y() - head_dy - action_dy)
        return {"halo": halo * whole, "body": body * whole, "head": head * whole}

    def current_animation_frame(self, t, st):
        """Return the active authored sample, or ``None`` for procedural rendering."""
        animation = st.get("animation")
        if not isinstance(animation, dict):
            return None
        clip = self.animations.get(animation.get("clip"))
        t0 = animation.get("t0")
        if clip is None or isinstance(t0, bool) or not isinstance(t0, (int, float)):
            return None
        return clip.sample(t - t0)

    def _authored_transform(self, st, s):
        """Return the only live transform allowed for authored frames: drag sway."""
        transform = QTransform()
        sway = st.get("sway", 0.0) if st.get("dragging") else 0.0
        if sway:
            ox, oy = self.pet_origin(s)
            anchor = QPointF(ox + PET_W * s / 2, oy + PET_H * s)
            transform.translate(anchor.x(), anchor.y())
            transform.rotate(sway)
            transform.translate(-anchor.x(), -anchor.y())
        return transform

    def hit_region(self, position, t, st, s):
        current = self.current_animation_frame(t, st)
        if current is not None:
            _, alpha, head_mask, _, _ = current
            transform = self._authored_transform(st, s)
            inverse, ok = transform.inverted()
            if not ok:
                return None
            point = inverse.map(QPointF(position))
            ox, oy = self.pet_origin(s)
            x, y = (point.x() - ox) / s, (point.y() - oy) / s
            if 0 <= x < PET_W and 0 <= y < PET_H and alpha[int(y), int(x)] > 10:
                if head_mask is not None:
                    return "head" if head_mask[int(y), int(x)] > 10 else "body"
                return "head" if y < CUT_Y else "body"
            return None

        ox, oy = self.pet_origin(s)
        transforms = self.transforms(t, st, s)
        for name in ("head", "body", "halo"):
            inverse, ok = transforms[name].inverted()
            if not ok:
                continue
            point = inverse.map(QPointF(position))
            x, y = (point.x() - ox) / s, (point.y() - oy) / s
            if 0 <= x < PET_W and 0 <= y < PET_H:
                if self._layer_alpha[name][int(y), int(x)] > 10:
                    return "head" if name == "head" else "body"
        return None

    def _hop_offset(self, t, st):
        """Click reaction: parabolic hop + stretch/squash. Returns (dy, sx, sy)."""
        hop_t0 = st.get("hop_t0")
        if hop_t0 is None:
            return 0.0, 1.0, 1.0
        dur, height = 0.55, 18.0 * (1.4 if st.get("hop_big") else 1.0)
        p = (t - hop_t0) / dur
        if p < 0 or p > 1:
            return 0.0, 1.0, 1.0
        dy = -height * math.sin(math.pi * p)
        sx = 0.94 if p < 0.2 else (1.05 if p > 0.82 else 1.0)
        sy = 1.06 if p < 0.2 else (0.93 if p > 0.82 else 1.0)
        return dy, sx, sy

    def _land_squash(self, t, st):
        """Squash right after being dropped."""
        t0 = st.get("land_t0")
        if t0 is None:
            return 1.0, 1.0
        p = (t - t0) / 0.25
        if p < 0 or p > 1:
            return 1.0, 1.0
        k = math.sin(math.pi * p) * 0.1
        return 1.0 + k, 1.0 - k

    # ---------- painting ----------
    def paint(self, p: QPainter, t, st, s):
        ox, oy = self.pet_origin(s)
        target = QRectF(ox, oy, PET_W * s, PET_H * s)
        current = self.current_animation_frame(t, st)
        if current is not None:
            image, _, _, _, _ = current
            transform = self._authored_transform(st, s)
            if not transform.isIdentity():
                p.save()
                p.setWorldTransform(transform, True)
                p.drawImage(target, image)
                p.restore()
            else:
                p.drawImage(target, image)
        else:
            transforms = self.transforms(t, st, s)
            for name in ("halo", "body", "head"):
                p.save()
                p.setWorldTransform(transforms[name], True)
                p.drawImage(target, getattr(self, name))
                p.restore()

    # ---------- bubble ----------
    def _draw_bubble(self, p: QPainter, b, s, ox, oy, now):
        text = b.get("text") or ""
        if not text:
            return
        age = now - b.get("t0", now)
        dur = b.get("dur", 2.4)
        alpha = 1.0
        if age < 0.12:
            alpha = age / 0.12
        elif age > dur - 0.15:
            alpha = max(0.0, (dur - age) / 0.15)
        if alpha <= 0:
            return

        px = max(12, min(26, int(28 * s)))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(px)
        p.save()
        p.setFont(font)
        fm = QFontMetricsF(font)
        br = fm.boundingRect(QRectF(0, 0, 190, 200), Qt.TextWordWrap, text)
        bw, bh = br.width() + 22, br.height() + 14
        head_cx = ox + PET_W * s / 2
        bx = min(max(head_cx - bw / 2, 6), PET_W * s + RIGHT_MARGIN - bw - 6)
        bottom = oy + 34 * s - 8                      # just above the halo
        by = bottom - bh

        p.setOpacity(alpha)
        path = QPainterPath()
        path.addRoundedRect(QRectF(bx, by, bw, bh), 9, 9)
        # tail pointing down at the head
        tx = min(max(head_cx - 9, bx + 10), bx + bw - 28)
        tail = QPainterPath()
        tail.moveTo(tx, by + bh - 1)
        tail.lineTo(tx + 18, by + bh - 1)
        tail.lineTo(tx + 6, by + bh + 11)
        tail.closeSubpath()
        p.setPen(QPen(QColor(196, 202, 216), 1.2))
        p.setBrush(QColor(255, 255, 255, 242))
        p.drawPath(path)
        p.drawPath(tail)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)                              # crisp border over tail seam

        p.setPen(QColor(61, 67, 79))
        p.drawText(QRectF(bx + 11, by + 7, bw - 22, bh - 14), Qt.TextWordWrap | Qt.AlignVCenter, text)
        p.restore()
