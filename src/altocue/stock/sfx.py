"""`altocue sfx`: sound effects you are allowed to use. The bundled set first, your own packs, the indexed open packs, then Freesound with your own key."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..generate import http
from ..mix import SFX_DIR
from ..project import Project
from . import base, credits, freesound, packs, sfxpacks
from .base import Result


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="Every bundled sound")
    find = actions.add_parser("search", help="Search the bundled set, then Freesound if you have a key")
    find.add_argument("words", nargs="+", help="What you want, such as transition")
    find.add_argument("--limit", type=int, default=6, help="Most results from Freesound")
    get = actions.add_parser("get", help="Copy one sound into this project")
    get.add_argument("id", help="A name from `altocue sfx list` or `altocue sfx search`, a pack sound such as vector:keyboard-01, or freesound:1234")
    get.add_argument("--project", "-p", default=".", help="Project folder")
    pack = actions.add_parser("pack", help="Register a sound pack you hold a licence for. It stays on this machine")
    pack.add_argument("name", help="A short name, such as vector")
    pack.add_argument("folder", help="The folder that holds the pack's sound files")
    pack.add_argument("--licence", required=True, help='Your licence in your own words, such as "Bought from the maker, single user"')
    actions.add_parser("packs", help="Every registered pack")
    forget = actions.add_parser("forget", help="Forget a pack. No file is deleted")
    forget.add_argument("name", help="A name from `altocue sfx packs`")


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


def indexed(query: str | None = None, id: str | None = None) -> list[dict[str, Any]]:
    """Sounds from the open packs in the library catalog, found by category, tag or name. None when there is no index yet."""
    from .. import library

    catalog = library.current()
    if id is not None:
        return [row for row in sfxpacks.indexed(catalog) if row["id"] == id]
    return [row for row in library.search(catalog, query or "", kind="sfx", tier="fetch") if row.get("source") == library.SOUND_SOURCE]


def fetch(project: Project, item: str) -> str:
    if packs.find(item) is not None:
        return packs.fetch(project, item)
    found_row = indexed(id=item) if not item.startswith("freesound:") else []
    if found_row:
        return sfxpacks.fetch(project, found_row[0])
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


def pack_run(args: argparse.Namespace) -> int:
    if args.action == "pack":
        added = packs.add(args.name, args.folder, args.licence)
        print(f"Pack {added['name']}: {added['sounds']} sounds in {added['folder']}. They stay on this machine.")
        print(f"Copy one into a project with `altocue sfx get {added['name']}:<sound> -p .`. It lands in {packs.PRIVATE}/, which git ignores.")
        return 0
    if args.action == "forget":
        packs.remove(args.name)
        print(f"Forgot pack {packs.slug(args.name)}. Its files are untouched.")
        return 0
    known = packs.packs()
    if not known:
        print('No packs. Add one with `altocue sfx pack <name> <folder> --licence "..."`.')
    for name, pack in known.items():
        there = Path(pack["folder"]).is_dir()
        count = len(packs.sounds(Path(pack["folder"]))) if there else 0
        print(f"{name:<12} {count:>4} sounds  {pack['licence']}  {pack['folder']}{'' if there else '  (folder missing)'}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.action in {"pack", "packs", "forget"}:
        return pack_run(args)
    if args.action == "list":
        print("Bundled sounds. Nobody has listened to these: they were chosen by name and measured length, so hear them before you ship.")
        for row in bundled():
            print(line(row))
        if packs.packs():
            print("Your own packs are listed by `altocue sfx packs`, and searched by `altocue sfx search`.")
        return 0
    if args.action == "search":
        query = " ".join(args.words)
        rows = matches(query)
        print(f"{len(rows)} bundled.")
        for row in rows:
            print(line(row))
        open_rows = indexed(query)
        if open_rows:
            print(f"{len(open_rows)} in the open packs. CC0, fetched with `altocue sfx get <id>`. The pack downloads once.")
            for row in open_rows[: max(args.limit, 10)]:
                print(f"{row['id']:<44} {row['category']:<12} {row.get('seconds') or 0:>5.2f} s  {row['pack_title']}")
        own = packs.matches(query)
        if own:
            print(f"{len(own)} in your packs. Licensed to you, so they stay private.")
            for name, sound, _ in own:
                print(f"{name}:{sound}")
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
