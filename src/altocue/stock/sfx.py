"""`altocue sfx`: sound effects you are allowed to use. The bundled set first, then Freesound with your own key."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from typing import Any

from ..errors import AltocueError
from ..generate import http
from ..mix import SFX_DIR
from ..project import Project
from . import base, credits, freesound
from .base import Result


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="Every bundled sound")
    find = actions.add_parser("search", help="Search the bundled set, then Freesound if you have a key")
    find.add_argument("words", nargs="+", help="What you want, such as transition")
    find.add_argument("--limit", type=int, default=6, help="Most results from Freesound")
    get = actions.add_parser("get", help="Copy one sound into this project")
    get.add_argument("id", help="A name from `altocue sfx list`, or an id such as freesound:1234")
    get.add_argument("--project", "-p", default=".", help="Project folder")


def bundled() -> list[dict[str, Any]]:
    return json.loads((SFX_DIR / "sounds.json").read_text())


def line(row: dict[str, Any]) -> str:
    return f"{row['name']:<10} {row['seconds']:>5.2f} s  {row['licence']}  {row['pack']}  {row['use']}"


def matches(query: str) -> list[dict[str, Any]]:
    words = set(re.findall(r"[a-z0-9-]+", query.lower()))
    return [row for row in bundled() if words & {row["name"], *row["tags"]}]


def show(found: Result) -> list[str]:
    lines = [
        f"freesound:{found.id}  {found.duration:.1f} s  {base.plain(found.title)} by {base.plain(found.author)}",
        f"    Licence: {found.licence}, {base.credit_field(found.licence_url)}",
    ]
    lines.append(f"    Credit owed: {base.clean_credit(found.credit) or 'none'}")
    return lines


def resolve(item: str) -> tuple[Result | None, dict[str, Any] | None]:
    source, _, number = item.partition(":")
    if source == "freesound" and number.isdigit():
        found = freesound.find(number)
        if found is None:
            raise AltocueError(f"Freesound's {item} is not CC0 or CC BY, so it cannot join a project.", fix="Pick another sound.")
        return found, None
    row = next((row for row in bundled() if row["name"] == item), None)
    if row is None:
        raise AltocueError(f"No sound named {item}.", fix="Run `altocue sfx list`, or `altocue sfx search <words>`.")
    return None, row


def fetch(project: Project, item: str) -> str:
    found, row = resolve(item)
    (project.root / "sfx").mkdir(exist_ok=True)
    if row is not None:
        relative = f"sfx/{row['name']}.ogg"
        shutil.copyfile(SFX_DIR / f"{row['name']}.ogg", project.root / relative)
        source = "bigsoundbank" if row["pack"] == "BigSoundBank" else "kenney"
        credits.record(project, file=relative, source=source, source_url=row["url"], licence=row["licence"], licence_url=base.CC0_URL, kind="audio", title=row["name"])
        return relative
    assert found is not None
    relative = f"sfx/freesound-{found.id}.mp3"
    http.download(found.url, project.root / relative)
    base.require_audio(project.root / relative)
    credits.record(
        project, file=relative, source="freesound", source_url=found.source_url, licence=found.licence, licence_url=found.licence_url,
        credit=found.credit, kind="audio", title=found.title, warnings=found.warnings,
    )  # fmt: skip
    print(f"{base.UNTRUSTED_NOTE}\n" + "\n".join(show(found)))
    print(freesound.TERMS)
    return relative


def run(args: argparse.Namespace) -> int:
    if args.action == "list":
        print("Bundled sounds. Nobody has listened to these: they were chosen by name and measured length, so hear them before you ship.")
        for row in bundled():
            print(line(row))
        return 0
    if args.action == "search":
        query = " ".join(args.words)
        rows = matches(query)
        print(f"{len(rows)} bundled.")
        for row in rows:
            print(line(row))
        if not os.environ.get(freesound.KEY):
            print(f"Freesound was not searched, because {freesound.KEY} is not set. It is a free key, from https://freesound.org/apiv2/apply.")
            return 0
        print(f"\nSearching Freesound. Only the search words are sent.\n{base.UNTRUSTED_NOTE}")
        try:
            sounds = freesound.search(query, limit=args.limit)
        except AltocueError as error:
            print(f"Skipped freesound: {str(error).splitlines()[0]}")
            return 0
        for found in sounds:
            print("\n".join(show(found)))
        print(freesound.TERMS)
        return 0
    project = Project.open(args.project)
    relative = fetch(project, args.id)
    print(f"Saved {relative}.")
    return 0
