"""Find the phrases and breaths in a voice, and level it phrase by phrase.

The gain moves only under speech. Pauses stay at unity, so room tone and breaths are not pumped,
and a breath comes down only when asked, and is never cut.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

FRAME = 2048
HOP_SECONDS = 0.01
SPEECH_BAND = (200.0, 4000.0)
HISS_BAND = (4500.0, 10000.0)
PITCH = (70.0, 400.0)
VOICED = 0.55  # a voice repeats itself at its pitch, and a breath does not
ACTIVE_OVER_ROOM_DB = 6.0
ACTIVE_UNDER_SPEECH_DB = 30.0
NEAR_VOICED = 0.1  # consonants sit beside the vowels
PHRASE_GAP = 0.25
SHORTEST_PHRASE = 0.1
LONGEST_PHRASE = 3.0  # a long run is split at its quietest dip, so a line that trails off can be caught
SHORTEST_SPLIT = 1.0
OVER_PEAK_DB = 15.0  # how far under its peak a frame can be and still say how loud the phrase is
MOST_BOOST_DB = 9.0
MOST_CUT_DB = 6.0
FULL_BOOST_CLEAR_DB = 40.0  # a room this far under the speech can rise with it unheard
NO_BOOST_CLEAR_DB = 20.0
RISE = 0.03
FALL = 0.12
BLEND = 0.12
BREATH_SHORTEST = 0.12
BREATH_LONGEST = 0.9
BREATH_UNDER_SPEECH_DB = 12.0
BREATH_RAMP = 0.025
HEADROOM_DB = 0.1


@dataclass
class Found:
    """Sample positions of what was heard."""

    phrases: list[tuple[int, int]]
    breaths: list[tuple[int, int]]


def _frames(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Speech-band level, hiss-band level and how strongly each frame repeats at a pitch."""
    hop = round(rate * HOP_SECONDS)
    count = (len(samples) - FRAME) // hop + 1 if len(samples) >= FRAME else 0
    speech, hiss, repeats = np.empty(count), np.empty(count), np.empty(count)
    window = np.hanning(FRAME)
    freqs = np.fft.rfftfreq(2 * FRAME, 1 / rate)
    voice_band = (freqs >= SPEECH_BAND[0]) & (freqs <= SPEECH_BAND[1])
    hiss_band = (freqs >= HISS_BAND[0]) & (freqs <= HISS_BAND[1])
    least, most = int(rate / PITCH[1]), int(rate / PITCH[0])
    taper = np.fft.irfft(np.abs(np.fft.rfft(window, 2 * FRAME)) ** 2)  # the window's own fall with lag, which low voices would lose to
    usable = samples.astype(np.float64)
    for first in range(0, count, 4096):
        block = np.stack([usable[i * hop : i * hop + FRAME] for i in range(first, min(first + 4096, count))]) * window
        power = np.abs(np.fft.rfft(block, 2 * FRAME, axis=1)) ** 2
        speech[first : first + 4096] = 10 * np.log10(power[:, voice_band].sum(axis=1) / FRAME**2 + 1e-12)
        hiss[first : first + 4096] = 10 * np.log10(power[:, hiss_band].sum(axis=1) / FRAME**2 + 1e-12)
        echo = np.fft.irfft(power * voice_band, axis=1)
        repeats[first : first + 4096] = (echo[:, least:most] / taper[least:most]).max(axis=1) * taper[0] / (echo[:, 0] + 1e-12)
    return speech, hiss, repeats


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True))


def _grow(mask: np.ndarray, frames: int) -> np.ndarray:
    return np.convolve(mask.astype(np.int32), np.ones(2 * frames + 1, dtype=np.int32))[frames : frames + len(mask)] > 0


def _close(mask: np.ndarray, frames: int, blocked: np.ndarray) -> np.ndarray:
    """Bridge short gaps, unless a breath sits in one: it stays out of the phrase's gain."""
    closed = mask.copy()
    for a, b in _runs(~mask):
        if 0 < a and b < len(mask) and b - a < frames and not blocked[a:b].any():
            closed[a:b] = True
    return closed


def _split(span: tuple[int, int], level: np.ndarray, longest: int, shortest: int) -> list[tuple[int, int]]:
    a, b = span
    if b - a <= longest:
        return [span]
    at = a + shortest + int(np.argmin(level[a + shortest : b - shortest]))
    return _split((a, at), level, longest, shortest) + _split((at, b), level, longest, shortest)


