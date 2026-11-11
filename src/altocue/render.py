"""Lay inserts and graphics over the plate, and mix the sound under them."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from . import brand as brands
from . import broll, caption, ffmpeg, frame, locks, mix, motion, overlays, plate, track, transitions
from . import timeline as timelines
from .cut.snap import frame_count
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
    if (project.root / "studio.json").is_file() and "studio_layer" not in timeline:
        from .studio import key_audio, load

        plan = load(project.root, total=round(timeline["total"] * project.fps), fps=project.fps)
        timeline = {**timeline, "cues": [*timeline.get("cues", []), *key_audio(plan, project.work / "studio" / "keys")]}
    music = (project.root / timeline["music"]["path"]).resolve() if timeline.get("music") else None
    room = voice.with_name(f"{voice.stem}-room.wav")
    music_end = timeline["music"].get("end", "land") if timeline.get("music") else "land"
    return mix.mix_bed(voice, dest, music=music, sfx=all_cues(timeline), tail=timeline["tail"], room=room, music_end=music_end, stems=stems)


def write_stems(project: Project, timeline: dict[str, Any], voice: Path, folder: Path) -> list[Path]:
    """Voice, music and effects as WAV files, at their levels in the mix and before mastering."""
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        mix_film(project, timeline, voice, Path(scratch) / "mix.wav", stems=folder)
    return [folder / f"{name}.wav" for name in mix.STEMS]


def insert_frames(insert: dict[str, Any], fps: int) -> tuple[int, int]:
    """An insert's first frame and length in frames. Times from a hand-edited motion.json or broll.json are checked here."""
    name = insert.get("file", "an insert")
    try:
        start, end = float(insert["start"]), float(insert["end"])
    except (KeyError, TypeError, ValueError) as error:
        raise AltocueError(f"The insert {name} has no usable start and end.", fix="Give it a start and an end in seconds.") from error
    first, frames = round(start * fps), frame_count(start, end, fps)
    if first < 0 or frames < 1:
        fix = "Start it at 0 seconds or later, and end it after it starts."
        raise AltocueError(f"The insert {name} runs from {start:g}s to {end:g}s, which is not a span of the film.", fix=fix)
    return first, frames


def compose_args(project: Project, picture: Path, audio: Path, layer: motion.Layer, timeline: dict[str, Any], c: plate.Canvas, dest: Path) -> list[str]:
    inputs = ["-i", str(picture), "-i", str(audio)]
    graph: list[str] = []
    tail = timeline["tail"]
    base = "base"
    graph.append(f"[0:v]tpad=stop_mode=clone:stop_duration={tail:.3f}[{base}]" if tail else f"[0:v]null[{base}]")
    next_input = 2
    extra, lines, base = track.graph(timeline.get("tracked", []), base, c, next_input)  # on the speaker's picture, under inserts
    inputs += extra
    graph += lines
    next_input += sum(item == "-i" for item in extra)
    for index, insert in enumerate(timeline["inserts"]):
        source = (project.root / insert.get("baked", insert["file"])).resolve()
        first, frames = insert_frames(insert, c.fps)
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
    if timeline.get("studio_layer"):
        # The ordinary caption/graphics layer remains below the deliberately authored areas.
        # A transparent lower half preserves the presenter and captions outside of full-screen proof shots.
        graph.append(f"[{base}][gfx]overlay=format=auto:eof_action=pass[standard]")
        inputs += ["-i", timeline["studio_layer"]]
        graph.append(f"[{next_input+1}:v]scale={c.width}:{c.height}:flags=lanczos,format=yuva444p[studio]")
        graph.append("[standard][studio]overlay=format=auto:eof_action=pass,format=yuv420p[video]")
    else:
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
    first, frames = insert_frames(insert, c.fps)
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
    ffmpeg.concat_list(listing, parts)
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
    tracked = track.prepare(project, timeline.get("tracked", []), picture, (c.width, c.height), c.fps, brands.load(project))
    return {**timeline, "inserts": baked, "overlay": overlays.prepare(project, timeline.get("overlay", []), c), "tracked": tracked}


def build(project: Project, *, draft: bool = False, renderer: str = "auto") -> Path:
    with locks.heavy("rendering"):
        frame.frame_project(project)  # a re-cut starts with no crops or punch-ins, and jump cuts would ship plain
        said = project.work / "progress.json"  # the same numbers the lines carry, for an agent that polls
        with ffmpeg.progress("Building the picture", project.edl().duration if project.edl_path.exists() else 0.0, said):
            picture, voice = plate.build(project, draft=draft)
        caption.build(project)
        broll.match_look(project)  # an insert prepared under another look is made again before it is laid in
        timeline = timelines.build(project, draft=draft)
        credits.write_txt(project)
        layer = motion.render_layers(project, renderer=renderer, draft=draft, timeline=timeline)  # built once: placing graphics reads the plate
        c = plate.canvas(project, draft=draft)
        if (project.root / "studio.json").is_file():
            from .studio import attach

            timeline = attach(project, timeline, c, draft=draft)
        audio = mix_film(project, timeline, voice, project.work / "mix.wav")
        dest = project.out / ("draft.mp4" if draft else "render.mp4")
        dest.parent.mkdir(parents=True, exist_ok=True)
        timeline = bake_edges(project, timeline, picture, c, draft=draft)
        with ffmpeg.progress("Compositing", timeline["total"], said):
            ffmpeg.run(compose_args(project, picture, audio, layer, timeline, c, dest))
    if not draft:
        write_json(project.work / "render.json", {"cut": project.cut_mark(), "studio": (project.root / "studio.json").is_file()})
    return dest


def run(args: argparse.Namespace) -> int:
    dest = build(Project.open(args.project), draft=args.draft, renderer=args.renderer)
    print(f"Rendered {dest}")
    return 0
