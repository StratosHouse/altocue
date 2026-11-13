"""A music track's beat grid, read from its own sound with NumPy: an onset envelope, a tempo by autocorrelation, the beat phase and a bar line.

The grid assumes a steady tempo, which most library and stock music has. It is a measurement, not a listen: the
report carries a confidence, and below `CONFIDENT` nothing is placed by it. The bar line is the beat of four that
carries the most low-end attack, which is where a kick drum usually sits. That is a guess, and the report says so.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DOWN = 4  # analysed at a quarter of the rate. Beats live in attacks, not in the top octave
HOP = 128  # 10.7 ms at 12 kHz
SIZE = 512
FINE_HOP = 120  # 2.5 ms at 48 kHz, to put the grid on the attacks once the tempo is known
FINE_REACH = 0.035  # seconds either way the grid may slide onto the attacks
TEMPO_RANGE = (70.0, 180.0)
TEMPO_PRIOR = 120.0  # a tie between a tempo and its double goes to the one nearer this
PRIOR_OCTAVES = 1.0
STRETCH = 0.02  # how far the tempo is searched around the autocorrelation peak, as a share
CONFIDENT = 0.3  # normalised autocorrelation at the beat period. A click track reads near 1, noise near 0. [house]
LEAST_BEATS = 16  # four bars, or the grid is not believed
BEATS_PER_BAR = 4
LOW_HZ = 150.0
SMOOTH = 0.4  # seconds of envelope mean taken off, so a swell is not an attack
ATTACK_FLOOR = 10.0  # the loudest attacks must rise this far, in summed log spectrum. A held tone reads about 2
NEAR = 3  # frames either side of a beat where its attack is looked for
CHUNK = 2048  # frames of spectrum held at once
BAR_SURE = 1.2  # the bar beat's attack over the next best beat's, before the bar line is used. Four even kicks read 1.0


def _mono(samples: np.ndarray) -> np.ndarray:
    return np.asarray(samples, dtype=np.float64).mean(axis=1) if np.ndim(samples) == 2 else np.asarray(samples, dtype=np.float64)


def _flux(mono: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Spectral flux, all bands and under `LOW_HZ`, in frames of `HOP` at a quarter of the rate."""
    count = len(mono) // DOWN
    low = mono[: count * DOWN].reshape(count, DOWN).mean(axis=1)
    rate_low = rate / DOWN
    frames = (len(low) - SIZE) // HOP + 1
    if frames < 8:
        return np.zeros(0), np.zeros(0), rate_low / HOP
    view = np.lib.stride_tricks.sliding_window_view(low, SIZE)[::HOP][:frames]
    bins = np.fft.rfftfreq(SIZE, 1 / rate_low) < LOW_HZ
    window = np.hanning(SIZE)
    flux, kick = np.zeros(frames), np.zeros(frames)
    before: np.ndarray | None = None
    for first in range(0, frames, CHUNK):  # in pieces, so a long track never holds its whole spectrum at once
        magnitude = np.abs(np.fft.rfft(view[first : first + CHUNK] * window, axis=1))
        joined = magnitude if before is None else np.vstack([before, magnitude])
        rise = np.maximum(np.diff(np.log1p(1000 * joined), axis=0), 0)
        low_rise = np.maximum(np.diff(joined[:, bins], axis=0), 0)  # linear, so a hat's spill into the low end stays small
        filled = slice(first + len(magnitude) - len(rise), first + len(magnitude))  # the first frame of all has nothing before it, and no rise
        flux[filled], kick[filled] = rise.sum(axis=1), low_rise.sum(axis=1)
        before = magnitude[-1:]
    frame_rate = rate_low / HOP
    width = max(int(SMOOTH * frame_rate), 1)

    def detrended(values: np.ndarray) -> np.ndarray:
        mean = np.convolve(values, np.ones(width) / width, mode="same")
        return np.maximum(values - mean, 0)

    return detrended(flux), detrended(kick), frame_rate


