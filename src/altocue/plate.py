"""The picture and sound edit, with no graphics. Each segment is cached by its recipe."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import ffmpeg, grade, mix
from .errors import AltocueError
from .models import Clip, Segment
from .project import Project

ZOOM_ORIGIN_Y = 0.42


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--draft", action="store_true", help="Half size for a quick look")


@dataclass(frozen=True)
class Canvas:
    width: int
    height: int
    fps: int
    crf: int
    preset: str


def canvas(project: Project, *, draft: bool) -> Canvas:
    width, height = project.size
    if draft:
        return Canvas(width // 2 - (width // 2) % 2, height // 2 - (height // 2) % 2, project.fps, 26, "veryfast")
    return Canvas(width, height, project.fps, 16, "medium")


def fit_crop(source: tuple[int, int], target: tuple[int, int], crop: list[float] | None) -> tuple[float, float, float, float]:
    """The crop in display pixels. With none given, centre the target aspect."""
    if crop and abs(crop[2] / crop[3] - target[0] / target[1]) < 0.02:  # a crop from an older aspect would stretch
        return tuple(float(v) for v in crop)  # type: ignore[return-value]
    src_w, src_h = source
    aspect = target[0] / target[1]
    if src_w / src_h > aspect:
        width = src_h * aspect
        return ((src_w - width) / 2, 0.0, width, float(src_h))
    height = src_w / aspect
    return (0.0, (src_h - height) * ZOOM_ORIGIN_Y, float(src_w), height)


def zoomed(crop: tuple[float, float, float, float], zoom: float) -> tuple[float, float, float, float]:
    x, y, width, height = crop
    new_w, new_h = width / zoom, height / zoom
    return (x + (width - new_w) / 2, y + (height - new_h) * ZOOM_ORIGIN_Y, new_w, new_h)


def _even(value: float) -> int:
    return max(2, int(value / 2) * 2)  # down, because an odd source has no pixel to round up to


def push_filter(c: Canvas, frames: int, start: float, end: float) -> str:
    """A slow zoom with sub-pixel motion. `crop` alone steps by whole pixels and jitters."""
    span = max(frames - 1, 1)
    z = f"({start}+({end}-{start})*n/{span})"
    w, h = f"(W/{z})", f"(H/{z})"
    x0, y0 = f"((W-{w})/2)", f"((H-{h})*{ZOOM_ORIGIN_Y})"
    x1, y1 = f"({x0}+{w})", f"({y0}+{h})"
    return f"perspective={x0}:{y0}:{x1}:{y0}:{x0}:{y1}:{x1}:{y1}:interpolation=cubic:eval=frame:sense=source"


def segment_filter(clip: Clip, segment: Segment, c: Canvas, lut: Path | None) -> str:
    steady = segment.zoom_from if segment.zoom_from == segment.zoom_to else min(segment.zoom_from, segment.zoom_to)
    x, y, width, height = zoomed(fit_crop(clip.display_size, (c.width, c.height), segment.crop), steady)
    geometry = f"crop={_even(width)}:{_even(height)}:{int(x)}:{int(y)},scale={c.width}:{c.height}:flags=lanczos"
    parts = [grade.video_filter(clip, lut, geometry=geometry), f"fps={c.fps}"]
    if segment.zoom_from != segment.zoom_to:
        frames = max(1, round((segment.end - segment.start) * c.fps))
        parts.append(push_filter(c, frames, segment.zoom_from / steady, segment.zoom_to / steady))
    parts.append("setsar=1")
    return ",".join(parts)


def encode_args(c: Canvas) -> list[str]:
    tags = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    return ["-c:v", "libx264", "-crf", str(c.crf), "-preset", c.preset, "-pix_fmt", "yuv420p", *tags]


def build_segment(project: Project, clip: Clip, segment: Segment, c: Canvas, folder: Path) -> Path:
    lut = grade.clip_lut(project, clip)
    frames = max(1, round((segment.end - segment.start) * c.fps))
    chain = segment_filter(clip, segment, c, lut)
    lut_hash = hashlib.sha256(lut.read_bytes()).hexdigest() if lut else ""
    source = Path(clip.path).stat()
    recipe = {"clip": clip.path, "size": source.st_size, "modified": source.st_mtime_ns, "start": segment.start, "frames": frames}
    recipe |= {"chain": chain, "canvas": asdict(c), "lut": lut_hash}
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    dest = folder / f"{segment.id}-{key}.mp4"
    if dest.exists():
        return dest
    temp = dest.with_suffix(".part.mp4")
    ffmpeg.run(["-ss", f"{segment.start:.4f}", "-i", clip.path, "-an", "-vf", chain, "-frames:v", str(frames), *encode_args(c), str(temp)])
    temp.rename(dest)
    return dest


def build(project: Project, *, draft: bool = False) -> tuple[Path, Path]:
    """Return the silent picture and the edited voice track."""
    edl = project.edl()
    if not edl.segments:
        raise AltocueError("The cut is empty.", fix="Run `altocue cut` and check edl.json.")
    c = canvas(project, draft=draft)
    clips = {clip.id: clip for clip in project.clips()}
    folder = project.work / ("segments-draft" if draft else "segments")
    folder.mkdir(parents=True, exist_ok=True)
    parts = [build_segment(project, clips[s.clip], s, c, folder) for s in edl.segments]
    listing = folder / "list.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "\n".join(f"file '{p.name}'" for p in parts) + "\n")
    picture = project.work / ("plate-draft.mp4" if draft else "plate.mp4")
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(picture)])
    voice = project.work / "voice.wav"
    mix.mix_voice(project, edl, voice)
    return picture, voice


def run(args: argparse.Namespace) -> int:
    picture, voice = build(Project.open(args.project), draft=args.draft)
    print(f"Plate: {picture}\nVoice: {voice}")
    return 0
