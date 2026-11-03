"""Prove this machine works: our published demo take, edited through every free stage to a QA verdict.

The take is one small file we publish ourselves: a generated presenter, a messy read and a planted retake. It comes down
to a temp folder when this runs, and the network is used for nothing else. If it cannot be fetched the demo stops, because
there is no other sample. No keys and no spending. The reviewer stops at the pack, since a SHIP needs a reviewer and this
command has none. Its files go unless `--keep` says otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import platform
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import doctor, ffmpeg, locks, motion, qa
from .errors import AltocueError
from .generate import http, import_asset, store
from .project import CONFIG, Project, write_json

SAMPLE_URL = "https://www.stratosagency.ai/altocue/demo/raw-a.mp4"
SAMPLE_HOST = "www.stratosagency.ai"
SAMPLE_SHA256 = "6bb66493c3396ce2d8f9b39e9d491a6547e9088314855b4a17fa78d4b7c54390"
SAMPLE_MOST = 5_000_000  # bytes. The take is about 730 KB
SAMPLE_NAME = "raw-a.mp4"
HEARD_AS = ("AltaQ", "Alta-Q", "Alta Q", "Altoque", "AltoQ", "Alto Q", "Altacue", "Alta cue", "Alto cue")  # a made-up name, so the transcriber guesses
BRIEF = """# Brief

A short reel for developers: what Altocue is and what it does. The presenter is generated, and the film says so on screen.

## Must say

