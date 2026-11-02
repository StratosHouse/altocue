"""The picture and sound edit, with no graphics. Each segment is cached by its recipe."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import ffmpeg, grade, locks, mix, transitions
from .errors import AltocueError
from .models import Clip, Edl, Segment
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


@dataclass(frozen=True)
class Piece:
    """A run of finished frames. Every frame of the film belongs to exactly one piece, in order."""

    start: int
    frames: int
    segment: int
    first: int = 0  # The frame of the segment it starts on. Before 0 or past the end reads the source beyond the cut.
    hold: bool = False  # One frame, held.
    blend: dict[str, Any] | None = None  # A transition out of this segment into the next.


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


def push_filter(c: Canvas, frames: int, start: float, end: float, first: int = 0) -> str:
    """A slow zoom with sub-pixel motion. `crop` alone steps by whole pixels and jitters.

    A piece that starts `first` frames in carries on from there, and past either end the zoom stays put.
    """
    span = max(frames - 1, 1)
    place = f"in/{span}" if not first else f"clip((in+{first})/{span},0,1)"  # perspective names its frame counter `in`, not `n`
    z = f"({start}+({end}-{start})*{place})"
    w, h = f"(W/{z})", f"(H/{z})"
    x0, y0 = f"((W-{w})/2)", f"((H-{h})*{ZOOM_ORIGIN_Y})"
    x1, y1 = f"({x0}+{w})", f"({y0}+{h})"
    return f"perspective={x0}:{y0}:{x1}:{y0}:{x0}:{y1}:{x1}:{y1}:interpolation=cubic:eval=frame:sense=source"


def segment_filter(clip: Clip, segment: Segment, c: Canvas, lut: Path | None, *, first: int = 0, finish: str = "") -> str:
    """`first` says which frame of the segment the piece starts on. `finish` runs on the finished frames."""
    steady = segment.zoom_from if segment.zoom_from == segment.zoom_to else min(segment.zoom_from, segment.zoom_to)
    x, y, width, height = zoomed(fit_crop(clip.display_size, (c.width, c.height), segment.crop), steady)
    geometry = f"crop={_even(width)}:{_even(height)}:{int(x)}:{int(y)},scale={c.width}:{c.height}:flags=lanczos"
    parts = [grade.video_filter(clip, lut, geometry=geometry), f"fps={c.fps}"]
    if segment.zoom_from != segment.zoom_to:
        frames = max(1, round((segment.end - segment.start) * c.fps))
        parts.append(push_filter(c, frames, segment.zoom_from / steady, segment.zoom_to / steady, first))
    if finish:
        parts.append(finish)
    parts.append("setsar=1")
    return ",".join(parts)


def encode_args(c: Canvas) -> list[str]:
    tags = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    return ["-c:v", "libx264", "-crf", str(c.crf), "-preset", c.preset, "-pix_fmt", "yuv420p", *tags]


def segment_frames(segment: Segment, fps: int) -> int:
    return max(1, round((segment.end - segment.start) * fps))


def _pad(seek: float, frames: int, clip: Clip, fps: int) -> tuple[float, int, str]:
    """Where to read from, and how to fill frames the source does not have: its first or last frame, held."""
    lead = max(0, math.ceil(-seek * fps - 1e-6))
    begin = max(seek, 0.0)
    pads = [f"tpad=start_mode=clone:start={lead}"] if lead else []
    if begin + (frames - lead) / fps > clip.duration - 1 / fps:
        pads.append("tpad=stop_mode=clone:stop=-1")
    return begin, lead, ",".join(pads)


def build_segment(project: Project, clip: Clip, segment: Segment, c: Canvas, folder: Path, *, first: int = 0, frames: int | None = None, hold: bool = False) -> Path:
    """Encode a run of one segment. By default that is all of it, and `first` and `frames` take a part or reach past it."""
    lut = grade.clip_lut(project, clip)
    frames = segment_frames(segment, c.fps) if frames is None else frames
    begin, lead, finish = _pad(segment.start + first / c.fps, 1 if hold else frames, clip, c.fps)
    if hold:
        finish = f"trim=end_frame=1,tpad=stop_mode=clone:stop={frames - 1}"
    chain = segment_filter(clip, segment, c, lut, first=first + lead, finish=finish)
    lut_hash = hashlib.sha256(lut.read_bytes()).hexdigest() if lut else ""
    source = Path(clip.path).stat()
    recipe = {"clip": clip.path, "size": source.st_size, "modified": source.st_mtime_ns, "start": begin, "frames": frames}
    recipe |= {"chain": chain, "canvas": asdict(c), "lut": lut_hash}
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    dest = folder / f"{segment.id}-{key}.mp4"
    if dest.exists():
        return dest
    temp = dest.with_suffix(".part.mp4")
    seek = max(begin - 0.5 / c.fps, 0.0)  # half a frame early: printed to four places, a start on the frame can round past it
    ffmpeg.run(["-ss", f"{seek:.4f}", "-i", clip.path, "-an", "-vf", chain, "-frames:v", str(frames), *encode_args(c), str(temp)])
    temp.rename(dest)
    return dest


def _carve(pieces: list[Piece], hold: Piece) -> list[Piece]:
    """Overwrite a run of frames with a hold, so what follows it stays exactly where it was."""
    end = hold.start + hold.frames
    kept: list[Piece] = []
    for piece in pieces:
        stop = piece.start + piece.frames
        if stop <= hold.start or piece.start >= end:
            kept.append(piece)
            continue
        if piece.blend:
            raise AltocueError(f"A freeze at frame {hold.start} falls inside a transition.", fix="Move the freeze clear of the cut, or drop the transition.")
        if piece.hold:
            raise AltocueError(f"Two freezes overlap at frame {hold.start}.", fix="Leave some picture between them.")
        if piece.start < hold.start:
            kept.append(Piece(piece.start, hold.start - piece.start, piece.segment, piece.first))
        if stop > end:
            kept.append(Piece(end, stop - end, piece.segment, piece.first + end - piece.start))
    return sorted([*kept, hold], key=lambda piece: piece.start)


def layout(lengths: list[int], blends: list[dict[str, Any]], holds: list[dict[str, Any]]) -> list[Piece]:
    """Which frames come from where. The film is `sum(lengths)` frames whatever is in here.

    A transition takes half its frames from each side of the cut, and each side reads on past its
    edge for the other half. A freeze overwrites frames, so picture comes back in step with the sound.
    """
    starts = [sum(lengths[:index]) for index in range(len(lengths))]
    total = sum(lengths)
    head, tail = [0] * len(lengths), [0] * len(lengths)
    joins = []
    for item in blends:
        cut, frames = item["cut"], item["frames"]
        before = frames // 2
        tail[cut - 1], head[cut] = before, frames - before
        joins.append(Piece(starts[cut] - before, frames, cut - 1, lengths[cut - 1] - before, blend=item))
    for index, length in enumerate(lengths):
        if head[index] + tail[index] > length:
            raise AltocueError(f"Segment {index + 1} is too short for its transitions, at {length} frames.", fix="Shorten them, or keep more of that segment.")
    plain = [Piece(starts[i] + head[i], lengths[i] - head[i] - tail[i], i, head[i]) for i in range(len(lengths)) if lengths[i] > head[i] + tail[i]]
    pieces = sorted([*plain, *joins], key=lambda piece: piece.start)
    for item in sorted(holds, key=lambda hold: hold["frame"]):
        frame, frames = item["frame"], item["frames"]
        if frame + frames > total:
            raise AltocueError(f"A freeze at frame {frame} runs past the end of the film.", fix="Shorten it, or move it earlier.")
        segment = max(index for index, start in enumerate(starts) if start <= frame)
        pieces = _carve(pieces, Piece(frame, frames, segment, frame - starts[segment], hold=True))
    return pieces


def _build_piece(project: Project, piece: Piece, edl: Edl, clips: dict[str, Clip], c: Canvas, folder: Path) -> Path:
    segment = edl.segments[piece.segment]
    run = build_segment(project, clips[segment.clip], segment, c, folder, first=piece.first, frames=piece.frames, hold=piece.hold)
    if piece.blend is None:
        return run
    following = edl.segments[piece.segment + 1]
    coming = build_segment(project, clips[following.clip], following, c, folder, first=-(piece.frames // 2), frames=piece.frames)
    return transitions.join(run, coming, piece.blend["type"], piece.frames, c, encode_args(c), folder, project)


def assemble(project: Project, edl: Edl, c: Canvas, pieces: list[Piece], folder: Path, picture: Path) -> Path:
    """Encode each piece once, then join them without encoding again."""
    folder.mkdir(parents=True, exist_ok=True)
    clips = {clip.id: clip for clip in project.clips()}
    with locks.heavy("building the picture"):
        parts = [_build_piece(project, piece, edl, clips, c, folder) for piece in pieces]
        listing = folder / "list.ffconcat"
        listing.write_text("ffconcat version 1.0\n" + "\n".join(f"file '{p.name}'" for p in parts) + "\n")
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(picture)])
    return picture


def build(project: Project, *, draft: bool = False) -> tuple[Path, Path]:
    """Return the silent picture and the edited voice track."""
    from . import timeline as timelines

    edl = project.edl()
    if not edl.segments:
        raise AltocueError("The cut is empty.", fix="Run `altocue cut` and check edl.json.")
    c = canvas(project, draft=draft)
    edits = timelines.picture_edits(project, edl)
    pieces = layout([segment_frames(s, c.fps) for s in edl.segments], edits["transitions"], edits["freezes"])
    folder = project.work / ("segments-draft" if draft else "segments")
    picture = assemble(project, edl, c, pieces, folder, project.work / ("plate-draft.mp4" if draft else "plate.mp4"))
    voice = project.work / "voice.wav"
    mix.mix_voice(project, edl, voice)
    return picture, voice


def run(args: argparse.Namespace) -> int:
    picture, voice = build(Project.open(args.project), draft=args.draft)
    print(f"Plate: {picture}\nVoice: {voice}")
    return 0
