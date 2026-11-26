"""Manifest-backed authored animation clips with lazy bounded frame caching."""
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage


CANVAS = (446, 602)
DEFAULT_CACHE_LIMIT = 96 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
ANIMATIONS_ROOT = ROOT / "assets" / "animations"


def _positive_number(value, description):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{description} must be a positive number")
    return float(value)


def _relative_path(root, value, description):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or relative.suffix.lower() != ".png":
        raise ValueError(f"{description} must be a relative PNG path")
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{description} escapes the manifest directory") from exc
    return path


def _image_plane(image, image_format):
    plane = image.convertToFormat(image_format)
    width, height = plane.width(), plane.height()
    return np.frombuffer(plane.constBits(), dtype=np.uint8).reshape(
        height, plane.bytesPerLine())[:, :width].copy()


@dataclass(frozen=True)
class AnimationFrame:
    file: Path
    duration: float
    head_mask: Path | None = None


class AnimationClip:
    """Immutable clip metadata; ``sample`` depends only on elapsed time."""

    def __init__(self, library, name, label, frames, mode, idle, anchor):
        self._library = library
        self.name = name
        self.label = label
        self.frames = tuple(frames)
        self.mode = mode
        self.idle = idle
        self.anchor = tuple(anchor)
        total = 0.0
        cumulative = []
        for frame in self.frames:
            total += frame.duration
            cumulative.append(total)
        self.duration = math.fsum(frame.duration for frame in self.frames)
        self.total_duration = self.duration
        cumulative[-1] = self.duration
        self._cumulative = tuple(cumulative)

    def sample(self, elapsed):
        """Return ``(image, alpha, head_mask, index, finished)`` at ``elapsed``."""
        if (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed)):
            raise ValueError("elapsed must be a finite number")
        elapsed = max(0.0, float(elapsed))
        boundary = math.isclose(elapsed, self.duration, rel_tol=1e-12, abs_tol=1e-12)
        finished = self.mode == "once" and (elapsed > self.duration or boundary)
        if self.mode == "loop":
            cycles = math.floor(elapsed / self.duration)
            position = elapsed - cycles * self.duration
            if (math.isclose(position, 0.0, rel_tol=0.0, abs_tol=1e-12)
                    or math.isclose(position, self.duration, rel_tol=1e-12, abs_tol=1e-12)):
                position = 0.0
        elif finished:
            position = self.duration
        else:
            position = elapsed
        index = min(bisect_right(self._cumulative, position), len(self.frames) - 1)
        image, alpha, head_mask = self._library._frame(self.frames[index])
        return image, alpha, head_mask, index, finished


