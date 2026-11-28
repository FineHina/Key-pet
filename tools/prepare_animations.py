"""Convert authored black-background references into anchored transparent sprite clips."""
import argparse
import json
import math
from pathlib import Path
import shutil

import imageio.v3 as iio
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT.parent
OUT = ROOT / "assets/animations"
CANVAS = (446, 602)
TARGET_ANCHOR = (223, 580)
TARGET_FPS = 20.0
CLIPS = {
    "idle_2": {"label": "待机动画2", "source": SOURCE_ROOT / "待机动画2.webm", "mode": "loop", "idle": True},
    "idle_1": {"label": "待机动作", "source": SOURCE_ROOT / "待机动作.webm", "mode": "loop", "idle": True},
    "action_1": {"label": "动作1", "source": SOURCE_ROOT / "动作1.webm", "mode": "once", "idle": False},
    "walk_in_place": {"label": "原地行走", "source": SOURCE_ROOT / "行走动画.webm", "mode": "loop", "idle": True},
}


def component_mask(frame):
    """Recover the center character and enclosed dark clothing from black video."""
    rgb = frame[:, :, :3]
    height, width = rgb.shape[:2]
    stride = 2
    small = rgb[::stride, ::stride]
    visible = small.max(axis=2) > 14
    # Source UI/weapons live far left. The character is the persistent center subject.
    visible[:, :round(width * .33 / stride)] = False
    visible[:, round(width * .68 / stride):] = False
    labels, count = ndimage.label(visible, structure=np.ones((3, 3), dtype=np.uint8))
    best = None
    for index in range(1, count + 1):
        ys, xs = np.where(labels == index)
        area = len(xs)
        if area < 25:
            continue
        cx = (xs.min() + xs.max()) / 2
        cy = (ys.min() + ys.max()) / 2
        center_penalty = 1 + abs(cx - width / stride / 2) / (width / stride)
        score = area / center_penalty
        if .12 * height / stride < cy < .95 * height / stride and (best is None or score > best[0]):
            best = (score, index, xs, ys)
    if best is None:
        return np.zeros((height, width), dtype=np.uint8), None, None
    _, index, xs, ys = best
    main = labels == index
    main_x0, main_x1 = int(xs.min()), int(xs.max() + 1)
    main_y0, main_y1 = int(ys.min()), int(ys.max() + 1)
    main_width = max(1, main_x1 - main_x0)
    main_height = max(1, main_y1 - main_y0)
    # Keep detached character pieces (especially the halo) only when they
    # substantially overlap the main character horizontally. Recorder UI and
    # weapon icons sit to either side and are deliberately excluded.
    combined = main.copy()
    for component in range(1, count + 1):
        if component == index:
            continue
        cy, cx = np.where(labels == component)
        if len(cx) < 8:
            continue
        cx0, cx1 = int(cx.min()), int(cx.max() + 1)
        cy0, cy1 = int(cy.min()), int(cy.max() + 1)
        overlap = max(0, min(cx1, main_x1) - max(cx0, main_x0))
        overlap_ratio = overlap / max(1, cx1 - cx0)
        vertical_gap = max(0, main_y0 - cy1, cy0 - main_y1)
        center_near = main_x0 - .08 * main_width <= (cx0 + cx1) / 2 <= main_x1 + .08 * main_width
        if (overlap_ratio >= .55 or center_near) and vertical_gap <= max(35, .18 * main_height):
            combined |= labels == component
    combined = ndimage.binary_fill_holes(ndimage.binary_dilation(combined, iterations=1))
    combined = ndimage.binary_closing(combined, iterations=1)
    full = Image.fromarray(combined.astype(np.uint8) * 255).resize((width, height), Image.Resampling.NEAREST)
    mask = np.asarray(full) > 0
    mask = ndimage.binary_dilation(mask, iterations=2)
    mask = ndimage.binary_closing(mask, iterations=1)
    alpha = ndimage.gaussian_filter(mask.astype(np.float32) * 255, .65).astype(np.uint8)
    ys_full, xs_full = np.where(alpha > 10)
    if not len(xs_full):
        return alpha, None, None
    bounds = [int(xs_full.min()), int(ys_full.min()), int(xs_full.max() + 1), int(ys_full.max() + 1)]
    lower = ys_full > np.quantile(ys_full, .82)
    root_x = float(np.median(xs_full[lower])) if np.any(lower) else (bounds[0] + bounds[2]) / 2
    return alpha, bounds, root_x


def clear_recording_holes(image):
    """Remove large black recorder-background islands enclosed by the halo."""
    rgba = np.array(image)
    dark = (rgba[:, :, 3] > 10) & (rgba[:, :, :3].max(axis=2) < 28)
    labels, count = ndimage.label(dark)
    for component in range(1, count + 1):
        ys, xs = np.where(labels == component)
        if not len(xs):
            continue
        width = int(xs.max() - xs.min() + 1)
        height = int(ys.max() - ys.min() + 1)
        density = len(xs) / (width * height)
        if (len(xs) >= 90 and width >= 9 and height >= 14
                and int(ys.max()) < 220 and density >= .38):
            rgba[labels == component, 3] = 0
    return Image.fromarray(rgba)


def alpha_crop(frame, alpha, source_anchor, scale):
    rgba = np.dstack((frame[:, :, :3], alpha))
    crop_width = CANVAS[0] / scale
    crop_height = CANVAS[1] / scale
    left = source_anchor[0] - TARGET_ANCHOR[0] / scale
    top = source_anchor[1] - TARGET_ANCHOR[1] / scale
    image = Image.fromarray(rgba)
    crop = image.crop((round(left), round(top), round(left + crop_width), round(top + crop_height)))
    return clear_recording_holes(crop.resize(CANVAS, Image.Resampling.LANCZOS))


