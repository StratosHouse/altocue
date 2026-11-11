"""The `altocue` command. Each stage lives in its own module and loads only when called."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import re
import sys
import time
from pathlib import Path

from . import __version__
from .errors import AltocueError

STAGES: dict[str, tuple[str, str]] = {
    "setup": ("altocue.setup", "Install what is missing, with your consent"),
    "doctor": ("altocue.doctor", "Check this machine"),
    "start": ("altocue.start", "Answer eight questions once, so every project starts the way you work"),
    "new": ("altocue.ingest", "Start a project from one or more clips"),
    "brand": ("altocue.brand", "Read your brand kit first: logo, faces and colours, before any asset"),
    "ingest": ("altocue.ingest", "Probe clips: rotation, frame rate, HDR, audio"),
    "transcribe": ("altocue.transcribe", "Word-timed transcripts, fillers kept"),
    "cut": ("altocue.cut", "Propose or apply the cut"),
    "clean": ("altocue.clean", "Clean each clip's audio"),
    "grade": ("altocue.grade", "Correct each clip's colour and apply a look"),
    "frame": ("altocue.frame", "Reframe to the target aspect"),
    "broll": ("altocue.broll", "Find B-roll spots and fill them"),
    "direct": ("altocue.direct", "Check a shot list and cost it"),
    "generate": ("altocue.generate", "Generate inserts, behind the spend gate"),
    "plate": ("altocue.plate", "Assemble the picture and sound edit"),
    "caption": ("altocue.caption", "Build word-timed captions"),
    "studio": ("altocue.studio", "Compose directed presenter scenes from real local assets and spoken triggers"),
    "motion": ("altocue.motion", "Render the graphics layers"),
    "render": ("altocue.render", "Composite a draft or final"),
    "master": ("altocue.master", "Master the sound"),
    "qa": ("altocue.qa", "Run the fact gates"),
    "review": ("altocue.review", "Editor in Chief review"),
    "run": ("altocue.run", "Run ingest to QA in order. B-roll, direct and generate stay separate"),
    "film": ("altocue.film", "Make a film with no footage: launch, explainer, ad and more"),
    "launch": ("altocue.film", "Make a launch video from a brief"),
    "library": ("altocue.library", "Search motion, stock, sound and looks"),
    "music": ("altocue.stock.music", "Find music you are allowed to use"),
    "sfx": ("altocue.stock.sfx", "Find sound effects you are allowed to use"),
    "credits": ("altocue.stock.credits", "List what this film must credit"),
    "deliver": ("altocue.deliver", "Subtitles, chapters, thumbnails and platform versions"),
    "timeline": ("altocue.timeline_view", "See the edit on a timeline in the HyperFrames studio"),
    "recipes": ("altocue.generate.recipes", "Browse and cost generation recipes"),
    "shorts": ("altocue.shorts", "Hand a real edit to Higgsfield Shorts Studio for motion design, then check the result"),
    "stageshow": ("altocue.stageshow", "Film a Stageshow product UI stage into a frame-exact insert"),
    "facts": ("altocue.facts", "Measure the finished film"),
    "benchmark": ("altocue.benchmark", "Compare against real videos from a CSV or your own key"),
    "learn": ("altocue.learn", "Learn from your own published results"),
    "clips": ("altocue.clips", "Find and cut short clips from a long video"),
    "watch": ("altocue.watch", "Look at the recordings or a render as frames: every line, every cut, every graphic"),
    "versions": ("altocue.versions", "Save, compare and restore versions of the edit, with timed notes. Nothing is uploaded"),
    "status": ("altocue.status", "Show what is done, stale and next"),
    "demo": ("altocue.demo", "Prove this machine works on a sample clip"),
    "mcp": ("altocue.mcp", "Serve Altocue to any MCP client over stdio"),
}


TRACEBACK = "ALTOCUE_TRACEBACK"  # set to 1 and a failure nobody planned for shows its traceback, as a developer wants
ISSUES = "https://github.com/StratosHouse/altocue/issues"
LOGS_KEPT = 20
RENDERER_HELP = (
    "What draws the graphics: hyperframes for full motion graphics, or basic for captions and cards with nothing to install. "
    "auto, the default, takes hyperframes when `altocue setup` has installed it"
)

# "Your brand kit comes first." These stages make a caption, a graphic or a render, so each stops on a missing brand
# before it does any slow work. The stages before them need no brand, and end by saying it comes next.
BRAND_FIRST = frozenset({"caption", "motion", "render", "timeline", "run"})
BEFORE_BRAND = frozenset({"transcribe", "cut", "clean", "grade", "frame"})


class Parser(argparse.ArgumentParser):
    """No abbreviated options at any depth. Older Pythons, such as Ubuntu 24.04's 3.12.3, read `learn pair --a`
    as an abbreviation of learn's own --age or --as-of. Subcommand parsers are made from this class too."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]


