"""Start a project and record what each clip really is."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import ffmpeg
from .errors import AltocueError
from .models import Clip
from .project import CONFIG, Project
from .tomledit import set_keys


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    if name == "new":
        from . import start

        parser.add_argument("folder", help="Project folder to create")
        parser.add_argument("--input", "-i", nargs="+", required=True, help="Source video files")
        parser.add_argument("--aspect", help="Any W:H, such as 9:16, 16:9, 1:1, 4:5, 4:3 or 21:9. Left out, it follows the kind and your profile, else 9:16")
        parser.add_argument(
            "--kind",
            choices=list(start.kinds()),
            help="The kind of video, which sets where its pace, framing and shape start. `altocue start --kinds` lists them. Left out, the kind in your profile is used",
        )
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
    notes: list[str] = []
    if args.command == "new":
        inputs = [Path(p).expanduser().resolve() for p in args.input]
        missing = [str(p) for p in inputs if not p.is_file()]
        if missing:
            raise AltocueError(f"Not found: {', '.join(missing)}")
        from . import start

        profile = start.saved()
        aspect, changes = start.starting_point(kind=args.kind, aspect=args.aspect, profile=profile)
        project = Project.create(args.folder, aspect=aspect or "9:16")
        if changes:  # with no kind and no profile the file stays exactly as `create` wrote it
            (project.root / CONFIG).write_text(set_keys((project.root / CONFIG).read_text(), changes))
            project = Project.open(project.root)
        taken: set[str] = set()
        order = [clip_id(source, taken) for source in inputs]
        for name, source in zip(order, inputs, strict=True):
            (project.sources / f"{name}{source.suffix.lower()}").symlink_to(source)
        (project.root / "brief.md").write_text(f"# Brief\n\n{args.brief}\n")
        kit = args.brand or (profile["brand"] if profile else "")
        if kit and not args.brand and kit != start.HOUSE and not Path(kit).expanduser().exists():
            print(f"The brand kit in your profile, {kit}, is not there now, so no brand was read.")
        elif kit:
            from . import brand as brands

            done = brands.ingest(project, None if kit == start.HOUSE else Path(kit), house=kit == start.HOUSE)
            print(f"Brand: {done['brand']}" + (f" (kept from the house look: {', '.join(done['kept'])})" if done["kept"] and kit != start.HOUSE else ""))
        kind = start.kind_of(project)
        if kind:
            source = "" if args.kind else ", from your profile"
            notes.append(f'Kind: {kind.name}, at {project.config["output"]["aspect"]}{source}. Its playbook is "{kind.playbook}" in the altocue-craft skill.')
        if profile is None and sys.stdout.isatty():
            notes.append("Tip: `altocue start` asks eight questions once, so every project starts the way you work.")
    else:
        project, order = Project.open(args.project), None
    for clip in ingest(project, order):
        print(describe(clip))
    print(f"Project ready at {project.root}")
    for note in notes:
        print(note)
    if not str(project.setting("brand", "name", "default")).strip():
        print("Next: `altocue brand ingest <kit folder>`. Altocue reads your logo, faces and colours before it makes any asset.")
    return 0
