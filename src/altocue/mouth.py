"""Mouth noise: take out clicks and lip smacks, and turn sharp S sounds down. Length never changes.

A mouth click is a short burst, under about 60 ms, standing alone in quiet: quiet before it and quiet
after it. A word's onset never is, because it runs on into the word. Each click is turned down 34 dB
under short fades, so the room tone under it stays and nothing is cut.

The de-esser is a split-band compressor. Only the 3.4 to 14 kHz band is turned down, only while it is
louder than its usual level in this clip, and at most the strength's limit. ffmpeg's own `deesser` did
nothing at gentle settings and took 12 dB at the next step on a real voice, so this one is measured here.
"""

from __future__ import annotations

from typing import Any

import numpy as np

HOP_SECONDS = 0.002
CLICK_HOPS = 30  # a burst longer than 60 ms is not a click
QUIET_BEFORE = 30  # 60 ms of quiet before it
QUIET_AFTER = 20  # 40 ms of quiet after it
QUIET_SHARE = 0.8
CLICK_GAIN_DB = -34.0
FADE = 0.004
LEAD, TAIL = 0.006, 0.010  # widen each click a little, so its edges go too
AFTER_WORD = 0.15  # a burst this soon after a word is the word's release
BEFORE_WORD = 0.09  # a burst this soon before a word may be its plosive
DEESS_DB = {"off": 0.0, "light": 4.0, "normal": 7.0, "strong": 11.0}
BAND = (3400.0, 14000.0)
SIZE, STEP = 1024, 256
SLOPE = 0.85  # dB taken per dB over the threshold
CHUNK = 4096  # frames per block, a multiple of SIZE // STEP


def _db(samples: np.ndarray, hop: int) -> np.ndarray:
    count = len(samples) // hop
    blocks = samples[: count * hop].reshape(count, hop).astype(np.float64)
    return 10 * np.log10((blocks**2).mean(axis=1) + 1e-12)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True))


def find_clicks(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None = None) -> list[tuple[float, float]]:
    """Clicks as (start, end) in seconds. With word times, only bursts in a real gap between words count."""
    hop = max(1, int(rate * HOP_SECONDS))
    level = _db(samples, hop)
    if len(level) < QUIET_BEFORE + QUIET_AFTER + 1:
        return []
    high = _db(np.r_[0.0, np.diff(samples)], hop)  # clicks live above the voice's body, so judge them with the lows taken off
    floor, floor_high = np.percentile(level, 15), np.percentile(high, 15)
    speech = np.percentile(level, 90)
    loud = (high > floor_high + 14) | (level > floor + 16)
    quiet = level < min(floor + 9, speech - 22)
    bridged = loud.copy()
    bridged[1:-1] |= loud[:-2] & loud[2:]  # a one-hop dip does not end a burst
    sums = np.r_[0, np.cumsum(quiet)]
    found = []
    for start, end in _runs(bridged):
        if end - start > CLICK_HOPS:
            continue
        before = (sums[start] - sums[start - QUIET_BEFORE]) / QUIET_BEFORE if start >= QUIET_BEFORE else 1.0
        after = (sums[end + QUIET_AFTER] - sums[end]) / QUIET_AFTER if end + QUIET_AFTER <= len(quiet) else 1.0
        if before > QUIET_SHARE and after > QUIET_SHARE:
            found.append((start * hop / rate, end * hop / rate))
    if not words:
        return found
    kept = []
    for start, end in found:
        if any(a <= start <= b for a, b in words):
            continue
        if any(0 <= start - b < AFTER_WORD for _, b in words):
            continue
        if any(0 <= a - end < BEFORE_WORD for a, _ in words):
            continue
        kept.append((start, end))
    return kept


def quiet_clicks(samples: np.ndarray, rate: int, clicks: list[tuple[float, float]]) -> np.ndarray:
    """Turn each click down under raised-cosine fades."""
    out = samples.astype(np.float32, copy=True)
    gain = 10 ** (CLICK_GAIN_DB / 20)
    fade = int(FADE * rate)
    for start, end in clicks:
        a, b = max(0, int((start - LEAD) * rate)), min(len(out), int((end + TAIL) * rate))
        g = np.full(b - a, gain, np.float32)
        f = min(fade, (b - a) // 2)
        if f:
            ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, f))).astype(np.float32)
            g[:f] = 1 + (gain - 1) * ramp
            g[-f:] = gain + (1 - gain) * ramp
        out[a:b] *= g
    return out


