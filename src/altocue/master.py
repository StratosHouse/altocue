"""Master the sound: two-pass loudness, then a true-peak limit checked on the decoded file."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import ffmpeg
from .errors import AltocueError
from .project import Project, write_json

RATE = 48000
LOUDNESS = -14.0
TRUE_PEAK = -1.5
HOT_LOUDNESS = -12.0
RANGE = 11.0
MAX_LINEAR_RANGE = 15.0  # speech with pauses reads a little over 11, and riding it costs more than it gives
HEADROOM_DB = 0.3  # the limiter starts this far under the target
STEP_DB = 0.1  # `ebur128` prints one decimal, so a pass must read one step under
LOUDNESS_TOLERANCE = 0.3
PASS_TOLERANCE = 0.5
MAX_LIMITING_DB = 6.0
ATTEMPTS = 6
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
AUDIO_CODECS = {".wav": ["-c:a", "pcm_s16le"], ".flac": ["-c:a", "flac"]}
AAC = ["-c:a", "aac", "-b:a", "192k"]


@dataclass(frozen=True)
class Target:
    """A named loudness target, and where its numbers come from. `primary` is False when no first-hand source publishes them."""

    name: str
    loudness: float
    true_peak: float
    source: str
    primary: bool


TARGETS = {
    "streaming": Target(
        "streaming",
        -14.0,
        -1.0,
        "Spotify for Artists: tracks are adjusted to -14 dB LUFS, and masters should stay below -1 dB true peak. "
        "https://support.spotify.com/us/artists/article/loudness-normalization/",
        True,
    ),
    "podcast": Target(
        "podcast",
        -16.0,
        -1.0,
        "Apple Podcasts audio requirements: around -16 dB LKFS, +/- 1 dB, true peak no higher than -1 dBFS. "
        "https://podcasters.apple.com/support/893-audio-requirements",
        True,
    ),
    "broadcast": Target(
        "broadcast",
        -23.0,
        -1.0,
        "EBU R 128 (2023): programme loudness -23.0 LUFS, true peak no higher than -1 dBTP. https://tech.ebu.ch/docs/r/r128.pdf",
        True,
    ),
    "social": Target(
        "social",
        LOUDNESS,
        TRUE_PEAK,
        "No primary source. TikTok, Instagram, YouTube and LinkedIn publish no loudness target, so this is Altocue's own default.",
        False,
    ),
    "hot": Target("hot", HOT_LOUDNESS, TRUE_PEAK, "No primary source. Altocue's loud option, for films that must compete on a phone speaker.", False),
}


def named_target(name: str) -> Target:
    if name not in TARGETS:
        raise AltocueError(f"Unknown loudness target {name}.", fix=f"Use {', '.join(TARGETS)}, or a number in LUFS.")
    return TARGETS[name]


def loudness_arg(value: str) -> float | Target:
    return TARGETS[value] if value in TARGETS else float(value)


def project_target(project: Project) -> tuple[float, float]:
    """The project's `[audio]` target. `loudness` is a number or a name, and a set `true_peak` wins."""
    chosen = project.setting("audio", "loudness", LOUDNESS)
    if isinstance(chosen, str):
        named = named_target(chosen)
        return named.loudness, float(project.setting("audio", "true_peak", named.true_peak))
    return float(chosen), float(project.setting("audio", "true_peak", TRUE_PEAK))


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--input", "-i", help="File to master. Default is the project's out/render.mp4")
    parser.add_argument("--output", "-o", help="Where to write it. Default is the project's out/final.mp4")
    parser.add_argument("--loudness", type=loudness_arg, help=f"Integrated LUFS, or a named target: {', '.join(TARGETS)}")
    parser.add_argument("--true-peak", type=float, help="True-peak ceiling in dBTP")


@lru_cache(maxsize=1)
def limiter_latency() -> str:
    """`latency=1` undoes the limiter's 5 ms delay. Older ffmpeg builds lack the option."""
    done = subprocess.run([ffmpeg.require(), "-hide_banner", "-h", "filter=alimiter"], capture_output=True, text=True)
    return ":latency=1" if "latency" in done.stdout else ""


def _loudnorm_json(text: str) -> dict[str, Any]:
    return json.loads(text[text.rindex("{") : text.rindex("}") + 1])


