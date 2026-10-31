"""At most two fix rounds, tracked in a file, then the open list goes to a human."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..project import Project, write_json

MAX_FIX_ROUNDS = 2


def path(project: Project) -> Path:
    return project.review_dir / "rounds.json"


def load(project: Project) -> dict[str, Any]:
    target = path(project)
    if target.exists():
        return json.loads(target.read_text())
    return {"max_fix_rounds": MAX_FIX_ROUNDS, "handed_off": False, "rounds": []}


def record(project: Project, digest: str, verdict: str, *, reviewer: str, weighted: float | None, open_items: int) -> dict[str, Any]:
    """Log one review. The same file reviewed again replaces its entry and costs no round."""
    log = load(project)
    entry = {
        "file_sha256": digest,
        "verdict": verdict,
        "reviewer": reviewer,
        "weighted": weighted,
        "open_items": open_items,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    log["rounds"] = [r for r in log["rounds"] if r["file_sha256"] != digest] + [entry]
    fixes = sum(1 for r in log["rounds"] if r["verdict"] == "FIX")
    log["handed_off"] = verdict == "FIX" and fixes > MAX_FIX_ROUNDS
    write_json(path(project), log)
    return log


def blocked(project: Project, digest: str) -> bool:
    """True once the rounds are used, for any render not already logged."""
    log = load(project)
    return bool(log["handed_off"]) and digest not in {r["file_sha256"] for r in log["rounds"]}


def reset(project: Project) -> None:
    path(project).unlink(missing_ok=True)
