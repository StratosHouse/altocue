"""What a run leaves on disk: job references, provenance, the manifest and the ledger."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project

GENERATED = "generated"


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_json(path: Path, data: Any) -> Path:
    """Write then rename. These files are how a crash resumes, so half a file is not an option."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".tmp")
    part.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(part, path)
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class JobStore:
    """One file per shot and request. The reference is written before the first poll."""

    def __init__(self, project: Project):
        self.folder = project.work / "jobs"

    def path(self, shot: str, digest: str) -> Path:
        return self.folder / f"{shot}-{digest}.json"

    def load(self, shot: str, digest: str) -> dict[str, Any] | None:
        path = self.path(shot, digest)
        return json.loads(path.read_text()) if path.exists() else None

    def save(self, record: dict[str, Any]) -> None:
        atomic_json(self.path(record["shot"], record["hash"]), record)

    def for_shot(self, shot: str) -> list[dict[str, Any]]:
        if not self.folder.exists():
            return []
        found = [json.loads(path.read_text()) for path in self.folder.glob(f"{shot}-*.json")]
        return [record for record in found if record.get("shot") == shot]


def generated_dir(project: Project) -> Path:
    return project.work / GENERATED


def manifest_path(project: Project) -> Path:
    return generated_dir(project) / "manifest.json"


def read_manifest(project: Project) -> dict[str, dict[str, Any]]:
    path = manifest_path(project)
    return json.loads(path.read_text())["shots"] if path.exists() else {}


def record_asset(project: Project, shot: str, *, asset: Path, digest: str, imported: bool, check_passed: bool | None) -> None:
    shots = read_manifest(project)
    shots[shot] = {
        "asset": str(asset.relative_to(project.root)),
        "provenance": str(provenance_path(asset).relative_to(project.root)),
        "hash": digest,
        "imported": imported,
        "check_passed": check_passed,
    }
    atomic_json(manifest_path(project), {"shots": shots})


def generated_asset(project: Project, shot: str, *, missing_ok: bool = False, require_passed: bool = True) -> Path | None:
    """The file to place for a shot. A clip that failed its check is a rush, not an insert."""
    entry = read_manifest(project).get(shot)
    path = project.root / entry["asset"] if entry else None
    if path is None or not path.exists():
        if missing_ok:
            return None
        raise AltocueError(f"Nothing has been generated for {shot}.", fix="Run `altocue generate`, or register a file with --import.")
    if require_passed and entry and entry.get("check_passed") is False:
        fix = f"Read {entry['provenance']}, then change the prompt and generate again."
        raise AltocueError(f"{shot} failed its check, so it will not be placed.", fix=fix)
    return path


def provenance_path(asset: Path) -> Path:
    return asset.with_name(asset.name + ".provenance.json")


def write_provenance(asset: Path, data: dict[str, Any]) -> Path:
    return atomic_json(provenance_path(asset), {"asset": asset.name, "sha256": sha256(asset), **data})


def ledger_path(project: Project) -> Path:
    return project.work / "spend.jsonl"


def log_spend(project: Project, row: dict[str, Any]) -> None:
    """One line per job outcome, once. A resumed job must not be counted again."""
    path = ledger_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = (row.get("job_id"), row.get("state"))
    if key[0] and path.exists():
        for line in path.read_text().splitlines():
            seen = json.loads(line)
            if (seen.get("job_id"), seen.get("state")) == key:
                return
    with path.open("a") as ledger:
        ledger.write(json.dumps({"t": now(), **row}, ensure_ascii=False) + "\n")
