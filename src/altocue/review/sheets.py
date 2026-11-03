"""Timeline sheets: one picture of a stretch of film, for a reviewer who can neither hear it nor watch it move.

Frames, captions, speech, waveform, loudness and cuts share one time axis, so rhythm and sound can be judged from a
still. Everything drawn is measured from the rendered file or is on screen for any viewer, and the scale never changes
between sheets, so two stretches of film can be compared by eye.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import ffmpeg
from ..qa.gates import stamp

SECONDS = 20.0
FRAME_STEP = 2.0
WIDTH = 1560  # wide enough to read, and the size most vision models take without shrinking
LEFT, RIGHT = 92, 12
HEADER, RULER = 34, 22
CAPTION_ROWS, CAPTION_ROW = 3, 20
SPEECH, SOUND, LOUDNESS = 14, 96, 96
GAP = 6
LOUDEST, QUIETEST = -5.0, -50.0

INK = (8, 13, 19)
PANEL = (17, 25, 32)
TEXT = (243, 247, 250)
MUTED = (143, 160, 173)
MINT = (181, 244, 217)
CYAN = (143, 212, 224)
AMBER = (255, 176, 84)
CAPTION = (38, 56, 66)
HUSH = (58, 34, 40)

LEVEL = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def pages(duration: float) -> list[tuple[float, float]]:
    """Fixed spans, so a second is the same width on every sheet and a short last sheet stays honest about the end."""
    count = max(int(np.ceil(duration / SECONDS - 1e-9)), 1)
    return [(index * SECONDS, (index + 1) * SECONDS) for index in range(count)]


def x_at(seconds: float, index: int) -> int:
    return LEFT + round((seconds - index * SECONDS) / SECONDS * (WIDTH - LEFT - RIGHT))


def lanes(height: int) -> dict[str, tuple[int, int]]:
    """Each lane's top and bottom. The frames take whatever the film's shape leaves."""
    bottom = height - GAP
    found: dict[str, tuple[int, int]] = {"loudness": (bottom - LOUDNESS, bottom)}
    found["sound"] = (found["loudness"][0] - GAP - SOUND, found["loudness"][0] - GAP)
    found["speech"] = (found["sound"][0] - GAP - SPEECH, found["sound"][0] - GAP)
    found["captions"] = (found["speech"][0] - GAP - CAPTION_ROWS * CAPTION_ROW, found["speech"][0] - GAP)
    found["frames"] = (HEADER + RULER, found["captions"][0] - GAP)
    return found


def loudness(file: Path) -> list[tuple[float, float]]:
    """Momentary loudness every 100 ms, in LUFS, as `ebur128` reports it."""
    log = ffmpeg.run(["-i", str(file), "-af", "ebur128=framelog=info", "-vn", "-f", "null", "-"]).stderr
    return [(float(at), float(level)) for at, level in LEVEL.findall(log)]


def pacing(shots: list[Any], words: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    """Rhythm as numbers: what a viewer feels as pace, for a reviewer who cannot feel it."""
    lengths = [round(float(shot.end) - float(shot.start), 2) for shot in shots] or [round(duration, 2)]
    gaps = [float(b["start"]) - float(a["end"]) for a, b in zip(words, words[1:], strict=False)]
    spoken = float(words[-1]["end"]) - float(words[0]["start"]) if words else 0.0
    return {
        "shots": len(lengths),
        "shot_lengths": lengths[:200],
        "shortest_shot": min(lengths),
        "longest_shot": max(lengths),
        "mean_shot": round(sum(lengths) / len(lengths), 2),
        "cuts_per_minute": round((len(lengths) - 1) / max(duration, 0.001) * 60, 1),
        "words_per_minute": round(len(words) / spoken * 60, 1) if spoken > 0 else 0.0,
        "longest_pause": round(max(gaps, default=0.0), 2),
    }


def _bars(picture: ImageDraw.ImageDraw, lane: tuple[int, int], samples: np.ndarray, rate: int, start: float, end: float, index: int) -> None:
    top, bottom = lane
    middle, reach = (top + bottom) // 2, (bottom - top) / 2
    first, last = x_at(start, index), min(x_at(end, index), WIDTH - RIGHT)
    for x in range(first, last):
        a = int(start * rate + (x - first) / (WIDTH - LEFT - RIGHT) * SECONDS * rate)
        chunk = samples[a : a + int(SECONDS * rate / (WIDTH - LEFT - RIGHT))]
        if chunk.size:
            size = float(np.abs(chunk).max()) * reach
            picture.line((x, middle - size, x, middle + size), fill=MINT)


def _trace(picture: ImageDraw.ImageDraw, lane: tuple[int, int], track: list[tuple[float, float]], start: float, end: float, index: int, target: float) -> None:
    top, bottom = lane

    def y_at(level: float) -> int:
        share = (LOUDEST - min(max(level, QUIETEST), LOUDEST)) / (LOUDEST - QUIETEST)
        return top + round(share * (bottom - top))

    for level in (target, -30.0):
        y = y_at(level)
        for x in range(LEFT, WIDTH - RIGHT, 10):
            picture.line((x, y, x + 4, y), fill=MUTED)
        picture.text((WIDTH - RIGHT - 34, y - 15), f"{level:g}", font=_font(12), fill=MUTED)
    points = [(x_at(at, index), y_at(level)) for at, level in track if start <= at <= end]
    if len(points) > 1:
        picture.line(points, fill=TEXT, width=2)


def _captions(picture: ImageDraw.ImageDraw, lane: tuple[int, int], captions: list[dict[str, Any]], start: float, end: float, index: int) -> None:
    """Rows take turns, so a line wider than its moment can run on without covering the next."""
    font = _font(13)
    shown = [page for page in captions if float(page["start"]) < end and float(page["end"]) > start]
    for order, page in enumerate(shown):
        top = lane[0] + (order % CAPTION_ROWS) * CAPTION_ROW
        left, right = x_at(max(float(page["start"]), start), index), x_at(min(float(page["end"]), end), index)
        picture.rectangle((left, top + 1, max(right, left + 2), top + CAPTION_ROW - 2), fill=CAPTION)
        text = " ".join(word["text"] for word in page["words"])
        picture.text((left + 3, top + 2), text, font=font, fill=TEXT)


def _frames(sheet: Image.Image, lane: tuple[int, int], frames: list[Path], start: float, end: float, index: int) -> None:
    for number, path in enumerate(frames):
        at = number * FRAME_STEP
        if start <= at < end:
            with Image.open(path) as picture:
                cell = picture.convert("RGB")
            wide = x_at(FRAME_STEP, 0) - LEFT - 2
            size = (wide, round(cell.height * wide / cell.width))
            sheet.paste(cell.resize(size), (x_at(at, index), lane[0]))


def draw(
    dest: Path,
    *,
    index: int,
    total: int,
    span: tuple[float, float],
    duration: float,
    frames: list[Path],
    samples: np.ndarray | None,
    rate: int,
    track: list[tuple[float, float]],
    captions: list[dict[str, Any]],
    words: list[dict[str, Any]],
    cuts: list[float],
    silence: list[tuple[float, float]],
    target: float,
    summary: str,
) -> Path:
    start, end = span
    if frames:
        with Image.open(frames[0]) as first:
            shape = first.height / first.width
    else:
        shape = 9 / 16
    tall = round((x_at(FRAME_STEP, 0) - LEFT - 2) * shape)
    height = HEADER + RULER + tall + 3 * GAP + CAPTION_ROWS * CAPTION_ROW + SPEECH + SOUND + LOUDNESS + 3 * GAP
    sheet = Image.new("RGB", (WIDTH, height), INK)
    paint = ImageDraw.Draw(sheet)
    found = lanes(height)
    edge = min(end, duration)

    paint.text((LEFT, 8), f"Timeline {index + 1} of {total}: {stamp(start)} to {stamp(edge)}", font=_font(17), fill=TEXT)
    wide = paint.textlength(summary, font=_font(14))
    paint.text((WIDTH - RIGHT - wide, 11), summary, font=_font(14), fill=MUTED)
    for second in range(int(start), int(end) + 1):
        x = x_at(second, index)
        strong = second % 5 == 0
        paint.line((x, HEADER + RULER - (9 if strong else 4), x, HEADER + RULER - 1), fill=MUTED)
        if strong and second < end:
            paint.text((x + 3, HEADER + 1), f"{second // 60:02d}:{second % 60:02d}", font=_font(12), fill=MUTED)

    for name, lane in found.items():
        if name != "frames":
            paint.rectangle((LEFT, lane[0], WIDTH - RIGHT, lane[1]), fill=PANEL)
        paint.text((10, lane[0] + 2), name, font=_font(13), fill=MUTED)
    for a, b in silence:
        if a < end and b > start:
            paint.rectangle((x_at(max(a, start), index), found["sound"][0], x_at(min(b, end), index), found["sound"][1]), fill=HUSH)

    _frames(sheet, found["frames"], frames, start, end, index)
    _captions(paint, found["captions"], captions, start, end, index)
    for word in words:
        if float(word["start"]) < end and float(word["end"]) > start:
            left, right = x_at(max(float(word["start"]), start), index), x_at(min(float(word["end"]), end), index)
            paint.rectangle((left, found["speech"][0] + 2, max(left + 1, right - 1), found["speech"][1] - 2), fill=CYAN)
    if samples is not None:
        _bars(paint, found["sound"], samples, rate, start, edge, index)
    _trace(paint, found["loudness"], track, start, edge, index, target)

    for at in cuts:
        if start <= at < end:
            x = x_at(at, index)
            paint.line((x, HEADER + RULER, x, height - GAP), fill=AMBER, width=2)
    if edge < end:
        x = x_at(edge, index)
        paint.line((x, HEADER, x, height - GAP), fill=TEXT, width=1)
        paint.text((x + 5, HEADER + 1), "end of film", font=_font(12), fill=TEXT)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, quality=90)
    return dest
