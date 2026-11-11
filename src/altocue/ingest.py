"""Start a project and record what each clip really is."""

from __future__ import annotations

import argparse
import math
import re
import shlex
import shutil
import sys
from pathlib import Path

from . import ffmpeg, platforms
from .errors import AltocueError
from .models import Clip
from .project import CONFIG, Project, size_for
from .tomledit import set_keys

# Words for the commonest shapes, so nobody has to remember which number comes first.
ASPECT_WORDS = {"vertical": "9:16", "portrait": "9:16", "square": "1:1", "wide": "16:9"}
DEFAULT_ASPECT = "9:16"
COMMON_SHAPES = ("9:16", "16:9", "1:1", "4:5", "5:4", "4:3", "3:4", "21:9", "3:2", "2:3", "2:1", "1:2")
RETRY = "Nothing was made, so the same command can run again."


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    if name == "new":
        from . import start

        parser.add_argument("folder", help="Project folder to create")
        parser.add_argument("--input", "-i", nargs="+", required=True, help="Source video files")
        parser.add_argument(
            "--aspect",
            help=f"The film's shape: any W:H, such as 9:16, 16:9, 1:1, 4:5, 4:3 or 21:9, or one of the words {', '.join(ASPECT_WORDS)}. "
            f"The default is {DEFAULT_ASPECT} (vertical), unless --platform, the kind or your profile says otherwise",
        )
        parser.add_argument(
            "--platform",
            choices=list(platforms.PLATFORMS),
            help="Where it is published first. Sets the shape, and `[output] platform` so text stays clear of that app's buttons",
        )
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


def shape_of(width: int, height: int) -> str:
    """A picture's shape as W:H: the common name when it is within one part in a hundred of one, else the pixels reduced."""
    for name in COMMON_SHAPES:
        wide, tall = (int(part) for part in name.split(":"))
        if abs(width * tall - height * wide) <= 0.01 * height * wide:
            return name
    shared = math.gcd(width, height) or 1
    return f"{width // shared}:{height // shared}"


def shape_note(clips: list[Clip], aspect: str, why: str) -> str | None:
    """One line when the film will not have the footage's shape, with the flag that keeps it. Silent when they match."""
    if not clips:
        return None
    width, height = max(clips, key=lambda clip: clip.duration).display_size
    if not width or not height:
        return None
    film_wide, film_tall = size_for(aspect)
    if abs(width * film_tall - height * film_wide) <= 0.01 * height * film_wide:
        return None
    footage = shape_of(width, height)
    keep = f"`--aspect {footage}` on `altocue new` keeps the footage's shape, as does aspect = \"{footage}\" in altocue.toml."
    try:
        size_for(footage)
    except AltocueError:
        keep = "No film is that shape, so pick the nearest with `--aspect`."
    return f"Shape: the footage is {footage} and this film is {aspect}{why}, so the picture is cropped to fit. {keep}"


