"""Read the text in frames. Apple Vision on a Mac, tesseract elsewhere, stated when neither."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from .. import ffmpeg

SWIFT = Path(__file__).with_name("ocr.swift")
BATCH = 80
TESSERACT_MODE = "6"
TESSERACT_FLOOR = 30
BRIGHT_TEXT = 200
TILES = {  # ffmpeg crop per tile, each 60 percent of the frame, so small print reaches the reader
    "tl": "iw*0.6:ih*0.6:0:0",
    "tr": "iw*0.6:ih*0.6:iw*0.4:0",
    "bl": "iw*0.6:ih*0.6:0:ih*0.4",
    "br": "iw*0.6:ih*0.6:iw*0.4:ih*0.4",
}
NO_READER = "No text reader on this machine. Install tesseract (brew install tesseract, or sudo apt install tesseract-ocr)."


def _cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "altocue"


@lru_cache(maxsize=1)
def vision_binary() -> Path | None:
    """Compile the Vision reader once per version of its source."""
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        return None
    source = SWIFT.read_bytes()
    target = _cache() / f"ocr-{hashlib.sha256(source).hexdigest()[:12]}"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(f".{os.getpid()}")
    try:
        done = subprocess.run(["swiftc", "-O", str(SWIFT), "-o", str(temp)], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None
    if done.returncode != 0 or not temp.exists():
        return None
    temp.replace(target)
    return target


def reader() -> tuple[str | None, str]:
    """The reader this machine will use, or None and why."""
    if vision_binary():
        return "vision", ""
    if shutil.which("tesseract"):
        return "tesseract", ""
    return None, NO_READER


def _vision(images: list[Path]) -> list[dict[str, Any]]:
    binary = vision_binary()
    found: list[dict[str, Any]] = []
    for start in range(0, len(images), BATCH):
        done = subprocess.run(
            [str(binary), *[str(p) for p in images[start : start + BATCH]]], capture_output=True, text=True, errors="replace"
        )
        for line in done.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "text" in record:
                found.append({**record, "reader": "vision"})
    return found


def _tesseract_lines(image: Path, source: Path, size: tuple[int, int]) -> list[dict[str, Any]]:
    done = subprocess.run(
        ["tesseract", str(image), "stdout", "--psm", TESSERACT_MODE, "tsv"], capture_output=True, text=True, errors="replace"
    )
    width, height = size
    lines: dict[tuple[str, str, str], list[list[str]]] = {}
    for row in done.stdout.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) >= 12 and cells[0] == "5" and cells[11].strip() and float(cells[10]) >= TESSERACT_FLOOR:
            lines.setdefault((cells[2], cells[3], cells[4]), []).append(cells)
    found = []
    for cells in lines.values():
        left = min(int(c[6]) for c in cells)
        top = min(int(c[7]) for c in cells)
        right = max(int(c[6]) + int(c[8]) for c in cells)
        bottom = max(int(c[7]) + int(c[9]) for c in cells)
        found.append(
            {
                "file": str(source),
                "text": " ".join(c[11] for c in cells),
                "confidence": sum(float(c[10]) for c in cells) / len(cells) / 100,
                "box": [left / width, top / height, (right - left) / width, (bottom - top) / height],
                "reader": "tesseract",
            }
        )
    return found


def _tesseract_one(image: Path) -> list[dict[str, Any]]:
    """Read the frame as it is, then again with only its bright text.

    Tesseract finds light captions over footage only once they are isolated. Its sparse modes,
    11 and 12, read nothing on these frames, so this is mode 6 twice.
    """
    with Image.open(image) as picture:
        size = picture.size
        bright = picture.convert("L").point([0 if v > BRIGHT_TEXT else 255 for v in range(256)])
    with tempfile.TemporaryDirectory(prefix="altocue-tess-") as folder:
        isolated = Path(folder) / "bright.png"
        bright.save(isolated)
        found = _tesseract_lines(image, image, size) + _tesseract_lines(isolated, image, size)
    unique: dict[str, dict[str, Any]] = {}
    for record in found:
        unique.setdefault(record["text"].lower(), record)
    return list(unique.values())


def read_images(images: list[Path]) -> list[dict[str, Any]]:
    """Text boxes as `file, text, confidence, box, reader`. Boxes are x, y, w, h from the top left, 0 to 1."""
    chosen, _ = reader()
    if chosen == "vision":
        return _vision(images)
    if chosen == "tesseract":
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
            return [record for found in pool.map(_tesseract_one, images) for record in found]
    return []


def sample(file: Path, fps: float, cuts: list[float], folder: Path) -> dict[str, float]:
    """Write whole frames, tiles and every cut's first frame. Returns image name to time."""
    ffmpeg.run(["-i", str(file), "-vf", f"fps={fps}", "-q:v", "2", str(folder / "full_%05d.jpg")])
    for name, crop in TILES.items():
        ffmpeg.run(
            ["-i", str(file), "-vf", f"fps={fps},crop={crop},scale=iw*2:ih*2:flags=lanczos", "-q:v", "2", str(folder / f"{name}_%05d.jpg")]
        )
    times = {p.name: (int(re.search(r"_(\d+)\.jpg$", p.name).group(1)) - 1) / fps for p in folder.glob("*.jpg")}  # type: ignore[union-attr]
    for index, at in enumerate(cuts):
        name = f"cut_{index:05d}.jpg"
        ffmpeg.frame(file, max(at, 0.0), folder / name)
        times[name] = at
    return times


def read_video(file: Path, fps: float, cuts: list[float], folder: Path) -> list[dict[str, Any]]:
    """Every text box in the sampled frames, each with the time it was seen."""
    times = sample(file, fps, cuts, folder)
    records = read_images(sorted(folder / name for name in times))
    return [{**r, "time": times[Path(r["file"]).name]} for r in records]


def confidence(hits: list[dict[str, Any]]) -> str:
    """Two readers or two samples is a finding. One of each is often texture read as letters."""
    if len({h["reader"] for h in hits}) > 1 or len({h["time"] for h in hits}) > 1:
        return "high"
    return "review"


def merge_ranges(times: list[float], step: float) -> list[list[float]]:
    ranges: list[list[float]] = []
    for at in sorted(set(times)):
        if ranges and at - ranges[-1][1] <= step * 1.5:
            ranges[-1][1] = at
        else:
            ranges.append([at, at])
    return [[round(a, 2), round(b + step, 2)] for a, b in ranges]
