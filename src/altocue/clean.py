"""Clean each clip's voice: denoise, hum, tone, de-ess, level. Timecodes never move."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import ffmpeg
from .errors import AltocueError
from .master import limiter_latency
from .models import Clip
from .project import Project, write_json

RATE = 48000
VOICE_LUFS = -16.0
INTO_COMPRESSOR_LUFS = -18.0  # the compressor threshold is absolute, so level first
SPEECH_LOSS_LIMIT_DB = 3.0
SHARE_LOST_LIMIT = 0.10  # by energy: quiet frames lost are noise, loud frames lost are words
FRAME_LOST_DB = 12.0
NOTHING_KEPT_DB = -12.0  # a denoiser that takes this much found no speech to keep
HUM_PROMINENCE_DB = 12.0
HUM_BELOW_SPEECH_DB = 55.0
HUM_MAX_WINDOWS = 240
DIGITAL_SILENCE_DB = -80.0
PEAK_LIMIT_DB = -1.0
MAX_LIMITING_DB = 6.0
VERSION_TIMEOUT = 10.0
PRINT_SECONDS = 0.25
TAIL = 4800  # `deep-filter -D` drops the last 1440 samples, so it gets spare ones

# Runs on Apple Silicon as downloaded. It is ad-hoc signed and embeds DeepFilterNet3.
# A script download carries no quarantine flag. A browser download does, and it then
# hangs at launch. Strip the flag before the first run; a file Gatekeeper already
# refused stays refused and has to be replaced.
DEEP_FILTER_VERSION = "0.5.6"
DEEP_FILTER_HOME = Path.home() / ".altocue" / "bin"
_RELEASE = f"https://github.com/Rikorose/DeepFilterNet/releases/download/v{DEEP_FILTER_VERSION}/deep-filter-{DEEP_FILTER_VERSION}-"
DEEP_FILTER_RELEASES: dict[str, dict[str, str]] = {
    "darwin-arm64": {
        "url": f"{_RELEASE}aarch64-apple-darwin",
        "sha256": "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081",
    },
    "darwin-x86_64": {
        "url": f"{_RELEASE}x86_64-apple-darwin",
        "sha256": "d3be84003acb7c23e738ad7f70a158ec779a8d233a82e7fa3e717d112eb5b50f",
    },
    "linux-x86_64": {
        "url": f"{_RELEASE}x86_64-unknown-linux-musl",
        "sha256": "70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da",
    },
    "linux-aarch64": {
        "url": f"{_RELEASE}aarch64-unknown-linux-gnu",
        "sha256": "14e02a1c0028f3ca0bdf83b62b3336e56ba0556894ef295a95e8573f06557166",
    },
}

# strength, least signal-to-noise in dB, deep-filter attenuation limit, afftdn reduction
STRENGTHS: tuple[tuple[str, float, float, float], ...] = (
    ("none", 45.0, 0.0, 0.0),
    ("light", 32.0, 10.0, 8.0),
    ("normal", 20.0, 18.0, 12.0),
    ("strong", -np.inf, 30.0, 18.0),
)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--force", action="store_true", help="Clean clips that are already done")


def find_deep_filter() -> str | None:
    override = os.environ.get("ALTOCUE_DEEP_FILTER")
    if override:
        return override if Path(override).is_file() else None
    installed = DEEP_FILTER_HOME / "deep-filter"
    return shutil.which("deep-filter") or (str(installed) if installed.is_file() else None)


def check_binary(binary: str) -> str | None:
    """Why this binary cannot run, or None. A quarantined file hangs rather than fails."""
    fix = f"Run `xattr -d com.apple.quarantine {binary}` on a fresh copy, or `altocue setup`."
    try:
        done = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"deep-filter did not answer, so it may be in quarantine. {fix}"
    except OSError as error:
        return f"deep-filter would not start: {error}. {fix}"
    return None if done.returncode == 0 else f"deep-filter failed with code {done.returncode}. {fix}"


def _frame_db(samples: np.ndarray, rate: int, ms: float = 50.0) -> np.ndarray:
    size = max(1, int(rate * ms / 1000))
    count = len(samples) // size
    if not count:
        return np.array([-120.0])
    frames = samples[: count * size].astype(np.float64).reshape(count, size)
    return 10 * np.log10(np.mean(frames**2, axis=1) + 1e-12)


def noise_floor(samples: np.ndarray, rate: int) -> float:
    """The level of the quiet tenth of the clip, which is the room between words."""
    return float(np.percentile(_frame_db(samples, rate), 10))


def speech_level(samples: np.ndarray, rate: int) -> float:
    return float(np.percentile(_frame_db(samples, rate), 95))


def _spectrum(samples: np.ndarray, rate: int) -> tuple[np.ndarray, float] | None:
    """Mean power per bin over the quietest quarter-seconds. A sine reads as its RMS."""
    size, hop = rate // 4, rate // 8
    if len(samples) < size:
        return None
    halves = _frame_db(samples, rate, 125.0)
    levels = np.maximum(halves[:-1], halves[1:]) if len(halves) > 1 else halves
    quiet = np.flatnonzero(levels <= np.percentile(levels, 30))
    chosen = quiet if len(quiet) >= 4 else np.arange(len(levels))
    picked = chosen[:: max(1, len(chosen) // HUM_MAX_WINDOWS)]
    windows = np.stack([samples[i * hop : i * hop + size] for i in picked if i * hop + size <= len(samples)]).astype(np.float64)
    power = np.mean(np.abs(np.fft.rfft(windows * np.hanning(size), axis=1)) ** 2, axis=0)
    return power * 8 / size**2, rate / size


def _line(power: np.ndarray, width: float, hz: float) -> tuple[float, float]:
    """A spectral line's level in dB, and how far it stands above its neighbours."""
    at = int(round(hz / width))
    peak = power[at - 1 : at + 2].max()
    around = np.median(np.r_[power[max(at - 8, 0) : at - 2], power[at + 3 : at + 9]])
    return float(10 * np.log10(peak + 1e-20)), float(10 * np.log10((peak + 1e-20) / (around + 1e-20)))