def checker(frame):
    bg = Image.new("RGBA", frame.size, "white")
    draw = ImageDraw.Draw(bg)
    size = 18
    for y in range(0, frame.height, size):
        for x in range(0, frame.width, size):
            if (x // size + y // size) % 2:
                draw.rectangle((x, y, x + size - 1, y + size - 1), fill="#d7dae0")
    bg.alpha_composite(frame)
    return bg.convert("RGB")


def prepare_clip(name, spec):
    metadata = iio.immeta(spec["source"], plugin="FFMPEG")
    source_fps = float(metadata.get("fps") or 0)
    duration = float(metadata.get("duration") or 0)
    if source_fps <= 0:
        source_fps = 60.0
    step = max(1, round(source_fps / TARGET_FPS))
    selected = []
    for index, frame in enumerate(iio.imiter(spec["source"], plugin="FFMPEG")):
        if index % step:
            continue
        frame = np.asarray(frame)
        alpha, bounds, root_x = component_mask(frame)
        if bounds is not None:
            selected.append((index, frame, alpha, bounds, root_x))
    if not selected:
        raise RuntimeError(f"no character frames extracted from {spec['source']}")
    widths = np.array([item[3][2] - item[3][0] for item in selected])
    heights = np.array([item[3][3] - item[3][1] for item in selected])
    roots = np.array([item[4] for item in selected])
    bottoms = np.array([item[3][3] for item in selected])
    scale = min(360 / np.quantile(widths, .98), 520 / np.quantile(heights, .98))
    fixed_root = (float(np.median(roots)), float(np.median(bottoms)))
    clip_dir = OUT / name
    if clip_dir.exists():
        shutil.rmtree(clip_dir)
    clip_dir.mkdir(parents=True)
    frames_manifest = []
    previews = []
    previous = None
    frame_duration = step / source_fps
    for index, frame, alpha, bounds, root_x in selected:
        anchor_x = root_x if name == "walk_in_place" else fixed_root[0]
        image = alpha_crop(frame, alpha, (anchor_x, fixed_root[1]), scale)
        pixels = np.asarray(image)
        signature = np.asarray(image.resize((112, 151), Image.Resampling.BILINEAR)).astype(np.int16)
        if previous is not None and float(np.mean(np.abs(signature - previous))) < .18:
            frames_manifest[-1]["duration"] += frame_duration
            continue
        filename = f"frame_{len(frames_manifest):04d}.png"
        image.save(clip_dir / filename, optimize=True)
        frames_manifest.append({"file": f"{name}/{filename}", "duration": frame_duration,
                                "source_frame": index})
        previous = signature
        previews.append(checker(image).resize((223, 301), Image.Resampling.LANCZOS))
    # Preserve source duration despite sampling/duplicate collapse.
    manifest_duration = sum(item["duration"] for item in frames_manifest)
    if duration and frames_manifest:
        frames_manifest[-1]["duration"] += max(0, duration - manifest_duration)
    alpha_union = np.zeros((CANVAS[1], CANVAS[0]), dtype=bool)
    for item in frames_manifest:
        alpha_union |= np.asarray(Image.open(OUT / item["file"]))[:, :, 3] > 10
    ys, xs = np.where(alpha_union)
    head_cut = int(ys.min() + (ys.max() - ys.min()) * .52) if len(ys) else 280
    sample_ids = sorted(set(round(i * (len(previews) - 1) / 7) for i in range(8)))
    contact = Image.new("RGB", (223 * len(sample_ids), 335), "#edf0f6")
    for column, sample in enumerate(sample_ids):
        contact.paste(previews[sample], (column * 223, 0))
        ImageDraw.Draw(contact).text((column * 223 + 5, 305), f"#{sample}", fill="#25334a")
    contact.save(OUT / f"{name}_contact.png")
    gif_frames = [previews[i].resize((178, 241), Image.Resampling.LANCZOS)
                  for i in range(0, len(previews), max(1, len(previews) // 80))]
    gif_frames[0].save(OUT / f"{name}.gif", save_all=True, append_images=gif_frames[1:],
                       duration=round(1000 / TARGET_FPS), loop=0, disposal=2)
    return {
        "label": spec["label"], "source": spec["source"].name, "mode": spec["mode"],
        "idle": spec["idle"], "canvas": list(CANVAS), "anchor": list(TARGET_ANCHOR),
        "head_cut_y": head_cut, "frames": frames_manifest,
        "duration": sum(item["duration"] for item in frames_manifest),
        "source_fps": source_fps, "source_duration": duration, "sample_step": step,
        "extracted_scale": scale, "fixed_source_anchor": list(fixed_root),
        "stationary": name == "walk_in_place",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", choices=list(CLIPS))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    existing = {}
    manifest_path = OUT / "manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8")).get("clips", {})
    targets = [args.clip] if args.clip else list(CLIPS)
    for name in targets:
        print(f"Preparing {name}...")
        existing[name] = prepare_clip(name, CLIPS[name])
        print(f"  {len(existing[name]['frames'])} frames, {existing[name]['duration']:.3f}s")
    manifest = {"version": 1, "canvas": list(CANVAS), "clips": existing}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Animation manifest: {manifest_path}")


if __name__ == "__main__":
    main()
