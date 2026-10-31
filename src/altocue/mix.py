"""Build the edited voice from the cut, then lay music and effects under it."""

from __future__ import annotations

import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import ffmpeg
from .errors import AltocueError
from .models import Edl
from .project import Project, write_json

RATE = 48000
FADE_MS = 30.0
SFX_DIR = Path(__file__).parent / "data" / "sfx"

VOICE_LUFS = -16.0  # the level the constants below were proven at
MUSIC_UNDER_VOICE_LU = 5.0  # before ducking
MUSIC_FADE_IN = 0.25
MUSIC_FADE_OUT = 2.6
DUCK = "threshold=0.06:ratio=3.5:attack=8:release=380:makeup=1"
RIDER_MARGIN_DB = 10.0  # a sidechain alone left music on short lines
RIDER_GATE_BELOW_VOICE_DB = 26.0
RIDER_HOLD_FRAMES = 3  # 150 ms either side
SFX_UNDER_DB = 10.0
SFX_UNDER_RANGE = (8.0, 12.0)
SFX_PEAK_OVER_VOICE_DB = 7.0
PEAK_LIMIT = 10 ** (-1 / 20)


@dataclass
class Piece:
    clip: str
    source: np.ndarray
    src: int
    start: int
    end: int
    gain: float
    lead: int = 0
    trail: int = 0
    fade_in: int = 0
    fade_out: int = 0
    linear_in: bool = False
    linear_out: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def spare_after(self) -> int:
        return max(len(self.source) - self.src - self.length, 0) if len(self.source) else self.length

    @property
    def spare_before(self) -> int:
        return self.src if len(self.source) else self.length


def _take(source: np.ndarray, first: int, count: int) -> np.ndarray:
    """A slice that reads as silence past either end of the source."""
    out = np.zeros(count, dtype=np.float64)
    low, high = max(first, 0), min(first + count, len(source))
    if high > low:
        out[low - first : high - first] = source[low:high]
    return out


def _curve(count: int, linear: bool) -> np.ndarray:
    position = (np.arange(count) + 0.5) / count
    return position if linear else np.sin(position * np.pi / 2)


def _pieces(project: Project, edl: Edl) -> list[Piece]:
    """Lay segments end to end. Each lasts exactly as many frames as the plate gives it."""
    clips = {clip.id: clip for clip in project.clips()}
    loaded: dict[str, np.ndarray] = {}
    pieces: list[Piece] = []
    frames = 0
    for segment in edl.segments:
        if segment.end <= segment.start:
            raise AltocueError(f"Segment {segment.id} ends before it starts.", fix="Fix edl.json, or run `altocue cut`.")
        if segment.clip not in loaded:
            path = project.audio_dir / f"{segment.clip}.wav"
            if segment.clip in clips and not clips[segment.clip].has_audio:
                loaded[segment.clip] = np.zeros(0, dtype=np.float32)
            elif path.exists():
                loaded[segment.clip], rate = ffmpeg.read_wav(path)
                if rate != RATE:
                    raise AltocueError(f"{path} is {rate} Hz, not {RATE}.", fix="Run `altocue clean --force`.")
            else:
                raise AltocueError(f"No cleaned audio for clip {segment.clip}.", fix="Run `altocue clean`.")
        start = round(frames / edl.fps * RATE)
        frames += max(1, round((segment.end - segment.start) * edl.fps))
        end = round(frames / edl.fps * RATE)
        pieces.append(Piece(segment.clip, loaded[segment.clip], round(segment.start * RATE), start, end, segment.gain))
    return pieces