class AnimationLibrary:
    """Parse an animation manifest and lazily cache decoded frame data."""

    def __init__(self, manifest_path=None, cache_limit=DEFAULT_CACHE_LIMIT):
        if (isinstance(cache_limit, bool) or not isinstance(cache_limit, (int, float))
                or not math.isfinite(cache_limit) or cache_limit <= 0):
            raise ValueError("cache_limit must be positive")
        self.cache_limit = int(cache_limit)
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self.clips = OrderedDict()
        self.canvas = CANVAS
        self.anchor = (CANVAS[0] / 2, CANVAS[1])

        default_manifest = manifest_path is None
        path = Path(ANIMATIONS_ROOT) / "manifest.json" if default_manifest else Path(manifest_path)
        if path.is_dir():
            path = path / "manifest.json"
        self.manifest_path = path
        self.root = path.parent
        if not path.exists():
            if default_manifest:
                return
            raise FileNotFoundError(path)
        self._parse(path)

    @property
    def cache_bytes(self):
        return self._cache_bytes

    @property
    def idle_names(self):
        return tuple(name for name, clip in self.clips.items() if clip.idle)

    @property
    def metadata(self):
        return self.behavior_metadata()

    def __len__(self):
        return len(self.clips)

    def __iter__(self):
        return iter(self.clips)

    def __contains__(self, name):
        return name in self.clips

    def __getitem__(self, name):
        return self.clips[name]

    def get(self, name, default=None):
        return self.clips.get(name, default)

    def behavior_metadata(self):
        return {
            name: {"duration": clip.duration, "mode": clip.mode, "idle": clip.idle,
                   "label": clip.label}
            for name, clip in self.clips.items()
        }

    def _parse(self, path):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid animation manifest: {path}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("animation manifest must be an object")
        if isinstance(manifest.get("version"), bool) or manifest.get("version") != 1:
            raise ValueError("animation manifest version must be 1")

        canvas = manifest.get("canvas")
        if (not isinstance(canvas, list) or len(canvas) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
                       for value in canvas)):
            raise ValueError("animation canvas dimensions must be positive integers")
        self.canvas = tuple(canvas)
        if self.canvas != CANVAS:
            raise ValueError(f"animation canvas must be {CANVAS[0]}x{CANVAS[1]}")

        clips = manifest.get("clips")
        if not isinstance(clips, dict):
            raise ValueError("animation clips must be an object")
        for name, entry in clips.items():
            if not isinstance(name, str) or not name:
                raise ValueError("animation clip names must be non-empty strings")
            if not isinstance(entry, dict):
                raise ValueError(f"animation clip {name!r} must be an object")
            label = entry.get("label")
            if not isinstance(label, str) or not label:
                raise ValueError(f"animation clip {name!r} needs a label")
            mode = entry.get("mode")
            if mode not in ("once", "loop"):
                raise ValueError(f"animation clip {name!r} mode must be 'once' or 'loop'")
            idle = entry.get("idle")
            if not isinstance(idle, bool):
                raise ValueError(f"animation clip {name!r} idle must be boolean")
            anchor = entry.get("anchor")
            if (not isinstance(anchor, list) or len(anchor) != 2
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in anchor)
                    or not 0 <= anchor[0] <= self.canvas[0]
                    or not 0 <= anchor[1] <= self.canvas[1]):
                raise ValueError(f"animation clip {name!r} has an invalid anchor")
            frame_entries = entry.get("frames")
            if not isinstance(frame_entries, list) or not frame_entries:
                raise ValueError(f"animation clip {name!r} needs at least one frame")
            frames = []
            for index, frame in enumerate(frame_entries):
                if not isinstance(frame, dict):
                    raise ValueError(f"animation clip {name!r} frame {index} must be an object")
                image_path = _relative_path(self.root, frame.get("file"),
                                            f"clip {name!r} frame {index} file")
                duration = _positive_number(frame.get("duration"),
                                            f"clip {name!r} frame {index} duration")
                mask_value = frame.get("head_mask")
                mask_path = None if mask_value is None else _relative_path(
                    self.root, mask_value, f"clip {name!r} frame {index} head_mask")
                frames.append(AnimationFrame(image_path, duration, mask_path))
            self.clips[name] = AnimationClip(
                self, name, label, frames, mode, idle, tuple(float(value) for value in anchor))

    def _frame(self, frame):
        key = (frame.file, frame.head_mask)
        cached = self._cache.pop(key, None)
        if cached is not None:
            self._cache[key] = cached
            return cached[0], cached[1], cached[2]

        source = QImage(str(frame.file))
        if source.isNull():
            raise ValueError(f"cannot load animation frame: {frame.file}")
        if (source.width(), source.height()) != self.canvas:
            raise ValueError(
                f"animation frame {frame.file} must be {self.canvas[0]}x{self.canvas[1]}")
        if not source.hasAlphaChannel():
            raise ValueError(f"animation frame must be RGBA: {frame.file}")
        image = source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        alpha = _image_plane(image, QImage.Format.Format_Alpha8)

        head_mask = None
        if frame.head_mask is not None:
            mask_image = QImage(str(frame.head_mask))
            if mask_image.isNull():
                raise ValueError(f"cannot load animation head mask: {frame.head_mask}")
            if (mask_image.width(), mask_image.height()) != self.canvas:
                raise ValueError(
                    f"animation head mask {frame.head_mask} must be "
                    f"{self.canvas[0]}x{self.canvas[1]}")
            if mask_image.hasAlphaChannel():
                mask_alpha = _image_plane(mask_image, QImage.Format.Format_Alpha8)
                mask_gray = _image_plane(mask_image, QImage.Format.Format_Grayscale8)
                head_mask = np.minimum(mask_alpha, mask_gray)
            else:
                head_mask = _image_plane(mask_image, QImage.Format.Format_Grayscale8)

        size = int(image.sizeInBytes()) + alpha.nbytes
        if head_mask is not None:
            size += head_mask.nbytes
        if size <= self.cache_limit:
            while self._cache and self._cache_bytes + size > self.cache_limit:
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= evicted[3]
            self._cache[key] = (image, alpha, head_mask, size)
            self._cache_bytes += size
        return image, alpha, head_mask
