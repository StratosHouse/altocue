"""Edges land on real speech, then on the frame grid. Ported constants, do not tune by eye."""

from __future__ import annotations

import math

from ..models import Word
from ..transcribe import HOP, Envelope

PRE, POST = 0.10, 0.14  # pad every cut: 100 to 200 ms, trimmed to the breath
IN_BACK, IN_AHEAD = 0.05, 0.9
OUT_BACK, OUT_AHEAD = 0.6, 0.05
LATE_ONSET = 0.8
FIRST_WORD_LEAD = 0.04
LAST_WORD_TAIL = 0.06
SHORTEST = 0.3
OVERLAP = 0.08  # a word is in the shot when this much of it plays
EPSILON = 1e-6
FRAME_SLACK = 1e-3  # edges are stored to the microsecond, so frame maths needs this much give


def snap(
    envelope: Envelope,
    cin: float,
    cout: float,
    *,
    snap_in: bool = True,
    snap_out: bool = True,
    first_word_start: float | None = None,
    last_word_end: float | None = None,
) -> tuple[float, float]:
    """In point to the first sustained speech onset near it, out point to the last speech frame before it."""
    speech = envelope.speech
    frames = len(speech)
    new_in, new_out = cin, cout
    if snap_in:
        first, stop = int(max(0.0, cin - IN_BACK) / HOP), int(min(frames, (cin + IN_AHEAD) / HOP))
        onset = next((i * HOP for i in range(first, stop) if speech[i : i + 3].all()), None)
        if onset is not None:
            new_in = max(cin - IN_BACK, round(onset - PRE, 3))
        if first_word_start is not None and new_in > first_word_start + LATE_ONSET:
            new_in = round(first_word_start - FIRST_WORD_LEAD, 3)
    if snap_out:
        first, stop = int(max(0.0, cout - OUT_BACK) / HOP), int(min(frames, (cout + OUT_AHEAD) / HOP))
        tail = next(((j + 1) * HOP for j in range(stop - 1, first - 1, -1) if speech[j]), None)
        if tail is not None:
            new_out = round(tail + POST, 3)
        if last_word_end is not None:
            new_out = max(new_out, round(last_word_end + LAST_WORD_TAIL, 3))
    if new_out - new_in < SHORTEST:
        return cin, cout
    return max(0.0, new_in), new_out


def to_grid(start: float, end: float, fps: int, *, floor: float, ceiling: float) -> tuple[float, float]:
    """Both edges sit on the frame grid, so a shot's length is a whole number of frames.

    Rounding each edge on its own later is what left black last frames at cuts. An edge on the
    grid cannot round twice, and `ceiling` stops a shot running past the last full frame.
    """
    first = math.floor(start * fps + FRAME_SLACK)
    if first / fps < floor - EPSILON:
        first += 1
    last = math.ceil(end * fps - FRAME_SLACK)
    if last / fps > ceiling + EPSILON:
        last -= 1
    return round(first / fps, 6), round(last / fps, 6)


def last_full_frame(duration: float, fps: int) -> float:
    return math.floor(duration * fps + FRAME_SLACK) / fps


def frame_count(start: float, end: float, fps: int) -> int:
    """The one place a shot's length becomes frames. Every stage must agree on it."""
    return round((end - start) * fps)


def overlap(word: Word, start: float, end: float) -> float:
    return min(word.end, end) - max(word.start, start)


def plays(word: Word, start: float, end: float, *, tolerance: float = 0.0) -> bool:
    """In when 80 ms of it plays, or all of it for a word shorter than that, give or take a frame."""
    length = word.end - word.start
    return overlap(word, start, end) >= min(OVERLAP, max(length - tolerance, length / 2)) - EPSILON
