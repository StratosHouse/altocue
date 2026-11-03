"""What is done, what is stale, and the one command to run next.

A stage is judged by what is on disk: real timestamps, the cut mark, and the hash QA and review
already record. Nothing here runs a stage or changes a file. Each check is one small function
in `CHECKS`, so a new stage only adds a row.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import frame, grade
from .errors import AltocueError
from .project import Project
from .qa import sha256

DONE, STALE, NOT_RUN = "done", "stale", "not run"
PAGE_FILM = re.compile(r'name="altocue-film" content="([0-9a-f]{64})"')


@dataclass
class Stage:
    name: str
    state: str
    detail: str = ""
    command: str = ""


@dataclass
class Look:
    """One stage's answer: its state, what to say, and any flags the command needs."""

    state: str
    detail: str = ""
    flags: str = ""
    command: str = ""


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def _newest(paths: list[Path]) -> tuple[float, Path | None]:
    found = [path for path in paths if path.exists()]
    newest = max(found, key=lambda path: path.stat().st_mtime, default=None)
    return (newest.stat().st_mtime, newest) if newest else (0.0, None)


def _older_than(file: Path, inputs: list[Path]) -> Path | None:
    """The newest input that changed after `file` was made."""
    when, newest = _newest(inputs)
    return newest if newest and when > file.stat().st_mtime else None


def _clip_sources(project: Project) -> list[Path]:
    return [Path(clip.path) for clip in project.clips()]


def check_ingest(project: Project) -> Look:
    if not project.media_path.exists():
        return Look(NOT_RUN)
    links = [link for link in project.sources.iterdir() if not link.name.startswith(".")]
    known = {clip.id for clip in project.clips()}
    added = [link.name for link in links if link.stem not in known]
    if added:
        return Look(STALE, f"new in sources: {', '.join(added)}")
    changed = _older_than(project.media_path, links)
    return Look(STALE, f"{changed.name} changed") if changed else Look(DONE, f"{len(known)} clip{'' if len(known) == 1 else 's'}")


def check_transcribe(project: Project) -> Look:
    if not project.media_path.exists():
        return Look(NOT_RUN)
    clips = project.clips()
    files = {clip.id: project.words_dir / f"{clip.id}.json" for clip in clips}
    missing = [name for name, path in files.items() if not path.exists()]
    if len(missing) == len(files):
        return Look(NOT_RUN)
    if missing:
        return Look(STALE, f"no words yet for {', '.join(missing)}")
    changed = [clip.id for clip in clips if _older_than(files[clip.id], [Path(clip.path)])]
    return Look(STALE, f"{', '.join(changed)} changed after it was transcribed", "--force") if changed else Look(DONE)


def check_cut(project: Project) -> Look:
    if not project.edl_path.exists():
        return Look(NOT_RUN)
    changed = _older_than(project.edl_path, sorted(project.words_dir.glob("*.json")))
    if changed:
        return Look(STALE, f"{changed.name} changed after the cut")
    waiting = sum(item.decision == "review" for item in project.edl().removed)
    return Look(DONE, f"{waiting} item{' waits' if waiting == 1 else 's wait'} for a ruling in work/cut-review.md" if waiting else "")


def check_clean(project: Project) -> Look:
    if not project.media_path.exists() or not (project.audio_dir / "report.json").exists() and not list(project.audio_dir.glob("*.wav")):
        return Look(NOT_RUN)
    voiced = [clip for clip in project.clips() if clip.has_audio]
    files = {clip.id: project.audio_dir / f"{clip.id}.wav" for clip in voiced}
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        return Look(STALE, f"no clean sound yet for {', '.join(missing)}")
    changed = [clip.id for clip in voiced if _older_than(files[clip.id], [Path(clip.path)])]
    return Look(STALE, f"{', '.join(changed)} changed after it was cleaned") if changed else Look(DONE)


def check_grade(project: Project) -> Look:
    if not (project.lut_dir / "report.json").exists() or not project.media_path.exists():
        return Look(NOT_RUN)
    return Look(DONE) if grade.up_to_date(project) else Look(STALE, "the cut, the look or a source changed")


def check_frame(project: Project) -> Look:
    if not project.setting("frame", "enabled", True):
        return Look(DONE, "off in altocue.toml")
    if not project.edl_path.exists() or not (project.work / "frame.json").exists():
        return Look(NOT_RUN)
    return Look(DONE) if frame.up_to_date(project) else Look(STALE, "the cut, the aspect or a frame setting changed")


def check_broll(project: Project) -> Look | None:
    """Only a project that chose B-roll has this step."""
    chosen = _read(project.root / "broll.json")
    if not chosen.get("inserts"):
        return None
    if not project.edl_path.exists() or chosen.get("cut") != project.cut_mark():
        return Look(STALE, "the cut changed after the B-roll was chosen")
    return Look(DONE)


def check_generate(project: Project) -> Look | None:
    """Only a project with a shot list has this step."""
    from .generate import store

    listed = [str(row["id"]) for row in _read(project.root / "shots.json").get("shots", []) if isinstance(row, dict) and row.get("id")]
    if not listed:
        return None
    made = store.read_manifest(project)
    missing = [shot for shot in listed if shot not in made or not (project.root / made[shot]["asset"]).exists()]
    if missing:
        return Look(NOT_RUN, f"still to generate: {', '.join(missing)}")
    failed = [shot for shot in listed if made[shot].get("check_passed") is False]
    return Look(STALE, f"{', '.join(failed)} failed its check") if failed else Look(DONE)


