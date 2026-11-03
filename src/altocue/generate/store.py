"""What a run leaves on disk: job references, provenance, the manifest and the ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project

GENERATED = "generated"
BILLED_STATES = {"submitting", "queued", "running", "succeeded"}  # paid for and not yet on the ledger


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


def holds(path: Path, needle: bytes) -> bool:
    """Whether these bytes are anywhere in a file, across block edges."""
    tail = b""
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            if needle in tail + block:
                return True
            tail = block[-(len(needle) - 1) :]
    return False


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


def record_asset(
    project: Project, shot: str, *, asset: Path, digest: str, imported: bool, check_passed: bool | None, meta: dict[str, Any] | None = None
) -> None:
    shots = read_manifest(project)
    shots[shot] = {
        "asset": str(asset.relative_to(project.root)),
        "provenance": str(provenance_path(asset).relative_to(project.root)),
        "hash": digest,
        "imported": imported,
        "check_passed": check_passed,
        **(meta or {}),
    }
    atomic_json(manifest_path(project), {"shots": shots})


def note(project: Project, shot: str, **fields: Any) -> None:
    """Add fields to one shot's manifest entry, leaving the rest as it was."""
    shots = read_manifest(project)
    shots[shot] = {**shots[shot], **fields}
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
    """Every asset gets a provenance id. A file Altocue makes from it later carries that id, so the honesty gates can follow it."""
    return atomic_json(provenance_path(asset), {"asset": asset.name, "sha256": sha256(asset), "provenance_id": uuid.uuid4().hex, **data})


def read_provenance(path: Path) -> dict[str, Any] | None:
    """The provenance beside a file, or beside the file a link points to."""
    sidecar = provenance_path(Path(path).resolve())
    try:
        data = json.loads(sidecar.read_text()) if sidecar.is_file() else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def lineage(data: dict[str, Any] | None) -> set[str]:
    """Every provenance id a file stands for: its own, and each one it was made from. An older record with no id goes by its bytes."""
    if not data:
        return set()
    own = data.get("provenance_id") or data.get("sha256")
    return {str(item) for item in [own, *(data.get("derived_from") or [])] if item}


def carry_provenance(source: Path, dest: Path, how: str) -> Path | None:
    """A trim, upscale or re-encode of a generated file is still that file, so its record goes with it."""
    data = read_provenance(source)
    if data is None:
        return None
    keep = {key: data[key] for key in ("shot", "purpose", "kind", "prompt", "script", "provider", "model", "synthetic", "consent", "claims") if key in data}
    made = {**keep, "made_by": how, "made_from": Path(source).name, "derived_from": sorted(lineage(data)), "date": now()}
    return write_provenance(Path(dest), made)


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


def usd(value: Any) -> float:
    """One booked figure. A negative or unreadable one becomes NaN, so the total it joins fails closed at the gate."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return math.nan
    return number if number >= 0 else math.nan


def spent_usd(project: Project) -> float:
    """Every ledger row, less a cancelled job the provider's billing showed was not charged."""
    path = ledger_path(project)
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return sum(usd(row.get("usd")) for row in rows if not row.get("not_billed"))


def clear_canceled(project: Project, shot: str) -> list[dict[str, Any]]:
    """Call inside `booking`. Marks this shot's cancelled jobs as not billed, and returns them."""
    path = ledger_path(project)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    cleared = [row for row in rows if row.get("shot") == shot and row.get("may_be_billed") and not row.get("not_billed")]
    for row in cleared:
        row["not_billed"] = now()
    if cleared:
        part = path.with_name(path.name + ".tmp")
        part.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        os.replace(part, path)
    return cleared


def in_flight_usd(project: Project) -> float:
    """Jobs that were paid for and have not reached the ledger, because it records them when they end."""
    folder = JobStore(project).folder
    if not folder.exists():
        return 0.0
    records = [json.loads(path.read_text()) for path in folder.glob("*.json")]
    return sum(usd(record.get("cost_usd")) for record in records if record.get("state") in BILLED_STATES)


def reservations_path(project: Project) -> Path:
    return generated_dir(project) / "reservations.json"


def read_reservations(project: Project) -> list[dict[str, Any]]:
    path = reservations_path(project)
    return json.loads(path.read_text())["reservations"] if path.exists() else []


def save_reservations(project: Project, rows: list[dict[str, Any]]) -> None:
    atomic_json(reservations_path(project), {"reservations": rows})


def open_reservation(project: Project, shot: str) -> dict[str, Any] | None:
    return next((row for row in read_reservations(project) if row["shot"] == shot and row["state"] == "open"), None)


def reserved_usd(project: Project) -> float:
    return sum(usd(row["usd"]) for row in read_reservations(project) if row["state"] == "open")


def holds_path(project: Project) -> Path:
    return generated_dir(project) / "holds.json"


def read_holds(project: Project) -> list[dict[str, Any]]:
    path = holds_path(project)
    return json.loads(path.read_text())["holds"] if path.exists() else []


def _alive(row: dict[str, Any]) -> bool:
    """A hold counts while the run that took it is alive. One on another machine always counts, because it cannot be checked."""
    if row.get("host") != socket.gethostname():
        return True
    try:
        os.kill(int(row["pid"]), 0)
    except ProcessLookupError:
        return False
    except (OSError, KeyError, TypeError, ValueError):
        return True
    return True


def held_usd(project: Project) -> float:
    """What running `generate` runs have booked and not yet sent. A run that died sent nothing more, so its hold no longer counts."""
    return sum(usd(row["usd"]) for row in read_holds(project) if row["state"] == "open" and _alive(row))


def open_hold(project: Project, run: str, amount: float) -> None:
    """Call inside `booking`. A run books its whole estimate before any job is sent."""
    rows = [row for row in read_holds(project) if _alive(row)]
    rows.append({"run": run, "state": "open", "usd": round(amount, 6), "pid": os.getpid(), "host": socket.gethostname(), "at": now()})
    atomic_json(holds_path(project), {"holds": rows})


def draw_hold(project: Project, run: str, amount: float) -> None:
    """Call inside `booking`, with the job record that now counts this money as in flight."""
    rows = read_holds(project)
    for row in rows:
        if row["run"] == run and row["state"] == "open":
            row["usd"] = round(max(usd(row["usd"]) - amount, 0.0), 6)
    atomic_json(holds_path(project), {"holds": rows})


def close_hold(project: Project, run: str) -> None:
    """Call inside `booking`. What the run did not send goes back to the cap."""
    path = holds_path(project)
    if path.exists():
        atomic_json(path, {"holds": [row for row in read_holds(project) if row["run"] != run and _alive(row)]})


def committed_usd(project: Project) -> float:
    """Everything the cap has to count: spent, running, promised to a reservation, and held by a run that has not sent it yet."""
    return round(spent_usd(project) + in_flight_usd(project) + reserved_usd(project) + held_usd(project), 6)


@contextmanager
def booking(project: Project) -> Iterator[None]:
    """Check and book as one step, so two agents cannot both take the last of the cap."""
    path = generated_dir(project) / ".booking.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
