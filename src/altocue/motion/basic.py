"""The fallback renderer: Pillow and ffmpeg only. No Node, no browser, simple motion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..brand import Brand
from .html import geometry
from .layer import Layer

ENTRY = 0.12


def _font(brand: Brand, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(brand.font_file), size)
    try:
        font.set_variation_by_axes([weight])
    except OSError:
        pass
    return font


def _rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def _wrap(draw: ImageDraw.ImageDraw, words: list[str], font: ImageFont.FreeTypeFont, width: int, space: float) -> list[list[int]]:
    lines: list[list[int]] = [[]]
    used = 0.0
    for index, word in enumerate(words):
        length = draw.textlength(word, font=font)
        if lines[-1] and used + space + length > width:
            lines.append([])
            used = 0.0
        used += (space if lines[-1] else 0) + length
        lines[-1].append(index)
    return lines


def _shadowed(canvas: Image.Image, paint: Image.Image, radius: int, alpha: float) -> None:
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(paint.getchannel("A").point(lambda v: int(v * alpha)))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius)))
    canvas.alpha_composite(paint)


def caption_frame(size: tuple[int, int], page: dict[str, Any], active: int, brand: Brand) -> Image.Image:
    width, height = size
    geo = geometry(width, height)
    style = brand.caption
    px = int(int(style["size"]) * (geo["scale"] if width > height else 1) * (0.76 if style["style"] == "rail" else 1))
    font = _font(brand, px, 800 if style["style"] == "bold" else 700)
    texts = [w["text"].upper() if style.get("uppercase") or style["style"] == "bold" else w["text"] for w in page["words"]]
    paint = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(paint)
    space = px * 0.26
    area = width - 2 * geo["gutter"] - geo["rail"]
    lines = _wrap(draw, texts, font, int(area), space)
    line_height = px * 1.14
    if style["style"] == "boxed":
        longest = max(sum(draw.textlength(texts[i], font=font) for i in line) + space * (len(line) - 1) for line in lines)
        pad_x, pad_y = px * 0.6, px * 0.34
        left = geo["gutter"] + (area - longest) / 2 - pad_x
        box = (left, geo["cap_y"] - pad_y, left + longest + 2 * pad_x, geo["cap_y"] + line_height * len(lines) + pad_y)
        draw.rounded_rectangle(box, radius=px * 0.42, fill=_rgba(brand.colors["panel"], 214), outline=(255, 255, 255, 26), width=2)
    y = float(geo["cap_y"])
    for line in lines:
        used = sum(draw.textlength(texts[i], font=font) for i in line) + space * (len(line) - 1)
        x = geo["gutter"] + (area - used) / 2
        for index in line:
            color = brand.colors["accent"] if index == active else brand.colors["ink"]
            draw.text((x, y), texts[index], font=font, fill=color)
            x += draw.textlength(texts[index], font=font) + space
        y += line_height
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    if style["style"] == "boxed":
        canvas.alpha_composite(paint)
    else:
        _shadowed(canvas, paint, 9, 0.6)
    return canvas


def card_frame(size: tuple[int, int], item: dict[str, Any], brand: Brand) -> Image.Image:
    """Every graphic reduced to one clean panel, for machines with no browser."""
    width, height = size
    geo = geometry(width, height)
    scale = geo["scale"]
    colors = brand.colors
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    kind = item["type"]
    if kind in {"title", "endcard"}:
        draw.rectangle((0, 0, width, height), fill=_rgba(colors["panel"]))
        text = (item.get("text") or item.get("headline") or "").replace("*", "")
        font = _font(brand, int(100 * scale), 600)
        lines = _wrap(draw, text.split(), font, width - 2 * geo["gutter"], 100 * scale * 0.26)
        words = text.split()
        y = height / 2 - len(lines) * 106 * scale / 2
        for line in lines:
            row = " ".join(words[i] for i in line)
            draw.text(((width - draw.textlength(row, font=font)) / 2, y), row, font=font, fill=colors["ink"])
            y += 106 * scale
        cta = item.get("cta") or item.get("subtitle")
        if cta:
            small = _font(brand, int(36 * scale), 500)
            draw.text(((width - draw.textlength(cta, font=small)) / 2, y + 34 * scale), cta, font=small, fill=colors["accent"])
        return canvas
    primary = str(item.get("title") or item.get("text") or item.get("value") or "").replace("*", "").replace("\n", " ")
    secondary = str(item.get("subtitle") or item.get("label") or item.get("by") or item.get("eyebrow") or "")
    if kind == "steps":
        secondary = "  ·  ".join(step["text"] for step in item.get("items", []))
    large = _font(brand, int((64 if kind in {"hook", "stat"} else 42) * scale), 600)
    small = _font(brand, int(30 * scale), 400)
    area = width - 2 * geo["gutter"] - geo["rail"] - int(64 * scale)
    words = primary.split()
    lines = _wrap(draw, words, large, area, large.size * 0.26) if words else []
    rows = [" ".join(words[i] for i in line) for line in lines]
    pad = int(32 * scale)
    tall = int(len(rows) * large.size * 1.14 + (small.size * 1.5 if secondary else 0) + pad * 2)
    wide = int(max([draw.textlength(r, font=large) for r in rows] + [draw.textlength(secondary, font=small)]) + pad * 2)
    top = item.get("y", geo["lt_y"] if kind == "lower_third" else geo["top"])
    box = (geo["gutter"], top, geo["gutter"] + wide, top + tall)
    draw.rounded_rectangle(box, radius=int(26 * scale), fill=_rgba(colors["surface"], 226), outline=(211, 238, 252, 34), width=2)
    y = top + pad
    for row in rows:
        draw.text((geo["gutter"] + pad, y), row, font=large, fill=colors["accent"] if kind == "stat" else colors["ink"])
        y += large.size * 1.14
    if secondary:
        draw.text((geo["gutter"] + pad, y + 6), secondary, font=small, fill=colors["soft"])
    return canvas


class BasicRenderer:
    id = "basic"
    licence = "MIT"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def render(self, timeline: dict[str, Any], brand: Brand, dest: Path, *, draft: bool = False) -> Layer:
        stage = dest / "basic"
        stage.mkdir(parents=True, exist_ok=True)
        for old in stage.glob("*.png"):
            old.unlink()
        size = (timeline["width"], timeline["height"])
        states: list[tuple[float, float, Image.Image | None]] = []
        for page in timeline["captions"]:
            words = page["words"]
            for index, word in enumerate(words):
                start = page["start"] if index == 0 else word["start"]
                end = page["end"] if index == len(words) - 1 else words[index + 1]["start"]
                if end > start:
                    states.append((start, end, caption_frame(size, page, index, brand)))
        cards = [(item["start"], item["end"], card_frame(size, item, brand)) for item in timeline["items"]]
        return Layer(path=self._write(states, cards, timeline["total"], stage), renderer=self.id, concat=True)

    @staticmethod
    def _write(states, cards, total: float, stage: Path) -> Path:
        """Flatten timed images into one list of still frames ffmpeg can overlay."""
        marks = sorted({0.0, total, *[t for s in states for t in s[:2]], *[t for c in cards for t in c[:2]]})
        entries = []
        size = (states or cards)[0][2].size if (states or cards) else (16, 16)
        blank = stage / "blank.png"
        Image.new("RGBA", size, (0, 0, 0, 0)).save(blank)
        for index, (start, end) in enumerate(zip(marks, marks[1:], strict=False)):
            if end - start < 1e-3:
                continue
            middle = (start + end) / 2
            live = [img for a, b, img in [*cards, *states] if a <= middle < b]
            if live:
                frame = Image.new("RGBA", size, (0, 0, 0, 0))
                for image in live:
                    frame.alpha_composite(image)
                path = stage / f"{index:05d}.png"
                frame.save(path, compress_level=1)
            else:
                path = blank
            entries.append(f"file '{path.name}'\nduration {end - start:.4f}")
        listing = stage / "layer.ffconcat"
        listing.write_text("ffconcat version 1.0\n" + "\n".join(entries) + f"\nfile '{blank.name}'\n")
        return listing