def render_inputs(project: Project) -> list[Path]:
    """What a render reads. A change to any of these changes the picture or the sound."""
    inputs = [project.edl_path, project.root / "motion.json", project.root / "broll.json", *project.audio_dir.glob("*.wav"), *project.lut_dir.glob("*.cube")]
    brand = str(project.setting("brand", "name", "default"))
    return [*inputs, project.root / brand] if brand != "default" else inputs


def check_render(project: Project) -> Look:
    file = project.out / "render.mp4"
    if not file.exists():
        return Look(NOT_RUN)
    made_from = _read(project.work / "render.json").get("cut")
    if made_from and project.edl_path.exists() and made_from != project.cut_mark():
        return Look(STALE, "the cut changed after this render")
    changed = _older_than(file, render_inputs(project))
    return Look(STALE, f"{changed.name} changed after this render") if changed else Look(DONE)


def check_master(project: Project) -> Look:
    file = project.out / "final.mp4"
    if not file.exists():
        return Look(NOT_RUN)
    return Look(STALE, "the render is newer") if _older_than(file, [project.out / "render.mp4"]) else Look(DONE)


def _final_hash(project: Project) -> str:
    file = project.out / "final.mp4"
    return sha256(file) if file.exists() else ""


def check_qa(project: Project) -> Look:
    saved = _read(project.review_dir / "qa.json")
    if not saved:
        return Look(NOT_RUN)
    if saved.get("sha256") != _final_hash(project):
        return Look(STALE, "the film changed after QA")
    changed = _older_than(project.review_dir / "qa.json", [project.root / "rules.json", project.root / "brief.md"])
    if changed:
        return Look(STALE, f"{changed.name} changed after QA")
    gates = saved.get("gates", [])
    failed = [g["id"] for g in gates if not g.get("passed") and not g.get("skipped")]
    skipped = [g["id"] for g in gates if g.get("skipped") and g.get("value") != "n/a"]
    notes = ([f"failed: {', '.join(failed)}"] if failed else []) + ([f"not checked: {', '.join(skipped)}"] if skipped else [])
    return Look(DONE, ", ".join(notes))


def check_review(project: Project) -> Look:
    saved = _read(project.review_dir / "report.json")
    if not saved:
        if (project.review_dir / "PACK.md").exists():
            return Look(NOT_RUN, "the pack is waiting for a reviewer", "--ingest review/verdict.json")
        return Look(NOT_RUN)
    if saved.get("file_sha256") != _final_hash(project):
        return Look(STALE, "the film changed after this review")
    if _older_than(project.review_dir / "report.json", [project.root / "brief.md"]):
        return Look(STALE, "brief.md changed after this review")
    return Look(DONE, str(saved.get("verdict", "")))


def check_deliver(project: Project) -> Look:
    saved = _read(project.root / "deliver" / "deliver.json")
    if not saved:
        return Look(NOT_RUN)
    return Look(DONE) if saved.get("final_sha256") == _final_hash(project) else Look(STALE, "the film changed after delivery")


def check_page(project: Project) -> Look | None:
    """Only a project that wrote a page has this step. A stale page plays an old film beside an old verdict."""
    page = project.review_dir / "page.html"
    if not page.exists():
        return None
    with page.open(errors="replace") as handle:
        film = PAGE_FILM.search(handle.read(1024))
    again = "altocue review -p {folder} --page"
    if not film or film.group(1) != _final_hash(project):
        return Look(STALE, "the film changed after this page", command=again)
    if _older_than(page, [project.review_dir / "report.json", project.review_dir / "qa.json"]):
        return Look(STALE, "the review changed after this page", command=again)
    return Look(DONE)


CHECKS: tuple[tuple[str, Callable[[Project], Look | None]], ...] = (
    ("ingest", check_ingest),
    ("transcribe", check_transcribe),
    ("cut", check_cut),
    ("clean", check_clean),
    ("grade", check_grade),
    ("frame", check_frame),
    ("broll", check_broll),
    ("generate", check_generate),
    ("render", check_render),
    ("master", check_master),
    ("qa", check_qa),
    ("review", check_review),
    ("deliver", check_deliver),
    ("page", check_page),
)


def collect(project: Project) -> list[Stage]:
    """Every stage in order. Once one is out of date, nothing after it can be trusted."""
    stages: list[Stage] = []
    behind = ""
    for name, check in CHECKS:
        try:
            found = check(project)
        except AltocueError as error:
            found = Look(NOT_RUN, str(error).splitlines()[0])
        if found is None:
            continue
        if behind and found.state == DONE:
            found = Look(STALE, f"waits on {behind}")
        stages.append(Stage(name, found.state, found.detail, found.command or f"altocue {name} -p {{folder}}" + (f" {found.flags}" if found.flags else "")))
        behind = behind or (name if found.state != DONE else "")
    return stages


def next_command(stages: list[Stage], folder: str) -> str | None:
    """The first stage that is not done, as a command to paste. None when everything is."""
    return next((stage.command.format(folder=folder) for stage in stages if stage.state != DONE), None)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")


def run(args: argparse.Namespace) -> int:
    stages = collect(Project.open(args.project))
    width = max(len(stage.name) for stage in stages)
    for stage in stages:
        print(f"{stage.name:<{width}}  {stage.state:<7}  {stage.detail}".rstrip())
    command = next_command(stages, args.project)
    print(f"\nNext: {command}" if command else "\nNext: nothing. This film is done and delivered.")
    return 0