def discard(root: Path, before: set[Path] | None, links: list[Path]) -> None:
    """Take back a project that failed part way, so the same `altocue new` runs again. Whatever was in the folder first stays."""
    if before is None:
        shutil.rmtree(root, ignore_errors=True)
        return
    for link in links:
        link.unlink(missing_ok=True)
    for path in root.iterdir():
        if path in before:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def new_project(args: argparse.Namespace) -> tuple[Project, list[Clip], list[str]]:
    """Everything that can be wrong is checked before the folder is made, and the folder goes again if a later step fails.

    A failed `altocue new` used to leave a project behind, and the retry then stopped on "already an Altocue project".
    """
    from . import brand as brands
    from . import start

    inputs = [Path(p).expanduser().resolve() for p in args.input]
    missing = [str(p) for p in inputs if not p.is_file()]
    if missing:
        raise AltocueError(f"Not found: {', '.join(missing)}", fix=f"Check the path, and put quotes round one that has spaces. {RETRY}")
    root = Path(args.folder).resolve()
    if (root / CONFIG).exists():
        raise AltocueError(f"{root} is already an Altocue project.", fix="Pick another folder name, or delete that folder to start it again.")
    if root.exists() and not root.is_dir():
        raise AltocueError(f"{root} is a file, so no project can go there.", fix="Pick another folder name.")

    profile = start.saved()
    asked = ASPECT_WORDS.get(args.aspect.strip().lower(), args.aspect.strip()) if args.aspect else None
    place = platforms.find(args.platform) if args.platform else None
    if place and asked and asked != place.aspect:
        fix = "Leave one of them out: --platform sets the shape itself."
        raise AltocueError(f"--platform {place.name} plays {place.aspect}, and --aspect asks for {asked}.", fix=fix)
    aspect, changes = start.starting_point(kind=args.kind, aspect=asked or (place.aspect if place else None), profile=profile)
    if place:
        changes.setdefault("output", {})["platform"] = place.name
    try:
        size_for(aspect or DEFAULT_ASPECT)
    except AltocueError as error:
        raise AltocueError(error.args[0], fix=f"{error.fix} Or use one of the words {', '.join(ASPECT_WORDS)}.") from error
    why = ""
    if not asked:
        why = f", from --platform {place.name}" if place else ", the default" if not aspect else ", from the kind" if args.kind else ", from your profile"
    aspect = aspect or DEFAULT_ASPECT

    taken: set[str] = set()
    order = [clip_id(source, taken) for source in inputs]
    for name, source in zip(order, inputs, strict=True):
        try:
            ffmpeg.probe_clip(source, clip_id=name)
        except AltocueError as error:
            raise AltocueError(error.args[0], fix=f"Give video files, such as .mp4 or .mov. {RETRY}") from error

    kit = args.brand or (profile["brand"] if profile else "")
    house = kit.strip().lower() == start.HOUSE
    gone = bool(kit) and not args.brand and not house and not Path(kit).expanduser().exists()
    if kit and not house and not gone:
        brands.read_kit(Path(kit))  # a kit that is not there, or not a kit, stops here with its own fix

    before = set(root.iterdir()) if root.is_dir() else None
    links: list[Path] = []
    try:
        project = Project.create(root, aspect=aspect, platform=platforms.default_for(size_for(aspect)))
        if changes:  # with no kind, no platform and no profile the file stays exactly as `create` wrote it
            (project.root / CONFIG).write_text(set_keys((project.root / CONFIG).read_text(), changes))
            project = Project.open(project.root)
        for name, source in zip(order, inputs, strict=True):
            links.append(project.sources / f"{name}{source.suffix.lower()}")
            links[-1].symlink_to(source)
        (project.root / "brief.md").write_text(f"# Brief\n\n{args.brief}\n")
        if gone:
            print(f"The brand kit in your profile, {kit}, is not there now, so no brand was read.")
        elif kit:
            done = brands.ingest(project, None if house else Path(kit), house=house)
            kept = f" (kept from the house look: {', '.join(done['kept'])})" if done["kept"] and not house else ""
            print("Brand: the house look, chosen on purpose" if house else f"Brand: {done['brand']}{kept}")
        clips = ingest(project, order)
    except BaseException:
        discard(root, before, links)
        raise

    notes = [note for note in [None if asked else shape_note(clips, aspect, why)] if note]  # a shape asked for by name needs no telling
    kind = start.kind_of(project)
    if kind:
        source = "" if args.kind else ", from your profile"
        notes.append(f'Kind: {kind.name}, at {project.config["output"]["aspect"]}{source}. Its playbook is "{kind.playbook}" in the altocue-craft skill.')
    if profile is None and sys.stdout.isatty():
        notes.append("Tip: `altocue start` asks eight questions once, so every project starts the way you work.")
    return project, clips, notes


def run(args: argparse.Namespace) -> int:
    from . import brand as brands

    if args.command == "new":
        project, clips, notes = new_project(args)
    else:
        project = Project.open(args.project)
        clips, notes = ingest(project), []
    for clip in clips:
        print(describe(clip))
    print(f"Project ready at {project.root}")
    for note in notes:
        print(note)
    if not brands.chosen_brand(project):
        folder = shlex.quote(args.folder if args.command == "new" else args.project)
        print(f"Next: choose the look, because Altocue makes no caption, graphic or render before it has one. {brands.ways_out('' if folder == '.' else folder)}")
        if args.command == "new":
            print("`altocue new` takes either too: `--brand <kit folder>` or `--brand house`.")
    return 0
