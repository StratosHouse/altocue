"""Thin wrappers around `ffmpeg` and `ffprobe`."""

from __future__ import annotations

import contextvars
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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


PROGRESS_AFTER = 5.0  # seconds a job runs before its first line, so a short job says nothing
PROGRESS_EVERY = 5.0  # seconds between lines


@dataclass
class Progress:
    """How far a long encode has got: lines on stderr and, with `file`, the same numbers as JSON for an agent to poll.

    `seconds` is the length of picture the whole job encodes. Several ffmpeg calls can share one job, and each adds
    the picture it encoded. A sound mix or a stream copy inside the job encodes no picture, so it does not move it.
    """

    label: str
    seconds: float
    file: Path | None = None
    encoded: float = 0.0  # picture finished by calls that are over
    began: float = field(default_factory=lambda: time.monotonic())
    said: float | None = None  # when the last line went out

    def at(self, encoded: float) -> None:
        """A call is `encoded` seconds into its own picture. Says so, unless the job is young or spoke a moment ago."""
        now = time.monotonic()
        if now - self.began < PROGRESS_AFTER or (self.said is not None and now - self.said < PROGRESS_EVERY):
            return
        self.said = now
        record = self.record(self.encoded + encoded, now, running=True)
        spent = int(now - self.began)
        print(f"{self.label}: {record['percent']}% after {spent // 60}:{spent % 60:02d}", file=sys.stderr, flush=True)  # never stdout: `altocue mcp` and pipes own it
        self.write(record)

    def record(self, encoded: float, now: float, *, running: bool) -> dict[str, object]:
        share = min(max(encoded / self.seconds, 0.0), 1.0)
        times = {"encoded": round(min(encoded, self.seconds), 3), "seconds": round(self.seconds, 3), "elapsed": round(now - self.began, 1)}
        return {"label": self.label, "percent": int(share * 100), **times, "running": running}

    def write(self, record: dict[str, object]) -> None:
        if self.file is None:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            part = self.file.with_name(self.file.name + ".part")
            part.write_text(json.dumps(record) + "\n")
            part.replace(self.file)
        except OSError:
            pass  # progress is a courtesy, and a full disk must stop the encode in ffmpeg's own words

    def close(self) -> None:
        """A file that says "running" must not outlive the job. A job that never spoke leaves no file."""
        if self.said is not None:
            self.write(self.record(self.encoded, time.monotonic(), running=False))


_JOB: contextvars.ContextVar[Progress | None] = contextvars.ContextVar("altocue_ffmpeg_progress", default=None)


@contextmanager
def progress(label: str, seconds: float, file: Path | None = None) -> Iterator[None]:
    """Every picture `run` encodes inside this block reports to one job, `seconds` of picture long.

    Opt-in: outside a block `run` is as quiet as ever. With no length to measure against, nothing is reported.
    """
    if seconds <= 0:
        yield
        return
    job = Progress(label, seconds, file)
    token = _JOB.set(job)
    try:
        yield
    finally:
        _JOB.reset(token)
        job.close()


def _reports(args: list[str]) -> bool:
    """False for a stream copy, which encodes nothing, and for a call that writes to stdout, where progress would land in its output."""
    copies = any(option in {"-c", "-c:v", "-codec", "-codec:v", "-vcodec"} and value == "copy" for option, value in zip(args, args[1:], strict=False))
    return not copies and not any(arg == "-" or arg.startswith("pipe:") for arg in args)


def _watched(cmd: list[str], job: Progress) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with its progress on stdout. Its log is still captured whole, so the result is the one a plain run gives."""
    watch = [*cmd[:4], "-progress", "pipe:1", "-nostats", *cmd[4:]]
    process = subprocess.Popen(watch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    log: list[str] = []
    reader = threading.Thread(target=lambda: log.append(process.stderr.read()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    encoded, picture = 0.0, False
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            key, _, value = line.strip().partition("=")
            picture = picture or key == "frame"  # a call with no picture never names a frame
            if picture and key == "out_time_us" and value.isdecimal():
                encoded = max(encoded, int(value) / 1e6)
                job.at(encoded)
        code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        reader.join()
        job.encoded += encoded
    return subprocess.CompletedProcess(cmd, code, "", "".join(log))


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg quietly. On failure the error carries the end of its log. Inside a `progress` block, a long encode says how far it is."""
    cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-y", *args]
    job = _JOB.get()
    if job is not None and capture and _reports(args):
        done = _watched(cmd, job)
    else:
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
