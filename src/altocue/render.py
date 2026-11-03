"""Lay inserts and graphics over the plate, and mix the sound under them."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from . import caption, ffmpeg, frame, locks, mix, motion, overlays, plate, transitions
from . import timeline as timelines
from .errors import AltocueError
from .motion import formats
from .project import Project, write_json
from .stock import credits


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--draft", action="store_true", help="Half size, quick encode, no mastering")
    parser.add_argument("--renderer", choices=[*motion.RENDERERS, "auto"], default="auto")


def sfx_cues(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """House sound for graphics: a pop on arrival, a tick per step, a whoosh to the end card.

    A wipe sounds at its midpoint, because that is where the cut lands.
    """
    cues: list[dict[str, Any]] = []
    for item in timeline["items"]:
        fmt = formats.find(item["type"])
        if fmt and fmt.sound:
            at = (item["start"] + item["end"]) / 2 if fmt.group == "wipe" else item["start"]
            cues.append({"name": fmt.sound, "at": at})
        if item["type"] in {"lower_third", "steps", "stat", "quote"}:
            cues.append({"name": "pop", "at": item["start"]})
        if item["type"] == "steps":
            cues += [{"name": "tick", "at": step["at"]} for step in item["items"]]
        if item["type"] == "endcard":
            cues.append({"name": "whoosh", "at": item["start"]})
    return cues


def all_cues(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """The house sounds, unless `sfx` turned them off, and every cue the project asked for."""
    return (sfx_cues(timeline) if timeline.get("sfx", True) else []) + timeline.get("cues", [])


def mix_film(project: Project, timeline: dict[str, Any], voice: Path, dest: Path, stems: Path | None = None) -> Path:
    """The film's sound, made one way, so the render, the stems and the timeline all hear the same mix."""
    music = (project.root / timeline["music"]["path"]).resolve() if timeline.get("music") else None
    room = voice.with_name(f"{voice.stem}-room.wav")
    music_end = timeline["music"].get("end", "land") if timeline.get("music") else "land"
    return mix.mix_bed(voice, dest, music=music, sfx=all_cues(timeline), tail=timeline["tail"], room=room, music_end=music_end, stems=stems)


def write_stems(project: Project, timeline: dict[str, Any], voice: Path, folder: Path) -> list[Path]:
    """Voice, music and effects as WAV files, at their levels in the mix and before mastering."""
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        mix_film(project, timeline, voice, Path(scratch) / "mix.wav", stems=folder)
    return [folder / f"{name}.wav" for name in mix.STEMS]


def compose_args(project: Project, picture: Path, audio: Path, layer: motion.Layer, timeline: dict[str, Any], c: plate.Canvas, dest: Path) -> list[str]:
    inputs = ["-i", str(picture), "-i", str(audio)]
    graph: list[str] = []
    tail = timeline["tail"]
    base = "base"
    graph.append(f"[0:v]tpad=stop_mode=clone:stop_duration={tail:.3f}[{base}]" if tail else f"[0:v]null[{base}]")
    next_input = 2
    for index, insert in enumerate(timeline["inserts"]):
        source = (project.root / insert.get("baked", insert["file"])).resolve()
        first = round(float(insert["start"]) * c.fps)
        frames = round((float(insert["end"]) - float(insert["start"])) * c.fps)
        loop = ["-loop", "1", "-framerate", str(c.fps)] if source.suffix.lower() in timelines.STILL_TYPES else []
        inputs += [*loop, "-t", f"{(frames + 2) / c.fps:.6f}", "-i", str(source)]
        fit = f"scale={c.width}:{c.height}:force_original_aspect_ratio=increase,crop={c.width}:{c.height},fps={c.fps},setsar=1"
        graph.append(f"[{next_input}:v]{fit},trim=end_frame={frames},setpts=PTS-STARTPTS+{first}/({c.fps}*TB)[ins{index}]")
        graph.append(f"[{base}][ins{index}]overlay=eof_action=pass:enable='between(n,{first},{first + frames - 1})'[after{index}]")
        base = f"after{index}"
        next_input += 1
    extra, lines, base = overlays.graph(timeline.get("overlay", []), base, c, next_input)
    inputs += extra
    graph += lines
    next_input += sum(item == "-i" for item in extra)
    if layer.concat:
        inputs += ["-f", "concat", "-safe", "0", "-i", str(layer.path)]
    else:
        inputs += ["-i", str(layer.path)]
    matrix = ":out_color_matrix=bt709:out_range=tv" if layer.concat else ""  # the basic layer is RGB, and the plate is BT.709
    graph.append(f"[{next_input}:v]scale={c.width}:{c.height}:flags=lanczos{matrix},format=yuva444p[gfx]")
    graph.append(f"[{base}][gfx]overlay=format=auto:eof_action=pass,format=yuv420p[video]")
    return [
        *inputs,
        "-filter_complex",
        ";".join(graph),
        "-map",
        "[video]",
        "-map",
        "1:a",
        "-t",
        f"{timeline['total']:.3f}",
        *plate.encode_args(plate.Canvas(c.width, c.height, c.fps, 28 if c.crf > 20 else 19, c.preset)),
        *([] if c.crf > 20 else ["-maxrate", "16M", "-bufsize", "32M"]),
        *(["-c:a", "aac", "-b:a", "192k"] if c.crf > 20 else ["-c:a", "alac"]),
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(dest),
    ]