def _join(a: Piece, b: Piece, half: int) -> None:
    """Blend two neighbours around their join, using sound from just outside each cut."""
    gap = b.src - (a.src + a.length)
    if a.clip == b.clip and abs(gap) <= 1:
        b.src = a.src + a.length
        return
    half = min(half, a.length // 2, b.length // 2)
    if half < 1:
        return
    correlated = a.clip == b.clip and abs(gap) < 2 * half  # equal power would bump the level
    trail = min(half, a.spare_after)
    lead = min(2 * half - trail, b.spare_before)
    trail = min(2 * half - lead, a.spare_after)
    if lead + trail < half:
        a.fade_out, b.fade_in = half, half
        return
    a.trail, b.lead = trail, lead
    a.fade_out, b.fade_in = lead + trail, lead + trail
    a.linear_out, b.linear_in = correlated, correlated


def mix_voice(project: Project, edl: Edl, dest: str | Path, fade_ms: float = FADE_MS) -> Path:
    """Write the edited voice: sample-accurate, frame-exact, and faded at every join."""
    if not edl.segments:
        raise AltocueError("The cut is empty.", fix="Run `altocue cut` and check edl.json.")
    pieces = _pieces(project, edl)
    half = max(1, round(fade_ms / 1000 * RATE / 2))
    for a, b in zip(pieces, pieces[1:], strict=False):
        _join(a, b, half)
    pieces[0].fade_in = min(half, pieces[0].length // 2)
    pieces[-1].fade_out = min(half, pieces[-1].length // 2)

    out = np.zeros(pieces[-1].end, dtype=np.float64)
    for piece in pieces:
        first, last = piece.start - piece.lead, piece.end + piece.trail
        chunk = _take(piece.source, piece.src - piece.lead, last - first) * piece.gain
        if piece.fade_in:
            chunk[: piece.fade_in] *= _curve(piece.fade_in, piece.linear_in)
        if piece.fade_out:
            chunk[-piece.fade_out :] *= _curve(piece.fade_out, piece.linear_out)[::-1]
        out[max(first, 0) : min(last, len(out))] += chunk[max(-first, 0) : len(chunk) - max(last - len(out), 0)]
    peak = np.abs(out).max()
    return ffmpeg.write_wav(dest, out / peak if peak > 1 else out, RATE)


def sfx_names() -> list[str]:
    return sorted(path.stem for path in SFX_DIR.glob("*.ogg"))


def sfx_path(name: str) -> Path:
    path = SFX_DIR / f"{name}.ogg"
    if not path.exists():
        raise AltocueError(f"No effect named {name}.", fix=f"Use one of {', '.join(sfx_names())}.")
    return path


def max_momentary(path: str | Path) -> float:
    """Loudest 400 ms in LUFS. Effects are too short for an integrated reading."""
    done = ffmpeg.run(["-i", str(path), "-af", "apad=whole_dur=1,ebur128=framelog=info", "-f", "null", "-"])
    values = [float(v) for v in re.findall(r"\bM:\s*(-?[\d.]+)", done.stderr)]
    return max(values, default=-120.0)


def _read_stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0


def _write_stereo(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.rint(np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return path


def _voice_level(path: Path, samples: np.ndarray) -> float:
    if np.abs(samples).max() < 1e-4:
        return VOICE_LUFS
    level = ffmpeg.loudness(path)["integrated"]
    return level if level > -69 else VOICE_LUFS


def _level_db(samples: np.ndarray, hop: int) -> np.ndarray:
    count = len(samples) // hop
    frames = samples[: count * hop].reshape(count, hop)
    return 20 * np.log10(np.sqrt(np.mean(frames**2, axis=1)) + 1e-9)


def _ride(bed: np.ndarray, voice: np.ndarray, gate_db: float) -> tuple[np.ndarray, float]:
    """Hold the bed under every word the sidechain let through. Gain only ever comes down.

    The voice is read with a short peak hold. Read frame by frame, every word tail would pull
    the music down and the slow release would keep it there, which is audible pumping.
    """
    hop = int(0.05 * RATE)
    count = min(len(voice), len(bed)) // hop
    voice_db = _level_db(voice[: count * hop], hop)
    held = np.lib.stride_tricks.sliding_window_view(np.pad(voice_db, RIDER_HOLD_FRAMES, mode="edge"), 2 * RIDER_HOLD_FRAMES + 1).max(axis=1)
    bed_db = _level_db(bed[: count * hop].mean(axis=1), hop)
    speaking = voice_db > gate_db
    want = np.where(speaking, np.minimum(0.0, held - RIDER_MARGIN_DB - bed_db), 0.0)
    want = np.minimum(want, np.minimum(np.r_[want[1:], 0.0], np.r_[0.0, want[:-1]]))
    attack, release = 1 - np.exp(-0.05 / 0.02), 1 - np.exp(-0.05 / 0.30)
    gain = np.zeros(count)
    for i in range(count):
        previous = gain[i - 1] if i else 0.0
        gain[i] = previous + (want[i] - previous) * (attack if want[i] < previous else release)
    times = np.arange(count) * hop + hop / 2
    curve = 10 ** (np.interp(np.arange(len(bed)), np.r_[times, times[-1] + 0.6 * RATE], np.r_[gain, 0.0]) / 20)  # music comes back after the last word
    ridden = bed * curve[:, None]
    after = _level_db(ridden[: count * hop].mean(axis=1), hop)
    return ridden, float(np.median((held - after)[speaking])) if speaking.any() else 0.0


def _bed(music: Path, voice: Path, samples: np.ndarray, level: float, total: int, folder: Path) -> tuple[np.ndarray, dict[str, Any]]:
    if not music.exists():
        raise AltocueError(f"No music at {music}.", fix="Check the path in motion.json.")
    gain = level - MUSIC_UNDER_VOICE_LU - ffmpeg.loudness(music)["integrated"]
    seconds = total / RATE
    fade_out = min(MUSIC_FADE_OUT, seconds / 3)
    key_level = min(max(10 ** ((VOICE_LUFS - level) / 20), 0.016), 64.0)
    graph = (
        f"[0:a]aresample={RATE},aformat=channel_layouts=stereo,aloop=loop=-1:size=2147483647,atrim=0:{seconds:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:d={MUSIC_FADE_IN},afade=t=out:st={seconds - fade_out:.6f}:d={fade_out:.3f},volume={gain:.2f}dB[music];"
        f"[1:a]aformat=channel_layouts=stereo,apad=whole_len={total}[key];"
        f"[music][key]sidechaincompress={DUCK}:level_sc={key_level:.4f},atrim=end_sample={total}[bed]"
    )
    path = folder / "bed.wav"
    ffmpeg.run(
        ["-i", str(music), "-i", str(voice), "-filter_complex", graph, "-map", "[bed]", "-ar", str(RATE), "-c:a", "pcm_s16le", str(path)]
    )
    ducked = _read_stereo(path)
    bed = np.zeros((total, 2))
    bed[: min(total, len(ducked))] = ducked[:total]
    ridden, under = _ride(bed, samples, level - RIDER_GATE_BELOW_VOICE_DB)
    return ridden, {"file": str(music), "gain_db": round(gain, 2), "under_voice_db": round(under, 1)}


def _effect(cue: dict[str, Any], index: int, level: float, total: int, folder: Path) -> tuple[np.ndarray, int, dict[str, Any]]:
    under = float(cue.get("under_db", SFX_UNDER_DB))
    if not SFX_UNDER_RANGE[0] <= under <= SFX_UNDER_RANGE[1]:
        raise AltocueError(f"Effect level {under:g} dB under the voice is out of range.", fix="Use 8 to 12 dB under the voice.")
    path = Path(cue["file"]) if "file" in cue else sfx_path(cue["name"])
    if not path.exists():
        raise AltocueError(f"No effect at {path}.", fix="Check the path.")
    start = round(float(cue["at"]) * RATE)
    if not 0 <= start < total:
        raise AltocueError(
            f"Effect {path.stem} at {float(cue['at']):.2f}s is outside the {total / RATE:.2f}s mix.",
            fix="Move it inside the film, or remove it.",
        )
    wav = ffmpeg.extract_wav(path, folder / f"effect{index}.wav")
    samples, _ = ffmpeg.read_wav(wav)
    peak = float(20 * np.log10(np.abs(samples).max() + 1e-9))
    gain = level - under - max_momentary(wav)
    ceiling = level + SFX_PEAK_OVER_VOICE_DB - peak
    placed = samples.astype(np.float64) * 10 ** (min(gain, ceiling) / 20)
    return (
        placed,
        start,
        {
            "name": cue.get("name", path.stem),
            "at": float(cue["at"]),
            "under_db": under,
            "gain_db": round(min(gain, ceiling), 2),
            "capped": ceiling < gain,
        },
    )


def mix_bed(voice: str | Path, dest: str | Path, music: str | Path | None = None, sfx: Any = (), tail: float = 0.0) -> Path:
    """Mix voice, ducked music and effects to stereo 48 kHz, running `tail` seconds past the voice.

    Each entry in `sfx` is `{"name": "pop", "at": 1.2}` or `{"file": path, "at": 1.2}`, with an
    optional `under_db` from 8 to 12. The numbers land in a `.json` beside the mix.
    """
    voice, dest = Path(voice), Path(dest)
    if not voice.exists():
        raise AltocueError(f"No voice track at {voice}.", fix="Run `altocue plate`.")
    samples, _ = ffmpeg.read_wav(voice)
    samples = samples.astype(np.float64)
    total = len(samples) + round(tail * RATE)
    level = _voice_level(voice, samples)
    out = np.zeros((total, 2))
    out[: len(samples)] = samples[:, None]
    report: dict[str, Any] = {"voice_lufs": level, "music": None, "sfx": []}
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        folder = Path(scratch)
        if music:
            bed, report["music"] = _bed(Path(music), voice, samples, level, total, folder)
            out += bed
        for index, cue in enumerate(sfx):
            placed, start, entry = _effect(cue, index, level, total, folder)
            end = min(start + len(placed), total)
            out[start:end] += placed[: end - start, None]
            report["sfx"].append(entry)
    peak = float(np.abs(out).max())
    if peak > PEAK_LIMIT:
        out *= PEAK_LIMIT / peak
    report["trim_db"] = round(20 * np.log10(PEAK_LIMIT / peak), 2) if peak > PEAK_LIMIT else 0.0
    _write_stereo(dest, out)
    write_json(dest.with_suffix(".json"), report)
    return dest
