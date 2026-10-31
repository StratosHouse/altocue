"""The Editor in Chief. Fact gates first, then a blind reviewer, then code decides."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .. import ffmpeg, qa
from ..errors import AltocueError
from ..models import GateResult
from ..project import Project
from . import codex, gemini, pack, report, rounds, rubric

__all__ = ["add_arguments", "codex", "editor_vendor", "gemini", "independence", "pack", "qa", "report", "rounds", "rubric", "run"]

HOSTS = (("CLAUDECODE", "anthropic"), ("CODEX_SANDBOX", "openai"), ("GEMINI_CLI", "google"))
REVIEWERS = ("auto", "gemini", "pack", "codex")
NO_REVIEWER = {"vendor": "none", "model": "none", "sees": "nothing"}


def editor_vendor(project: Project, flag: str | None) -> str | None:
    """Who made the edit: the flag, then altocue.toml, then the agent we are running inside."""
    if flag:
        return flag
    configured = project.setting("review", "editor_vendor", "")
    if configured:
        return configured
    return next((vendor for name, vendor in HOSTS if os.environ.get(name)), None)


def independence(reviewer: str, editor: str | None) -> tuple[bool, str]:
    """Independent means a different vendor from the editor, and we can say which."""
    if not reviewer:
        return False, "The reviewer's vendor is unknown."
    if not editor:
        return (
            False,
            "The editor's vendor is not recorded, so independence cannot be claimed. Set editor_vendor under [review] in altocue.toml.",
        )
    if reviewer.lower() == editor.lower():
        return (
            False,
            f"Reviewer and editor are both {editor}. The reviewer had a fresh context and no edit reasons, "
            "but it is not an outside opinion.",
        )
    return True, f"Reviewer is {reviewer}, editor is {editor}."


def finish(project: Project, file: Path, digest: str, result: dict[str, Any], reviewer: dict[str, Any], gates: list[GateResult]) -> int:
    result = {**result, "reviewer": reviewer, "file_sha256": digest}
    log = rounds.record(
        project,
        digest,
        result["verdict"],
        reviewer=str(reviewer.get("vendor")),
        weighted=result["weighted"],
        open_items=len(qa.failed(gates)) + len(result["notes"]),
    )
    path = report.write(project, file, result, gates, log)
    print(f"\n{result['verdict']}" + (f" (weighted {result['weighted']:.2f})" if result["weighted"] is not None else ""))
    if reviewer.get("independent") is False:
        print(f"  Not an independent review. {reviewer['independent_why']}")
    for reason in result["reasons"]:
        print(f"  {reason}")
    if log["handed_off"]:
        print("Both fix rounds are used. This goes to a human now. The open list is in the report.")
    print(f"Report: {path}")
    return 0 if result["verdict"] == "SHIP" else 1


def conclude(
    project: Project, file: Path, digest: str, raw: dict[str, Any], reviewer: dict[str, Any], gates: list[GateResult], editor: str | None
) -> int:
    result = rubric.score(raw, gates, ffmpeg.probe_clip(file).duration)
    flag, why = independence(str(reviewer.get("vendor", "")), editor)
    if reviewer.get("via") == "review pack":
        flag, why = False, f"The verdict file says the reviewer is {reviewer.get('vendor')}. A file cannot prove that, so it does not count as independent."
    return finish(project, file, digest, result, {**reviewer, "independent": flag, "independent_why": why, "editor_vendor": editor}, gates)


def ingest(project: Project, file: Path, source: str, editor: str | None) -> int:
    target = Path(source).expanduser()
    if not target.exists() and (project.root / target).exists():
        target = project.root / target
    if not target.is_file():
        raise AltocueError(f"No verdict at {target}.", fix="Have the reviewer write review/verdict.json first.")
    try:
        raw = json.loads(target.read_text())
    except json.JSONDecodeError as error:
        raise AltocueError(f"{target.name} is not valid JSON: {error}", fix="Fix the file and ingest it again.") from error
    reviewer = raw.get("reviewer") or {}
    if not reviewer.get("vendor"):
        raise AltocueError(
            "The verdict does not name the reviewer's vendor.", fix='Add "reviewer": {"vendor": "...", "model": "..."} to the file.'
        )
    digest = qa.sha256(file)
    stated = raw.get("file_sha256")
    if stated != digest:
        raise AltocueError(
            f"This verdict is for another render, not {file.name}.",
            fix="Build a new pack with `altocue review --reviewer pack` and review that.",
        )
    if rounds.blocked(project, digest):
        print("Both fix rounds are used, so this verdict was not scored. This is with a human now. Use --reset-rounds to start again.")
        return 1
    gates = qa.run_gates(project, file)  # measured here, because a saved result is a file the editor can change
    return conclude(
        project,
        file,
        digest,
        raw,
        {"vendor": reviewer["vendor"], "model": reviewer.get("model", "unknown"), "sees": "stills", "via": "review pack"},
        gates,
        editor,
    )


def pick(project: Project, args: argparse.Namespace) -> str:
    chosen = args.reviewer or project.setting("review", "reviewer", "auto")
    if chosen not in REVIEWERS:
        raise AltocueError(f"Unknown reviewer {chosen}.", fix=f"Use one of {', '.join(REVIEWERS)}.")
    if chosen == "auto":
        return "gemini" if gemini.ready(project, upload=args.upload, paid=args.paid_key) else "pack"
    return chosen


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--file", help="The render to review. Defaults to out/final.mp4")
    parser.add_argument("--reviewer", choices=REVIEWERS, help="auto uses Gemini once you have opted in, and the pack otherwise")
    parser.add_argument("--ingest", metavar="VERDICT", help="Score a verdict.json written by an agent")
    parser.add_argument("--upload", action="store_true", help="Opt in to sending this video or its frames to the chosen reviewer's vendor")
    parser.add_argument("--paid-key", action="store_true", help="Confirm GEMINI_API_KEY is on a billed project")
    parser.add_argument("--editor-vendor", help="Who made the edit, to label independence: anthropic, openai, google")
    parser.add_argument("--reset-rounds", action="store_true", help="Forget the fix rounds and start again")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    file = qa.resolve(project, args.file, "final")
    if not args.file and project.edl_path.exists() and project.edl_path.stat().st_mtime > file.stat().st_mtime:
        raise AltocueError(f"{file.name} is older than the cut.", fix="Run `altocue run`, then `altocue review`.")
    if args.reset_rounds:
        rounds.reset(project)
    editor = editor_vendor(project, args.editor_vendor)
    if args.ingest:
        return ingest(project, file, args.ingest, editor)

    chosen = pick(project, args)
    gates = qa.run_gates(project, file)
    qa.print_table(gates)
    digest = qa.sha256(file)
    if qa.failed(gates):
        return finish(project, file, digest, rubric.gates_only(gates), dict(NO_REVIEWER), gates)
    if rounds.blocked(project, digest):
        print("Both fix rounds are used, so no reviewer was called. This is with a human now. Use --reset-rounds to start again.")
        return 1
    if chosen == "gemini":
        gemini.require(project, upload=args.upload, paid=args.paid_key)

    info = pack.build(project, file, gates)
    if chosen == "pack":
        for stale in ("REPORT.md", "report.json"):
            (project.review_dir / stale).unlink(missing_ok=True)
        path = pack.write_prompt(project, info)
        print(f"\nPack ready: {path}")
        print("Nothing was uploaded. Whatever agent reads the pack sends those frames to its own vendor.")
        print("Have a fresh-context agent follow it (in Claude Code, the editor-in-chief agent), then run:")
        print("  altocue review --ingest review/verdict.json")
        return 0
    prompt = pack.prompt_text(info)
    if chosen == "gemini":
        raw, reviewer = gemini.review(
            project, file, duration=ffmpeg.probe_clip(file).duration, prompt=prompt, upload=args.upload, paid=args.paid_key
        )
    else:
        raw, reviewer = codex.review(project, prompt, upload=args.upload)
    return conclude(project, file, digest, raw, reviewer, gates, editor)