class Silent(AltocueError):
    """There is no sound to measure."""


def _allowed_range(found: dict[str, float]) -> float:
    return max(RANGE, found["input_lra"]) if found["input_lra"] <= MAX_LINEAR_RANGE else RANGE


def measure(path: str | Path, loudness: float, true_peak: float) -> dict[str, float]:
    """The first `loudnorm` pass."""
    done = ffmpeg.run(["-i", str(path), "-af", f"loudnorm=I={loudness}:TP={true_peak}:LRA={RANGE}:print_format=json", "-f", "null", "-"])
    found = _loudnorm_json(done.stderr)
    values = {key: float(found[key]) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    if not all(math.isfinite(v) for v in values.values()) or values["input_i"] < -70:
        raise Silent(
            f"{path} is silent or too short to measure.", fix="Check the sound in the render. Master needs at least a second of audio."
        )
    return values


def _level_filter(found: dict[str, float], loudness: float, true_peak: float) -> str:
    """Linear `loudnorm`, unless plain gain and the limiter are the better linear path.

    `loudnorm` drops to dynamic mode when gain would pass the peak target, even by a hair, or
    when the range is over target. Dynamic mode rides the level, so a few peaks go to the
    limiter instead, and only a range past `MAX_LINEAR_RANGE` is handed to dynamic mode.
    """
    gain = loudness - found["input_i"]
    over = found["input_tp"] + gain - true_peak
    allowed = _allowed_range(found)
    if 0 < over <= MAX_LIMITING_DB and found["input_lra"] <= allowed:
        return f"volume={gain:.2f}dB"
    measured = f"measured_I={found['input_i']}:measured_TP={found['input_tp']}:measured_LRA={found['input_lra']}"
    second = f"{measured}:measured_thresh={found['input_thresh']}:offset={found['target_offset']}:linear=true:print_format=json"
    return f"loudnorm=I={loudness}:TP={true_peak}:LRA={allowed}:{second}"


def _chain(level: str, trim: float, ceiling: float) -> str:
    parts = [level] + ([f"volume={trim:.2f}dB"] if trim else [])
    parts += ["aresample=192000", f"alimiter=limit={10 ** (ceiling / 20):.5f}:level=disabled{limiter_latency()}", f"aresample={RATE}"]
    return ",".join(parts)


def _encode(src: Path, dest: Path, chain: str, has_video: bool) -> str:
    args = ["-i", str(src)]
    if has_video:
        args += ["-map", "0:v:0", "-c:v", "copy"]
    args += ["-map", "0:a:0", "-af", chain, "-ar", str(RATE), *AUDIO_CODECS.get(dest.suffix.lower(), AAC)]
    if dest.suffix.lower() in VIDEO_SUFFIXES:
        args += ["-movflags", "+faststart"]
    return ffmpeg.run([*args, str(dest)]).stderr


def master_file(
    src: str | Path, dest: str | Path, loudness: float = LOUDNESS, true_peak: float = TRUE_PEAK, *, silent_film: bool = False
) -> dict[str, Any]:
    """Master `src` to `dest` and return the numbers measured on the finished file.

    AAC overshoots a limiter on transients, so the ceiling is checked on the decoded
    result and lowered until the file itself reads under the target.
    """
    src, dest = Path(src), Path(dest)
    if not src.is_file():
        raise AltocueError(f"Not found: {src}", fix="Run `altocue render`, or check the path.")
    if src.resolve() == dest.resolve():
        raise AltocueError("Master would overwrite its own source.", fix="Choose a different --output.")
    streams = ffmpeg.probe_json(src).get("streams", [])
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise AltocueError(f"{src} has no audio stream.", fix="Check the render. A silent film needs no master.")
    has_video = any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams)
    try:
        found = measure(src, loudness, true_peak)
    except Silent:
        if not silent_film:
            raise
        dest.parent.mkdir(parents=True, exist_ok=True)
        _encode(src, dest, "anull", has_video)
        return {"mode": "silent", "passed": True, "warnings": []}
    level = _level_filter(found, loudness, true_peak)

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.stem}.part{dest.suffix}")
    ceiling, trim, attempts = true_peak - HEADROOM_DB, 0.0, 0
    log = ""
    measured: dict[str, float] = {}
    try:
        while attempts < ATTEMPTS:
            attempts += 1
            log = _encode(src, part, _chain(level, trim, ceiling), has_video)
            measured = ffmpeg.loudness(part)
            over = measured["true_peak"] - (true_peak - STEP_DB)
            short = loudness - measured["integrated"]
            if over <= 0 and abs(short) <= LOUDNESS_TOLERANCE:
                break
            if over > 0:
                ceiling -= over + STEP_DB
            if abs(short) > LOUDNESS_TOLERANCE:
                trim += short
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)

    mode = _loudnorm_json(log).get("normalization_type", "linear") if level.startswith("loudnorm") else "linear"
    warnings = []
    if mode == "dynamic":
        warnings.append(
            f"loudnorm used dynamic mode: range {found['input_lra']:g} LU, peaks {found['input_tp']:g} dBTP. Level rides through the file."
        )
    passed = measured["true_peak"] < true_peak and abs(measured["integrated"] - loudness) <= PASS_TOLERANCE
    if not passed:
        warnings.append(f"Missed the target after {attempts} tries: {measured['integrated']} LUFS, {measured['true_peak']} dBTP.")
    return {
        "integrated": measured["integrated"],
        "true_peak": measured["true_peak"],
        "range": measured["range"],
        "target_loudness": loudness,
        "target_true_peak": true_peak,
        "passed": passed,
        "mode": mode,
        "limiter": not level.startswith("loudnorm"),
        "ceiling_db": round(ceiling, 2),
        "attempts": attempts,
        "source": {"integrated": found["input_i"], "true_peak": found["input_tp"], "range": found["input_lra"]},
        "warnings": warnings,
    }


