"""Original music written in code, fitted to the film's scene marks. The same seed gives the same file, sample for sample.

The rules are Altocue's own: scene marks set the grid, energy decides which parts play, chords move by the
smallest step they can, one short motif comes back, and the last scene lands on the home chord. No samples,
no model and nothing to license.

Nobody at Altocue's end can hear this. Numbers show it is the right length, on the grid, and not clipped.
Whether it is any good is a job for a person with ears, so every report says so.
"""

from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .errors import AltocueError

RATE = 48000
BEATS_PER_BAR = 4
HISTORY = ".altocue/scores.json"
SAME_SOUND = 0.992  # cosine between two sound prints. Calibrated on the five styles at several seeds
LISTEN = "Nobody has heard this yet. A person must listen before it ships."
ENERGY = {"hook": 3, "problem": 5, "product": 7, "payoff": 9, "close": 6}

MINOR = (0, 2, 3, 5, 7, 8, 10)
MAJOR = (0, 2, 4, 5, 7, 9, 11)
LYDIAN = (0, 2, 4, 6, 7, 9, 11)
DORIAN = (0, 2, 3, 5, 7, 9, 10)
MIXOLYDIAN = (0, 2, 4, 5, 7, 9, 10)


@dataclass(frozen=True)
class Style:
    summary: str
    tempo: tuple[int, int]
    scale: tuple[int, ...]
    roots: tuple[int, ...]  # midi notes for the home key's bass
    progressions: tuple[tuple[int, ...], ...]  # scale degrees, from 1
    pad: float  # how fast the harmonics fall away: higher is softer
    pluck: tuple[float, float, float]  # FM ratio, depth and decay in seconds
    kick: str  # four, half or heart
    hats: str  # eighths, offbeat, ticks or none
    bass: str  # pulse, long or walk
    swing: float = 0.0


STYLES = {
    "pulse": Style(
        "Driving and modern. A held pulse under a bright motif, for launches.",
        (112, 124),
        MINOR,
        (45, 47, 48, 50),
        ((1, 6, 3, 7), (1, 4, 6, 5), (6, 4, 1, 7)),
        1.7,
        (2.0, 2.4, 0.26),
        "four",
        "eighths",
        "pulse",
    ),
    "glass": Style(
        "Airy and exact. Bell plucks over soft chords, for calm products.",
        (100, 112),
        LYDIAN,
        (48, 50, 52),
        ((1, 5, 6, 4), (1, 2, 6, 5), (4, 1, 5, 6)),
        2.2,
        (3.5, 1.5, 0.6),
        "half",
        "offbeat",
        "long",
    ),
    "paper": Style(
        "Warm and light on its feet. Wooden plucks and a walking bass, for explainers.",
        (100, 116),
        MAJOR,
        (48, 50, 53),
        ((1, 4, 5, 1), (1, 6, 4, 5), (4, 5, 3, 6)),
        2.4,
        (4.0, 0.9, 0.24),
        "half",
        "ticks",
        "walk",
        0.12,
    ),
    "night": Style(
        "Dark and patient. Low chords and far-off bells, for case files.",
        (100, 106),
        DORIAN,
        (45, 46, 50),
        ((1, 7, 6, 7), (1, 4, 1, 7), (6, 7, 1, 1)),
        2.6,
        (1.41, 3.0, 1.1),
        "heart",
        "none",
        "long",
    ),
    "bright": Style(
        "Up and open. Short stabs and a straight kick, for ads.",
        (118, 125),
        MIXOLYDIAN,
        (48, 50, 52, 55),
        ((1, 7, 4, 1), (1, 4, 5, 4), (4, 1, 7, 4)),
        1.5,
        (1.0, 1.3, 0.2),
        "four",
        "offbeat",
        "pulse",
    ),
}
TYPE_STYLE = {"launch": "pulse", "explainer": "paper", "demo": "glass", "ad": "bright", "gauge": "paper", "case": "night", "line": "paper", "music": "pulse"}


def _hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _table(fall: float, count: int = 40, size: int = 4096) -> np.ndarray:
    phase = np.arange(size) / size * 2 * np.pi
    wave_ = sum(np.sin(phase * k) / k**fall for k in range(1, count + 1))
    return np.asarray(wave_ / np.abs(wave_).max())