def _piece(source: Path, first: int, frames: int, c: plate.Canvas, folder: Path, name: str) -> Path:
    """Copy out whole frames. The seek is half a frame early, so the frame it lands on is never skipped."""
    dest = folder / f"{name}.mp4"
    ffmpeg.run(["-ss", f"{max(first - 0.5, 0) / c.fps:.6f}", "-i", str(source), "-an", "-frames:v", str(frames), *plate.encode_args(c), str(dest)])
    return dest


def bake_insert(project: Project, insert: dict[str, Any], picture: Path, c: plate.Canvas, folder: Path) -> str:
    """An insert with an `in` or `out` becomes one clip for its span, with the plate blended in at those edges.

    The transitions sit inside the span, so the insert never covers more of the film than it was given.
    """
    source = (project.root / insert["file"]).resolve()
    first, frames = round(float(insert["start"]) * c.fps), round((float(insert["end"]) - float(insert["start"])) * c.fps)
    arrive = max(2, round(insert["in"]["duration"] * c.fps)) if insert.get("in") else 0
    leave = max(2, round(insert["out"]["duration"] * c.fps)) if insert.get("out") else 0
    if arrive + leave > frames:
        raise AltocueError(f"The insert {insert['file']} is too short for its transitions.", fix="Shorten them, or lengthen the insert.")
    stamps = [[path.stat().st_size, path.stat().st_mtime_ns] for path in (picture, source)]
    recipe = {"files": stamps, "insert": {key: insert.get(key) for key in ("file", "start", "end", "in", "out")}, "canvas": repr(c)}
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    work = folder / key
    dest = folder / f"insert-{key}.mp4"
    if dest.exists():
        return str(dest)
    work.mkdir(parents=True, exist_ok=True)
    loop = ["-loop", "1", "-framerate", str(c.fps)] if source.suffix.lower() in timelines.STILL_TYPES else []
    fit = f"scale={c.width}:{c.height}:force_original_aspect_ratio=increase,crop={c.width}:{c.height},fps={c.fps},setsar=1,tpad=stop_mode=clone:stop=-1"
    fitted = work / "fitted.mp4"
    ffmpeg.run([*loop, "-i", str(source), "-an", "-vf", fit, "-frames:v", str(frames), *plate.encode_args(c), str(fitted)])
    parts = []
    if arrive:
        before, after = _piece(picture, first, arrive, c, work, "in-a"), _piece(fitted, 0, arrive, c, work, "in-b")
        parts.append(transitions.join(before, after, insert["in"]["type"], arrive, c, plate.encode_args(c), work, project))
    if frames > arrive + leave:
        parts.append(_piece(fitted, arrive, frames - arrive - leave, c, work, "middle"))
    if leave:
        before, after = _piece(fitted, frames - leave, leave, c, work, "out-a"), _piece(picture, first + frames - leave, leave, c, work, "out-b")
        parts.append(transitions.join(before, after, insert["out"]["type"], leave, c, plate.encode_args(c), work, project))
    listing = work / "list.ffconcat"
    listing.write_text("ffconcat version 1.0\n" + "\n".join(f"file '{p.resolve()}'" for p in parts) + "\n")
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)])
    return str(dest)


def bake_edges(project: Project, timeline: dict[str, Any], picture: Path, c: plate.Canvas, *, draft: bool) -> dict[str, Any]:
    """Only the copy used for this composite changes. `timeline.json` keeps what was asked for."""
    folder = project.work / ("inserts-draft" if draft else "inserts")
    baked = []
    for insert in timeline["inserts"]:
        if insert.get("in") or insert.get("out"):
            folder.mkdir(parents=True, exist_ok=True)
            insert = {**insert, "baked": bake_insert(project, insert, picture, c, folder)}
        baked.append(insert)
    return {**timeline, "inserts": baked, "overlay": overlays.prepare(project, timeline.get("overlay", []), c)}


def build(project: Project, *, draft: bool = False, renderer: str = "auto") -> Path:
    with locks.heavy("rendering"):
        frame.frame_project(project)  # a re-cut starts with no crops or punch-ins, and jump cuts would ship plain
        picture, voice = plate.build(project, draft=draft)
        caption.build(project)
        timeline = timelines.build(project)
        credits.write_txt(project)
        layer = motion.render_layers(project, renderer=renderer, draft=draft)
        audio = mix_film(project, timeline, voice, project.work / "mix.wav")
        dest = project.out / ("draft.mp4" if draft else "render.mp4")
        dest.parent.mkdir(parents=True, exist_ok=True)
        c = plate.canvas(project, draft=draft)
        timeline = bake_edges(project, timeline, picture, c, draft=draft)
        ffmpeg.run(compose_args(project, picture, audio, layer, timeline, c, dest))
    if not draft:
        write_json(project.work / "render.json", {"cut": project.cut_mark()})
    return dest


def run(args: argparse.Namespace) -> int:
    dest = build(Project.open(args.project), draft=args.draft, renderer=args.renderer)
    print(f"Rendered {dest}")
    return 0
