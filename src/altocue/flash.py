"""Count flashes in the picture. WCAG 2.2 criterion 2.3.1 allows no more than three in any one second.

A flash is a pair of opposing swings in relative luminance, each at least a tenth of full scale, with the
darker state under 0.8. The frame is read in tiles, so a small corner flicker and a full-frame blink both
count, at a coarse size. This is a measure to catch mistakes, not a certified test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from . import ffmpeg

LIMIT = 3
SWING = 0.1
DARK = 0.8
TILES = (6, 4)


def _tiles(path: Path, fps: float) -> tuple[np.ndarray, float]:
    columns, rows = TILES
    command = [ffmpeg.require(), "-v", "error", "-i", str(path), "-vf", f"scale={columns}:{rows}:flags=area,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, rows * columns).astype(np.float64) / 255
    return frames**2.2, fps


def swings(trace: np.ndarray) -> list[int]:
    """Frame indexes where the level has swung the far way from its last turn."""
    turns, low, high, rising, at = [], trace[0], trace[0], None, 0
    for at, value in enumerate(trace):
        low, high = min(low, value), max(high, value)
        if rising is not True and value - low >= SWING and low < DARK:
            turns.append(at)
            rising, high = True, value
        elif rising is not False and high - value >= SWING and value < DARK:
            turns.append(at)
            rising, low = False, value
    return turns


def measure(path: str | Path) -> dict[str, Any]:
    """The worst one-second window in any tile, in flashes."""
    target = Path(path)
    fps = ffmpeg.probe_clip(target).fps or 30.0
    frames, _ = _tiles(target, fps)
    peak, when = 0.0, 0.0
    for tile in frames.T:
        turns = np.array(swings(tile)) / fps
        for index, at in enumerate(turns):
            pairs = int(np.sum(turns[index:] < at + 1.0)) / 2
            if pairs > peak:
                peak, when = pairs, float(at)
    return {"peak_per_second": peak, "at": round(when, 3), "limit": LIMIT, "passed": peak <= LIMIT}
