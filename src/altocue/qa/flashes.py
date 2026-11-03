"""Flashes, measured the way WCAG 2.2 success criterion 2.3.1 defines them.

Three a second at most. A general flash is a pair of opposing changes in relative luminance of a tenth or more,
where the darker state is under 0.80. A red flash is a pair of opposing changes in and out of saturated red. Either
one counts only over a large enough area: a quarter of any window a third of the picture each way, which is WCAG's
341 by 256 on a 1024 by 768 screen, applied to the film's own frame. Real viewing size is unknown, so this measures
the file and clears nobody. It is not a medical test and is no Harding test.

https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
"""

from __future__ import annotations

import subprocess
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import ffmpeg
from ..errors import AltocueError

MOST = 3  # flashes in any one second
SWING = 0.10  # of full relative luminance
DARKER_UNDER = 0.80
RED_SHARE = 0.8  # R / (R + G + B) in a saturated red state
RED_SHIFT = 0.2  # distance between states on the CIE 1976 u'v' diagram
GRID = 36  # cells each way. A window is 12 of them, and a quarter of a window is 36 cells
WINDOW = GRID // 3
AREA_CELLS = WINDOW * WINDOW // 4
LINEAR = np.array([value / 255 / 12.92 if value / 255 <= 0.04045 else ((value / 255 + 0.055) / 1.055) ** 2.4 for value in range(256)], np.float32)
MATRIX = np.array(((0.4124564, 0.3575761, 0.1804375), (0.2126729, 0.7151522, 0.0721750), (0.0193339, 0.1191920, 0.9503041)), np.float32)
WHITE_UV = (0.1978, 0.4683)


def read_frames(file: Path) -> Iterator[np.ndarray]:
    """Every frame, shrunk to the grid and streamed, so a long film costs no more memory than a short one."""
    command = [ffmpeg.require(), "-v", "error", "-nostdin", "-i", str(file), "-map", "0:v:0", "-fps_mode", "passthrough"]
    command += ["-vf", f"scale={GRID}:{GRID}:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    size = GRID * GRID * 3
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as reader:
        assert reader.stdout is not None
        while len(block := reader.stdout.read(size)) == size:
            yield np.frombuffer(block, np.uint8).reshape(-1, 3)
        reader.stdout.close()
        if reader.wait() != 0:
            raise AltocueError("ffmpeg could not read the picture to count flashes.")


def luminance(frame: np.ndarray) -> np.ndarray:
    return LINEAR[frame] @ MATRIX[1]


class Tally:
    """How many changes each cell made in the last second, and the worst any large enough area reached."""

    def __init__(self, frames: int):
        self.count = np.zeros(GRID * GRID, np.int32)
        self.recent: deque[np.ndarray] = deque(maxlen=frames)
        self.most = 0
        self.at = 0

    def add(self, changed: np.ndarray, frame: int) -> None:
        if len(self.recent) == self.recent.maxlen:
            self.count -= self.recent[0]
        self.recent.append(changed.astype(np.int32))
        self.count += self.recent[-1]
        reach = int(np.partition(self.count, -AREA_CELLS)[-AREA_CELLS])  # the most that enough cells reached, close together or not
        if reach > self.most and (reach <= 2 * MOST or together(self.count >= reach)):
            self.most, self.at = reach, max(frame - len(self.recent) + 1, 0)


def together(cells: np.ndarray) -> bool:
    """Whether enough of these cells fall inside one window of the picture."""
    grid = np.zeros((GRID + 1, GRID + 1), np.int32)
    grid[1:, 1:] = cells.reshape(GRID, GRID).cumsum(0).cumsum(1)
    inside = grid[WINDOW:, WINDOW:] - grid[:-WINDOW, WINDOW:] - grid[WINDOW:, :-WINDOW] + grid[:-WINDOW, :-WINDOW]
    return int(inside.max()) >= AREA_CELLS


class Swings:
    """Finds each change of a tenth or more in luminance, however many frames it took."""

    def __init__(self, first: np.ndarray):
        self.low, self.high = first.copy(), first.copy()
        self.going = np.zeros(first.shape, np.int8)

    def step(self, level: np.ndarray) -> np.ndarray:
        self.low = np.minimum(self.low, level)
        self.high = np.maximum(self.high, level)
        rises = (self.going <= 0) & (level - self.low >= SWING) & (self.low < DARKER_UNDER)
        falls = (self.going >= 0) & (self.high - level >= SWING) & (level < DARKER_UNDER) & ~rises
        self.going[rises], self.high[rises] = 1, level[rises]
        self.going[falls], self.low[falls] = -1, level[falls]
        return rises | falls


class Reds:
    """Finds each move into or out of saturated red that travels far enough across the colour diagram."""

    def __init__(self, first: np.ndarray):
        self.red, self.u, self.v = self.read(first)

    @staticmethod
    def read(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        light = LINEAR[frame]
        total = light.sum(axis=1)
        red = (light[:, 0] >= RED_SHARE * total) & (total > 0)
        x, y, z = (light @ MATRIX.T).T
        under = x + 15 * y + 3 * z
        shown = under > 0
        u, v = np.full(x.shape, WHITE_UV[0], np.float32), np.full(x.shape, WHITE_UV[1], np.float32)
        u[shown], v[shown] = 4 * x[shown] / under[shown], 9 * y[shown] / under[shown]
        return red, u, v

    def step(self, frame: np.ndarray) -> np.ndarray:
        red, u, v = self.read(frame)
        same = red == self.red
        moved = ~same & (np.hypot(u - self.u, v - self.v) > RED_SHIFT)
        keep = same | moved
        self.red[moved] = red[moved]
        self.u[keep], self.v[keep] = u[keep], v[keep]
        return moved


@dataclass
class Found:
    general: float
    red: float
    at: float  # seconds, the start of the worst second

    @property
    def worst(self) -> float:
        return max(self.general, self.red)

    @property
    def over(self) -> bool:
        return self.worst > MOST


def scan(frames: Iterable[np.ndarray], fps: float) -> Found:
    """The most flashes in any one second. A flash is two changes, so seven changes is the fourth one begun."""
    second = max(round(fps), 1)
    general, reds = Tally(second), Tally(second)
    swings, red = None, None
    for index, frame in enumerate(frames):
        level = luminance(frame)
        if swings is None or red is None:
            swings, red = Swings(level), Reds(frame)
            continue
        general.add(swings.step(level), index)
        reds.add(red.step(frame), index)
    worst = general if general.most >= reds.most else reds
    return Found(general.most // 2 + general.most % 2, reds.most // 2 + reds.most % 2, worst.at / max(fps, 0.001))
