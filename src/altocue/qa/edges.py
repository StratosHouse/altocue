"""Stray frames where an insert meets the film: a frame of the wrong shot, or of neither.

A clean hard edge changes once, on the frame the render planned. So settled frames either side of the edge stand for
the two shots, and each frame between must look like the shot it belongs to. Two shots that look alike cannot show a
wrong side, which is why a frame from neither shot is a test of its own. It reads pixels, so a shot that is meant to
contain a flash at its first frame will be named, and a person decides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .. import ffmpeg

REACH = 5  # frames either side of an edge
SETTLED = 2  # the outermost frames each side, which stand for the two shots
SIDE = 48
ALIKE = 6.0  # mean difference from 0 to 255. Under this, two shots cannot be told apart
MARGIN = 0.25  # how much nearer the wrong shot a frame must be, as a share of the gap between shots
FOREIGN_FLOOR = 30.0
FOREIGN_MOTION = 6.0  # a frame from neither shot stands this many times clear of the shots' own motion
EARLY, LATE, FOREIGN = "the next shot arrives early", "the last shot runs late", "a frame from neither shot"


def frame_edges(insert: dict[str, Any], fps: float) -> tuple[int, int]:
    """The first frame of an insert and the first frame after it, rounded as `render.compose_args` rounds them."""
    first = round(float(insert["start"]) * fps)
    return first, first + round((float(insert["end"]) - float(insert["start"])) * fps)


def read_edge(file: Path, frame: int, fps: float) -> list[np.ndarray]:
    """The frames around one edge, shrunk. The file's own timestamps pick them, because sound priming moves a plain seek by a frame."""
    first = max(frame - REACH, 0)
    chosen = f"select='gte(t,{(first - 0.5) / fps:.6f})*lt(t,{(frame + REACH - 0.5) / fps:.6f})'"
    command = ["-ss", f"{max(first / fps - 1.0, 0):.6f}", "-copyts", "-i", str(file), "-map", "0:v:0"]
    command += ["-frames:v", str(frame + REACH - first), "-fps_mode", "passthrough"]
    command += ["-vf", f"{chosen},scale={SIDE}:{SIDE}:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    done = ffmpeg.run_bytes(command)
    size = SIDE * SIDE * 3
    return [np.frombuffer(done[at : at + size], np.uint8).reshape(SIDE, SIDE, 3).astype(np.float32) for at in range(0, len(done) - size + 1, size)]


def apart(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def stray(frames: list[np.ndarray], edge: int = REACH) -> list[tuple[int, str]]:
    """Each wrong frame as its place from the edge, where 0 is the first frame of the new shot."""
    if edge < SETTLED + 1 or len(frames) - edge < SETTLED + 1:
        return []
    old, new = np.mean(frames[:SETTLED], axis=0), np.mean(frames[-SETTLED:], axis=0)
    gap = apart(old, new)
    motion = max(apart(frames[0], frames[1]), apart(frames[-2], frames[-1]))
    found: list[tuple[int, str]] = []
    for index in range(SETTLED, len(frames) - SETTLED):
        frame = frames[index]
        before, after = apart(frame, old), apart(frame, new)
        early = index < edge
        wanted, other = (before, after) if early else (after, before)
        if min(before, after) > max(FOREIGN_FLOOR, FOREIGN_MOTION * motion):
            found.append((index - edge, FOREIGN))
        elif gap >= ALIKE and other + MARGIN * gap < wanted:
            found.append((index - edge, EARLY if early else LATE))
    return found