def no_sound_in(project: Project) -> bool:
    """A film with nothing to hear. Decided from the render's own mix, because a clip can carry a track of pure silence."""
    try:
        if not any(clip.has_audio for clip in project.clips()):
            return True
    except AltocueError:
        pass
    mix = project.work / "mix.wav"
    if not mix.is_file():
        return False
    stat = mix.stat()
    key = (str(mix.resolve()), stat.st_mtime_ns, stat.st_size)
    if key not in _SILENT_MIX:
        try:
            measure(mix, LOUDNESS, TRUE_PEAK)
            _SILENT_MIX[key] = False
        except Silent:
            _SILENT_MIX[key] = True
        except (AltocueError, KeyError, ValueError):
            _SILENT_MIX[key] = False
    return _SILENT_MIX[key]


_SILENT_MIX: dict[tuple[str, int, int], bool] = {}  # one measurement per mix file, however many stages ask


def run(args: argparse.Namespace) -> int:
    project = None
    if args.input:
        if not args.output:
            raise AltocueError("--input needs --output.", fix="Add `--output final.mp4`.")
        src, dest = Path(args.input), Path(args.output)
    else:
        project = Project.open(args.project)
        src, dest = project.out / "render.mp4", project.out / "final.mp4"
        if not src.exists():
            raise AltocueError(f"No render at {src}.", fix="Run `altocue render` first.")
    loudness, true_peak = project_target(project) if project else (LOUDNESS, TRUE_PEAK)
    named = args.loudness if isinstance(args.loudness, Target) else None
    if named:
        loudness, true_peak = named.loudness, named.true_peak
        print(f"Target {named.name}: {loudness:g} LUFS, {true_peak:g} dBTP. {named.source}")
    elif args.loudness is not None:
        loudness = args.loudness
    true_peak = args.true_peak if args.true_peak is not None else true_peak
    try:
        result = master_file(src, dest, loudness=loudness, true_peak=true_peak)
    except Silent:
        if not (project and no_sound_in(project)):  # asked only when the render is silent, because it reads the whole mix
            raise
        result = master_file(src, dest, loudness=loudness, true_peak=true_peak, silent_film=True)
    if project:
        write_json(project.audio_dir / "master.json", result)
    if result["mode"] == "silent":
        print(f"This film has nothing to hear, so {dest} is the render as it was.")
        return 0
    print(f"Mastered {dest}: {result['integrated']} LUFS, {result['true_peak']} dBTP, range {result['range']} LU, {result['mode']}")
    for note in result["warnings"]:
        print(f"Warning: {note}")
    return 0 if result["passed"] else 1
