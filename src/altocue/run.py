"""Run ingest to QA in order, stopping wherever a person or agent must decide. B-roll, direct and generate stay separate steps."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from pathlib import Path

from .cli import stage_args
from .errors import AltocueError
from .project import Project

ORDER = ("ingest", "transcribe", "cut", "clean", "grade", "frame", "render", "master", "qa", "review")


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--from", dest="start", choices=ORDER, default=ORDER[0], help="First stage")
    parser.add_argument("--to", dest="end", choices=ORDER, default="qa", help="Last stage")
    parser.add_argument("--draft", action="store_true", help="Stop after a draft render")
    parser.add_argument("--renderer", default="auto")


def stages(project: Project, args: argparse.Namespace) -> dict[str, Callable[[], object]]:
    def load(module: str):
        return importlib.import_module(f"altocue.{module}")

    def do_cut() -> None:
        if project.edl_path.exists() and args.start != "cut":
            print("Keeping the cut in edl.json, with its rulings. Run `altocue cut` for a new proposal.")
            return
        cut = load("cut")
        edl = cut.propose(project)
        project.save_edl(edl)
        cut.write_report(project, edl)
        cut.write_review(project, edl)
        waiting = sum(item.decision == "review" for item in edl.removed)
        print(f"Cut to {edl.duration:.1f} s in {len(edl.segments)} segments.")
        if waiting:
            print(f"{waiting} items stay in until someone rules on them: work/cut-review.md")

    def do_qa() -> int:
        results = load("qa").run_gates(project, project.out / "final.mp4")
        failed = [gate.id for gate in results if not gate.passed and not gate.skipped]
        print(f"{sum(gate.passed for gate in results)} gates passed." + (f" Failed: {', '.join(failed)}." if failed else ""))
        return 1 if failed else 0

    def do_frame() -> None:
        if project.setting("frame", "enabled", True):
            load("frame").frame_project(project)

    def do_review() -> int:
        chosen = stage_args(
            "review", project=str(project.root), file=None, reviewer=None, ingest=None, upload=False, paid_key=False, editor_vendor=None, reset_rounds=False
        )
        return load("review").run(chosen)

    return {
        "ingest": lambda: load("ingest").ingest(project),
        "transcribe": lambda: load("transcribe").transcribe_project(project),
        "cut": do_cut,
        "clean": lambda: load("clean").clean_project(project),
        "grade": lambda: load("grade").grade_project(project),
        "frame": do_frame,
        "render": lambda: load("render").build(project, draft=args.draft, renderer=args.renderer),
        "master": lambda: load("master").run(stage_args("master", project=str(project.root), input=None, output=None, loudness=None, true_peak=None)),
        "qa": do_qa,
        "review": do_review,
    }


def made(project: Project, stage: str, *, draft: bool = False) -> Path:
    """What a stage leaves behind. The closing line names what this run made, never a file an earlier run may have left."""
    return {
        "ingest": project.media_path,
        "transcribe": project.words_dir,
        "cut": project.edl_path,
        "clean": project.audio_dir,
        "grade": project.lut_dir,
        "frame": project.work / "frame.json",
        "render": project.out / ("draft.mp4" if draft else "render.mp4"),
        "master": project.out / "final.mp4",
        "qa": project.review_dir / "qa.json",
        "review": project.review_dir,
    }[stage]


def span(args: argparse.Namespace) -> tuple[str, ...]:
    """The stages this run covers, in order. An empty run is refused, because it would report success having done nothing."""
    stop = "render" if args.draft else args.end
    first, last = ORDER.index(args.start), ORDER.index(stop)
    if first > last:
        if args.draft:
            fix = "Drop --draft, or start at render or earlier: `altocue run --from render --draft`."
            raise AltocueError(f"--from {args.start} comes after render, where --draft stops, so no stage would run.", fix=fix)
        fix = f"Swap them: `altocue run --from {stop} --to {args.start}`. The order is {', '.join(ORDER)}."
        raise AltocueError(f"--from {args.start} comes after --to {stop}, so no stage would run.", fix=fix)
    return ORDER[first : last + 1]


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    ran = span(args)
    from . import brand as brands

    brands.load(project)  # the brand kit comes first: stop before any stage spends time on a look the owner never chose
    table = stages(project, args)
    for name in ran:
        print(f"== {name}", flush=True)
        code = table[name]()
        if isinstance(code, int) and code != 0:
            print(f"Stopped at {name}. Fix what failed, rule on work/cut-review.md if it lists anything, then `altocue run` again.")
            return code
    film = [made(project, "master")] if "master" in ran else []  # the film first, then what the last stage wrote about it
    shown = [path for path in dict.fromkeys([*film, made(project, ran[-1], draft=args.draft)]) if path.exists()]
    print(f"Done: {' and '.join(str(path) for path in shown)}" if shown else f"Done: {ran[0]} to {ran[-1]} ran and left no file to show.")
    if not args.draft and args.end == "qa":
        print("Next: rule on work/cut-review.md if it lists anything, then run `altocue review`.")
    return 0
