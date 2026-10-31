"""Start a project and record what each clip really is."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import ffmpeg
from .errors import AltocueError
from .models import Clip
from .project import Project


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    if name == "new":
        parser.add_argument("folder", help="Project folder to create")
        parser.add_argument("--input", "-i", nargs="+", required=True, help="Source video files")
        parser.add_argument("--aspect", default="9:16", help="9:16, 16:9, 1:1 or 4:5")
        parser.add_argument("--brief", default="", help="The goal and audience, in your words")
    else:
        parser.add_argument("--project", "-p", default=".", help="Project folder")


def clip_id(path: Path, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-") or "clip"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def ingest(project: Project) -> list[Clip]:
    clips = []
    for link in sorted(project.sources.iterdir()):
        if link.name.startswith("."):
            continue
        clips.append(ffmpeg.probe_clip(link, clip_id=link.stem))
    if not clips:
        raise AltocueError("No clips in sources/.", fix="Add video files with `altocue new`.")
    project.save_clips(clips)
    return clips


def describe(clip: Clip) -> str:
    width, height = clip.display_size
    notes = []
    if clip.is_hdr:
        notes.append("HDR")
    if clip.variable_frame_rate:
        notes.append("variable frame rate")
    if clip.rotation:
        notes.append(f"rotated {clip.rotation}")
    if not clip.has_audio:
        notes.append("no audio")
    extra = f" ({', '.join(notes)})" if notes else ""
    return f"{clip.id}: {width}x{height}, {clip.fps:.2f} fps, {clip.duration:.1f}s{extra}"


def run(args: argparse.Namespace) -> int:
    if args.command == "new":
        inputs = [Path(p).expanduser().resolve() for p in args.input]
        missing = [str(p) for p in inputs if not p.is_file()]
        if missing:
            raise AltocueError(f"Not found: {', '.join(missing)}")
        project = Project.create(args.folder, aspect=args.aspect)
        taken: set[str] = set()
        for source in inputs:
            (project.sources / f"{clip_id(source, taken)}{source.suffix.lower()}").symlink_to(source)
        (project.root / "brief.md").write_text(f"# Brief\n\n{args.brief}\n")
    else:
        project = Project.open(args.project)
    for clip in ingest(project):
        print(describe(clip))
    print(f"Project ready at {project.root}")
    return 0
