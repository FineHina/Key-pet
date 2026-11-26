"""Decode local WebM references and write deterministic metadata/contact sheets."""
import json
from pathlib import Path
import subprocess

import imageio.v3 as iio
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT.parent
OUT = ROOT / "assets/video-analysis"
VIDEOS = {
    "idle_2": SOURCE_ROOT / "待机动画2.webm",
    "idle_1": SOURCE_ROOT / "待机动作.webm",
    "action_1": SOURCE_ROOT / "动作1.webm",
    "walk_in_place": SOURCE_ROOT / "行走动画.webm",
}


def probe(path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    process = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    return process.stderr


def bbox(mask):
    ys, xs = np.nonzero(mask)
    return None if not len(xs) else [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    font = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default()
    manifest = {}
    sheets = []
    for clip, path in VIDEOS.items():
        metadata = iio.immeta(path, plugin="FFMPEG")
        reported_fps = float(metadata.get("fps") or 0)
        fps = reported_fps if reported_fps > 0 else 60.0
        expected = max(1, int(round(float(metadata.get("duration") or 0) * fps)))
        sample_targets = sorted(set(round(i * max(0, expected - 1) / 7) for i in range(8))) if expected else []
        sampled = {}
        boxes, centers, feet, adjacent = [], [], [], []
        corner_samples = []
        first = last = previous_small = None
        count = 0
        height = width = channels = 0
        for frame in iio.imiter(path, plugin="FFMPEG"):
            frame = np.asarray(frame)
            if count == 0:
                height, width, channels = frame.shape
                first = frame.copy()
            # Downsample statistics to cap memory while preserving movement trends.
            stride = max(1, round(max(width, height) / 480))
            small = frame[::stride, ::stride, :3]
            corners = np.concatenate((small[:4, :4].reshape(-1, 3), small[:4, -4:].reshape(-1, 3),
                                      small[-4:, :4].reshape(-1, 3), small[-4:, -4:].reshape(-1, 3)))
            bg = np.median(corners, axis=0).astype(np.int16)
            corner_samples.append(bg)
            distance = np.abs(small.astype(np.int16) - bg).sum(axis=2)
            box_small = bbox(distance > 45)
            box = None if box_small is None else [value * stride for value in box_small]
            boxes.append(box)
            centers.append([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2] if box else None)
            feet.append(box[3] if box else None)
            if previous_small is not None:
                adjacent.append(float(np.mean(np.abs(small.astype(np.int16) - previous_small.astype(np.int16)))))
            previous_small = small.copy()
            if count in sample_targets:
                sampled[count] = frame.copy()
            last = frame.copy()
            count += 1
        if count == 0:
            raise RuntimeError(f"no decoded frames: {path}")
        duration = float(metadata.get("duration") or (count / fps if fps else 0))
        if reported_fps <= 0 and duration > 0:
            fps = count / duration
            sample_targets = sorted(set(round(i * (count - 1) / 7) for i in range(8)))
            # The first pass used a conservative default; fill any missing targets
            # with the nearest frame already sampled around that timeline point.
            if not sampled:
                raise RuntimeError(f"could not sample frames: {path}")
        bg = np.median(np.asarray(corner_samples), axis=0)
        first_last_mae = float(np.mean(np.abs(first[::stride, ::stride, :3].astype(np.int16)
                                                   - last[::stride, ::stride, :3].astype(np.int16))))
        sample_indices = sorted(sampled)
        tile_w = 240
        tiles = []
        for index in sample_indices:
            frame = Image.fromarray(sampled[index][:, :, :3]).convert("RGB")
            frame.thumbnail((tile_w, 330), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (tile_w, 370), "#edf0f6")
            tile.paste(frame, ((tile_w - frame.width) // 2, 25))
            ImageDraw.Draw(tile).text((8, 4), f"#{index}  {index / fps:.2f}s" if fps else f"#{index}",
                                      font=font, fill="#25334a")
            tiles.append(tile)
        sheet = Image.new("RGB", (tile_w * len(tiles), 410), "#dfe5ef")
        draw = ImageDraw.Draw(sheet)
        draw.text((10, 382), f"{clip} | {width}×{height} | {fps:g} fps | {count} frames | {duration:.3f}s",
                  font=font, fill="#25334a")
        for i, tile in enumerate(tiles):
            sheet.paste(tile, (i * tile_w, 0))
        sheet.save(OUT / f"{clip}_contact.png")
        sheets.append((clip, sheet))
        manifest[clip] = {
            "source": str(path), "shape": [width, height], "channels": channels,
            "fps": fps, "duration": duration, "frame_count": count,
            "metadata": {key: str(value) for key, value in metadata.items()},
            "corner_median_rgb": bg.tolist(), "decoded_alpha": channels == 4,
            "first_last_mae": first_last_mae,
            "adjacent_mae_median": float(np.median(adjacent)) if adjacent else 0,
            "bbox_first": boxes[0], "bbox_last": boxes[-1],
            "center_x_range": [min(c[0] for c in centers if c), max(c[0] for c in centers if c)],
            "feet_y_range": [min(f for f in feet if f is not None), max(f for f in feet if f is not None)],
            "probe": probe(path), "sample_indices": sample_indices,
        }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    overview = Image.new("RGB", (max(sheet.width for _, sheet in sheets), 410 * len(sheets)), "#edf0f6")
    for i, (_, sheet) in enumerate(sheets):
        overview.paste(sheet, (0, i * 410))
    overview.save(OUT / "all_videos_contact.png")
    print(json.dumps({k: {field: value for field, value in v.items() if field in
          ("shape", "channels", "fps", "duration", "frame_count", "corner_median_rgb",
           "decoded_alpha", "first_last_mae", "adjacent_mae_median", "center_x_range", "feet_y_range")}
          for k, v in manifest.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
