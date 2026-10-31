"""Keep graphics off faces by looking at the actual picture under them."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFont

from .. import ffmpeg, frame
from ..brand import Brand
from .html import geometry

FULL_SCREEN = {"title", "endcard"}
FACE_MARGIN = 0.16  # share of a face kept clear on every side
SAMPLE_WIDTH = 540
TALL_TOPS = (None, 0.095, 0.30, 0.38, 0.46, 0.50, 0.54)
WIDE_TOPS = (None, 0.08, 0.42, 0.56)
Box = tuple[float, float, float, float]


def line_count(text: str, size: float, width: float, brand: Brand) -> int:
    font = ImageFont.truetype(str(brand.font_file), int(size))
    total = 0
    for line in text.replace("*", "").split("\n"):
        used, rows = 0.0, 1
        for word in line.split():
            length = font.getlength(word + " ")
            if used and used + length > width:
                rows, used = rows + 1, 0.0
            used += length
        total += rows
    return total


def estimated_height(item: dict[str, Any], width: int, height: int, brand: Brand) -> float:
    """A close guess at the graphic's height, in canvas pixels."""
    geo = geometry(width, height)
    scale, area = geo["scale"], width - 2 * geo["gutter"] - geo["rail"]
    kind = item["type"]
    if kind == "hook":
        eyebrow = 78 * scale if item.get("eyebrow") else 0
        return eyebrow + line_count(item["text"], 104 * scale, area, brand) * 102 * scale
    if kind == "chip":
        return 70 * scale
    if kind == "lower_third":
        return 132 * scale
    if kind == "stat":
        return 300 * scale
    if kind == "quote":
        return line_count(item["text"], 52 * scale, area - 92 * scale, brand) * 60 * scale + 150 * scale
    if kind == "steps":
        return 110 * scale + len(item.get("items", [])) * 78 * scale
    return 200 * scale


def faces_during(picture: Path, start: float, end: float, size: tuple[int, int]) -> list[Box]:
    """Face boxes in canvas pixels, from three frames across the graphic's time on screen."""
    width, height = size
    span = max(end - start, 0.0)
    boxes: list[Box] = []
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        for index, at in enumerate((start + min(0.2, span / 3), start + span / 2, max(start, end - 0.2))):
            still = Path(scratch) / f"{index}.png"
            try:
                ffmpeg.frame(picture, at, still, width=SAMPLE_WIDTH)
            except Exception:
                continue
            if not still.exists():
                continue
            with Image.open(still) as image:
                pixels = np.asarray(image.convert("RGB"))
            ratio = width / pixels.shape[1]
            for face in frame.detect_faces(pixels):
                boxes.append((face.x * ratio, face.y * ratio, face.w * ratio, face.h * ratio))
    return boxes


def overlap_area(top: float, bottom: float, left: float, right: float, faces: list[Box]) -> float:
    total = 0.0
    for x, y, w, h in faces:
        margin_x, margin_y = w * FACE_MARGIN, h * FACE_MARGIN
        wide = min(right, x + w + margin_x) - max(left, x - margin_x)
        tall = min(bottom, y + h + margin_y) - max(top, y - margin_y)
        if wide > 0 and tall > 0:
            total += wide * tall
    return total


def best_top(item: dict[str, Any], tall: float, faces: list[Box], size: tuple[int, int], caption_y: float) -> float | None:
    """The first position clear of every face. None keeps the graphic's normal place."""
    width, height = size
    geo = geometry(width, height)
    home = geo["lt_y"] if item["type"] == "lower_third" else geo["top"]
    left, right = float(geo["gutter"]), float(width - geo["gutter"] - geo["rail"])
    tops = TALL_TOPS if height > width else WIDE_TOPS
    scored = []
    for order, share in enumerate(tops):
        top = float(home) if share is None else height * share
        if top + tall > caption_y - 12:
            continue
        scored.append((overlap_area(top, top + tall, left, right, faces), order, None if share is None else top))
    return min(scored)[2] if scored else None


def place(items: list[dict[str, Any]], picture: Path | None, size: tuple[int, int], brand: Brand) -> list[dict[str, Any]]:
    """Add `y` to any graphic that would land on a face. A hand-set `y` is left alone."""
    if picture is None or not picture.exists():
        return items
    geo = geometry(*size)
    placed = []
    for item in items:
        if item["type"] in FULL_SCREEN or "y" in item:
            placed.append(item)
            continue
        faces = faces_during(picture, item["start"], item["end"], size)
        tall = estimated_height(item, size[0], size[1], brand)
        top = best_top(item, tall, faces, size, geo["cap_y"]) if faces else None
        placed.append({**item, "y": round(top)} if top is not None else item)
    return placed
