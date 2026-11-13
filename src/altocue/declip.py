"""Clipped speech: find the flat tops where a recording hit its ceiling, and draw the peaks back in.

A clipped run is three or more samples in a row held at the recording's own extreme, to within a step
of 16-bit audio, struck at a slope on both sides. A clean recording's loudest moment is a single sample,
or a gentle rounded top, so a clean file has no run and passes through this step untouched. Each short run is redrawn with a cubic fitted to the samples either
side of it, never under the flat top and never more than 6 dB over it. Then the whole clip is turned
down by just enough that the redrawn peaks fit under -1 dBFS. A run longer than 2.5 ms is left as it is,
because too much of the wave is missing to guess. This runs ahead of the denoiser, which hears a flat
top as noise to remove.
"""

from __future__ import annotations

from typing import Any

import numpy as np

TOLERANCE = 1.5 / 32768  # a step and a half of 16-bit audio
LEAST_RUN = 3  # samples. Two equal samples can straddle a real peak
EDGE_STEP = 8 / 32768  # a flat top is struck at a slope: the samples either side sit at least this far under it.
# A deep hum's smooth peak can hold three samples level too, but it rounds over gently, a step or two at a time
LONGEST_SECONDS = 0.0025  # a longer run is left alone
LEAST_LEVEL = 0.25  # a flat top under -12 dBFS is a quiet clip's loudest moment, not a ceiling
CONTEXT = (4, 16)  # samples either side the cubic is fitted to: the run's length, within these
MOST_OVER = 2.0  # a redrawn peak goes at most 6 dB over its flat top
HEADROOM = 10 ** (-1 / 20)  # redrawn peaks fit under -1 dBFS


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True))


def find_clipped(samples: np.ndarray) -> list[tuple[int, int, int]]:
    """Flat tops as (first sample, end, sign), in order. Each polarity is read apart, as a recorder may clip one side only."""
    found: list[tuple[int, int, int]] = []
    if not len(samples):
        return found
    for sign in (1, -1):
        side = sign * np.asarray(samples, np.float64)
        top = float(side.max())
        if top < LEAST_LEVEL:
            continue
        for a, b in _runs(side >= top - TOLERANCE):
            edges = [top - side[i] for i in (a - 1, b) if 0 <= i < len(side)]
            if b - a >= LEAST_RUN and edges and min(edges) >= EDGE_STEP:
                found.append((a, b, sign))
    return sorted(found)


def _redraw(out: np.ndarray, clipped: np.ndarray, a: int, b: int, sign: int) -> bool:
    reach = int(np.clip(b - a, *CONTEXT))
    left = np.arange(max(0, a - reach), a)
    right = np.arange(b, min(len(out), b + reach))
    left, right = left[~clipped[left]], right[~clipped[right]]
    if len(left) < 2 or len(right) < 2 or len(left) + len(right) < 6:
        return False  # the file's edge, or another flat top too close: nothing to fit
    known = np.r_[left, right]
    middle, scale = (a + b - 1) / 2, max(b - a, 1)
    fitted = np.polyval(np.polyfit((known - middle) / scale, out[known], 3), (np.arange(a, b) - middle) / scale)
    flat = sign * out[a:b]
    out[a:b] = sign * np.clip(sign * fitted, flat, flat * MOST_OVER)
    return True


def declip(samples: np.ndarray, rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Redraw short flat tops. Returns `samples` itself when nothing was clipped or nothing could be redrawn."""
    runs = find_clipped(samples)
    lengths = [b - a for a, b, _ in runs]
    report: dict[str, Any] = {
        "runs": len(runs),
        "samples": int(sum(lengths)),
        "longest_ms": round(max(lengths, default=0) / rate * 1000, 2),
        "repaired": 0,
        "headroom_db": 0.0,
    }
    if not runs:
        return samples, report
    out = np.asarray(samples, np.float64).copy()
    clipped = np.zeros(len(out), bool)
    for a, b, _ in runs:
        clipped[a:b] = True
    longest = max(LEAST_RUN, round(LONGEST_SECONDS * rate))
    report["repaired"] = sum(_redraw(out, clipped, a, b, sign) for a, b, sign in runs if b - a <= longest)
    if not report["repaired"]:
        return samples, report
    peak = float(np.abs(out).max())
    if peak > HEADROOM:
        out *= HEADROOM / peak
        report["headroom_db"] = round(float(20 * np.log10(HEADROOM / peak)), 2)
    return out.astype(np.float32), report