def detect_hum(samples: np.ndarray, rate: int) -> dict[str, Any] | None:
    """Find mains hum in the quiet sections. Returns the mains frequency, or None."""
    found = _spectrum(samples, rate)
    if found is None:
        return None
    power, width = found
    least = speech_level(samples, rate) - HUM_BELOW_SPEECH_DB
    best: dict[str, Any] | None = None
    for hz in (50, 60):
        lines = [_line(power, width, hz * k) for k in (1, 2, 3)]
        hits = [level >= least and prominence >= HUM_PROMINENCE_DB for level, prominence in lines]
        if hits[0] or (hits[1] and hits[2]):
            level = max(line[0] for line, hit in zip(lines, hits, strict=True) if hit)
            if best is None or level > best["level_db"]:
                best = {"hz": hz, "level_db": round(level, 1)}
    return best


def plan_denoise(snr: float) -> dict[str, Any]:
    """How hard to work: clean audio is left alone, and the worse it is the more we take."""
    strength, atten, reduction = next((name, atten, reduction) for name, least, atten, reduction in STRENGTHS if snr >= least)
    return {"strength": strength, "atten_db": atten, "reduction_db": reduction}


def tone_filters(hum: dict[str, Any] | None) -> str:
    """High-pass, optional hum notches, gentle EQ, de-esser, compressor. Stock filters only."""
    parts = ["highpass=f=80:p=2"]
    if hum:
        parts += [f"bandreject=f={hum['hz'] * k}:t=q:w=30" for k in (1, 2, 3)]
    parts += [
        "equalizer=f=300:t=q:w=1.2:g=-1.5",
        "equalizer=f=3000:t=q:w=1.2:g=1.5",
        "deesser=i=0.3",
        "acompressor=threshold=0.15:ratio=2:attack=10:release=150:makeup=1.3",
    ]
    return ",".join(parts)


def _band_db(samples: np.ndarray, rate: int, low: float = 200.0, high: float = 4000.0, size: int = 1024) -> np.ndarray:
    """Speech-band level per frame. Wind and rumble do not count as speech here."""
    count = len(samples) // size
    freqs = np.fft.rfftfreq(size, 1 / rate)
    keep = (freqs >= low) & (freqs <= high)
    window = np.hanning(size)
    usable = samples[: count * size].astype(np.float64)
    levels = np.empty(count)
    for first in range(0, count, 2048):
        block = usable[first * size : (first + 2048) * size].reshape(-1, size) * window
        levels[first : first + 2048] = np.sum(np.abs(np.fft.rfft(block, axis=1))[:, keep] ** 2, axis=1)
    return 10 * np.log10(levels / size**2 + 1e-12)