def _frames(padded: np.ndarray) -> np.ndarray:
    return np.lib.stride_tricks.sliding_window_view(padded, SIZE)[::STEP]


def deess(samples: np.ndarray, rate: int, most_db: float) -> tuple[np.ndarray, dict[str, float]]:
    """Turn the S band down while it is louder than usual, by at most `most_db`."""
    if most_db <= 0 or len(samples) < SIZE:
        return samples, {"most_db": 0.0, "share": 0.0}
    window = np.hanning(SIZE).astype(np.float32)
    padded = np.pad(samples.astype(np.float32), (SIZE, SIZE + STEP))
    frames = _frames(padded)
    count = len(frames) - len(frames) % (SIZE // STEP)
    freqs = np.fft.rfftfreq(SIZE, 1 / rate)
    low, high = BAND
    shape = (np.clip((freqs - low) / 800, 0, 1) * np.clip((high - freqs) / 1500, 0, 1)).astype(np.float32)
    level = np.empty(count, np.float64)
    for at in range(0, count, CHUNK):
        spectrum = np.fft.rfft(frames[at : at + CHUNK][: count - at] * window, axis=1)
        level[at : at + len(spectrum)] = 10 * np.log10(((np.abs(spectrum) ** 2) * shape).sum(axis=1) / SIZE + 1e-12)
    voiced = level[level > np.percentile(level, 60)]
    if not len(voiced):
        return samples, {"most_db": 0.0, "share": 0.0}
    threshold = np.percentile(voiced, 60)  # only the loud sibilants
    taken = np.convolve(np.clip((level - threshold) * SLOPE, 0, most_db), np.ones(5) / 5, mode="same")  # no zipper noise
    if taken.max() <= 0.05:
        return samples, {"most_db": 0.0, "share": 0.0}
    out = np.zeros(len(padded), np.float64)
    norm = np.zeros(len(padded), np.float64)
    overlap = SIZE // STEP
    for at in range(0, count, CHUNK):
        block = frames[at : at + CHUNK][: count - at]
        gains = 10 ** (-(taken[at : at + len(block), None] * shape[None, :]) / 20)
        back = np.fft.irfft(np.fft.rfft(block * window, axis=1) * gains, n=SIZE, axis=1) * window
        for k in range(overlap):  # frames k, k+4, k+8... sit end to end, so each set adds in one slice
            part = back[k::overlap]
            first = (at + k) * STEP
            out[first : first + part.size] += part.ravel()
            norm[first : first + part.size] += np.tile(window**2, len(part))
    out /= np.maximum(norm, 1e-6)
    cleaned = out[SIZE : SIZE + len(samples)].astype(np.float32)
    return cleaned, {"most_db": round(float(taken.max()), 1), "share": round(float((taken > 1).mean()), 3)}


def sibilance_db(samples: np.ndarray, rate: int) -> float:
    """The loudest 1% of the 5 to 10 kHz band, in 10 ms frames."""
    spectrum = np.fft.rfft(samples.astype(np.float64))
    freqs = np.fft.rfftfreq(len(samples), 1 / rate)
    band = np.fft.irfft(spectrum * ((freqs >= 5000) & (freqs <= 10000)), len(samples))
    level = _db(band, max(1, rate // 100))
    return round(float(np.percentile(level, 99)), 1) if len(level) else -120.0


def clean_mouth(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None, declick: bool, strength: str) -> tuple[np.ndarray, dict[str, Any]]:
    """Declick, then de-ess. The report says what was found and how much was taken."""
    clicks = find_clicks(samples, rate, words) if declick else []
    out = quiet_clicks(samples, rate, clicks) if clicks else samples
    before = sibilance_db(out, rate)
    out, taken = deess(out, rate, DEESS_DB[strength])
    report = {
        "clicks": len(clicks),
        "clicks_at": [round(start, 3) for start, _ in clicks[:50]],
        "deess_most_db": taken["most_db"],
        "deess_share": taken["share"],
        "sibilance_db": [before, sibilance_db(out, rate)],
    }
    return out, report
