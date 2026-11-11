"""Motion tracking: effects and graphics that stay on something as it moves.

A tracked item in motion.json names what to follow and what to do with it:

    "tracked": [
      {"type": "blur", "target": "face", "start": 2.0, "end": 6.5},
      {"type": "pixelate", "target": {"box": [0.62, 0.30, 0.20, 0.12], "at": 3.0}},
      {"type": "spotlight", "target": "face", "amount": 0.55},
      {"type": "label", "text": "Dr Rivera", "target": "face", "anchor": "below"},
      {"type": "sticker", "file": "art/arrow.png", "target": {"box": [...], "at": 4}, "anchor": "above"}
    ]

`target` is `"face"` (the biggest, or `{"face": "left"}` / `"right"`), or a box in shares of the film
frame, drawn at the time `at`, followed forward and back from there. Boxes follow with optical flow,
faces with the YuNet detector that ships with Altocue. Both run on the finished picture, so a track
lives in film pixels, after every crop, punch-in and moving crop. Nothing is uploaded.

Each item becomes one short lossless clip, a mask or a picture with alpha, laid over the film only
while the item runs. Blur and pixelate are for privacy: a face, a screen, a number plate.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import ffmpeg, frame
from .brand import Brand
from .cut.snap import frame_count
from .errors import AltocueError
from .project import Project, write_json

KINDS = ("blur", "pixelate", "spotlight", "label", "sticker")
ANCHORS = ("centre", "above", "below", "left", "right")
ANALYSE_WIDTH = 540
SMOOTH = 0.08  # seconds. Takes the shiver out of a detector without lagging a fast move
PAD = 0.25  # share of the box added on every side of a blur, so an edge never peeks out
CHIN = 0.3  # share of a face box added below it for a blur: the detector's box stops above the chin and beard
MIN_POINTS = 8
VERSION = 2


def parse(raw: Any) -> list[dict[str, Any]]:
    """Check every item before anything is tracked, so a typo fails in a second, not after a render."""
    items = []
    for entry in raw or []:
        if not isinstance(entry, dict) or entry.get("type") not in KINDS:
            raise AltocueError(f"Unknown tracked item {entry!r}.", fix=f'Use {{"type": "blur", "target": "face"}}. Types: {", ".join(KINDS)}.')
        target = entry.get("target", "face")
        if not (target == "face" or (isinstance(target, dict) and ("face" in target or "box" in target))):
            raise AltocueError(f"Cannot follow {target!r}.", fix='Use "face", {"face": "left"}, or {"box": [x, y, w, h], "at": seconds} in shares of the frame.')
        if isinstance(target, dict) and "box" in target:
            box = target["box"]
            if len(box) != 4 or not all(0 <= float(v) <= 1 for v in box) or float(box[2]) <= 0 or float(box[3]) <= 0:
                raise AltocueError(f"The box {box} is not four shares of the frame.", fix="Give [left, top, width, height], each between 0 and 1.")
        if entry.get("anchor", "centre") not in ANCHORS:
            raise AltocueError(f"Unknown anchor {entry.get('anchor')!r}.", fix=f"Use one of {', '.join(ANCHORS)}.")
        if entry["type"] == "label" and not str(entry.get("text", "")).strip():
            raise AltocueError("A tracked label has no text.", fix='Add "text": "...".')
        if entry["type"] == "sticker" and not entry.get("file"):
            raise AltocueError("A tracked sticker names no file.", fix='Add "file": "art/arrow.png", a PNG with transparency.')
        amount = float(entry.get("amount", 0.6))
        if not 0 < amount <= 1:
            raise AltocueError(f"A tracked amount of {amount:g} is not between 0 and 1.", fix="Most want 0.4 to 0.8.")
        items.append({**entry, "target": target, "amount": amount})
    return items


def read_frames(picture: Path, start: float, end: float, fps: int, width: int = ANALYSE_WIDTH) -> tuple[list[np.ndarray], float]:
    """RGB frames of the film between two times, small enough to analyse, and the factor back to film pixels."""
    clip = ffmpeg.probe_clip(picture, clip_id="picture")
    full_w, full_h = clip.display_size
    side = min(width, full_w)
    height = max(2, round(full_h * side / full_w / 2) * 2)
    cmd = [ffmpeg.require("ffmpeg"), "-v", "error", "-ss", f"{start:.4f}", "-i", str(picture), "-t", f"{max(end - start, 1 / fps):.4f}"]
    cmd += ["-vf", f"fps={fps},scale={side}:{height}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=False).stdout
    size = side * height * 3
    frames = [np.frombuffer(raw[i : i + size], np.uint8).reshape(height, side, 3) for i in range(0, len(raw) - size + 1, size)]
    if not frames:
        raise AltocueError(f"Could not read the film between {start:.2f} s and {end:.2f} s.", fix="Render once without tracked items, then check the times.")
    return frames, full_w / side


def follow_face(frames: list[np.ndarray], which: str = "largest") -> list[tuple[float, float, float, float] | None]:
    """The chosen face in every frame, kept on the same person by staying near where it was."""
    boxes: list[tuple[float, float, float, float] | None] = []
    last = None
    for image in frames:
        found = frame.detect_faces(image)
        if not found:
            boxes.append(None)
            continue
        if last is None:
            if which == "left":
                pick = min(found[:3], key=lambda f: f.center[0])
            elif which == "right":
                pick = max(found[:3], key=lambda f: f.center[0])
            else:
                pick = found[0]
        else:
            pick = min(found, key=lambda f: math.dist(f.center, (last[0] + last[2] / 2, last[1] + last[3] / 2)))
            if math.dist(pick.center, (last[0] + last[2] / 2, last[1] + last[3] / 2)) > 1.5 * max(pick.w, last[2]):
                boxes.append(None)  # another face, after a cut: leave a gap rather than jump to them
                continue
        top = pick.y - pick.h * frame.HAIR * 0.5
        last = (pick.x, top, pick.w, pick.h * (1 + frame.HAIR * 0.5))
        boxes.append(last)
    return boxes


Box = tuple[float, float, float, float]


def _flow_step(before: np.ndarray, after: np.ndarray, box: Box, points: np.ndarray | None) -> tuple[Box, np.ndarray | None]:
    x, y, w, h = box
    if points is None or len(points) < MIN_POINTS:
        mask = np.zeros_like(before)
        mask[max(int(y), 0) : int(y + h), max(int(x), 0) : int(x + w)] = 255
        points = cv2.goodFeaturesToTrack(before, 60, 0.01, max(2.0, min(w, h) / 12), mask=mask)
        if points is None:
            return box, None
    moved, ok, _ = cv2.calcOpticalFlowPyrLK(before, after, points, None, winSize=(21, 21), maxLevel=3)
    back, ok_back, _ = cv2.calcOpticalFlowPyrLK(after, before, moved, None, winSize=(21, 21), maxLevel=3)
    good = (ok.ravel() == 1) & (ok_back.ravel() == 1) & (np.linalg.norm((back - points).reshape(-1, 2), axis=1) < 1.0)
    if good.sum() < 3:
        return box, None
    old, new = points.reshape(-1, 2)[good], moved.reshape(-1, 2)[good]
    dx, dy = np.median(new - old, axis=0)
    scale = 1.0
    if len(old) >= 6:
        spread_old = np.linalg.norm(old - old.mean(axis=0), axis=1).mean()
        spread_new = np.linalg.norm(new - new.mean(axis=0), axis=1).mean()
        scale = float(np.clip(spread_new / spread_old, 0.9, 1.1)) if spread_old > 1 else 1.0
    cx, cy = x + w / 2 + float(dx), y + h / 2 + float(dy)
    w, h = w * scale, h * scale
    return (cx - w / 2, cy - h / 2, w, h), new.reshape(-1, 1, 2).astype(np.float32)


def follow_box(frames: list[np.ndarray], start: int, box: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """A box drawn on frame `start`, followed both ways with pyramidal Lucas-Kanade optical flow."""
    grey = [cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) for image in frames]
    out: list[tuple[float, float, float, float]] = [box] * len(frames)
    for step in (1, -1):
        current, points = box, None
        index = start
        while 0 <= index + step < len(frames):
            current, points = _flow_step(grey[index], grey[index + step], current, points)
            index += step
            out[index] = current
    return out


def fill_and_smooth(boxes: list[tuple[float, float, float, float] | None], fps: int) -> list[tuple[float, float, float, float] | None]:
    """Fill short gaps by straight lines, then smooth both ways. A long gap stays empty, and nothing is drawn there."""
    known = [i for i, b in enumerate(boxes) if b is not None]
    if not known:
        return boxes
    values = np.full((len(boxes), 4), np.nan)
    for i in known:
        values[i] = boxes[i]
    longest_gap = fps // 2
    for left, right in zip(known, known[1:], strict=False):
        if 1 < right - left <= longest_gap:
            for i in range(left + 1, right):
                share = (i - left) / (right - left)
                values[i] = values[left] + (values[right] - values[left]) * share
    values[: known[0]] = values[known[0]] if known[0] <= longest_gap else np.nan
    values[known[-1] + 1 :] = values[known[-1]] if len(boxes) - known[-1] <= longest_gap else np.nan
    alpha = 1 - math.exp(-1 / (fps * SMOOTH))
    for order in (range(len(values)), range(len(values) - 1, -1, -1)):
        last = None
        for i in order:
            if np.isnan(values[i, 0]):
                last = None
                continue
            if last is not None:
                values[i] = last + alpha * (values[i] - last)
            last = values[i].copy()
    return [None if np.isnan(row[0]) else tuple(float(v) for v in row) for row in values]  # type: ignore[misc]


def track_item(picture: Path, item: dict[str, Any], fps: int) -> list[tuple[float, float, float, float] | None]:
    """Boxes in film pixels, one per film frame from the item's start to its end."""
    frames, factor = read_frames(picture, item["start"], item["end"], fps)
    target = item["target"]
    if target == "face" or "face" in target:
        boxes = follow_face(frames, "largest" if target == "face" else str(target["face"]))
    else:
        height, width = frames[0].shape[:2]
        at = min(max(frame_count(item["start"], float(target.get("at", item["start"])), fps), 0), len(frames) - 1)
        x, y, w, h = (float(v) for v in target["box"])
        boxes = follow_box(frames, at, (x * width, y * height, w * width, h * height))  # type: ignore[assignment]
    smooth = fill_and_smooth(boxes, fps)
    return [None if b is None else (b[0] * factor, b[1] * factor, b[2] * factor, b[3] * factor) for b in smooth]