def _osc(table: np.ndarray, hz: float, count: int, phase: float = 0.0) -> np.ndarray:
    position = (phase + np.arange(count) * hz / RATE) % 1.0 * len(table)
    low = position.astype(np.int64)
    share = position - low
    return table[low] * (1 - share) + table[(low + 1) % len(table)] * share


def _env(count: int, attack: float, release: float) -> np.ndarray:
    a, r = max(int(attack * RATE), 1), max(int(release * RATE), 1)
    shape_ = np.ones(count)
    a, r = min(a, count // 2 or 1), min(r, count // 2 or 1)
    shape_[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    shape_[-r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return shape_


class Mixer:
    """Buses on one sample clock. Sound is added by time, so a bar stretched to fit a scene stays in place."""

    def __init__(self, total: float) -> None:
        self.count = round(total * RATE)
        self.buses = {name: np.zeros((self.count, 2)) for name in ("pad", "keys", "bass", "drums", "fx")}

    def add(self, bus: str, at: float, sound: np.ndarray, gain: float = 1.0, pan: float = 0.0) -> None:
        start = round(at * RATE)
        if start >= self.count or start < -len(sound):
            return
        first = max(-start, 0)
        start = max(start, 0)
        part = sound[first : first + self.count - start]
        target = self.buses[bus][start : start + len(part)]
        if part.ndim == 1:
            left, right = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
            target[:, 0] += part * gain * left * 1.414
            target[:, 1] += part * gain * right * 1.414
        else:
            target += part * gain


def _pad_note(table: np.ndarray, note: int, seconds: float, rng: np.random.Generator) -> np.ndarray:
    count = max(int(seconds * RATE), 16)
    hz = _hz(note)
    voices = np.zeros((count, 2))
    for side, cents in ((0, -7.0), (1, 7.0), (0, 3.0), (1, -3.0)):
        voices[:, side] += _osc(table, hz * 2 ** (cents / 1200), count, float(rng.random()))
    return voices * _env(count, min(0.35, seconds / 3), min(0.9, seconds / 2))[:, None] / 3


def _pluck(note: int, ratio: float, depth: float, decay: float, seconds: float = 1.4) -> np.ndarray:
    count = int(seconds * RATE)
    time_ = np.arange(count) / RATE
    hz = _hz(note)
    swell = np.exp(-time_ / (decay * 0.5))
    sound = np.sin(2 * np.pi * hz * time_ + depth * swell * np.sin(2 * np.pi * hz * ratio * time_)) * np.exp(-time_ / decay)
    return sound * _env(count, 0.002, 0.08)


def _kick(punch: float = 1.0) -> np.ndarray:
    count = int(0.32 * RATE)
    time_ = np.arange(count) / RATE
    pitch = 46 + 120 * np.exp(-time_ / 0.03)
    return np.sin(2 * np.pi * np.cumsum(pitch) / RATE) * np.exp(-time_ / 0.11) * punch


def _hiss(rng: np.random.Generator, seconds: float, decay: float) -> np.ndarray:
    count = int(seconds * RATE)
    noise = np.diff(rng.standard_normal(count + 2), n=2) / 3
    return noise * np.exp(-np.arange(count) / RATE / decay)


def _clap(rng: np.random.Generator) -> np.ndarray:
    count = int(0.24 * RATE)
    sound = np.zeros(count)
    for start in (0.0, 0.011, 0.023):
        burst = np.diff(rng.standard_normal(count + 1)) * np.exp(-np.arange(count) / RATE / 0.012)
        at = int(start * RATE)
        sound[at:] += burst[: count - at]
    tail_ = np.diff(rng.standard_normal(count + 1)) * np.exp(-np.arange(count) / RATE / 0.07) * 0.5
    return (sound + tail_) * 0.28


def _riser(rng: np.random.Generator, seconds: float) -> np.ndarray:
    count = max(int(seconds * RATE), 64)
    share = np.linspace(0, 1, count)
    noise = np.diff(rng.standard_normal(count + 1))
    smooth = np.convolve(rng.standard_normal(count), np.ones(24) / 24, "same") * 4
    sweep = np.sin(2 * np.pi * np.cumsum(180 + 1400 * share**3) / RATE) * 0.5
    return (noise * share**2 * 0.5 + smooth * (1 - share) * 0.3 + sweep) * share**3


def _impact(rng: np.random.Generator) -> np.ndarray:
    count = int(2.2 * RATE)
    time_ = np.arange(count) / RATE
    low = np.sin(2 * np.pi * np.cumsum(32 + 90 * np.exp(-time_ / 0.08)) / RATE) * np.exp(-time_ / 0.55)
    air = np.diff(rng.standard_normal(count + 1)) * np.exp(-time_ / 0.25) * 0.16
    return low + air


def _room(dry: np.ndarray, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """A plain decaying noise response, the same on every run, convolved on the full length."""
    count = int(seconds * RATE)
    time_ = np.arange(count) / RATE
    response = rng.standard_normal((count, 2)) * np.exp(-time_ * 6.5 / seconds)[:, None]
    response = np.apply_along_axis(lambda channel: np.convolve(channel, np.ones(9) / 9, "same"), 0, response)
    size = 1 << int(np.ceil(np.log2(len(dry) + count)))
    wet = np.fft.irfft(np.fft.rfft(dry, size, axis=0) * np.fft.rfft(response, size, axis=0), size, axis=0)[: len(dry)]
    return wet / max(np.abs(wet).max(), 1e-9) * max(np.abs(dry).max(), 1e-9)


def fit_tempo(lengths: list[float], low: int, high: int, wanted: float) -> tuple[float, list[int]]:
    """The tempo that needs the least stretch for every scene to hold a whole number of beats, and each scene's count."""
    best: tuple[float, float, float, list[int]] | None = None
    for bpm in np.arange(low, high + 0.25, 0.25):
        counts = [max(2, round(seconds * bpm / 60)) for seconds in lengths]
        stretch = max(abs(seconds / count * bpm / 60 - 1) for seconds, count in zip(lengths, counts, strict=True))
        score_ = (round(stretch, 3), abs(float(bpm) - wanted))
        if best is None or score_ < best[:2]:
            best = (*score_, float(bpm), counts)
    assert best is not None
    return best[2], best[3]


def grid(scenes: list[dict[str, Any]], *, style: str = "pulse", seed: int = 1, tempo: float | None = None) -> dict[str, Any]:
    """Every beat in film seconds. Each scene holds a whole number of beats, so the music turns on every cut, and a longer scene moves the rest."""
    chosen = pick(style)
    rng = np.random.default_rng(seed)
    low, high = chosen.tempo
    wanted = float(tempo) if tempo else float(rng.uniform(low, high))
    lengths = [float(scene["end"] - scene["start"]) for scene in scenes]
    bpm, counts = fit_tempo(lengths, *((int(tempo), int(tempo)) if tempo else (low, high)), wanted)
    beats: list[float] = []
    sections = []
    for scene, seconds, count in zip(scenes, lengths, counts, strict=True):
        step = seconds / count
        first = len(beats)
        beats += [float(scene["start"]) + index * step for index in range(count)]
        sections.append({"id": scene.get("id"), "first": first, "count": count, "step": step, "start": float(scene["start"]), "end": float(scene["end"])})
    return {"bpm": bpm, "beats": [round(beat, 6) for beat in beats], "sections": sections}


def pick(name: str) -> Style:
    if name not in STYLES:
        raise AltocueError(f"Unknown music style {name}.", fix=f"Use one of {', '.join(STYLES)}.")
    return STYLES[name]


def _chord(degree: int, scale: tuple[int, ...], root: int, size: int = 4) -> list[int]:
    notes = []
    for stack in range(size):
        place = degree - 1 + stack * 2
        notes.append(root + scale[place % 7] + 12 * (place // 7))
    return notes


def _nearest_voicing(notes: list[int], before: list[int] | None) -> list[int]:
    """Chords move by the smallest step they can. Each note may sit in any octave between C3 and C5."""
    placed = []
    for index, note in enumerate(notes):
        options = [note + 12 * shift for shift in range(-3, 4) if 50 <= note + 12 * shift <= 74]
        target = before[index] if before and index < len(before) else 60 + index * 3
        placed.append(min(options or [note], key=lambda option: abs(option - target)))
    return placed


def _motif(rng: np.random.Generator, scale: tuple[int, ...]) -> list[tuple[int, int]]:
    """A short idea in sixteenths and scale steps. It comes back through the film, so the piece feels written."""
    places = sorted(int(place) for place in rng.choice(np.arange(0, 28, 2), size=int(rng.integers(5, 7)), replace=False))
    step, line = int(rng.integers(0, 5)), []
    for place in places:
        line.append((place, step))
        step = max(min(step + int(rng.choice((-2, -1, 1, 2, 3))), len(scale) + 2), -1)
    return line


def _in_scale(scale: tuple[int, ...], root: int, step: int) -> int:
    return root + scale[step % 7] + 12 * (step // 7)


def _energy(scene: dict[str, Any], index: int, count: int) -> float:
    value = scene.get("energy")
    if value is None:
        value = ENERGY.get(str(scene.get("beat")), 3 + 6 * index / max(count - 1, 1))
    return float(value) / 10


def compose(
    scenes: list[dict[str, Any]], total: float, *, style: str = "pulse", seed: int = 1, tempo: float | None = None, hero: float | None = None, silences: Any = ()
) -> tuple[np.ndarray, dict[str, Any]]:
    """The piece as stereo floats, and what it is."""
    chosen = pick(style)
    rng = np.random.default_rng(seed)
    placed = grid(scenes, style=style, seed=seed, tempo=tempo)
    beats = placed["beats"]
    mix = Mixer(total)
    root = int(rng.choice(chosen.roots))
    home = root + 24
    progression = chosen.progressions[int(rng.integers(len(chosen.progressions)))]
    table = _table(chosen.pad + float(rng.uniform(-0.1, 0.1)))
    bass_table = _table(2.4, count=16)
    ratio, depth, decay = chosen.pluck
    motif = _motif(rng, chosen.scale)
    kick, hat, clap, tick = _kick(), _hiss(rng, 0.12, 0.022), _clap(rng), _hiss(rng, 0.04, 0.008)
    voicing: list[int] | None = None
    chord_at = 0
    sections = placed["sections"]
    levels = [_energy(scene, index, len(scenes)) for index, scene in enumerate(scenes)]
    chords_used: list[int] = []
    for number_, section in enumerate(sections):
        energy, step, first, count = levels[number_], section["step"], section["first"], section["count"]
        last_section = number_ == len(sections) - 1
        hold = 4 if energy >= 0.6 else 8
        swing = chosen.swing * step / 2
        for beat in range(0, count, hold):
            place = progression[chord_at % len(progression)]
            chord_at += 1
            final = last_section and beat + hold >= count
            if last_section:
                place = 1
            elif number_ == len(sections) - 2 and beat + hold >= count:
                place = 5  # the turn home
            chords_used.append(place)
            voicing = _nearest_voicing(_chord(place, chosen.scale, home), voicing)
            start = beats[first + beat]
            seconds = (min(hold, count - beat) * step) + (1.2 if not final else max(total - start, 1.0))
            for note in voicing:
                mix.add("pad", start, _pad_note(table, note, seconds, rng), 0.11 + 0.1 * energy)
            bass = root + chosen.scale[(place - 1) % 7] - (12 if chosen.scale[(place - 1) % 7] > 7 else 0)
            for at in range(min(hold, count - beat)):
                time_ = beats[first + beat + at]
                if chosen.bass == "pulse" and energy >= 0.3:
                    length = step * 0.45
                    for half in (0, 1):
                        sound = _osc(bass_table, _hz(bass), int(length * RATE)) * _env(int(length * RATE), 0.005, 0.05)
                        mix.add("bass", time_ + half * step / 2, sound, 0.34 + 0.14 * energy)
                elif chosen.bass == "walk" and energy >= 0.3 and at % 2 == 0:
                    length = step * 1.4
                    note = bass + (chosen.scale[(at // 2 * 2) % 5] if at else 0)
                    sound = _osc(bass_table, _hz(note), int(length * RATE)) * _env(int(length * RATE), 0.008, 0.2)
                    mix.add("bass", time_ + swing, sound, 0.36)
                elif chosen.bass == "long" and at == 0:
                    length = min(hold, count - beat) * step
                    sound = _osc(bass_table, _hz(bass), int(length * RATE)) * _env(int(length * RATE), 0.08, 0.4)
                    mix.add("bass", time_, sound, 0.26 + 0.1 * energy)
                if energy >= 0.35 and voicing:
                    per_beat = 4 if energy >= 0.75 else 2
                    for inner in range(per_beat):
                        pick_ = voicing[(at * per_beat + inner) % len(voicing)] + 12
                        offset = inner * step / per_beat + (swing if inner % 2 else 0)
                        pan = 0.32 * np.sin(at + inner)
                        mix.add("keys", time_ + offset, _pluck(pick_, ratio, depth, decay), (0.06 + 0.07 * energy) * (1 if inner == 0 else 0.7), float(pan))
        for beat in range(count):
            time_ = beats[first + beat]
            on_bar = beat % BEATS_PER_BAR == 0
            if chosen.kick == "four" and energy >= 0.5:
                mix.add("drums", time_, kick, 0.72)
            elif chosen.kick == "half" and energy >= 0.5 and on_bar:
                mix.add("drums", time_, kick, 0.6)
            elif chosen.kick == "heart" and energy >= 0.4 and on_bar:
                mix.add("drums", time_, kick, 0.5)
                mix.add("drums", time_ + step * 0.5, kick, 0.34)
            if energy >= 0.65 and beat % BEATS_PER_BAR == 2 and chosen.kick != "heart":
                mix.add("drums", time_, clap, 0.5, 0.05)
            if chosen.hats == "eighths" and energy >= 0.4:
                mix.add("drums", time_ + step / 2, hat, 0.16 + 0.1 * energy, 0.18)
                if energy >= 0.8:
                    mix.add("drums", time_, hat, 0.1, -0.18)
            elif chosen.hats == "offbeat" and energy >= 0.4:
                mix.add("drums", time_ + step / 2, hat, 0.18, 0.12)
            elif chosen.hats == "ticks" and energy >= 0.3:
                mix.add("drums", time_ + (swing if beat % 2 else 0), tick, 0.24, 0.2 * (1 if beat % 2 else -1))
        if energy >= 0.55 or last_section:
            bars = max(count // 8, 1)
            for repeat in range(bars):
                offset = first + repeat * 8
                if offset >= len(beats):
                    break
                for place, moved in motif:
                    index = offset + place // 4
                    if index >= first + count:
                        continue
                    time_ = beats[index] + (place % 4) * step / 4
                    note = _in_scale(chosen.scale, home + 12, moved)
                    if last_section and repeat == bars - 1 and place == motif[-1][0]:
                        note = home + 12  # the idea comes home
                    mix.add("keys", time_, _pluck(note, ratio, depth * 1.1, decay * 1.5, 1.8), 0.2, 0.08)
        if number_ + 1 < len(sections):
            rise = levels[number_ + 1] - energy
            cut = section["end"]
            if rise > 0.05:
                length = min(2 * step, section["end"] - section["start"])
                mix.add("fx", cut - length, _riser(rng, length), 0.2 + 0.2 * rise, 0.0)
            if rise >= 0.15:
                mix.add("fx", cut, _impact(rng), 0.42)
    if hero is not None:
        mix.add("fx", hero, _impact(rng), 0.6)
    pumped = np.ones(mix.count)
    if chosen.kick != "heart":
        for section, energy in zip(sections, levels, strict=True):
            if energy < 0.5:
                continue
            spaced = 1 if chosen.kick == "four" else BEATS_PER_BAR
            for beat in range(0, section["count"], spaced):
                start = round(beats[section["first"] + beat] * RATE)
                length = int(section["step"] * 0.62 * RATE)
                curve = 1 - 0.52 * np.exp(-np.arange(length) / RATE / 0.09)
                end = min(start + length, mix.count)
                pumped[start:end] = np.minimum(pumped[start:end], curve[: end - start])
    buses = mix.buses
    buses["pad"] *= pumped[:, None]
    buses["bass"] *= pumped[:, None] ** 0.7
    dry = buses["pad"] + buses["keys"] * 0.9 + buses["fx"]
    wet = _room(buses["keys"] + buses["pad"] * 0.4 + buses["fx"] * 0.4, 2.4, rng)
    out = dry + wet * 0.2 + buses["bass"] + buses["drums"]
    fall = np.ones(mix.count)
    for span in silences or ():
        begin, end = round(float(span["from"]) * RATE), round(float(span["to"]) * RATE)
        ramp = int(0.02 * RATE)
        fall[begin - ramp : begin] = np.minimum(fall[begin - ramp : begin], np.linspace(1, 0.012, ramp))
        fall[begin:end] = 0.012
        fall[end : end + ramp] = np.linspace(0.012, 1, len(fall[end : end + ramp]))
    out *= fall[:, None]
    release = min(int(0.35 * RATE), mix.count)
    out[-release:] *= np.cos(np.linspace(0, np.pi / 2, release))[:, None] ** 2
    out = np.tanh(out * 1.15) / np.tanh(1.15)
    out *= 10 ** (-1.5 / 20) / max(np.abs(out).max(), 1e-9)
    info = {
        "made_by": "altocue",
        "label": "Original score written in code by Altocue. No samples, no model, nothing to license.",
        "listen": LISTEN,
        "style": style,
        "seed": seed,
        "bpm": placed["bpm"],
        "key": ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][root % 12],
        "chords": chords_used,
        "beats": placed["beats"],
        "marks": [float(scene["start"]) for scene in scenes],
    }
    return out, info


def write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def build(dest: str | Path, scenes: list[dict[str, Any]], total: float, **options: Any) -> tuple[Path, dict[str, Any]]:
    samples, info = compose(scenes, total, **options)
    info["print"] = sound_print(samples, info["bpm"])
    return write_wav(Path(dest), samples), info


def style_for(kind: str, chosen: str | None = None) -> str:
    return chosen or TYPE_STYLE.get(kind, "pulse")


def seed_of(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % 100000


def sound_print(samples: np.ndarray, bpm: float) -> list[float]:
    """A short fingerprint: which notes, how bright, how the level moves, and how fast. Two films with near prints sound alike."""
    mono = samples.mean(axis=1)[::4]
    rate = RATE / 4
    size = 8192
    hop = size // 2
    windows = [mono[start : start + size] * np.hanning(size) for start in range(0, len(mono) - size, hop)] or [np.zeros(size)]
    spectrum = np.abs(np.fft.rfft(np.stack(windows), axis=1))
    freqs = np.fft.rfftfreq(size, 1 / rate)
    used = (freqs > 55) & (freqs < 2000)
    pitch = np.rint(12 * np.log2(freqs[used] / 440)).astype(int) % 12
    chroma = np.zeros(12)
    np.add.at(chroma, pitch, spectrum[:, used].sum(axis=0))
    chroma /= max(np.linalg.norm(chroma), 1e-9)
    bands = [(20, 150), (150, 800), (800, 4000), (4000, 12000)]
    shape_ = np.array([spectrum[:, (freqs >= low) & (freqs < high)].sum() for low, high in bands])
    shape_ /= max(np.linalg.norm(shape_), 1e-9)
    level = np.array([chunk.mean() for chunk in np.array_split(spectrum.sum(axis=1), 16)])
    level /= max(np.linalg.norm(level), 1e-9)
    return [round(float(value), 4) for value in (*chroma, *shape_, *level, bpm / 200)]


def likeness(a: list[float], b: list[float]) -> float:
    one, two = np.array(a), np.array(b)
    return float(one @ two / max(np.linalg.norm(one) * np.linalg.norm(two), 1e-9))


def remember(film: str, info: dict[str, Any]) -> str | None:
    """Keep each film's print on this machine, and say when a new score sounds like one made before."""
    path = Path.home() / HISTORY
    rows = json.loads(path.read_text()) if path.exists() else []
    print_ = info["print"]
    warning = None
    for row in rows:
        if row["film"] != film and likeness(row["print"], print_) >= SAME_SOUND:
            warning = f"This score sounds close to the one in {row['film']} ({row['style']}, {row['bpm']:g} bpm). Change the style or the seed so the films differ."
            break
    rows = [row for row in rows if row["film"] != film]
    rows.append({"film": film, "style": info["style"], "seed": info["seed"], "bpm": info["bpm"], "print": print_})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows[-200:], indent=1))
    return warning
