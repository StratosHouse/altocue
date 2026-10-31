"""Thin wrappers around `ffmpeg` and `ffprobe`."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np

from .errors import AltocueError
from .models import Clip

INSTALL_FIX = "macOS: brew install ffmpeg. Ubuntu or Debian: sudo apt install ffmpeg."


def require(tool: str = "ffmpeg") -> str:
    found = shutil.which(tool)
    if not found:
        raise AltocueError(f"{tool} is not on PATH.", fix=INSTALL_FIX)
    return found


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg quietly. On failure the error carries the end of its log."""
    cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-y", *args]
    done = subprocess.run(cmd, capture_output=capture, text=True, errors="replace")
    if done.returncode != 0:
        tail = "\n".join((done.stderr or "").strip().splitlines()[-8:])
        raise AltocueError(f"ffmpeg failed:\n{tail}")
    return done


def probe_json(path: str | Path) -> dict:
    cmd = [require("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    done = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if done.returncode != 0:
        raise AltocueError(f"Cannot read {path}: {done.stderr.strip()}")
    return json.loads(done.stdout)


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return int(side["rotation"])
    tag = stream.get("tags", {}).get("rotate")
    return int(tag) if tag else 0


def _rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    return float(Fraction(value))


def probe_clip(path: str | Path, clip_id: str | None = None) -> Clip:
    """Read one source file. A file with no video stream is an error."""
    info = probe_json(path)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    if video is None:
        raise AltocueError(f"{path} has no video stream.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    avg, real = _rate(video.get("avg_frame_rate")), _rate(video.get("r_frame_rate"))
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    return Clip(
        id=clip_id or Path(path).stem,
        path=str(path),
        duration=duration,
        fps=avg or real,
        width=int(video["width"]),
        height=int(video["height"]),
        rotation=_rotation(video),
        pix_fmt=video.get("pix_fmt", ""),
        transfer=video.get("color_transfer", ""),
        primaries=video.get("color_primaries", ""),
        has_audio=audio is not None,
        sample_rate=int(audio.get("sample_rate", 0)) if audio else 0,
        variable_frame_rate=bool(avg and real and abs(avg - real) > 0.5),
    )


@lru_cache(maxsize=1)
def filters() -> frozenset[str]:
    done = subprocess.run([require("ffmpeg"), "-hide_banner", "-filters"], capture_output=True, text=True)
    return frozenset(m.group(1) for m in re.finditer(r"^\s*[T.|A-Z]{2,3}\s+(\S+)\s+\S+->\S+", done.stdout, re.MULTILINE))


def has_filter(name: str) -> bool:
    return name in filters()


def extract_wav(source: str | Path, dest: str | Path, *, rate: int = 48000, start: float | None = None, end: float | None = None) -> Path:
    """Write mono 16-bit PCM. The one audio format every stage reads."""
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    run([*args, "-i", str(source), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dest)])
    return Path(dest)


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read mono PCM as floats from -1 to 1."""
    with wave.open(str(path), "rb") as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise AltocueError(f"{path} is not 16-bit PCM.")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return (samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples), rate


def write_wav(path: str | Path, samples: np.ndarray, rate: int) -> Path:
    pcm = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())
    return Path(path)


def loudness(path: str | Path) -> dict[str, float]:
    """Integrated loudness, range and true peak, measured with `ebur128`."""
    done = run(["-i", str(path), "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
    text = done.stderr
    found = {
        "integrated": re.search(r"I:\s+(-?[\d.]+) LUFS", text),
        "range": re.search(r"LRA:\s+(-?[\d.]+) LU", text),
        "true_peak": re.search(r"Peak:\s+(-?[\d.]+) dBFS", text),
    }
    if not all(found.values()):
        raise AltocueError(f"Could not measure loudness of {path}.")
    return {key: float(match.group(1)) for key, match in found.items()}  # type: ignore[union-attr]


def frame(source: str | Path, at: float, dest: str | Path, *, width: int | None = None) -> Path:
    args = ["-ss", f"{at:.3f}", "-i", str(source), "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-2"]
    run([*args, "-q:v", "2", str(dest)])
    return Path(dest)