- an AI video editor
- lives inside your coding agent
- reads every word
"""
LABEL = "AI-generated presenter"
PLAN = {
    "items": [
        {"type": "hook", "text": "Raw footage in.\n*Finished* video out.", "start": 0.1, "duration": 2.8},
        {"type": "chip", "text": LABEL, "start": 0.1, "duration": 3.0, "y": 420},  # under the hook, and clear of the presenter's hair
        {"type": "endcard", "headline": "*Altocue*", "cta": "The AI edit suite for coding agents", "duration": 3.0},
    ]
}


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--keep", nargs="?", const="altocue-demo", metavar="FOLDER", help="Keep the film, the pack and the review page in this folder")
    parser.add_argument("--renderer", choices=[*motion.RENDERERS, "auto"], default="auto")


def fetch_sample(folder: Path) -> Path:
    """One file, checked against the hash this version ships with. There is no fallback, so a failure stops the demo here."""
    again = "Check this machine can reach the web, then run `altocue demo` again."
    try:
        take = http.download(SAMPLE_URL, folder / SAMPLE_NAME, timeout=60, limit=SAMPLE_MOST, headers={"User-Agent": http.AGENT})
    except AltocueError as error:
        raise AltocueError(f"Could not fetch the demo take, so the demo stops here. {str(error).splitlines()[0]}", fix=again) from error
    if store.sha256(take) != SAMPLE_SHA256:
        take.unlink()
        fix = "Update Altocue, then run `altocue demo` again."
        raise AltocueError(f"The file at {SAMPLE_URL} is not the take this version expects, so the demo stops here.", fix=fix)
    return take


def spell_the_name(project: Project) -> None:
    """The transcriber has never met the word Altocue, and a viewer should not see its guess in a caption."""
    config = project.root / CONFIG
    fixes = "".join(f'"{heard}" = "Altocue"\n' for heard in HEARD_AS)
    config.write_text(config.read_text().replace('# "Altoque" = "Altocue"\n', fixes))


def start_project(root: Path, sample: Path) -> Project:
    """Registered as what it is, so the claims and disclosure gates hold this film to the rules for a generated person."""
    project = Project.create(root / "film")
    spell_the_name(project)
    project = Project.open(project.root)
    prompt = "Altocue's published demo take. The prompt behind it is not recorded here."
    made = {"prompt": prompt, "model": "not recorded", "cost": 0, "synthetic": ["person"], "consent": "none-generated-adult"}
    asset = import_asset(project, file=sample, shot="presenter", out=lambda _: None, **made)
    (project.sources / SAMPLE_NAME).symlink_to(asset)
    (project.root / "brief.md").write_text(BRIEF)
    write_json(project.root / "motion.json", PLAN)
    return project


def preflight() -> None:
    """Stop before any work when the first run has nowhere to put the speech model."""
    from . import transcribe

    check = doctor.check_ffmpeg()
    if not check.ok:
        raise AltocueError(f"ffmpeg: {check.detail}", fix=check.fix)
    engine = transcribe.select()
    if doctor.model_cached(engine):
        return
    print(f"First run. {engine.notice(0.0)} The demo waits for it, so this run takes longer than the usual 3 minutes.", flush=True)
    room = doctor.check_disk()
    if not room.ok:
        raise AltocueError(f"Not enough disk for the speech model: {room.detail}.", fix=room.fix)


def review_run(namespace: argparse.Namespace) -> int:
    from . import review

    return review.run(namespace)


def review_stage(project: Project) -> str:
    """Gates, then the pack. Free by construction: it costs nothing and sends nothing, whatever keys this machine holds."""
    review_run(
        argparse.Namespace(
            project=str(project.root),
            file=None,
            reviewer="pack",
            ingest=None,
            upload=False,
            paid_key=False,
            editor_vendor=None,
            reset_rounds=False,
            page=False,
            open_page=False,
        )
    )
    pack = "pack written" if (project.review_dir / "PACK.md").exists() else "no pack, because a fact gate failed"
    return f"{qa_detail(project)}, {pack}"


def deliver_stage(project: Project, renderer: str) -> str:
    from . import deliver

    deliver.run(argparse.Namespace(project=str(project.root), platforms=None, loudness=None, renderer=renderer))
    saved = json.loads((project.root / "deliver" / "deliver.json").read_text())
    return f"subtitles, {len(saved['thumbnails']['candidates'])} thumbnails, film with soft subtitles"


def page_stage(project: Project) -> str:
    from .review import page

    target = page.write(project, project.out / "final.mp4")
    return f"{target.stat().st_size / 1024**2:.1f} MB, one file"


@dataclass
class Step:
    name: str
    work: Callable[[], Any]


def rule_on_the_cut(project: Project) -> None:
    """An agent reads `work/cut-review.md` and rules. Here that is one false start before a spoken redo, so the demo plays the editor."""
    from . import cut

    edl = project.edl()
    waiting = [item for item in edl.removed if item.decision == "review"]
    if not waiting:
        return
    rulings = write_json(project.work / "rulings.json", {cut.review.removal_id(item): "cut" for item in waiting})
    edl = cut.apply_review(project, rulings)
    project.save_edl(edl)
    cut.write_report(project, edl)
    cut.write_review(project, edl)


def cut_detail(project: Project) -> str:
    rule_on_the_cut(project)
    edl = project.edl()
    return f"{len(edl.segments)} shots, {len(edl.removed)} removals, {edl.duration:.1f} s kept"


def qa_detail(project: Project) -> str:
    saved = json.loads((project.review_dir / "qa.json").read_text())["gates"]
    passed = sum(bool(g["passed"]) for g in saved)
    return f"{passed} gates passed, {sum(not g['passed'] and not g['skipped'] for g in saved)} failed"


def steps(project: Project, renderer: str) -> list[Step]:
    from . import run as runner

    table = runner.stages(project, argparse.Namespace(start="ingest", end="qa", draft=False, renderer=renderer))
    words = lambda: f"{sum(len(project.words(clip.id)) for clip in project.clips())} words"  # noqa: E731
    used = lambda: f"{renderer if renderer != 'auto' else motion.pick('auto').id} renderer"  # noqa: E731
    then = lambda stage, detail: lambda: (table[stage](), detail(project))[1]  # noqa: E731
    return [
        Step("ingest", then("ingest", lambda p: f"{len(p.clips())} clip")),
        Step("transcribe", then("transcribe", lambda p: words())),
        Step("cut", then("cut", cut_detail)),
        Step("clean", then("clean", lambda p: "voice cleaned")),
        Step("grade", then("grade", lambda p: "colour corrected")),
        Step("frame", then("frame", lambda p: "framed for 9:16")),
        Step("render", then("render", lambda p: f"{used()}, captions and graphics")),
        Step("master", then("master", lambda p: "sound mastered")),
        Step("qa", lambda: review_stage(project)),
        Step("deliver", lambda: deliver_stage(project, renderer)),
        Step("page", lambda: page_stage(project)),
    ]


def outcome(project: Project) -> tuple[str, bool]:
    """Reported as saved. A SHIP needs a reviewer, so clear gates are never called one."""
    gates = json.loads((project.review_dir / "qa.json").read_text())["gates"]
    failed = [g for g in gates if not g["passed"] and not g["skipped"]]
    missing = [g["id"] for g in gates if g["skipped"] and g.get("value") != qa.gates.NOT_APPLICABLE]
    report = project.review_dir / "report.json"
    if failed or report.exists():
        saved = json.loads(report.read_text()) if report.exists() else {"verdict": "FIX", "reasons": [f"{g['id']}: {g.get('detail', '')}" for g in failed]}
        return f"Verdict: {saved['verdict']}, as code decided it. " + " ".join(saved.get("reasons", [])), saved["verdict"] == "SHIP"
    judged = [g for g in gates if g.get("value") != qa.gates.NOT_APPLICABLE]
    said = f"Fact gates: {sum(bool(g['passed']) for g in judged)} of {len(judged)} passed."
    if missing:
        said += f" Not run: {', '.join(missing)}. That blocks SHIP."
    return f"{said} SHIP or FIX needs a reviewer, and this demo has none, so the pack waits.", True


def run_step(step: Step) -> tuple[float, str]:
    """A stage's own printing is kept back, and shown only if it fails."""
    began = time.monotonic()
    chatter = io.StringIO()
    try:
        with contextlib.redirect_stdout(chatter):
            detail = step.work()
    except Exception as error:  # a proof of the machine reports any stage that breaks, and still cleans up
        log = "\n".join(chatter.getvalue().strip().splitlines()[-12:])
        raise AltocueError(f"{step.name} failed after {time.monotonic() - began:.1f}s: {error}\n{log}".rstrip()) from error
    return time.monotonic() - began, str(detail or "")


