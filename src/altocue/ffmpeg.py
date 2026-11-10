"""Thin wrappers around `ffmpeg` and `ffprobe`."""

from __future__ import annotations

import json
import os
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
SOURCE_CLOCK = "source-pts-v1"
SOURCE_CLOCK_FIX = (
    "Back up the project, then run `altocue transcribe --force` and `altocue clean --force`. "
    "Review the existing cut against the original before rendering. Existing words and cuts have not been changed."
)


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


def run_bytes(args: list[str]) -> bytes:
    """Run ffmpeg and hand back what it wrote to stdout, for raw frames."""
    done = subprocess.run([require("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", *args], capture_output=True)
    if done.returncode != 0:
        raise AltocueError(f"ffmpeg failed:\n{done.stderr.decode(errors='replace')[-400:]}")
    return done.stdout


def concat_list(listing: Path, parts: list[Path]) -> Path:
    """An ffconcat list that names each part from the list's own folder, with any quote escaped, so no path can break it."""
    lines = []
    for part in parts:
        name = os.path.relpath(Path(part).resolve(), listing.parent.resolve()).replace("'", "'\\''")
        lines.append(f"file '{name}'")
    listing.write_text("ffconcat version 1.0\n" + "\n".join(lines) + "\n")
    return listing


STILL_BRANDS = {b"heic", b"heix", b"mif1", b"msf1", b"avif", b"avis"}


def sniff(path: str | Path) -> str | None:
    """ "video", "image" or None, from the first bytes. A name proves nothing: ffmpeg follows a playlist or a list hidden in any file."""
    with Path(path).open("rb") as handle:
        head = handle.read(16)
    if head[4:8] == b"ftyp":
        return "image" if head[8:12] in STILL_BRANDS else "video"
    if head[:4] in {b"\x1a\x45\xdf\xa3", b"OggS"} or (head[:4] == b"RIFF" and head[8:12] == b"AVI "):
        return "video"
    pictures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")
    if head.startswith(pictures) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    return None


def require_media(path: str | Path, kind: str, name: str) -> None:
    """Refuse a file that is not really a video or a picture before ffmpeg reads it."""
    if sniff(path) != kind:
        word = "a picture" if kind == "image" else "a video"
        fix = "Use a real media file: MP4, MOV, WebM or MKV for video, and PNG, JPEG, WebP or HEIC for a still."
        raise AltocueError(f"{name} is not {word} file, so nothing read it.", fix=fix)


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


def source_clock_filter(rate: int, *, samples: int | None = None) -> str:
    """Follow packet timestamps, filling gaps and trimming overlaps without changing playback speed.

    A decoder can return more samples than the source clock allows, even when the
    container reports the right duration. Trimming only the tail cannot repair
    that drift. Keep the incoming PTS until aresample has conformed the samples.
    """
    chain = f"aresample={rate}:async=1:min_hard_comp=0.001:first_pts=0"
    if samples is not None:
        chain += f",apad=whole_len={samples},atrim=end_sample={samples}"
    return chain


def extract_wav(source: str | Path, dest: str | Path, *, rate: int = 48000, start: float | None = None, end: float | None = None) -> Path:
    """Write source-clock mono 16-bit PCM; a requested range starts at WAV zero."""
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    run([*args, "-i", str(source), "-vn", "-map", "0:a:0", "-af", source_clock_filter(rate), "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dest)])
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


def loudness(path: str | Path, *, seconds: float | None = None) -> dict[str, float]:
    """Integrated loudness, range and true peak, measured with `ebur128`. `seconds` stops early."""
    limit = ["-t", f"{seconds:.3f}"] if seconds else []
    # framelog=verbose keeps the per-frame lines out of the log at ffmpeg's default level. "quiet" needs ffmpeg 6 or later,
    # and ffmpeg 5.1 (Debian 12, Ubuntu 22.04 backports) refuses it with "Invalid argument", which stopped every clean.
    done = run([*limit, "-i", str(path), "-af", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"])
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
