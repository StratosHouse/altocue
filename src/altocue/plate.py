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
from .cut.snap import frame_count
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


def fits_path(clip: Clip, segment: Segment, c: Canvas) -> bool:
    """A path is only used with the crop it was solved from, in this canvas's shape. A crop moved by hand wins."""
    crop, path = segment.crop, segment.path
    if not (crop and path) or abs(crop[2] / crop[3] - c.width / c.height) >= 0.02:
        return False
    return abs(crop[0] - path[0][1]) < 0.5 and abs(crop[1] - path[0][2]) < 0.5


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
    place = f"in/{span}" if not first else f"clip((in+{first})/{span}\\,0\\,1)"  # perspective names its frame counter `in`, not `n`
    z = f"({start}+({end}-{start})*{place})"
    w, h = f"(W/{z})", f"(H/{z})"
    x0, y0 = f"((W-{w})/2)", f"((H-{h})*{ZOOM_ORIGIN_Y})"
    x1, y1 = f"({x0}+{w})", f"({y0}+{h})"
    return f"perspective={x0}:{y0}:{x1}:{y0}:{x0}:{y1}:{x1}:{y1}:interpolation=cubic:eval=frame:sense=source"


def line_through(keys: list[tuple[float, float]], var: str) -> str:
    """Straight lines through (frame, value) keys, held flat past both ends. Commas come escaped for a filtergraph."""
    if len(keys) == 1:
        return f"{keys[0][1]:.3f}"
    at = f"clip({var},{keys[0][0]:.4f},{keys[-1][0]:.4f})"
    expr = f"{keys[-1][1]:.3f}"
    for (f0, v0), (f1, v1) in reversed(list(zip(keys, keys[1:], strict=False))):
        span = max(f1 - f0, 1e-6)
        expr = f"if(lt({at},{f1:.4f}),{v0:.3f}+({v1 - v0:.3f})*({at}-{f0:.4f})/{span:.4f},{expr})"
    return expr.replace(",", "\\,")


