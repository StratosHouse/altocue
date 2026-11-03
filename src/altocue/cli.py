"""The `altocue` command. Each stage lives in its own module and loads only when called."""

from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__
from .errors import AltocueError

STAGES: dict[str, tuple[str, str]] = {
    "setup": ("altocue.setup", "Install what is missing, with your consent"),
    "doctor": ("altocue.doctor", "Check this machine"),
    "new": ("altocue.ingest", "Start a project from one or more clips"),
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
    "facts": ("altocue.facts", "Measure the finished film"),
    "benchmark": ("altocue.benchmark", "Compare against real videos from a CSV or your own key"),
    "learn": ("altocue.learn", "Learn from your own published results"),
    "clips": ("altocue.clips", "Find and cut short clips from a long video"),
    "status": ("altocue.status", "Show what is done, stale and next"),
    "demo": ("altocue.demo", "Prove this machine works on a sample clip"),
}


def build_parser(argv: list[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="altocue", description="Edit raw footage into finished video.")
    parser.add_argument("--version", action="version", version=f"altocue {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    chosen = next((arg for arg in argv if arg in STAGES), None)
    for name, (module, summary) in STAGES.items():
        stage = commands.add_parser(name, help=summary, description=summary)
        stage.set_defaults(module=module)
        if name == chosen:
            importlib.import_module(module).add_arguments(name, stage)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser(argv).parse_args(argv)
    try:
        return importlib.import_module(args.module).run(args) or 0
    except AltocueError as error:
        print(f"altocue: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
