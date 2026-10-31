"""Run every stage in order, stopping wherever a person or agent must decide."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable

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
        chosen = argparse.Namespace(project=str(project.root), file=None, reviewer=None, ingest=None)
        return load("review").run(argparse.Namespace(**vars(chosen), upload=False, paid_key=False, editor_vendor=None, reset_rounds=False))

    return {
        "ingest": lambda: load("ingest").ingest(project),
        "transcribe": lambda: load("transcribe").transcribe_project(project),
        "cut": do_cut,
        "clean": lambda: load("clean").clean_project(project),
        "grade": lambda: load("grade").grade_project(project),
        "frame": do_frame,
        "render": lambda: load("render").build(project, draft=args.draft, renderer=args.renderer),
        "master": lambda: load("master").run(argparse.Namespace(project=str(project.root), input=None, output=None, loudness=None, true_peak=None)),
        "qa": do_qa,
        "review": do_review,
    }


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    table = stages(project, args)
    first, last = ORDER.index(args.start), ORDER.index("render" if args.draft else args.end)
    for name in ORDER[first : last + 1]:
        print(f"== {name}", flush=True)
        code = table[name]()
        if isinstance(code, int) and code != 0:
            print(f"Stopped at {name}. Fix what failed, rule on work/cut-review.md if it lists anything, then `altocue run` again.")
            return code
    target = project.out / ("draft.mp4" if args.draft else "final.mp4")
    print(f"Done: {target}")
    if not args.draft and args.end == "qa":
        print("Next: rule on work/cut-review.md if it lists anything, then run `altocue review`.")
    return 0