def follow_geometry(clip: Clip, segment: Segment, c: Canvas, steady: float, first: int) -> tuple[str, str]:
    """A moving crop in two steps: a still crop round everything the path visits, scaled so the window is
    exactly canvas size, then a sub-pixel slide of that window to the corner and a crop to the canvas.

    The slide runs after `fps`, so its frame counter is the film's, whatever rate the source has.
    """
    width, height = segment.crop[2], segment.crop[3]  # type: ignore[index]
    src_w, src_h = clip.display_size
    windows = [(t, zoomed((x, y, width, height), steady)) for t, x, y in segment.path or []]
    left = max(0, int(min(w[0] for _, w in windows)) // 2 * 2)
    top = max(0, int(min(w[1] for _, w in windows)) // 2 * 2)
    right = min(src_w, math.ceil(max(w[0] + w[2] for _, w in windows)))
    bottom = min(src_h, math.ceil(max(w[1] + w[3] for _, w in windows)))
    box_w, box_h = _even(right - left), _even(bottom - top)
    win_w = windows[0][1][2]
    scale = c.width / win_w
    out_w, out_h = max(c.width, _even(box_w * scale + 1)), max(c.height, _even(box_h * scale + 1))
    kx, ky = out_w / box_w, out_h / box_h
    frames = [(t * c.fps, (w[0] - left) * kx, (w[1] - top) * ky) for t, w in windows]
    place = f"(in+{first})" if first else "in"
    x = line_through([(f, x) for f, x, _ in frames], place)
    y = line_through([(f, y) for f, _, y in frames], place)
    still = f"crop={box_w}:{box_h}:{left}:{top},scale={out_w}:{out_h}:flags=lanczos"
    slide = (
        f"perspective={x}:{y}:{x}+W:{y}:{x}:{y}+H:{x}+W:{y}+H:interpolation=cubic:eval=frame:sense=source,crop={c.width}:{c.height}:0:0"
    )
    return still, slide


def segment_filter(clip: Clip, segment: Segment, c: Canvas, lut: Path | None, *, first: int = 0, finish: str = "") -> str:
    """`first` says which frame of the segment the piece starts on. `finish` runs on the finished frames."""
    steady = segment.zoom_from if segment.zoom_from == segment.zoom_to else min(segment.zoom_from, segment.zoom_to)
    slide = ""
    if segment.path and fits_path(clip, segment, c):
        geometry, slide = follow_geometry(clip, segment, c, steady, first)
    else:
        x, y, width, height = zoomed(fit_crop(clip.display_size, (c.width, c.height), segment.crop), steady)
        geometry = f"crop={_even(width)}:{_even(height)}:{int(x)}:{int(y)},scale={c.width}:{c.height}:flags=lanczos"
    parts = [grade.video_filter(clip, lut, geometry=geometry), f"fps={c.fps}"]
    if slide:
        parts.append(slide)
    if segment.zoom_from != segment.zoom_to:
        frames = segment_frames(segment, c.fps)
        parts.append(push_filter(c, frames, segment.zoom_from / steady, segment.zoom_to / steady, first))
    if finish:
        parts.append(finish)
    parts.append("setsar=1")
    return ",".join(parts)


def encode_args(c: Canvas) -> list[str]:
    tags = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    return ["-c:v", "libx264", "-crf", str(c.crf), "-preset", c.preset, "-pix_fmt", "yuv420p", *tags]


def segment_frames(segment: Segment, fps: int) -> int:
    """The cut's own count. A shot of no frames is refused, never padded to one, so no stage counts it differently."""
    frames = frame_count(segment.start, segment.end, fps)
    if frames < 1:
        raise AltocueError(f"Segment {segment.id} is shorter than one frame at {fps} fps.", fix="Lengthen or delete it in edl.json, or run `altocue cut`.")
    return frames


def _pad(seek: float, frames: int, clip: Clip, fps: int) -> tuple[float, int, str]:
    """Where to read from, and how to fill frames the source does not have: its first or last frame, held."""
    lead = max(0, math.ceil(-seek * fps - 1e-6))
    begin = max(seek, 0.0)
    pads = [f"tpad=start_mode=clone:start={lead}"] if lead else []
    if begin + (frames - lead) / fps > clip.picture_duration - 1 / fps:  # the picture's own end: sound can outlast it, and then no frame comes
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
    recipe |= {"chain": chain, "canvas": asdict(c), "lut": lut_hash, "streams": "picture"}
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    dest = folder / f"{segment.id}-{key}.mp4"
    if dest.exists():
        return dest
    temp = dest.with_suffix(".part.mp4")
    seek = max(begin - 0.5 / c.fps, 0.0)  # half a frame early: printed to four places, a start on the frame can round past it
    # Picture only. A source's chapter or data track (Zoom writes one) starts before the picture, so each part would
    # seem to start a frame early and every join would add a frame of gap: 15 joins put a short 0.6 s behind its sound.
    picture_only = ["-map", "0:v:0", "-an", "-sn", "-dn", "-map_chapters", "-1", "-map_metadata", "-1"]
    ffmpeg.run(["-ss", f"{seek:.4f}", "-i", clip.path, *picture_only, "-vf", chain, "-frames:v", str(frames), *encode_args(c), str(temp)])
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
        if frame < 0:
            raise AltocueError(f"A freeze at frame {frame} starts before the film starts.", fix="Anchor it at 0 seconds or later, or use a smaller offset.")
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
        ffmpeg.concat_list(listing, parts)
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(picture)])
    return picture


def same_rate(project: Project, edl: Edl) -> None:
    """Picture is counted at the output rate, and sound, captions and transitions at the cut's. Two rates drift apart."""
    if int(edl.fps) != int(project.fps):
        raise AltocueError(
            f"The cut is at {edl.fps} fps and the output is set to {project.fps} fps, so sound and picture would drift apart.",
            fix=f"Set fps = {edl.fps} under [output] in altocue.toml, or run `altocue cut` again at the new rate.",
        )


def build(project: Project, *, draft: bool = False) -> tuple[Path, Path]:
    """Return the silent picture and the edited voice track."""
    from . import timeline as timelines

    edl = project.edl()
    if not edl.segments:
        raise AltocueError("The cut is empty.", fix="Run `altocue cut` and check edl.json.")
    same_rate(project, edl)
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
