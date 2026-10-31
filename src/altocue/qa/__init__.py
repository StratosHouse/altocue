"""Fact gates in code. QA measures the render and reports to a human. It never fixes anything."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from ..errors import AltocueError
from ..models import GateResult
from ..project import Project, write_json
from . import faces, gates, ocr, rules

__all__ = ["add_arguments", "faces", "failed", "gates", "not_run", "ocr", "print_table", "rules", "run", "run_gates", "sha256"]


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def failed(results: list[GateResult]) -> list[GateResult]:
    return [g for g in results if not g.passed and not g.skipped]


def not_run(results: list[GateResult]) -> list[GateResult]:
    """Gates that should have measured something and could not. Not applicable gates are not here."""
    return [g for g in results if g.skipped and g.value != gates.NOT_APPLICABLE]


def run_gates(
    project: Project, file: str | Path, stage: str = "final", *, rules_path: str | Path | None = None, ocr_fps: float = gates.OCR_FPS
) -> list[GateResult]:
    """Run every gate, save `review/qa.json`, and return the results."""
    context = gates.Context.open(project, file, stage, rules_path=rules_path)
    context.ocr_fps = ocr_fps
    results = gates.run_all(context)
    digest = sha256(file)
    if context._heard is not None:
        write_json(project.review_dir / "transcript.json", gates.transcript_record(digest, *context._heard))
    write_json(
        project.review_dir / "qa.json",
        {
            "file": str(file),
            "sha256": digest,
            "stage": stage,
            "passed": not failed(results) and not not_run(results),
            "gates": [g.to_dict() for g in results],
        },
    )
    return results


def _cell(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return "" if value is None else str(value)


def print_table(results: list[GateResult]) -> None:
    width = max([len(g.id) for g in results] + [4])
    print(f"{'Gate':<{width}}  Result  Value      Detail")
    for gate in results:
        result = "n/a" if gate.value == gates.NOT_APPLICABLE else "SKIP" if gate.skipped else "PASS" if gate.passed else "FAIL"
        value = "" if isinstance(gate.value, dict) else _cell(gate.value)
        print(f"{gate.id:<{width}}  {result:<6}  {value[:9]:<9}  {gate.detail}")
    passed = sum(1 for g in results if g.passed)
    missing = not_run(results)
    print(f"\n{passed} passed, {len(failed(results))} failed, {len(missing)} skipped, {sum(g.value == gates.NOT_APPLICABLE for g in results)} not applicable.")
    if missing:
        print(f"Not checked: {', '.join(g.id for g in missing)}. A skipped gate is not a pass.")


def resolve(project: Project, file: str | None, stage: str) -> Path:
    default = "draft.mp4" if stage == "draft" else "final.mp4"
    if file is None:
        target = project.out / default
    else:
        target = Path(file).expanduser()
        if not target.exists() and (project.root / target).exists():
            target = project.root / target
    if not target.is_file():
        raise AltocueError(f"No file at {target}.", fix="Render it with `altocue render`, then `altocue master`, or pass --file.")
    return target.resolve()


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--file", help="The render to check. Defaults to out/final.mp4")
    parser.add_argument("--stage", choices=["final", "draft"], default="final", help="Drafts skip the mastering gates")
    parser.add_argument("--rules", help="Rules file. Defaults to rules.json in the project")
    parser.add_argument("--ocr-fps", type=float, default=gates.OCR_FPS, help="Frames read per second when checking rules")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    file = resolve(project, args.file, args.stage)
    results = run_gates(project, file, args.stage, rules_path=args.rules, ocr_fps=args.ocr_fps)
    print_table(results)
    print(f"Saved {project.review_dir / 'qa.json'}")
    return 1 if failed(results) else 0