def _comb(envelope: np.ndarray, period: float, phases: np.ndarray) -> np.ndarray:
    """Mean envelope on a grid of this period, for each phase."""
    count = int((len(envelope) - 1 - phases.max()) // period) + 1
    if count < 1:
        return np.zeros(len(phases))
    at = phases[:, None] + period * np.arange(count)[None, :]
    return np.interp(at.ravel(), np.arange(len(envelope)), envelope).reshape(at.shape).mean(axis=1)


def _tempo(envelope: np.ndarray, frame_rate: float) -> tuple[float, float]:
    """Beat period in frames, and the normalised autocorrelation there."""
    centred = envelope - envelope.mean()
    size = 1 << int(np.ceil(np.log2(2 * len(centred))))
    spectrum = np.fft.rfft(centred, size)
    ac = np.fft.irfft(np.abs(spectrum) ** 2, size)[: len(centred)]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    shortest, longest = int(np.floor(60 / TEMPO_RANGE[1] * frame_rate)), int(np.ceil(60 / TEMPO_RANGE[0] * frame_rate))
    if longest + 2 >= len(ac):
        return 0.0, 0.0
    lags = np.arange(max(shortest, 1), longest + 1)
    bpm = 60 * frame_rate / lags
    prior = np.exp(-0.5 * (np.log2(bpm / TEMPO_PRIOR) / PRIOR_OCTAVES) ** 2)
    best = int(lags[np.argmax(np.maximum(ac[lags], 0) * prior)])
    left, middle, right = ac[best - 1], ac[best], ac[best + 1]
    bend = left - 2 * middle + right
    shift = 0.5 * (left - right) / bend if bend < 0 else 0.0
    return best + float(np.clip(shift, -0.5, 0.5)), float(max(middle, 0.0))


def _around(envelope: np.ndarray, frames: np.ndarray) -> np.ndarray:
    """The strongest attack within `NEAR` frames of each position. An attack is a frame or two wide."""
    padded = np.pad(envelope, NEAR)
    centres = np.clip(np.rint(frames).astype(int), 0, len(envelope) - 1) + NEAR
    return np.stack([padded[centres + shift] for shift in range(-NEAR, NEAR + 1)]).max(axis=0)


def _fine(mono: np.ndarray, rate: int) -> np.ndarray:
    """Rise in level every `FINE_HOP` samples: a sharp attack envelope for placing the grid."""
    count = len(mono) // FINE_HOP
    level = np.log10(np.mean(mono[: count * FINE_HOP].reshape(count, FINE_HOP) ** 2, axis=1) + 1e-10)
    return np.maximum(np.diff(level, prepend=level[:1]), 0)


def grid(samples: np.ndarray, rate: int) -> dict[str, Any]:
    """The beat grid of a track, in its own seconds.

    Returns `bpm`, `confidence` (0 to 1), `confident`, `beats` and `downbeats` in seconds from the top of the
    track, `beats_per_bar`, `bar_strength` (how much the chosen bar beat stands out over the next best, 1 is
    no difference), `bar_sure`, and `why` when the grid is not to be used.
    """
    mono = _mono(samples)
    seconds = len(mono) / rate
    empty = {"bpm": None, "confidence": 0.0, "confident": False, "beats": [], "downbeats": [], "beats_per_bar": BEATS_PER_BAR, "bar_strength": None, "bar_sure": False}
    envelope, low, frame_rate = _flux(mono, rate)
    if not len(envelope) or np.percentile(envelope, 99) < ATTACK_FLOOR:
        return {**empty, "why": "no clear attacks to count"}
    lag, confidence = _tempo(envelope, frame_rate)
    if not lag:
        return {**empty, "why": "too short to hold a beat"}
    periods = lag * (1 + np.linspace(-STRETCH, STRETCH, 41))
    # The beat is where the low end hits as well as the rest. Off-beat hats alone would put the grid half a beat out.
    placing = envelope / (envelope.mean() + 1e-12) + (low / (low.mean() + 1e-12) if low.any() else 0)
    best = (-1.0, lag, 0.0)
    for period in periods:
        phases = np.arange(0, period, 0.25)
        scores = _comb(placing, period, phases)
        index = int(np.argmax(scores))
        if scores[index] > best[0]:
            best = (float(scores[index]), float(period), float(phases[index]))
    _, period, phase = best
    step = period / frame_rate
    first = phase / frame_rate + SIZE / 2 / (rate / DOWN)  # a frame's flux belongs to the middle of its window
    fine = _fine(mono, rate)
    fine_rate = rate / FINE_HOP
    offsets = np.arange(-FINE_REACH, FINE_REACH + 1e-9, 1 / fine_rate)
    starts = (first % step) + offsets
    count = int((seconds - starts.max()) // step) + 1
    if count >= 1 and len(fine):
        at = (starts[:, None] + step * np.arange(count)[None, :]) * fine_rate
        scores = np.interp(at.ravel(), np.arange(len(fine)), fine).reshape(at.shape).sum(axis=1)
        first = float(starts[int(np.argmax(scores))])
    first = first % step
    beats = np.arange(first, seconds, step)
    bpm = 60 / step
    if len(beats) < LEAST_BEATS:
        return {**empty, "bpm": round(bpm, 2), "confidence": round(confidence, 3), "why": f"only {len(beats)} beats, fewer than {LEAST_BEATS}"}
    accents = _around(low if low.any() else envelope, beats * frame_rate - SIZE / 2 / HOP)
    strength = np.array([accents[index::BEATS_PER_BAR].mean() for index in range(BEATS_PER_BAR)])
    order = np.argsort(strength)[::-1]
    bar = int(order[0])
    bar_strength = float(strength[order[0]] / strength[order[1]]) if strength[order[1]] > 0 else None
    bar_sure = bool(strength[order[0]] > 0) if bar_strength is None else bar_strength >= BAR_SURE
    confident = confidence >= CONFIDENT
    return {
        "bpm": round(bpm, 2),
        "confidence": round(confidence, 3),
        "confident": confident,
        "beats": [round(float(beat), 4) for beat in beats],
        "downbeats": [round(float(beat), 4) for beat in beats[bar::BEATS_PER_BAR]],
        "beats_per_bar": BEATS_PER_BAR,
        "bar_strength": None if bar_strength is None else round(bar_strength, 2),
        "bar_sure": bar_sure,
        **({} if confident else {"why": f"tempo confidence {confidence:.2f} is under {CONFIDENT}"}),
    }
