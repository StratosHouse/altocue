"""Lay inserts and graphics over the plate, and mix the sound under them."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import caption, ffmpeg, frame, mix, motion, plate
from . import timeline as timelines
from .project import Project


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--draft", action="store_true", help="Half size, quick encode, no mastering")
    parser.add_argument("--renderer", choices=[*motion.RENDERERS, "auto"], default="auto")


def sfx_cues(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """House sound for graphics: a pop on arrival, a tick per step, a whoosh to the end card."""
    cues: list[dict[str, Any]] = []
    for item in timeline["items"]:
        if item["type"] in {"lower_third", "steps", "stat", "quote"}:
            cues.append({"name": "pop", "at": item["start"]})
        if item["type"] == "steps":
            cues += [{"name": "tick", "at": step["at"]} for step in item["items"]]
        if item["type"] == "endcard":
            cues.append({"name": "whoosh", "at": item["start"]})
    return cues


def compose_args(project: Project, picture: Path, audio: Path, layer: motion.Layer, timeline: dict[str, Any], c: plate.Canvas, dest: Path) -> list[str]:
    inputs = ["-i", str(picture), "-i", str(audio)]
    graph: list[str] = []
    tail = timeline["tail"]
    base = "base"
    graph.append(f"[0:v]tpad=stop_mode=clone:stop_duration={tail:.3f}[{base}]" if tail else f"[0:v]null[{base}]")
    next_input = 2
    for index, insert in enumerate(timeline["inserts"]):
        source = (project.root / insert["file"]).resolve()
        first = round(float(insert["start"]) * c.fps)
        frames = round((float(insert["end"]) - float(insert["start"])) * c.fps)
        loop = ["-loop", "1", "-framerate", str(c.fps)] if source.suffix.lower() in timelines.STILL_TYPES else []
        inputs += [*loop, "-t", f"{(frames + 2) / c.fps:.6f}", "-i", str(source)]
        fit = f"scale={c.width}:{c.height}:force_original_aspect_ratio=increase,crop={c.width}:{c.height},fps={c.fps},setsar=1"
        graph.append(f"[{next_input}:v]{fit},trim=end_frame={frames},setpts=PTS-STARTPTS+{first}/({c.fps}*TB)[ins{index}]")
        graph.append(f"[{base}][ins{index}]overlay=eof_action=pass:enable='between(n,{first},{first + frames - 1})'[after{index}]")
        base = f"after{index}"
        next_input += 1
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


def build(project: Project, *, draft: bool = False, renderer: str = "auto") -> Path:
    frame.frame_project(project)  # a re-cut starts with no crops or punch-ins, and jump cuts would ship plain
    picture, voice = plate.build(project, draft=draft)
    caption.build(project)
    timeline = timelines.build(project)
    layer = motion.render_layers(project, renderer=renderer, draft=draft)
    audio = project.work / "mix.wav"
    music = (project.root / timeline["music"]["path"]).resolve() if timeline.get("music") else None
    mix.mix_bed(voice, audio, music=music, sfx=sfx_cues(timeline) if timeline.get("sfx", True) else [], tail=timeline["tail"])
    dest = project.out / ("draft.mp4" if draft else "render.mp4")
    ffmpeg.run(compose_args(project, picture, audio, layer, timeline, plate.canvas(project, draft=draft), dest))
    return dest


def run(args: argparse.Namespace) -> int:
    dest = build(Project.open(args.project), draft=args.draft, renderer=args.renderer)
    print(f"Rendered {dest}")
    return 0