def build_parser(argv: list[str]) -> argparse.ArgumentParser:
    parser = Parser(prog="altocue", description="Edit raw footage into finished video.")
    parser.add_argument("--version", action="version", version=f"altocue {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    chosen = next((arg for arg in argv if arg in STAGES), None)
    for name, (module, summary) in STAGES.items():
        stage = commands.add_parser(name, help=summary, description=summary)
        stage.set_defaults(module=module)
        if name == chosen:
            importlib.import_module(module).add_arguments(name, stage)
    return parser


def stage_args(name: str, **given: object) -> argparse.Namespace:
    """A stage's own defaults with `given` on top, for code that runs a stage without a command line.

    Read from the stage's parser, so an option added to a stage later is never missing here.
    """
    parser = Parser(prog=f"altocue {name}")
    importlib.import_module(STAGES[name][0]).add_arguments(name, parser)
    defaults = vars(parser.parse_args([]))
    unknown = sorted(set(given) - set(defaults))
    if unknown:
        raise TypeError(f"altocue {name} has no option {', '.join(unknown)}")
    return argparse.Namespace(**{**defaults, **given})


def forget_expired_data(args: argparse.Namespace) -> None:
    """Every command first deletes YouTube statistics past their date, wherever they were saved."""
    from datetime import date

    from . import retention

    as_of = getattr(args, "as_of", None) if getattr(args, "module", "") == "altocue.benchmark" else None
    try:
        project = getattr(args, "project", None)
        if project is not None:  # a command run from inside a project still means the project
            from .project import find_root

            project = find_root(Path(project).expanduser().resolve()) or project
        retention.purge_everywhere(project, date.fromisoformat(as_of) if as_of else None)
    except (OSError, ValueError) as error:
        print(f"altocue: could not check for expired YouTube data: {error}", file=sys.stderr)


def brand_first(args: argparse.Namespace) -> None:
    """Stop a stage that would make an asset while the project has no brand, before it loads or measures anything."""
    if args.command not in BRAND_FIRST or getattr(args, "stop", False):
        return
    from . import brand as brands
    from .project import Project

    project = Project.open(args.project)
    if not (project.root / "storyboard.json").exists():  # a film from a storyboard may take its brand from the brief
        brands.load(project)


def brand_next(args: argparse.Namespace) -> None:
    """After a stage that needs no brand: say the brand comes next, so no closing hint leads into the gate."""
    if args.command not in BEFORE_BRAND:
        return
    from . import brand as brands
    from .project import Project

    try:
        project = Project.open(args.project)
    except AltocueError:
        return
    if not brands.chosen_brand(project) and not (project.root / "storyboard.json").exists():
        print(f"This project has no brand yet, so `altocue run` and `altocue render` stop until it has one. {brands.ways_out()}")


def crash_log(argv: list[str], trace: str) -> Path | None:
    """The full traceback, kept in the user folder so the terminal shows three lines. None when that folder cannot be written."""
    from .start import home

    name = re.sub(r"[^a-z0-9]+", "-", (argv[0] if argv else "altocue").lower()).strip("-") or "altocue"
    try:
        folder = home() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.log"
        target.write_text(f"altocue {__version__}, Python {sys.version.split()[0]} on {sys.platform}\nCommand: altocue {' '.join(argv)}\n\n{trace}")
        for old in sorted(folder.glob("*.log"))[:-LOGS_KEPT]:
            old.unlink(missing_ok=True)
    except OSError:
        return None
    return target


def report_crash(argv: list[str], error: Exception) -> int:
    """One line on what failed, how to report it, and where the traceback went."""
    import traceback

    log = crash_log(argv, "".join(traceback.format_exception(error)))
    said = (str(error).strip().splitlines() or [""])[0][:300]
    what = f"{type(error).__name__}: {said}" if said else type(error).__name__
    print(f"altocue: `altocue {argv[0] if argv else ''}` stopped on an error it did not plan for. {what}".rstrip(), file=sys.stderr)
    where = "with the log below, after taking out anything private" if log else "with the command you ran"
    print(f"Fix: Run it again. If it stops the same way, report it at {ISSUES} {where}. {TRACEBACK}=1 prints the traceback here.", file=sys.stderr)
    print(f"Log: {log}" if log else "Log: none, because the Altocue user folder could not be written.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser(argv).parse_args(argv)
    # `altocue mcp` speaks its protocol on stdout, so a note about deleted data goes to stderr there.
    with contextlib.redirect_stdout(sys.stderr) if args.module == "altocue.mcp" else contextlib.nullcontext():
        forget_expired_data(args)
    try:
        brand_first(args)
        code = importlib.import_module(args.module).run(args) or 0
        if code == 0:
            brand_next(args)
        return code
    except AltocueError as error:
        print(f"altocue: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if os.environ.get(TRACEBACK, "") not in ("", "0"):
            raise
        print("altocue: stopped.", file=sys.stderr)
        return 130
    except Exception as error:  # not SystemExit: a stage that exits on purpose still does
        if os.environ.get(TRACEBACK, "") not in ("", "0"):
            raise
        return report_crash(argv, error)


if __name__ == "__main__":
    raise SystemExit(main())