def analyse(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None = None) -> Found:
    """Phrases and breaths. `words` are transcript times, and noise under a word is never a breath."""
    speech, hiss, repeats = _frames(samples, rate)
    if not len(speech):
        return Found([], [])
    per_second = 1 / HOP_SECONDS
    loudest, room = float(np.percentile(speech, 95)), float(np.percentile(speech, 10))
    active = speech > max(room + ACTIVE_OVER_ROOM_DB, loudest - ACTIVE_UNDER_SPEECH_DB)
    voiced = active & (repeats > VOICED)
    spoken = _grow(voiced, round(NEAR_VOICED * per_second)) & active
    for start, end in words or ():
        where = slice(round(start * per_second), round(end * per_second))
        spoken[where] |= active[where]
    breaths = [
        (a, b)
        for a, b in _runs(active & ~spoken)
        if BREATH_SHORTEST * per_second <= b - a <= BREATH_LONGEST * per_second
        and speech[a:b].max() <= loudest - BREATH_UNDER_SPEECH_DB
        and hiss[a:b].mean() < speech[a:b].mean()  # hiss above the voice band is a sibilant
    ]
    blocked = np.zeros(len(speech), dtype=bool)
    for a, b in breaths:
        blocked[a:b] = True
    closed = _close(spoken, round(PHRASE_GAP * per_second), blocked)
    smooth = np.convolve(speech, np.ones(21) / 21)[10 : 10 + len(speech)]
    phrases: list[tuple[int, int]] = []
    for span in _runs(closed):
        if span[1] - span[0] >= SHORTEST_PHRASE * per_second and voiced[span[0] : span[1]].any():
            phrases += _split(span, smooth, round(LONGEST_PHRASE * per_second), round(SHORTEST_SPLIT * per_second))
    hop = round(rate * HOP_SECONDS)
    centre = FRAME // 2 - hop // 2
    return Found([(a * hop + centre, b * hop + centre) for a, b in phrases], [(a * hop + centre, b * hop + centre) for a, b in breaths])


def _phrase_level(level: np.ndarray) -> float:
    """Mean level of the frames near the phrase's peak, so its pauses and tails do not count."""
    loud = level[level > level.max() - OVER_PEAK_DB]
    return float(10 * np.log10(np.mean(10 ** (loud / 10))))


def _gains(samples: np.ndarray, rate: int, phrases: list[tuple[int, int]]) -> list[float]:
    speech, _, _ = _frames(samples, rate)
    hop = round(rate * HOP_SECONDS)
    spans = [(a // hop, max(b // hop, a // hop + 1)) for a, b in phrases]
    levels = np.array([_phrase_level(speech[a:b]) for a, b in spans])
    weights = np.array([b - a for a, b in spans], dtype=float)
    target = float(np.average(levels, weights=weights))
    clear = float(np.percentile(speech, 95) - np.percentile(speech, 10))
    boost = MOST_BOOST_DB * float(np.clip((clear - NO_BOOST_CLEAR_DB) / (FULL_BOOST_CLEAR_DB - NO_BOOST_CLEAR_DB), 0, 1))
    gains = []
    for (a, b), found in zip(phrases, levels, strict=True):
        peak = 20 * np.log10(np.abs(samples[a:b]).max() + 1e-9)
        gains.append(float(min(np.clip(target - found, -MOST_CUT_DB, boost), -HEADROOM_DB - peak)))
    return gains


def _shoulders(count: int, first: int, last: int, rise: int, fall: int) -> tuple[slice, np.ndarray]:
    """A window of 1 from `first` to `last` with raised-cosine shoulders, clipped to the clip."""
    low, high = max(first - rise, 0), min(last + fall, count)
    at = np.arange(low, high)
    up = np.clip((at - (first - rise)) / max(rise, 1), 0, 1)
    down = np.clip(((last + fall) - at) / max(fall, 1), 0, 1)
    return slice(low, high), (0.5 - 0.5 * np.cos(np.pi * up)) * (0.5 - 0.5 * np.cos(np.pi * down))


def gain_curve(count: int, rate: int, found: Found, gains: list[float], breath_down_db: float) -> np.ndarray:
    """Gain in dB per sample. Neighbours from a split phrase hand over to each other."""
    curve = np.zeros(count)
    for index, ((a, b), gain) in enumerate(zip(found.phrases, gains, strict=True)):
        joined_before = index > 0 and found.phrases[index - 1][1] == a
        joined_after = index + 1 < len(found.phrases) and found.phrases[index + 1][0] == b
        half = round(BLEND * rate) // 2
        where, shape = _shoulders(
            count,
            a + half if joined_before else a,
            b - half if joined_after else b,
            2 * half if joined_before else round(RISE * rate),
            2 * half if joined_after else round(FALL * rate),
        )
        curve[where] += gain * shape
    ramp = round(BREATH_RAMP * rate)
    for a, b in found.breaths if breath_down_db else ():
        where, shape = _shoulders(count, a, b, ramp, ramp)
        curve[where] -= breath_down_db * shape
    return curve


def level_voice(
    samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None = None, phrases: bool = True, breath_down_db: float = 0.0
) -> tuple[np.ndarray, dict[str, Any]]:
    """The voice with its phrases level and its breaths turned down, and what was done."""
    found = analyse(samples, rate, words)
    gains = _gains(samples, rate, found.phrases) if found.phrases and phrases else [0.0] * len(found.phrases)
    report = {
        "phrases": len(found.phrases),
        "breaths": len(found.breaths),
        "breath_down_db": breath_down_db if found.breaths else 0.0,
        "most_boost_db": round(max(gains, default=0.0), 2) if phrases else 0,
        "most_cut_db": round(min(gains, default=0.0), 2) if phrases else 0,
    }
    if not any(gains) and not (found.breaths and breath_down_db):
        return samples, report
    return samples * 10 ** (gain_curve(len(samples), rate, found, gains, breath_down_db) / 20), report
