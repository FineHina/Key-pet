"""Original layered PNG poses and authored frame animation."""
import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform

from .animations import AnimationLibrary
from .character_renderer import (
    BUBBLE_BAND, PET_H, PET_W, RIGHT_MARGIN, CharacterFrame, CharacterLayer, image_alpha,
)

LAYERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "layers")

CUT_Y = 280                      # neck pivot row (full-res)

TWO_PI = 2 * math.pi


def _load_image(name):
    img = QImage(os.path.join(LAYERS, name))
    if img.isNull():
        raise SystemExit(f"缺少素材 assets/layers/{name}，请检查项目 assets/layers/ 目录是否完整。")
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


class PetScene:
    def __init__(self, animation_manifest=None):
        self.halo = _load_image("halo.png")
        self.halo_highlight = self.halo.copy()
        painter = QPainter(self.halo_highlight)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(self.halo_highlight.rect(), QColor("#fff0fb"))
        painter.end()
        self.body = _load_image("body.png")
        self.head = _load_image("head.png")
        self.animations = AnimationLibrary(animation_manifest)
        self._layer_alpha = {
            name: image_alpha(image)
            for name, image in (("head", self.head), ("body", self.body), ("halo", self.halo))
        }

        # Halo pivot is its visual center in the source canvas.
        ys, xs = (self._layer_alpha["halo"] > 8).nonzero()
        if len(xs):
            self.halo_c = QPointF(xs.mean(), ys.mean())
        else:
            self.halo_c = QPointF(210, 95)

    def size_for(self, scale):
        return int(PET_W * scale) + RIGHT_MARGIN, int(PET_H * scale) + BUBBLE_BAND

    def paint(self, painter, now, state, scale):
        self.sample(now, state, scale).paint(painter)

    def hit_region(self, position, now, state, scale):
        return self.sample(now, state, scale).hit_region(position)

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

    @staticmethod
    def _interaction_envelope(t, st, *poses):
        action = st.get("reaction")
        if action and action["kind"] in poses:
            progress = (t - action["t0"]) / action["dur"]
            if 0 < progress < 1:
                return math.sin(math.pi * progress) ** 2
        return 0.0

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
            elif kind in ("nod", "warm_nod", "curious", "receive", "relax", "proud", "hop"):
                if kind == "curious":
                    head_ang += 5 * envelope * math.cos(math.pi * progress)
                    head_dy -= 5 * envelope * progress
                    lean += 1.4 * envelope
                elif kind in ("receive", "proud"):
                    head_dy += (6 if kind == "proud" else 4 * math.cos(math.pi * progress)) * envelope
                    head_ang -= 3 * envelope
                elif kind == "relax":
                    head_dy -= 6 * envelope
                    head_ang += 3 * envelope
                else:
                    head_dy -= (5 if kind == "hop" else 7) * envelope
                    head_ang += 2 * envelope
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

    def whole_transform(self, t, st, s, lean=0.0):
        anchor = QPointF(PET_W * s / 2, BUBBLE_BAND + PET_H * s)
        hop_dy, hop_sx, hop_sy = self._hop_offset(t, st)
        land_sx, land_sy = self._land_squash(t, st)
        if st.get("dragging"):
            hop_dy, hop_sx, hop_sy = 0.0, 1.0, 1.0
            land_sx, land_sy = 1.0, 1.0
        whole = QTransform()
        whole.translate(anchor.x(), anchor.y())
        whole.rotate(st.get("sway", 0.0) + lean)
        whole.scale(hop_sx * land_sx, hop_sy * land_sy)
        whole.translate(0, hop_dy)
        whole.translate(-anchor.x(), -anchor.y())
        return whole

    def transforms(self, t, st, s):
        ox, oy = (0, BUBBLE_BAND)
        anchor = QPointF(ox + PET_W * s / 2, oy + PET_H * s)
        neck = QPointF(ox + PET_W * s / 2, oy + CUT_Y * s)
        body_sy, head_dy, head_ang, halo_dy, halo_ang = self._pose(t, st)
        relax = self._interaction_envelope(t, st, "relax")
        damping = 1 - 0.65 * relax
        body_sy = 1 + (body_sy - 1) * damping
        head_dy *= damping
        head_ang *= damping
        halo_dy *= damping
        halo_ang *= damping
        halo_dy += 4.5 * self._interaction_envelope(t, st, "warm_nod", "halo")
        action_dy, action_ang, lean, _, _ = self._action_pose(t, st)
        if st.get("dragging"):
            body_sy = 1.0
        whole = self.whole_transform(t, st, s, lean)
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
            ox, oy = (0, BUBBLE_BAND)
            anchor = QPointF(ox + PET_W * s / 2, oy + PET_H * s)
            transform.translate(anchor.x(), anchor.y())
            transform.rotate(sway)
            transform.translate(-anchor.x(), -anchor.y())
        return transform

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

    def sample(self, now, state, scale):
        target = QRectF(0, BUBBLE_BAND, PET_W * scale, PET_H * scale)
        current = self.current_animation_frame(now, state)
        if current is not None:
            image, alpha, head_mask, _, _ = current
            layer = CharacterLayer(image, alpha, self._authored_transform(state, scale),
                                   head_mask=head_mask, head_cut_y=CUT_Y)
            return CharacterFrame((layer,), target)

        transforms = self.transforms(now, state, scale)
        glow = self._interaction_envelope(now, state, "warm_nod", "halo")
        layers = (
            CharacterLayer(self.halo, self._layer_alpha["halo"], transforms["halo"],
                           highlight=self.halo_highlight, highlight_opacity=glow * 0.66),
            CharacterLayer(self.body, self._layer_alpha["body"], transforms["body"]),
            CharacterLayer(self.head, self._layer_alpha["head"], transforms["head"], region="head"),
        )
        return CharacterFrame(layers, target)
