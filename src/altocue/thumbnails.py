"""Thumbnail candidates from real frames. A sharp face with open eyes wins where one exists."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import ffmpeg, frame
from .brand import data_path
from .errors import AltocueError
from .models import Clip

CANDIDATES = 6
MOST = 60  # frames read at most, so a long film takes no longer than a short one
PER_SHOT = 6
SAMPLE_EVERY = 0.4  # seconds, close enough to step past a blink
CUT_MARGIN = 0.15  # motion blur and zooms land on the cut
MIN_APART = 1.0  # seconds between candidates, so the options differ
SHARP_SIDE = 160
EYE_SIDE = 48
EYE_WIDTH = 0.30  # of the face box, around YuNet's eye point
EYE_HEIGHT = 0.17
EYE_FLAT = 12  # grey levels. A patch with less contrast than this holds no eye to read
OPEN_EYES = 0.21  # calibrated by eye on 182 real frames of two faces, blinks included
BIG_FACE = 0.12  # share of the frame
BLACK, WHITE = 16, 240
SHEET_TILE = 300
SHEET_COLUMNS = 3
SHEET_LABEL = 52
FONT = ("fonts", "Geist-Variable.ttf")


@dataclass
class Moment:
    """One frame, measured."""

    at: float
    face: bool
    eyes: float | None
    sharp: float
    size: float


def _patch(grey: np.ndarray, centre: tuple[float, float], face: frame.Face) -> np.ndarray | None:
    wide, tall = face.w * EYE_WIDTH, face.h * EYE_HEIGHT
    x, y = centre
    left, right, top, bottom = int(x - wide / 2), int(x + wide / 2), int(y - tall / 2), int(y + tall / 2)
    patch = grey[max(top, 0) : bottom, max(left, 0) : right]
    return cv2.resize(patch, (EYE_SIDE, max(4, round(EYE_SIDE * tall / wide))), interpolation=cv2.INTER_CUBIC) if patch.size else None


def _one_eye(patch: np.ndarray) -> float:
    """How tall the dark of the eye stands, as a share of its width. An iris is tall. A shut lid leaves a thin lash line."""
    middle, darkest = float(np.median(patch)), float(np.percentile(patch, 2))
    if middle - darkest < EYE_FLAT:
        return 0.0
    dark = patch < darkest + 0.45 * (middle - darkest)
    height, width = dark.shape
    return float(np.percentile(dark[:, width // 4 : 3 * width // 4].sum(axis=0), 80) / width)


def eye_openness(grey: np.ndarray, face: frame.Face) -> float | None:
    """The mean of both eyes, in greys. None when the detector gave no eye points to measure."""
    patches = [_patch(grey, eye, face) for eye in face.eyes]
    found = [_one_eye(patch) for patch in patches if patch is not None]
    return sum(found) / len(found) if found else None


def sharpness(grey: np.ndarray) -> float:
    """Variance of the Laplacian at a fixed size, so a big face does not read sharper than a small one."""
    height, width = grey.shape
    small = cv2.resize(grey, (SHARP_SIDE, max(4, round(SHARP_SIDE * height / width))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(small, cv2.CV_32F).var())


def moments(shots: list[tuple[float, float]]) -> list[float]:
    """Times worth looking at, inside each shot and clear of its cuts."""
    times: list[float] = []
    for start, end in shots:
        span = end - start - 2 * CUT_MARGIN
        if span <= 0:
            continue
        count = min(PER_SHOT, max(1, round(span / SAMPLE_EVERY)))
        times += [round(start + CUT_MARGIN + span * (index + 0.5) / count, 3) for index in range(count)]
    if len(times) > MOST:
        times = [times[index * len(times) // MOST] for index in range(MOST)]
    return times


def measure(pixels: np.ndarray, at: float) -> Moment | None:
    """Score one RGB uint8 frame. Black and blown-out frames are not candidates."""
    grey = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY).astype(np.float32)
    if not BLACK < float(grey.mean()) < WHITE:
        return None
    faces = frame.detect_faces(pixels)
    if not faces:
        return Moment(at, False, None, sharpness(grey), 0.0)
    face = faces[0]
    left, top = max(int(face.x), 0), max(int(face.y), 0)
    crop = grey[top : int(face.y + face.h), left : int(face.x + face.w)]
    height, width = grey.shape
    return Moment(at, True, eye_openness(grey, face), sharpness(crop if crop.size else grey), face.area / (width * height))


def _tier(moment: Moment) -> int:
    if not moment.face:
        return 0
    return 2 if moment.eyes is not None and moment.eyes >= OPEN_EYES else 1


def _quality(moment: Moment, sharpest: float) -> float:
    eyes = 0.6 + 0.4 * min((moment.eyes or 0.0) / (OPEN_EYES * 1.5), 1.0)
    size = 0.7 + 0.3 * min(moment.size / BIG_FACE, 1.0)
    return moment.sharp / max(sharpest, 1e-6) * eyes * size


def pick(found: list[Moment], count: int) -> list[Moment]:
    """The best frames that are not near each other: open eyes, then any face, then the sharpest picture."""
    sharpest = max((m.sharp for m in found), default=1.0)
    ranked = sorted(found, key=lambda m: (_tier(m), _quality(m, sharpest)), reverse=True)
    chosen: list[Moment] = []
    for moment in ranked:
        if all(abs(moment.at - other.at) >= MIN_APART for other in chosen):
            chosen.append(moment)
        if len(chosen) == count:
            break
    return chosen


def why(moment: Moment) -> str:
    if not moment.face:
        return "no face, sharpest picture"
    return "sharp face, eyes open" if _tier(moment) == 2 else "face, eyes look shut or could not be read"


def _sheet(chosen: list[dict[str, Any]], folder: Path) -> str:
    """Every option side by side, numbered to match its file."""
    font = ImageFont.truetype(str(data_path(*FONT)), 20)
    tiles = []
    for number, candidate in enumerate(chosen, 1):
        with Image.open(folder / candidate["file"]) as image:
            tile = image.convert("RGB")
        tile.thumbnail((SHEET_TILE, SHEET_TILE * 2))
        tiles.append((number, candidate, tile))
    tall = max(tile.height for _, _, tile in tiles)
    columns = min(SHEET_COLUMNS, len(tiles))
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * SHEET_TILE, rows * (tall + SHEET_LABEL)), (12, 16, 20))
    draw = ImageDraw.Draw(sheet)
    for index, (number, candidate, tile) in enumerate(tiles):
        x, y = index % columns * SHEET_TILE, index // columns * (tall + SHEET_LABEL)
        sheet.paste(tile, (x + (SHEET_TILE - tile.width) // 2, y))
        draw.text((x + 10, y + tall + 12), f"{number}   {candidate['at']:.1f} s", font=font, fill=(243, 247, 250))
    sheet.save(folder / "contact-sheet.jpg", quality=88)
    return "contact-sheet.jpg"


def write(source: str | Path, shots: list[tuple[float, float]], folder: Path, *, count: int = CANDIDATES) -> dict[str, Any]:
    """Measure frames of `source`, save the best as stills beside a contact sheet, and report why each was chosen."""
    clip: Clip = ffmpeg.probe_clip(source)
    times = moments(shots) or [clip.duration / 2]

    def load(at: float) -> np.ndarray | None:
        return frame.read_rgb(clip, at)

    found: list[Moment] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for at, pixels in zip(times, pool.map(load, times), strict=True):
            scored = measure((pixels * 255 + 0.5).astype(np.uint8), at) if pixels is not None else None
            if scored:
                found.append(scored)
    if not found:
        raise AltocueError(f"No usable frame in {source}.", fix="Check the film: every sampled frame was black, white or unreadable.")
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("thumb-*.jpg"):
        old.unlink()
    chosen = []
    for number, moment in enumerate(pick(found, count), 1):
        name = f"thumb-{number}.jpg"
        ffmpeg.frame(source, moment.at, folder / name)
        chosen.append({"file": name, **asdict(moment), "why": why(moment)})
    return {"read": len(found), "candidates": chosen, "sheet": _sheet(chosen, folder)}
