"""Sound packs you bought or recorded yourself. Altocue points at the folder, copies a sound into one project when asked,
and keeps it out of anything public. It never uploads, shares or bundles a pack."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project, write_json
from . import credits

REGISTRY = Path.home() / ".altocue" / "packs.json"
AUDIO = (".wav", ".aif", ".aiff", ".flac", ".ogg", ".mp3", ".m4a")
PRIVATE = Path("sfx") / "private"  # inside a project
IGNORE = "# Licensed sounds. Never commit, upload or share these.\n*\n!.gitignore\n"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def packs() -> dict[str, dict[str, Any]]:
    return json.loads(REGISTRY.read_text()).get("packs", {}) if REGISTRY.exists() else {}


def add(name: str, folder: str | Path, licence: str) -> dict[str, Any]:
    """Register a folder under a short name. The licence is your own words, such as "Ocular Sounds, single user"."""
    key, root = slug(name), Path(folder).expanduser().resolve()
    if not key:
        raise AltocueError(f"{name!r} cannot be a pack name.", fix="Use letters and numbers, such as vector.")
    if not root.is_dir():
        raise AltocueError(f"No folder at {root}.", fix="Pass the folder that holds the pack's sound files.")
    if not licence.strip():
        raise AltocueError("A pack needs its licence.", fix='Add --licence "who sold it, and for what use".')
    found = sounds(root)
    if not found:
        raise AltocueError(f"No sound files in {root}.", fix=f"A pack holds {', '.join(AUDIO)} files.")
    entry = {"folder": str(root), "licence": licence.strip()}
    write_json(REGISTRY, {"packs": {**packs(), key: entry}})
    return {"name": key, **entry, "sounds": len(found)}


def remove(name: str) -> None:
    """Forget a pack. Its files and any project copies stay where they are."""
    known = packs()
    if slug(name) not in known:
        raise AltocueError(f"No pack named {name}.", fix="Run `altocue sfx packs`.")
    known.pop(slug(name))
    write_json(REGISTRY, {"packs": known})


def sounds(root: Path) -> dict[str, Path]:
    """Every sound in a folder by a short name. Two files with one name keep their folder in it."""
    found: dict[str, Path] = {}
    for path in sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO and not p.name.startswith(".")):
        name = slug(path.stem)
        if name in found:
            name = slug(f"{path.parent.name} {path.stem}")
        found.setdefault(name, path)
    return found


def entry(name: str) -> dict[str, Any]:
    pack = packs().get(slug(name))
    if pack is None:
        raise AltocueError(f"No pack named {name}.", fix="Run `altocue sfx packs`, or `altocue sfx pack`.")
    if not Path(pack["folder"]).is_dir():
        raise AltocueError(f"Pack {name} was at {pack['folder']}, and that folder is gone.", fix="Run `altocue sfx pack` with its new folder.")
    return pack


def matches(query: str) -> list[tuple[str, str, Path]]:
    """Pack sounds whose name has any of the words, as (pack, sound, file)."""
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    rows = []
    for name, pack in packs().items():
        if not Path(pack["folder"]).is_dir():
            continue
        rows += [(name, sound, path) for sound, path in sounds(Path(pack["folder"])).items() if words & set(sound.split("-"))]
    return rows


def find(item: str) -> tuple[str, str, Path] | None:
    """`pack:sound` to its file, or None when `item` names no registered pack."""
    name, _, sound = item.partition(":")
    if not sound or slug(name) not in packs():
        return None
    found = sounds(Path(entry(name)["folder"]))
    if slug(sound) not in found:
        close = [known for known in found if slug(sound) in known][:5]
        raise AltocueError(f"Pack {name} has no sound named {sound}.", fix=f"Try {', '.join(close)}." if close else f"Run `altocue sfx search {sound}`.")
    return slug(name), slug(sound), found[slug(sound)]


def fetch(project: Project, item: str) -> str:
    """Copy one pack sound into this project's private folder, and record its licence. Returns its project path."""
    found = find(item)
    if found is None:
        raise AltocueError(f"{item} names no pack.", fix="Use pack:sound, such as vector:keyboard-01.")
    name, sound, path = found
    folder = project.root / PRIVATE
    (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / ".gitignore").write_text(IGNORE)
    relative = (PRIVATE / name / f"{sound}{path.suffix.lower()}").as_posix()
    shutil.copyfile(path, project.root / relative)
    credits.record(project, file=relative, source=f"pack:{name}", source_url="", licence=entry(name)["licence"], kind="audio", title=sound, licensed=True)
    return relative
