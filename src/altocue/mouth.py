"""Mouth noise: take out clicks and lip smacks, and soften harsh S sounds. Length never changes.

A mouth click is a short burst, under about 60 ms, standing alone in quiet: quiet before it and quiet
after it. A word's onset never is, because it runs on into the word. Each click is turned down 34 dB
under short fades, so the room tone under it stays and nothing is cut.

The de-esser judges each S against the voice around it, never against the other S sounds, so a good
microphone with natural S sounds comes out untouched. A frame is voiced when the body of the voice,
200 to 3000 Hz, outweighs its S band. A frame belongs to an S when the S band, 3.5 to 12 kHz, outweighs
the body, for longer than a plosive's burst. Each S is measured as its S band against the voiced speech
within half a second of it: its S-to-voice, in dB. Only an S above the strength's ceiling is softened,
only down toward that ceiling, never by more than the strength's cap and never under the lisp floor.
The cut is one gain for the whole S, eased in and out, in a bell around the centre of this speaker's S
energy, so the air above it and every vowel, breath and plosive stay exactly as they were.
ffmpeg's own `deesser` did nothing at gentle settings and took 12 dB at the next step on a real voice.
A first version of this one judged each S against the clip's other S sounds, so it always turned the
louder half down, and at 7 and 11 dB it made a good voice lisp. Hence the floor and the small caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .errors import AltocueError

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
SIZE, STEP = 1024, 256
CHUNK = 4096  # frames per block
BODY = (200.0, 3000.0)  # the body of the voice
SIB = (3500.0, 12000.0)  # where an S is measured. Fixed, so clips and reports compare
CENTRE_RANGE = (3500.0, 9500.0)  # where this speaker's S centre is looked for
FULL_OCTAVES = 0.5  # the cut is full this far either side of the centre: one octave, where the old shelf took two
BELOW_OCTAVES, ABOVE_OCTAVES = 0.5, 1 / 3  # then eased to nothing, sooner above, so the air is left alone
VOICED_OVER_DB = 6.0  # a voiced frame: its body this much over its S band
VOICE_RANGE_DB = 30.0  # and within this of the clip's loud voice
SIB_OVER_DB = 0.0  # an S frame: its S band at least as strong as its body
SIB_RANGE_DB = 30.0  # and no more than this under the voice. Quieter is room tone
SIB_SECONDS = 0.035  # an S is longer than this. A plosive's burst or a click is shorter
BRIDGE = 2  # frames. A dip this short does not split an S
BESIDE_SECONDS = 0.1  # an S runs into or out of voiced speech this close. A breath alone in a pause does not
NEAR_SECONDS = 0.5  # the voice an S is judged against: the voiced speech this close to it
NEAR_LEAST = 10  # voiced frames needed nearby, or the clip's voice as a whole is used
EASE = 3  # frames eased in and out at each end of an S, about 16 ms at 48 kHz
LEAST_DB = 0.3  # a smaller cut is not made
# Rules of thumb on this measure, not yet calibrated on real voices by ear. A natural S reads roughly 8 to 12 dB
# under the voice. An S level with the voice is harsh. Taken far under the floor, an S starts to sound like a th.
LISP_FLOOR_DB = -10.0  # no S is ever taken under this, on any strength
DEESS_CEILING = {"off": None, "light": -2.0, "normal": -4.0, "strong": -6.0}  # an S above this is softened toward it
DEESS_DB = {"off": 0.0, "light": 2.0, "normal": 3.5, "strong": 5.0}  # the most each strength takes off one S
GUARD_MOST_DB = max(DEESS_DB.values())  # what any strength may take. A report over this predates the guard


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


WINDOW_SUM = 1.5  # a periodic Hann window, squared and laid every quarter of its length, sums to this everywhere


def _frames(padded: np.ndarray) -> np.ndarray:
    return np.lib.stride_tricks.sliding_window_view(padded, SIZE)[::STEP]


def _padded(samples: np.ndarray) -> np.ndarray:
    return np.pad(samples.astype(np.float32), (SIZE, SIZE + STEP))


def _window() -> np.ndarray:
    return np.hanning(SIZE + 1)[:-1].astype(np.float32)


def _power_db(levels: np.ndarray) -> float:
    """The power mean of levels in dB, so a loud stretch counts as loud."""
    return float(10 * np.log10(np.mean(10 ** (np.asarray(levels, np.float64) / 10)) + 1e-12))


def _levels(frames: np.ndarray, rate: int, bands: tuple[tuple[float, float], ...]) -> list[np.ndarray]:
    """Each band's level per frame, in dB."""
    freqs = np.fft.rfftfreq(SIZE, 1 / rate)
    masks = [(freqs >= low) & (freqs <= high) for low, high in bands]
    window = _window()
    found = [np.empty(len(frames)) for _ in bands]
    for at in range(0, len(frames), CHUNK):
        power = np.abs(np.fft.rfft(frames[at : at + CHUNK] * window, axis=1)) ** 2
        for level, mask in zip(found, masks, strict=True):
            level[at : at + len(power)] = 10 * np.log10(power[:, mask].sum(axis=1) / SIZE + 1e-12)
    return found


