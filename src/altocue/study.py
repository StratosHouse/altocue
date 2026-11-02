"""Measure a reference film into a style card: how it is paced, its colours, how loud it is and what it says.

Learn pacing and structure from a reference. Only recreate work you own or have rights to.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from . import ffmpeg
from .errors import AltocueError
from .qa import ocr

RULE = "Learn pacing and structure from references. Only recreate work you own or have rights to."
CUT_SCORE = 10.0  # ffmpeg scdet's score. Its own default, not a finding
SWATCHES = 6
SAMPLES = 24
MIN_GAP = 0.12  # a flash inside a cut reads as two cuts without this
TEXT_SAMPLES = 12


def cut_points(path: Path) -> list[float]:
    done = ffmpeg.run(["-i", str(path), "-vf", f"scale=320:-2,scdet=threshold={CUT_SCORE}", "-an", "-f", "null", "-"])
    found = [float(at) for at in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", done.stderr)]
    kept: list[float] = []
    for at in found:
        if at > 0 and (not kept or at - kept[-1] >= MIN_GAP):
            kept.append(round(at, 3))
    return kept


def palette(path: Path, duration: float) -> list[str]:
    """The main colours across the film, most used first. Near-identical shades merge."""
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        pixels = []
        for index in range(SAMPLES):
            at = duration * (index + 0.5) / SAMPLES
            still = Path(scratch) / f"{index}.png"
            ffmpeg.frame(path, at, still, width=96)
            if still.exists():
                with Image.open(still) as image:
                    pixels.append(np.asarray(image.convert("RGB")).reshape(-1, 3))
    if not pixels:
        return []
    joined = Image.fromarray(np.concatenate(pixels)[None, ...].astype(np.uint8), "RGB")
    quant = joined.quantize(colors=SWATCHES, method=Image.Quantize.MEDIANCUT)
    table = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)
    return [f"#{table[i * 3]:02x}{table[i * 3 + 1]:02x}{table[i * 3 + 2]:02x}" for _, i in counts]


def words_on_screen(path: Path, duration: float) -> tuple[list[str], str]:
    """Words on screen, from a spread of frames. Empty with a reason if this machine has no text reader."""
    name, reason = ocr.reader()
    if name is None:
        return [], reason
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        stills = []
        for index in range(TEXT_SAMPLES):
            still = Path(scratch) / f"{index}.png"
            ffmpeg.frame(path, duration * (index + 0.5) / TEXT_SAMPLES, still)
            if still.exists():
                stills.append(still)
        rows = ocr.read_images(stills)
    seen: dict[str, None] = {}
    for row in rows:
        text = str(row.get("text", "")).strip()
        if text:
            seen.setdefault(text)
    return list(seen), ""


def study(path: str | Path, *, read_text: bool = True) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise AltocueError(f"No video at {target}.", fix="Check the path.")
    clip = ffmpeg.probe_clip(target)
    duration = clip.duration
    cuts = cut_points(target)
    edges = [0.0, *cuts, duration]
    shots = [round(b - a, 3) for a, b in zip(edges, edges[1:], strict=False) if b > a]
    card: dict[str, Any] = {
        "source": target.name,
        "note": f"Measured for pacing and structure. {RULE} The card holds numbers, never pixels, so check your rights before recreating anything.",
        "duration": round(duration, 3),
        "fps": clip.fps,
        "size": list(clip.display_size),
        "cuts": cuts,
        "shots": len(shots),
        "average_shot": round(sum(shots) / len(shots), 3) if shots else None,
        "shortest_shot": min(shots, default=None),
        "longest_shot": max(shots, default=None),
        "cuts_per_10s": round(len(cuts) / duration * 10, 2) if duration else 0.0,
        "palette": palette(target, duration),
        "loudness": None,
        "text": [],
    }
    if clip.has_audio:
        try:
            card["loudness"] = ffmpeg.loudness(target)
        except AltocueError as error:
            card["loudness_error"] = str(error.args[0])
    if read_text:
        card["text"], reason = words_on_screen(target, duration)
        if reason:
            card["text_note"] = reason
    return card