def speech_change(before: np.ndarray, after: np.ndarray, rate: int) -> dict[str, float]:
    """What a denoiser did to the speech itself, with the noise it was meant to take removed."""
    old, new = _band_db(before, rate), _band_db(after, rate)
    if len(old) < 5:
        return {"retained_db": 0.0, "share_lost": 0.0}
    active = old > max(float(np.percentile(old, 10)) + 10, old.max() - 40)
    if active.sum() < 5 or active.all():
        return {"retained_db": 0.0, "share_lost": 0.0}
    power_old, power_new = 10 ** (old / 10), 10 ** (new / 10)
    was = max(power_old[active].mean() - power_old[~active].mean(), 1e-12)
    now = max(power_new[active].mean() - power_new[~active].mean(), 1e-12)
    lost = active & (new < old - FRAME_LOST_DB)
    return {
        "retained_db": round(float(10 * np.log10(now / was)), 2),
        "share_lost": round(float(power_old[lost].sum() / power_old[active].sum()), 3),
    }


def _extract(clip: Clip, dest: Path) -> int:
    """Mono 48 kHz, starting at the source's zero and running its exact length."""
    if not Path(clip.path).exists():
        raise AltocueError(f"Clip {clip.id}: no file at {clip.path}.", fix="Put the source back, then run `altocue ingest`.")
    count = round(clip.duration * RATE)
    fit = f"aresample={RATE}:first_pts=0,apad=whole_len={count},atrim=end_sample={count}"
    ffmpeg.run(["-i", clip.path, "-vn", "-map", "0:a:0", "-af", fit, "-ac", "1", "-c:a", "pcm_s16le", str(dest)])
    return count


