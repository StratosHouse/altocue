"""Start a project and record what each clip really is."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import ffmpeg, platforms
from .errors import AltocueError
from .models import Clip
from .project import Project, size_for


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    if name == "new":
        parser.add_argument("folder", help="Project folder to create")
        parser.add_argument("--input", "-i", nargs="+", required=True, help="Source video files")
        parser.add_argument("--aspect", default="9:16", help="Any W:H, such as 9:16, 16:9, 1:1, 4:5, 4:3 or 21:9")
        parser.add_argument("--brief", default="", help="The goal and audience, in your words")
        parser.add_argument("--brand", help="The brand kit folder or brand.json to read first, or `house` for Altocue's own look")
    else:
        parser.add_argument("--project", "-p", default=".", help="Project folder")


def clip_id(path: Path, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-") or "clip"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def ingest(project: Project, order: list[str] | None = None) -> list[Clip]:
    """Clips play in the order given. Later runs keep it, and a clip added since goes last."""
    if order is None and project.media_path.exists():
        order = [clip.id for clip in project.clips()]
    ranks = {name: rank for rank, name in enumerate(order or [])}
    links = [link for link in project.sources.iterdir() if not link.name.startswith(".") and not link.name.endswith(".provenance.json")]
    clips = [ffmpeg.probe_clip(link, clip_id=link.stem) for link in sorted(links, key=lambda link: (ranks.get(link.stem, len(ranks)), link.name))]
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
        project = Project.create(args.folder, aspect=args.aspect, platform=platforms.default_for(size_for(args.aspect)))
        taken: set[str] = set()
        order = [clip_id(source, taken) for source in inputs]
        for name, source in zip(order, inputs, strict=True):
            (project.sources / f"{name}{source.suffix.lower()}").symlink_to(source)
        (project.root / "brief.md").write_text(f"# Brief\n\n{args.brief}\n")
        if args.brand:
            from . import brand as brands

            done = brands.ingest(project, None if args.brand == "house" else Path(args.brand), house=args.brand == "house")
            print(f"Brand: {done['brand']}" + (f" (kept from the house look: {', '.join(done['kept'])})" if done["kept"] and args.brand != "house" else ""))
    else:
        project, order = Project.open(args.project), None
    for clip in ingest(project, order):
        print(describe(clip))
    print(f"Project ready at {project.root}")
    if not str(project.setting("brand", "name", "default")).strip():
        print("Next: `altocue brand ingest <kit folder>`. Altocue reads your logo, faces and colours before it makes any asset.")
    return 0