def report_step(name: str, seconds: float, detail: str) -> None:
    print(f"  ok  {name:<11}{seconds:>6.1f}s  {detail}".rstrip(), flush=True)


def take(root: Path, renderer: str) -> tuple[bool, Project | None]:
    """Run everything in `root`, print each stage as it lands, and say whether the film came through."""
    began = time.monotonic()
    project = None
    try:
        preflight()
        seconds, detail = run_step(Step("sample", lambda: sample_stage(root)))
        report_step("sample", seconds, detail)
        project = start_project(root, root / "sample" / SAMPLE_NAME)
        for step in steps(project, renderer):
            report_step(step.name, *run_step(step))
    except AltocueError as error:
        print(f"  !!  {error}", flush=True)
        return False, project
    said, passed = outcome(project)
    print(f"\n{said}")
    print(f"Took {_clock(time.monotonic() - began)} on {platform.machine()}, {platform.system()}. No keys, no spending, nothing uploaded.")
    return passed, project


def sample_stage(root: Path) -> str:
    take = fetch_sample(root / "sample")
    return f"{take.stat().st_size / 1024:.0f} KB from {SAMPLE_HOST}, {ffmpeg.probe_clip(take).duration:.1f} s, a generated presenter"


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}m {seconds % 60:04.1f}s" if seconds >= 60 else f"{seconds:.1f}s"


def run(args: argparse.Namespace) -> int:
    root = Path(args.keep).resolve() if args.keep else Path(tempfile.mkdtemp(prefix="altocue-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    print("Altocue demo. It edits our published demo take, which shows a generated presenter and no real person.", flush=True)
    print(f"Network: one download of about 730 KB from {SAMPLE_HOST}. Nothing else is fetched, unless the speech model is missing, and nothing is uploaded.", flush=True)
    try:
        with locks.heavy("the demo"):
            passed, project = take(root, args.renderer)
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    if args.keep:
        print(f"Kept in {root}. The film is at {root / 'film' / 'out' / 'final.mp4'}, and the review page at {root / 'film' / 'review' / 'page.html'}.")
    else:
        print("Removed the demo files. Run `altocue demo --keep` to look at the film and its review page.")
    return 0 if passed else 1