def _run_deep_filter(binary: str, source: Path, folder: Path, atten: float, count: int) -> Path:
    padded = folder / "padded.wav"
    ffmpeg.run(["-i", str(source), "-af", f"apad=pad_len={TAIL}", "-c:a", "pcm_s16le", str(padded)])
    out = folder / "deep-filter"
    try:
        done = subprocess.run(
            [binary, "-D", "-a", f"{atten:g}", "-o", str(out), str(padded)],
            capture_output=True,
            text=True,
            timeout=max(120.0, count / RATE * 10),
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise AltocueError(f"deep-filter failed: {error}") from error
    if done.returncode != 0 or not (out / padded.name).exists():
        raise AltocueError(f"deep-filter failed with code {done.returncode}.")
    dest = folder / "denoised.wav"
    ffmpeg.run(["-i", str(out / padded.name), "-af", f"atrim=end_sample={count}", "-c:a", "pcm_s16le", str(dest)])
    return dest


@lru_cache(maxsize=1)
def _afftdn_delay() -> int:
    """`afftdn` delays its output and keeps the length. Measure by how much, once."""
    probe = np.random.default_rng(0).normal(0, 0.1, RATE) * (np.arange(RATE) % 9600 < 4800)
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        source = ffmpeg.write_wav(Path(scratch) / "in.wav", probe, RATE)
        out = Path(scratch) / "out.wav"
        ffmpeg.run(["-i", str(source), "-af", "afftdn=nr=1", "-c:a", "pcm_s16le", str(out)])
        back, _ = ffmpeg.read_wav(out)
    size = len(probe) + len(back)
    corr = np.fft.irfft(np.fft.rfft(back, size) * np.conj(np.fft.rfft(probe, size)), size)
    return int(np.argmax(np.abs(corr[:6000])))


def _quietest(samples: np.ndarray, rate: int) -> np.ndarray:
    """The quietest stretch of room tone that is not digital silence."""
    levels = _frame_db(samples, rate, 10.0)
    span = round(PRINT_SECONDS * 100)
    if len(levels) <= span:
        return samples
    rolling = np.convolve(levels, np.ones(span) / span, mode="valid")
    rolling[rolling < -100] = np.inf
    first = int(np.argmin(rolling)) * rate // 100
    return samples[first : first + span * rate // 100]


def _run_afftdn(samples: np.ndarray, folder: Path, reduction: float) -> Path:
    """Real rooms are not white noise, so `afftdn` learns a noise print first.

    The print is laid in front of the clip, so the whole clip gets it, then trimmed off.
    """
    delay = _afftdn_delay()
    room = _quietest(samples, RATE)
    printed = ffmpeg.write_wav(folder / "printed.wav", np.r_[room, samples], RATE)
    learn = f"asendcmd=c='0.0 afftdn sn start',asendcmd=c='{len(room) / RATE:.3f} afftdn sn stop'"
    chain = f"apad=pad_len={delay},{learn},afftdn=nr={reduction:g}:nf=-40,atrim=start_sample={delay + len(room)}"
    dest = folder / "denoised.wav"
    ffmpeg.run(["-i", str(printed), "-af", chain, "-c:a", "pcm_s16le", str(dest)])
    return dest


def _denoise(
    raw: Path, samples: np.ndarray, folder: Path, plan: dict[str, Any], deep_filter: str | None, warnings: list[str]
) -> tuple[Path, str]:
    if plan["strength"] == "none":
        return raw, "none"
    if deep_filter:
        try:
            return _run_deep_filter(deep_filter, raw, folder, plan["atten_db"], len(samples)), "deep-filter"
        except AltocueError as error:
            warnings.append(f"{error} Used afftdn instead.")
    return _run_afftdn(samples, folder, plan["reduction_db"]), "afftdn"


def clean_clip(
    clip: Clip, dest: str | Path, *, deep_filter: str | None = None, denoise: bool = True, spoken: bool | None = None
) -> dict[str, Any]:
    """Clean one clip to `dest` and report what was measured and what was done.

    `spoken` comes from the transcript. A clip with no speech keeps its own sound and level,
    because levelling ambience to speech level would turn wind into the loudest thing in the film.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        folder = Path(scratch)
        raw = folder / "raw.wav"
        count = _extract(clip, raw)
        before, _ = ffmpeg.read_wav(raw)
        peak = float(20 * np.log10(np.abs(before).max() + 1e-9))
        report: dict[str, Any] = {"id": clip.id, "status": "cleaned", "samples": count, "peak_before": round(peak, 1), "warnings": []}
        if peak < DIGITAL_SILENCE_DB:
            shutil.copy(raw, dest)
            report.update(status="silent", engine="none", strength="none", hum_hz=None, noise_floor_before=None, noise_floor_after=None)
            report["warnings"].append("The audio track is silent.")
            return report

        floor, speech = noise_floor(before, RATE), speech_level(before, RATE)
        report.update(
            engine="none",
            strength="none",
            hum_hz=None,
            kept_original=False,
            noise_floor_before=round(floor, 1),
            noise_floor_after=round(floor, 1),
        )
        if spoken is False:
            shutil.copy(raw, dest)
            report["status"] = "no_speech"
            report["warnings"].append("The transcript has no words, so this clip keeps its own sound and level.")
            return report

        hum = detect_hum(before, RATE)
        plan = plan_denoise(speech - floor) if denoise else plan_denoise(float("inf"))
        loud = ffmpeg.loudness(raw)
        voice, engine = _denoise(raw, before, folder, plan, deep_filter, report["warnings"])
        change = {"retained_db": 0.0, "share_lost": 0.0}
        kept_original = False
        if voice != raw:
            denoised, _ = ffmpeg.read_wav(voice)
            change = speech_change(before, denoised, RATE)
            if change["retained_db"] < -SPEECH_LOSS_LIMIT_DB or change["share_lost"] > SHARE_LOST_LIMIT:
                taken = f"{change['retained_db']} dB, {change['share_lost']:.0%} of its energy"
                report["warnings"].append(f"{engine} took too much speech ({taken}), so we kept the original.")
                voice, engine, kept_original = raw, "none", True
                if change["retained_db"] <= NOTHING_KEPT_DB:
                    shutil.copy(raw, dest)
                    report.update(
                        status="no_speech",
                        kept_original=True,
                        speech_retained_db=change["retained_db"],
                        speech_share_lost=change["share_lost"],
                    )
                    return report

        into = ffmpeg.loudness(voice)["integrated"]
        pre = INTO_COMPRESSOR_LUFS - into if into > -69 else 0.0
        toned = folder / "toned.wav"
        ffmpeg.run(["-i", str(voice), "-af", f"volume={pre:.2f}dB,{tone_filters(hum)}", "-c:a", "pcm_f32le", str(toned)])
        out = ffmpeg.loudness(toned)
        gain = VOICE_LUFS - out["integrated"] if out["integrated"] > -69 else 0.0
        room = PEAK_LIMIT_DB - out["true_peak"] + MAX_LIMITING_DB
        if gain > room:
            report["warnings"].append(f"Peaks held the level {gain - room:.1f} dB short of {VOICE_LUFS:g} LUFS.")
            gain = room
        limit = f"alimiter=limit={10 ** (PEAK_LIMIT_DB / 20):.5f}:level=disabled{limiter_latency()}"
        ffmpeg.run(["-i", str(toned), "-af", f"volume={gain:.2f}dB,{limit}", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(dest)])

        after, _ = ffmpeg.read_wav(dest)
        if len(after) != count:
            raise AltocueError(f"Clip {clip.id}: cleaning changed the length from {count} to {len(after)} samples.")
        final = ffmpeg.loudness(dest)
        floor_after, speech_after = noise_floor(after, RATE), speech_level(after, RATE)
        report.update(
            engine=engine,
            strength=plan["strength"],
            kept_original=kept_original,
            hum_hz=hum["hz"] if hum else None,
            noise_floor_before=round(floor, 1),
            noise_floor_after=round(floor_after, 1),
            snr_before=round(speech - floor, 1),
            snr_after=round(speech_after - floor_after, 1),
            noise_drop_db=round((floor - loud["integrated"]) - (floor_after - final["integrated"]), 1),
            speech_retained_db=change["retained_db"],
            speech_share_lost=change["share_lost"],
            level_before_lufs=loud["integrated"],
            level_lufs=final["integrated"],
            true_peak=final["true_peak"],
            peak_after=round(float(20 * np.log10(np.abs(after).max() + 1e-9)), 1),
            clipped_before=int(np.sum(np.abs(before) >= 0.999)),
            clipped_after=int(np.sum(np.abs(after) >= 0.999)),
        )
    return report


def _spoken(project: Project, clip: Clip) -> bool | None:
    try:
        return bool(project.words(clip.id))
    except AltocueError:
        return None


def clean_project(project: Project, force: bool = False) -> dict[str, Any]:
    """Clean every clip to `work/audio/<clip id>.wav` and save the report beside them."""
    path = project.audio_dir / "report.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    binary, notes = find_deep_filter(), []
    problem = check_binary(binary) if binary else None
    if problem:
        notes.append(f"{problem} Used afftdn instead.")
        binary = None
    use_denoiser = bool(project.setting("audio", "denoise", True))
    denoiser = "deep-filter" if binary else "afftdn"
    same = saved.get("denoise", True) == use_denoiser and saved.get("denoiser") == denoiser  # a changed setting redoes the sound
    earlier = saved.get("clips", {}) if same else {}
    entries: dict[str, Any] = {}
    for clip in project.clips():
        dest = project.audio_dir / f"{clip.id}.wav"
        if not clip.has_audio:
            entries[clip.id] = {
                "id": clip.id,
                "status": "no_audio",
                "engine": "none",
                "hum_hz": None,
                "noise_floor_before": None,
                "noise_floor_after": None,
                "warnings": ["This clip has no audio track."],
            }
        elif not force and clip.id in earlier and dest.exists() and dest.stat().st_mtime >= Path(clip.path).stat().st_mtime:
            entries[clip.id] = {**earlier[clip.id], "status": "reused"}
        else:
            entries[clip.id] = clean_clip(clip, dest, deep_filter=binary, denoise=use_denoiser, spoken=_spoken(project, clip))
    report = {"denoiser": denoiser, "denoise": use_denoiser, "target_lufs": VOICE_LUFS, "warnings": notes, "clips": entries}
    write_json(path, report)
    return report


def describe(entry: dict[str, Any]) -> str:
    if entry["status"] == "no_audio":
        return f"{entry['id']}: no audio"
    if entry["status"] in {"silent", "no_speech"}:
        return f"{entry['id']}: {entry['status'].replace('_', ' ')}, left as it was"
    hum = f"hum {entry['hum_hz']} Hz" if entry["hum_hz"] else "no hum"
    floor = f"noise floor {entry['noise_floor_before']} to {entry['noise_floor_after']} dB"
    line = f"{entry['id']}: {entry['engine']} ({entry['strength']}), {floor}, {hum}, {entry['level_lufs']} LUFS"
    return f"{line} (reused)" if entry["status"] == "reused" else line


def run(args: argparse.Namespace) -> int:
    report = clean_project(Project.open(args.project), force=args.force)
    for note in report["warnings"]:
        print(f"Warning: {note}")
    for entry in report["clips"].values():
        print(describe(entry))
        for note in entry["warnings"]:
            print(f"  Warning: {note}")
    return 0