@dataclass
class Sibilants:
    """Every S in one voice, and the voice each is judged against. Levels are dB per frame, STEP samples apart."""

    sib: np.ndarray
    voice: np.ndarray
    runs: list[tuple[int, int]]

    def each(self, sib: np.ndarray | None = None) -> list[float]:
        """Each S against the voice near it: its S-to-voice, in dB."""
        level = self.sib if sib is None else sib
        return [_power_db(level[a:b]) - _power_db(self.voice[a:b]) for a, b in self.runs]

    def overall(self, sib: np.ndarray | None = None) -> float | None:
        """All the S sounds together against the voice: how bright this voice's S sounds are."""
        if not self.runs:
            return None
        level = self.sib if sib is None else sib
        frames = np.concatenate([np.arange(a, b) for a, b in self.runs])
        return round(_power_db(level[frames]) - _power_db(self.voice[frames]), 1)


def _find(frames: np.ndarray, rate: int) -> Sibilants | None:
    body, sib = _levels(frames, rate, (BODY, SIB))
    voiced = (body > sib + VOICED_OVER_DB) & (body > np.percentile(body, 95) - VOICE_RANGE_DB)
    if voiced.sum() < 2 * NEAR_LEAST:
        return None
    power = np.where(voiced, 10 ** (body / 10), 0.0)
    whole = 10 * np.log10(power[voiced].mean())
    reach = max(1, round(NEAR_SECONDS * rate / STEP))
    at = np.arange(len(body))
    low, high = np.clip(at - reach, 0, len(body)), np.clip(at + reach + 1, 0, len(body))
    sums, counts = np.r_[0.0, np.cumsum(power)], np.r_[0, np.cumsum(voiced)]
    near, many = sums[high] - sums[low], counts[high] - counts[low]
    voice = np.where(many >= NEAR_LEAST, 10 * np.log10(np.maximum(near, 1e-20) / np.maximum(many, 1)), whole)
    hissing = (sib > body + SIB_OVER_DB) & (sib > voice - SIB_RANGE_DB)
    runs: list[tuple[int, int]] = []
    for start, end in _runs(hissing):
        if runs and start - runs[-1][1] <= BRIDGE:
            runs[-1] = (runs[-1][0], end)
        else:
            runs.append((start, end))
    least = int(np.ceil((SIB_SECONDS * rate + SIZE / 2) / STEP))  # a frame hears SIZE samples, so a short burst lights several
    beside = max(1, round(BESIDE_SECONDS * rate / STEP))  # an S frame is never voiced, so any voiced frame in reach is beside it
    kept = [(a, b) for a, b in runs if b - a >= least and counts[min(b + beside, len(body))] > counts[max(a - beside, 0)]]
    return Sibilants(sib, voice, kept)