def soft_ellipse(size: tuple[int, int], box: tuple[float, float, float, float], pad: float, feather: float) -> np.ndarray:
    """1 inside the box's ellipse, falling to 0 over `feather` of its radius."""
    width, height = size
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    rx, ry = w * (0.5 + pad), h * (0.5 + pad)
    left, right = max(int(cx - rx * 1.3), 0), min(int(cx + rx * 1.3) + 1, width)
    top, bottom = max(int(cy - ry * 1.3), 0), min(int(cy + ry * 1.3) + 1, height)
    mask = np.zeros((height, width), np.float32)
    if right <= left or bottom <= top:
        return mask
    yy, xx = np.mgrid[top:bottom, left:right].astype(np.float32)
    distance = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    mask[top:bottom, left:right] = np.clip((1 + feather - distance) / max(feather, 1e-3), 0, 1)
    return mask


def placed(box: tuple[float, float, float, float], art: tuple[int, int], anchor: str, size: tuple[int, int]) -> tuple[int, int]:
    """Where art of this size goes beside the box, kept inside the frame."""
    x, y, w, h = box
    aw, ah = art
    gap = 0.12 * h
    spots = {
        "centre": (x + w / 2 - aw / 2, y + h / 2 - ah / 2),
        "above": (x + w / 2 - aw / 2, y - gap - ah),
        "below": (x + w / 2 - aw / 2, y + h + gap),
        "left": (x - gap - aw, y + h / 2 - ah / 2),
        "right": (x + w + gap, y + h / 2 - ah / 2),
    }
    left, top = spots[anchor]
    return int(min(max(left, 0), size[0] - aw)), int(min(max(top, 0), size[1] - ah))


