"""`altocue music`: find music you are allowed to use. Licence and credit show before anything downloads."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..generate import http
from ..project import Project, write_json
from . import base, ccmixter, credits, incompetech
from .base import Result

DATA = Path(__file__).parent.parent / "data" / "music"
SHORT = 15.0  # the mix loops music to the film, and a loop this short is heard as one
SEARCHED = (ccmixter, incompetech)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    find = actions.add_parser("search", help="Search every source. Nothing downloads")
    find.add_argument("words", nargs="+", help="What you want, such as calm piano")
    find.add_argument("--limit", type=int, default=6, help="Most results per source")
    get = actions.add_parser("get", help="Download one result into this project")
    get.add_argument("id", help="An id from `altocue music search`, such as ccmixter:16626")
    get.add_argument("--project", "-p", default=".", help="Project folder")
    get.add_argument("--no-bed", action="store_true", help="Keep the file, and leave `music` in motion.json as it is")


def bundled() -> list[dict[str, Any]]:
    return json.loads((DATA / "tracks.json").read_text())


def jingle(row: dict[str, Any]) -> Result:
    return Result(
        row["name"], row["use"], "audio", "", "", row["seconds"], 0, 0, row["licence"], base.CC0_URL, "", "kenney", row["url"], author=row["author"],
        warnings=[f"A {row['seconds']:.1f} s sting. The mix loops music, so it suits an ending or a cue, not a bed."],
    )  # fmt: skip


def jingles(query: str) -> list[Result]:
    words = set(re.findall(r"[a-z0-9-]+", query.lower()))
    return [jingle(row) for row in bundled() if words & {*row["tags"], *row["name"].split("-")}]


def search(query: str, *, limit: int = 6) -> list[Result]:
    """Every source that answers. One that fails is named, and the others still count."""
    results: list[Result] = []
    for module in SEARCHED:
        try:
            results += module.search(query, limit=limit)
        except AltocueError as error:
            print(f"Skipped {module.__name__.rsplit('.', 1)[-1]}: {str(error).splitlines()[0]}")
    return results + jingles(query)


def show(found: Result) -> list[str]:
    length = f"{int(found.duration // 60)}:{int(found.duration % 60):02d}" if found.duration >= 60 else f"{found.duration:.1f} s"
    lines = [f"{found.source}:{found.id}  {length}  {base.plain(found.title)}" + (f" by {base.plain(found.author)}" if found.author else "")]
    lines.append(f"    Licence: {found.licence}" + (f", {base.credit_field(found.licence_url)}" if found.licence_url else ""))
    lines += [f"    Credit owed: {line}" for line in base.clean_credit(found.credit).splitlines()] or ["    Credit owed: none"]
    lines += [f"    Note: {base.credit_field(line, 300)}" for line in found.warnings]
    return lines


def find(item: str) -> Result:
    source, _, number = item.partition(":")
    found = None
    if source == "kenney":
        found = next((jingle(row) for row in bundled() if row["name"] == number), None)
    elif source == "ccmixter" and number.isdigit():
        found = ccmixter.find(number)
    elif source == "incompetech":
        found = incompetech.find(number)
    if found is None:
        raise AltocueError(f"No track {item}.", fix="Run `altocue music search <words>`, then use an id it prints.")
    return found


def point_motion_at(project: Project, relative: str) -> str | None:
    """Only `music` changes. Everything else in motion.json is someone's work."""
    path = project.root / "motion.json"
    motion = json.loads(path.read_text()) if path.exists() else {}
    before = (motion.get("music") or {}).get("path")
    write_json(path, {**motion, "music": {"path": relative}})
    return before if before != relative else None


def fetch(project: Project, item: str, *, bed: bool = True) -> tuple[Result, str]:
    found = find(item)
    if not base.clear(found.licence):
        raise AltocueError(f"{found.title} has no clear licence, so it cannot join a project.", fix="Pick another track.")
    suffix = ".ogg" if found.source == "kenney" else ".mp3"
    relative = f"music/{found.source}-{base.slug(found.id)}{suffix}"
    dest = project.root / relative
    dest.parent.mkdir(exist_ok=True)
    if found.source == "kenney":
        shutil.copyfile(DATA / f"{found.id}.ogg", dest)
    else:
        http.download(found.url, dest, headers={"Referer": found.source_url})  # ccMixter only serves files to a visit from the track's page
        base.require_audio(dest)
    credits.record(
        project, file=relative, source=found.source, source_url=found.source_url, licence=found.licence, licence_url=found.licence_url,
        credit=found.credit, kind="audio", title=found.title, warnings=found.warnings,
    )  # fmt: skip
    if bed:
        replaced = point_motion_at(project, relative)
        if replaced:
            print(f"motion.json played {replaced}. It now plays {relative}.")
    return found, relative


def run(args: argparse.Namespace) -> int:
    if args.action == "search":
        query = " ".join(args.words)
        print(f"Searching ccMixter and Incompetech. Only the search words are sent.\n{base.UNTRUSTED_NOTE}\n")
        results = search(query, limit=args.limit)
        for found in results:
            print("\n".join(show(found)))
        print(
            f"\n{len(results)} tracks. Nothing has been downloaded. Take one with `altocue music get <id> -p .`."
            if results
            else "No tracks found. Try fewer or plainer words."
        )
        return 0
    project = Project.open(args.project)
    found, relative = fetch(project, args.id, bed=not args.no_bed)
    print(f"{base.UNTRUSTED_NOTE}\nSaved {relative}.")
    print("\n".join(show(found)))
    if not args.no_bed:
        print(f"motion.json `music` now points at {relative}. The mix ducks it under the voice in the next render.")
        if found.duration < SHORT:
            print(f"This track lasts {found.duration:.1f} s and the mix loops music, so you will hear the loop. Use --no-bed to keep the file without making it the bed.")
    if found.credit:
        print("The credit lands in out/credits.txt at render. `altocue credits` prints it.")
    return 0