def find_sibilants(samples: np.ndarray, rate: int) -> Sibilants | None:
    """Every S in a voice. None when there is too little voiced speech to judge an S against."""
    return _find(_frames(_padded(samples)), rate) if len(samples) >= SIZE else None


def s_to_voice(samples: np.ndarray, rate: int) -> float | None:
    """How loud this voice's S sounds are against its body, in dB. None with no S or too little voice."""
    found = find_sibilants(samples, rate)
    return found.overall() if found else None


def _centre(frames: np.ndarray, found: Sibilants, rate: int) -> float:
    """The centre of this speaker's S energy: the power-weighted mean of the log frequency, over every S."""
    picked = np.concatenate([np.arange(a, b) for a, b in found.runs])
    window = _window()
    total = np.zeros(SIZE // 2 + 1)
    for at in range(0, len(picked), CHUNK):
        total += (np.abs(np.fft.rfft(frames[picked[at : at + CHUNK]] * window, axis=1)) ** 2).sum(axis=0)
    freqs = np.fft.rfftfreq(SIZE, 1 / rate)
    band = (freqs >= SIB[0]) & (freqs <= SIB[1])
    weight = total[band] / max(float(total[band].sum()), 1e-30)
    return float(np.clip(2 ** float((weight * np.log2(freqs[band])).sum()), *CENTRE_RANGE))


def bell(freqs: np.ndarray, centre: float) -> np.ndarray:
    """1 within half an octave of the centre, eased to 0 over half an octave below and a third above."""
    octaves = np.log2(np.maximum(freqs, 1.0) / centre)
    over = np.where(octaves < 0, (-octaves - FULL_OCTAVES) / BELOW_OCTAVES, (octaves - FULL_OCTAVES) / ABOVE_OCTAVES)
    return np.where(over <= 0, 1.0, np.where(over >= 1, 0.0, 0.5 + 0.5 * np.cos(np.pi * np.clip(over, 0, 1))))


def _eased(length: int) -> np.ndarray:
    """One gain for the whole S, eased in and out, so nothing pumps or clicks."""
    ramp = min(EASE, length // 3)
    shape = np.ones(length)
    if ramp:
        edge = 0.5 - 0.5 * np.cos(np.pi * np.arange(1, ramp + 1) / (ramp + 1))
        shape[:ramp], shape[-ramp:] = edge, edge[::-1]
    return shape


def plan_cut(level: float, strength: str) -> tuple[float, bool]:
    """How many dB to take off one S at this S-to-voice, and whether the guard held any back.

    Only down toward the ceiling, never more than the strength's cap, never under the lisp floor.
    """
    ceiling = DEESS_CEILING[strength]
    if ceiling is None:
        return 0.0, False
    wanted = level - ceiling
    if wanted < LEAST_DB:
        return 0.0, False
    cut = min(wanted, DEESS_DB[strength], GUARD_MOST_DB, level - LISP_FLOOR_DB)
    return (cut if cut >= LEAST_DB else 0.0), cut < wanted - 0.05


def deess(samples: np.ndarray, rate: int, strength: str = "light") -> tuple[np.ndarray, dict[str, Any]]:
    """Soften each S that stands above the strength's ceiling against the voice. Nothing else changes.

    Returns `samples` itself when nothing needed softening, with what was measured and what was taken.
    """
    if strength not in DEESS_DB:
        raise AltocueError(f"Unknown de-essing strength {strength}.", fix=f"Use one of {', '.join(DEESS_DB)}.")
    info: dict[str, Any] = {"sibilants": 0, "softened": 0, "held": 0, "most_db": 0.0, "share": 0.0, "centre_hz": None}
    info.update(s_to_voice_db=[None, None], loudest_db=[None, None], lowest_softened_db=None)
    if len(samples) < SIZE:
        return samples, info
    padded = _padded(samples)
    frames = _frames(padded)
    found = _find(frames, rate)
    if found is None or not found.runs:
        return samples, info
    before = found.each()
    info.update(sibilants=len(found.runs), s_to_voice_db=[found.overall()] * 2, loudest_db=[round(max(before), 1)] * 2)
    taken = np.zeros(len(frames))
    softened: list[int] = []
    for index, ((a, b), level) in enumerate(zip(found.runs, before, strict=True)):
        cut, held = plan_cut(level, strength)
        info["held"] += int(held)
        if cut:
            taken[a:b] = cut * _eased(b - a)
            softened.append(index)
    if not softened:
        return samples, info
    centre = _centre(frames, found, rate)
    shape = bell(np.fft.rfftfreq(SIZE, 1 / rate), centre)
    window = _window()
    change = np.zeros(len(padded))
    touched = np.flatnonzero(taken > 0)
    for at in range(0, len(touched), CHUNK):
        rows = touched[at : at + CHUNK]
        gains = 10 ** (-(taken[rows, None] * shape[None, :]) / 20) - 1  # only the change, so every untouched sample stays exact
        back = np.fft.irfft(np.fft.rfft(frames[rows] * window, axis=1) * gains, n=SIZE, axis=1) * window
        for row, frame in zip(back, rows, strict=True):
            change[frame * STEP : frame * STEP + SIZE] += row
    result = padded + change / WINDOW_SUM
    picked = np.concatenate([np.arange(a, b) for a, b in found.runs])
    sib = found.sib.copy()
    sib[picked] = _levels(_frames(result.astype(np.float32))[picked], rate, (SIB,))[0]
    after = found.each(sib)
    info.update(
        softened=len(softened),
        most_db=round(float(taken.max()), 1),
        share=round(float((taken > 0).mean()), 3),
        centre_hz=round(centre),
        s_to_voice_db=[found.overall(), found.overall(sib)],
        loudest_db=[round(max(before), 1), round(max(after), 1)],
        lowest_softened_db=round(min(after[index] for index in softened), 1),
    )
    return result[SIZE : SIZE + len(samples)].astype(np.float32), info


def sibilance_db(samples: np.ndarray, rate: int) -> float:
    """The loudest 1% of the 5 to 10 kHz band, in 10 ms frames."""
    spectrum = np.fft.rfft(samples.astype(np.float64))
    freqs = np.fft.rfftfreq(len(samples), 1 / rate)
    band = np.fft.irfft(spectrum * ((freqs >= 5000) & (freqs <= 10000)), len(samples))
    level = _db(band, max(1, rate // 100))
    return round(float(np.percentile(level, 99)), 1) if len(level) else -120.0


def clean_mouth(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None, declick: bool, strength: str) -> tuple[np.ndarray, dict[str, Any]]:
    """Declick, then de-ess. The report says what was found, what was taken and whether the lisp guard held any back."""
    clicks = find_clicks(samples, rate, words) if declick else []
    out = quiet_clicks(samples, rate, clicks) if clicks else samples
    before = sibilance_db(out, rate)
    out, said = deess(out, rate, strength)
    report = {
        "clicks": len(clicks),
        "clicks_at": [round(start, 3) for start, _ in clicks[:50]],
        "deess_most_db": said["most_db"],
        "deess_share": said["share"],
        "sibilance_db": [before, sibilance_db(out, rate)],
        "s_to_voice_db": said["s_to_voice_db"],
        "s_to_voice_loudest_db": said["loudest_db"],
        "sibilants": said["sibilants"],
        "sibilants_softened": said["softened"],
        "deess_held": said["held"],
        "deess_lowest_db": said["lowest_softened_db"],
        "deess_centre_hz": said["centre_hz"],
        "deess_limits": {"ceiling_db": DEESS_CEILING[strength], "most_db": DEESS_DB[strength], "floor_db": LISP_FLOOR_DB},
    }
    return out, report
