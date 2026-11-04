"""What a film owes. Every download writes one record, and render writes the block a creator pastes."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project, write_json
from . import SOURCES, base

FILE = "credits.json"
TEXT = "credits.txt"
HEADINGS = {"audio": "Music and sound", "video": "Footage", "photo": "Photos"}
OTHER_SOURCES = {"ccmixter", "incompetech", "freesound", "kenney", "bigsoundbank"}


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")


def records(project: Project) -> list[dict[str, Any]]:
    path = project.root / FILE
    return json.loads(path.read_text()).get("assets", []) if path.exists() else []


def digest(path: Path) -> str:
    if not path.is_file():
        return ""
    hashed = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hashed.update(block)
    return hashed.hexdigest()


def record(
    project: Project,
    *,
    file: str,
    source: str,
    source_url: str,
    licence: str,
    licence_url: str = "",
    credit: str = "",
    kind: str = "",
    title: str = "",
    warnings: Any = (),
    licensed: bool = False,
) -> dict[str, Any]:
    """One record per file. Fetching the same file again replaces it, so a re-run never doubles a credit.

    `licensed` is a sound pack the user holds their own licence for. It needs no open licence, owes a credit
    only if one is given, and is marked private so it never ships as a file.
    """
    if not licensed and not base.clear(licence):
        raise AltocueError(f"{title or file} has no clear licence, so it cannot join a project.", fix="Pick another result.")
    row = {
        "file": file,
        "title": base.credit_field(title),
        "kind": kind,
        "source": source,
        "source_url": source_url,
        "licence": licence,
        "licence_url": licence_url,
        "credit": base.clean_credit(credit),
        "date": datetime.date.today().isoformat(),
        "sha256": digest(project.root / file),
        "warnings": [base.credit_field(line, 300) for line in warnings],
        **({"licensed": True, "private": True} if licensed else {}),
    }
    rows = [saved for saved in records(project) if saved["file"] != file]
    write_json(project.root / FILE, {"assets": [*rows, row]})
    return row


def carried(project: Project) -> list[dict[str, Any]]:
    """Stock inserts in the timeline carry their licence, so a lost credits.json cannot hide a debt."""
    if not project.timeline_path.exists():
        return []
    inserts = json.loads(project.timeline_path.read_text()).get("inserts", [])
    return [
        {
            "file": row.get("source_file", row["file"]),
            "title": row.get("title", row["file"]),
            "kind": "video",
            "licence": row.get("licence", ""),
            "credit": base.clean_credit(row.get("credit", "")),
        }
        for row in inserts
        if row.get("source") in {*SOURCES, *OTHER_SOURCES}
    ]


def film_text(project: Project) -> str:
    """Everything that says what this film uses. Paths are searched as text, because each file names them its own way."""
    # a film from a storyboard names its track there
    named = (project.timeline_path, project.root / "motion.json", project.root / "broll.json", project.root / "storyboard.json")
    return "\n".join(path.read_text() for path in named if path.exists())


def used(project: Project) -> list[dict[str, Any]]:
    """The records this film really uses. A downloaded file that was later dropped owes nothing."""
    text = film_text(project)
    rows = [row for row in records(project) if row["file"] in text]
    known = {row["file"] for row in rows}
    return rows + [row for row in carried(project) if row["file"] not in known]


def unused(project: Project) -> list[dict[str, Any]]:
    text = film_text(project)
    return [row for row in records(project) if row["file"] not in text]


def owes(row: dict[str, Any]) -> bool:
    if row.get("licensed"):
        return bool(row.get("credit"))
    return bool(row.get("credit")) or base.needs_credit(row.get("licence", "")) or not base.clear(row.get("licence", ""))


def block(rows: list[dict[str, Any]]) -> str:
    """The text a creator pastes into a description. Credits only, so it stays clean."""
    grouped: dict[str, dict[str, None]] = {heading: {} for heading in HEADINGS.values()}
    for row in rows:
        if row.get("credit"):
            grouped[HEADINGS.get(row.get("kind", ""), HEADINGS["video"])][row["credit"]] = None
    lines = [line for heading, owed in grouped.items() if owed for line in ("", heading, *owed)]
    return "\n".join(["Credits", *lines]) + "\n" if lines else ""


def write_txt(project: Project) -> Path | None:
    """Render calls this, so a film that owes a credit always has the file beside it."""
    path = project.out / TEXT
    text = block(used(project))
    if not text:
        path.unlink(missing_ok=True)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    rows = used(project)
    print(base.UNTRUSTED_NOTE, file=sys.stderr)
    text = block(rows)
    print(text or "Nothing in this film owes a credit.")
    write_txt(project)  # the file beside the film follows what it owes now, with no new render
    free = [row for row in rows if not owes(row)]
    if free:
        print(f"{len(free)} more need no credit: {', '.join(base.plain(row.get('title') or row['file']) for row in free)}.", file=sys.stderr)
    spare = unused(project)
    if spare:
        print(f"Downloaded, but not in this film, so left out: {', '.join(row['file'] for row in spare)}.", file=sys.stderr)
    if any(base.share_alike(row.get("licence", "")) for row in rows):
        print(base.SHARE_ALIKE_WARNING, file=sys.stderr)
    return 0
