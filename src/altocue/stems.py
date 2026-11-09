"""Take music and echo off a voice, before it is cleaned. Both on this machine, both optional.

- **Music under speech.** Demucs (MIT, Meta AI Research) splits the voice from everything else.
  It is an optional install, `uv tool install --force altocue --with "demucs>=4.1,<5"`, because it
  brings PyTorch. It stays out of Altocue's own dependencies for that reason. A GPU makes it quick; a CPU gets there.
- **Echo.** Weighted prediction error (Nakatani and Yoshioka, 2008) predicts the late reflections of
  a room from the sound a few frames before and takes them away. It is plain NumPy, so it is always
  there, and it is modest: on a synthetic 0.6 s room it took under 1 dB off the tail. It is off until
  asked for. The ring of the room is always measured, and a room that rings says so.

Music `auto` runs only when it is measured: when the gaps between words stay loud and tonal. A step
that takes more of the words than it should is undone, and the report says so. The sample count
never changes, so timecodes never move.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from . import ffmpeg
from .errors import AltocueError

MODES = ("auto", "on", "off")
ECHO = ("off", "light", "strong")
MODEL = "htdemucs"  # Demucs v4, hybrid transformer. Its weights come with Demucs's own MIT licence
MUSIC_GAP_DB = 28.0  # gaps this close to the speech level are not room noise
MUSIC_FLATNESS = 0.35  # noise is flat; music is not
RT60_ECHO = 0.45  # seconds. A dry room is under 0.3, a bare one rings past 0.6
WORD_LOSS_DB = 6.0  # a step that takes more than this off the words themselves is undone
FFT, HOP = 2048, 512  # 43 ms frames, 11 ms apart. Room tails are predicted over 20 frames or more
DEREVERB = {"light": (16, 2), "strong": (24, 3)}  # taps, passes
TOP_HZ = 8000.0  # echo above this dies fast and costs the most bins, so it is left alone
DELAY = 3  # frames. Speech within this of itself is the voice, not the room
CHUNK = 8.0  # seconds dereverbed at a time, so an hour never sits in memory as one matrix
INSTALL = 'uv tool install --force altocue --with "demucs>=4.1,<5"'


def have_demucs() -> bool:
    return importlib.util.find_spec("demucs") is not None


def _frames_db(samples: np.ndarray, rate: int, ms: float = 20.0) -> np.ndarray:
    size = max(1, int(rate * ms / 1000))
    count = len(samples) // size
    blocks = samples[: count * size].reshape(count, size).astype(np.float64)
    return 10 * np.log10(np.mean(blocks**2, axis=1) + 1e-12)


def _gap_mask(count: int, rate: int, words: list[tuple[float, float]], size: int, guard: float = 0.12) -> np.ndarray:
    """True for frames clear of every word by `guard` seconds."""
    clear = np.ones(count, bool)
    for start, end in words:
        first, last = max(int((start - guard) * rate / size), 0), min(int((end + guard) * rate / size) + 1, count)
        clear[first:last] = False
    return clear


def music_under(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None) -> dict[str, Any]:
    """Is something loud and tonal going on between the words? That is music, not a noisy room."""
    if not words or len(samples) < rate:
        return {"music": False, "gap_db": None, "flatness": None}
    size = int(rate * 0.02)
    levels = _frames_db(samples, rate)
    gaps = _gap_mask(len(levels), rate, words, size)
    talk = ~_gap_mask(len(levels), rate, words, size, guard=0.0)
    if gaps.sum() < 25 or talk.sum() < 25:
        return {"music": False, "gap_db": None, "flatness": None}
    speech = float(np.percentile(levels[talk], 90))
    gap = float(np.median(levels[gaps]))
    frames = np.flatnonzero(gaps)[:: max(1, int(gaps.sum() // 400))]
    window = np.hanning(2048)
    flat = []
    for index in frames:
        piece = samples[index * size : index * size + 2048]
        if len(piece) < 2048:
            continue
        power = np.abs(np.fft.rfft(piece * window))[5:400] ** 2 + 1e-12
        flat.append(float(np.exp(np.mean(np.log(power))) / np.mean(power)))
    flatness = float(np.median(flat)) if flat else 1.0
    below = speech - gap
    return {"music": bool(below < MUSIC_GAP_DB and flatness < MUSIC_FLATNESS), "gap_db": round(below, 1), "flatness": round(flatness, 3)}


def ring_time(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None) -> float | None:
    """A rough RT60 from how fast the sound dies after each word that has room behind it."""
    if not words:
        return None
    levels = _frames_db(samples, rate, 10.0)
    floor = float(np.percentile(levels, 10))
    slopes = []
    ordered = sorted(words)
    after = [start for start, _ in ordered[1:]] + [ordered[-1][1] + 10]
    for (_, end), next_start in zip(ordered, after, strict=True):
        if next_start - end < 0.25:
            continue
        first, last = int(end * 100), int(min(end + 0.25, next_start - 0.02) * 100)
        tail = levels[first:last]
        if len(tail) < 8 or tail[0] - floor < 20:
            continue
        above = tail[tail > floor + 5]
        if len(above) < 4:
            continue
        slope = np.polyfit(np.arange(len(above)) / 100, above, 1)[0]
        if slope < -1:
            slopes.append(-60 / slope)
    return round(float(np.median(slopes)), 3) if len(slopes) >= 5 else None


def word_level(samples: np.ndarray, rate: int, words: list[tuple[float, float]] | None) -> float:
    """Mean power of the words alone, in dB. Without words, of the loudest tenth."""
    levels = _frames_db(samples, rate)
    if words:
        talk = ~_gap_mask(len(levels), rate, words, int(rate * 0.02), guard=0.0)
        if talk.sum() >= 10:
            return float(10 * np.log10(np.mean(10 ** (levels[talk] / 10))))
    return float(np.percentile(levels, 90))


def separate(source: Path, folder: Path, count: int, rate: int) -> Path:
    """The vocal stem from Demucs, mono at `rate`, exactly `count` samples long."""
    if not have_demucs():
        raise AltocueError("Demucs is not installed.", fix=f"Run `{INSTALL}`, or set [audio] music = \"off\".")
    out = folder / "demucs"
    env = {**os.environ, "PYTHONWARNINGS": "ignore"}
    command = [sys.executable, "-m", "demucs.separate", "-n", MODEL, "--two-stems", "vocals", "-o", str(out), str(source)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=max(600.0, count / rate * 20))
    except (subprocess.TimeoutExpired, OSError) as error:
        raise AltocueError(f"Demucs failed: {error}", fix="Run it again; on a CPU an hour of sound takes a while.") from error
    found = sorted(out.rglob("vocals.wav"))
    if done.returncode != 0 or not found:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
        raise AltocueError(f"Demucs failed: {tail[0]}", fix="Check `python -m demucs.separate --help`, or set [audio] music = \"off\".")
    dest = folder / "vocals.wav"
    fit = f"aresample={rate},atrim=end_sample={count},apad=whole_len={count}"
    ffmpeg.run(["-i", str(found[0]), "-af", fit, "-ac", "1", "-c:a", "pcm_s16le", str(dest)])
    return dest


def _stft(samples: np.ndarray) -> np.ndarray:
    window = np.hanning(FFT).astype(np.float32)
    padded = np.pad(samples.astype(np.float32), (FFT, FFT))
    count = 1 + (len(padded) - FFT) // HOP
    frames = np.lib.stride_tricks.as_strided(padded, (count, FFT), (padded.strides[0] * HOP, padded.strides[0]))
    return np.fft.rfft(frames * window, axis=1).astype(np.complex64)  # frames by bins


def _istft(spec: np.ndarray, length: int) -> np.ndarray:
    window = np.hanning(FFT).astype(np.float32)
    frames = np.fft.irfft(spec, n=FFT, axis=1).astype(np.float32) * window
    total = FFT + HOP * (len(frames) - 1)
    out, norm = np.zeros(total, np.float32), np.zeros(total, np.float32)
    for index, piece in enumerate(frames):
        out[index * HOP : index * HOP + FFT] += piece
        norm[index * HOP : index * HOP + FFT] += window**2
    out /= np.maximum(norm, 1e-6)
    return out[FFT : FFT + length]


def wpe(samples: np.ndarray, taps: int, passes: int) -> np.ndarray:
    """Single-channel weighted prediction error dereverberation. Same length out as in."""
    full = _stft(samples)  # T by F
    keep = int(TOP_HZ / (48000 / FFT)) + 1
    spec = full[:, :keep]
    frames, bins = spec.shape
    clean = spec.copy()
    lagged = np.zeros((taps, frames, bins), np.complex64)
    for tap in range(taps):
        shift = DELAY + tap
        if shift < frames:
            lagged[tap, shift:] = spec[: frames - shift]
    for _ in range(passes):
        power = np.maximum(np.abs(clean) ** 2, 1e-10)  # T by F
        weighted = lagged / power[None]
        corr = np.einsum("atf,btf->fab", weighted, lagged.conj())  # F by taps by taps
        cross = np.einsum("atf,tf->fa", weighted, spec.conj())  # F by taps
        corr += np.eye(taps, dtype=np.complex64)[None] * 1e-6 * np.trace(corr, axis1=1, axis2=2).real[:, None, None] / taps
        filters = np.linalg.solve(corr, cross[..., None])[..., 0]  # F by taps
        clean = spec - np.einsum("fa,atf->tf", filters.conj(), lagged)
    full[:, :keep] = clean
    return _istft(full, len(samples))


def dereverb(samples: np.ndarray, rate: int, strength: str) -> np.ndarray:
    """WPE in overlapping chunks with a crossfade, so memory stays flat on a long recording."""
    taps, passes = DEREVERB[strength]
    size, overlap = int(CHUNK * rate), int(rate)
    if len(samples) <= size + overlap:
        return wpe(samples, taps, passes)
    out = np.zeros(len(samples), np.float32)
    weight = np.zeros(len(samples), np.float32)
    ramp = np.linspace(0, 1, overlap, dtype=np.float32)
    start = 0
    while start < len(samples):
        end = min(start + size + overlap, len(samples))
        piece = wpe(samples[start:end], taps, passes)
        fade = np.ones(end - start, np.float32)
        if start > 0:
            fade[:overlap] = ramp[: min(overlap, end - start)]
        if end < len(samples):
            fade[-overlap:] = np.minimum(fade[-overlap:], ramp[::-1])
        out[start:end] += piece * fade
        weight[start:end] += fade
        if end == len(samples):
            break
        start = end - overlap
    return out / np.maximum(weight, 1e-6)


def prepare_voice(
    raw: Path,
    samples: np.ndarray,
    rate: int,
    words: list[tuple[float, float]] | None,
    music: str,
    echo: str,
    warnings: list[str],
) -> tuple[Path, np.ndarray, dict[str, Any]]:
    """Run what is asked for, or measured when `auto`. Return the voice, its samples and what was done."""
    report: dict[str, Any] = {"music": "off", "echo": "off"}
    if music != "off":
        found = music_under(samples, rate, words)
        report.update(music_gap_db=found["gap_db"], music_flatness=found["flatness"])
        wanted = music == "on" or found["music"]
        if wanted and not have_demucs():
            report["music"] = "not installed"
            if found["music"]:
                warnings.append(f"Music was measured under the voice, but Demucs is not installed. Run `{INSTALL}` to take it off.")
            else:
                warnings.append(f"Demucs is not installed. Run `{INSTALL}`.")
        elif wanted:
            with tempfile.TemporaryDirectory(prefix="altocue-stems-") as scratch:
                stem = separate(raw, Path(scratch), len(samples), rate)
                voice, _ = ffmpeg.read_wav(stem)
            lost = word_level(samples, rate, words) - word_level(voice, rate, words)
            if lost > WORD_LOSS_DB:
                warnings.append(f"Demucs took {lost:.1f} dB off the words themselves, so the music stayed.")
                report["music"] = "undone"
            else:
                samples = voice
                report["music"] = "removed"
                report["music_word_loss_db"] = round(lost, 1)
    rt60 = ring_time(samples, rate, words)
    report["rt60"] = rt60
    if echo == "off" and rt60 is not None and rt60 > RT60_ECHO:
        warnings.append(
            f"This room rings (RT60 about {rt60:.2f} s). Set [audio] echo = \"light\" to take a little off. "
            "On a Mac, the apple denoiser removes more."
        )
    if echo in DEREVERB:
        dry = dereverb(samples, rate, echo)
        peak = float(np.abs(dry).max())
        dry = dry / peak * min(peak, 0.98) if peak > 0.98 else dry
        lost = word_level(samples, rate, words) - word_level(dry, rate, words)
        if lost > WORD_LOSS_DB:
            warnings.append(f"Echo removal took {lost:.1f} dB off the words, so the room stayed.")
            report["echo"] = "undone"
        else:
            samples = dry
            report["echo"] = echo
            report["rt60_after"] = ring_time(samples, rate, words)
    if report["music"] in {"removed"} or report["echo"] in DEREVERB:
        dest = raw.with_name("voice-stem.wav")
        return ffmpeg.write_wav(dest, samples, rate), samples, report
    return raw, samples, report