def label_art(text: str, brand: Brand, height: int) -> Image.Image:
    """A pill in the brand's panel colour with the text in its ink, sized to the film."""
    size = max(14, round(height * 0.028))
    font = ImageFont.truetype(str(brand.font_file), size)
    left, top, right, bottom = font.getbbox(text)
    pad_x, pad_y = round(size * 0.7), round(size * 0.45)
    art = Image.new("RGBA", (right - left + 2 * pad_x, bottom - top + 2 * pad_y), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)
    draw.rounded_rectangle((0, 0, art.width - 1, art.height - 1), radius=art.height // 2, fill=brand.colors["panel"] + "e6")
    draw.text((pad_x - left, pad_y - top), text, font=font, fill=brand.colors["ink"])
    return art


def sticker_art(project: Project, item: dict[str, Any], width: int) -> Image.Image:
    path = (project.root / item["file"]).resolve()
    if not path.exists():
        raise AltocueError(f"The sticker {item['file']} was not found.", fix="Give a path inside the project, such as art/arrow.png.")
    art = Image.open(path).convert("RGBA")
    wide = max(8, round(width * float(item.get("width", 0.18))))
    return art.resize((wide, max(1, round(art.height * wide / art.width))), Image.Resampling.LANCZOS)


def _encode(dest: Path, frames: Any, size: tuple[int, int], fps: int, pix_fmt: str) -> Path:
    """Stream frames into a lossless clip, so a long item never sits in memory."""
    temp = dest.with_suffix(".part.mkv")
    out_fmt = "gray" if pix_fmt == "gray" else "yuva444p"
    cmd = [ffmpeg.require("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-"]
    cmd += ["-c:v", "ffv1", "-pix_fmt", out_fmt, str(temp)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as encoder:
        assert encoder.stdin is not None
        for picture in frames:
            encoder.stdin.write(picture.tobytes())
        encoder.stdin.close()
        error = encoder.stderr.read().decode() if encoder.stderr else ""
        if encoder.wait() != 0:
            raise AltocueError(f"Could not write a tracked layer: {error.strip()[-200:]}", fix="Run `altocue doctor`.")
    temp.rename(dest)
    return dest


def bake(project: Project, item: dict[str, Any], boxes: list[Any], size: tuple[int, int], fps: int, dest: Path, brand: Brand) -> Path:
    width, height = size
    kind = item["type"]
    if kind in {"blur", "pixelate"}:
        pad = float(item.get("pad", PAD))
        empty = np.zeros((height, width), np.uint8)
        if item.get("target") == "face" or isinstance(item.get("target"), dict) and "face" in item["target"]:
            boxes = [None if b is None else (b[0], b[1], b[2], b[3] * (1 + CHIN)) for b in boxes]
        masks = (empty if b is None else (soft_ellipse(size, b, pad, 0.15) * 255).astype(np.uint8) for b in boxes)
        return _encode(dest, masks, size, fps, "gray")
    if kind == "spotlight":
        amount = item["amount"]

        def dim() -> Any:
            for b in boxes:
                shade = np.zeros((height, width, 4), np.uint8)
                light = np.zeros((height, width), np.float32) if b is None else soft_ellipse(size, b, float(item.get("pad", 0.35)), 0.5)
                shade[..., 3] = ((1 - light) * amount * 255).astype(np.uint8) if b is not None else 0
                yield shade

        return _encode(dest, dim(), size, fps, "rgba")
    art = label_art(str(item["text"]), brand, height) if kind == "label" else sticker_art(project, item, width)
    anchor = item.get("anchor", "above" if kind == "label" else "centre")

    def frames() -> Any:
        for b in boxes:
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            if b is not None:
                canvas.alpha_composite(art, placed(b, art.size, anchor, size))
            yield np.asarray(canvas)

    return _encode(dest, frames(), size, fps, "rgba")


def prepare(project: Project, items: list[dict[str, Any]], picture: Path, size: tuple[int, int], fps: int, brand: Brand) -> list[dict[str, Any]]:
    """Track each item on the picture and bake its layer, once. A changed picture or item tracks again."""
    if not items:
        return []
    folder = project.work / "tracks"
    folder.mkdir(parents=True, exist_ok=True)
    stat = picture.stat()
    ready = []
    for item in items:
        recipe = {"v": VERSION, "picture": [stat.st_size, stat.st_mtime_ns], "item": item, "size": size, "fps": fps, "brand": brand.data}
        key = hashlib.sha256(json.dumps(recipe, sort_keys=True, default=str).encode()).hexdigest()[:16]
        dest = folder / f"{item['type']}-{key}.mkv"
        record = dest.with_suffix(".json")
        if not dest.exists():
            boxes = track_item(picture, item, fps)
            if all(b is None for b in boxes):
                raise AltocueError(f"The tracked {item['type']} found nothing to follow.", fix="Check the target and times, or draw a box at a frame where it shows.")
            write_json(record, {"item": item, "fps": fps, "boxes": boxes})
            bake(project, item, boxes, size, fps, dest, brand)
        ready.append({**item, "layer": str(dest)})
    return ready


def graph(items: list[dict[str, Any]], base: str, c: Any, next_input: int) -> tuple[list[str], list[str], str]:
    """Extra inputs, filter lines, and the label the picture ends on."""
    inputs: list[str] = []
    lines: list[str] = []
    for index, item in enumerate(items):
        start, end = float(item["start"]), float(item["end"])
        shift = f"setpts=PTS-STARTPTS+{start:.4f}/TB"
        when = f"enable='between(t,{start:.4f},{end:.4f})'"
        inputs += ["-i", item["layer"]]
        after = f"tracked{index}"
        if item["type"] in {"blur", "pixelate"}:
            if item["type"] == "blur":
                hide = f"gblur=sigma={max(c.width, c.height) * 0.012 * (0.5 + item['amount']):.2f}"
            else:
                block = max(4, round(c.width * 0.035 * (0.5 + item["amount"])))
                hide = f"scale=iw/{block}:-2:flags=area,scale={c.width}:{c.height}:flags=neighbor"
            lines.append(f"[{base}]split[keep{index}][hide{index}]")
            lines.append(f"[hide{index}]trim=start={start:.4f}:end={end:.4f},setpts=PTS-STARTPTS,format=yuv444p,{hide},format=yuva444p[soft{index}]")
            lines.append(f"[{next_input}:v]format=gray[mask{index}]")
            lines.append(f"[soft{index}][mask{index}]alphamerge,{shift}[cover{index}]")
            lines.append(f"[keep{index}][cover{index}]overlay=format=auto:eof_action=pass:{when}[{after}]")
        else:
            lines.append(f"[{next_input}:v]format=yuva444p,{shift}[art{index}]")
            lines.append(f"[{base}][art{index}]overlay=format=auto:eof_action=pass:{when}[{after}]")
        base = after
        next_input += 1
    return inputs, lines, base
