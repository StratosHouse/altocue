"""Single sounds from open packs. The pack downloads once into a cache, is held to its sha256, and only the one file reaches a project.

The packs are listed in `library/sfx/packs.json` and their sounds in `library/sfx/index.json`, written by
`scripts/index_sfx.py`. Each sound becomes a catalog row of kind sfx with the pack's address and hash on it, so a
downloaded catalog carries everything a fetch needs and the repo holds no audio.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import AltocueError
from ..generate import http
from ..project import Project
from . import credits

AUDIO = {".ogg", ".wav", ".mp3", ".flac", ".aif", ".aiff", ".m4a", ".opus"}
FOLDER = "sfx"
MAX_PACK = 500_000_000

# First match wins, so the narrow words come before the broad ones. Each category is a word an editor searches for.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("jingle", ("jingle", "jingles", "fanfare", "sting", "victory", "gameover", "game-over", "levelup", "level-up")),
    ("voice", ("voice", "voiceover", "announcer", "male", "female", "speech")),
    ("typing", ("typing", "typewriter", "keyboard", "keypress", "key-press", "keystroke")),
    ("notification", ("notification", "notify", "alert", "ding", "chime", "bell", "message", "confirmation", "confirm", "success", "error", "beep", "tone")),
    ("camera", ("camera", "shutter", "snapshot", "photo")),
    ("glitch", ("glitch", "static", "noise", "interference", "distort", "computer", "digital", "data", "bitcrush")),
    ("riser", ("riser", "rise", "rising", "powerup", "power-up", "maximize", "charge", "upgrade", "sweep-up", "build")),
    ("whoosh", ("whoosh", "swoosh", "woosh", "swish", "swing", "whip", "wind", "fly", "pass")),
    ("swipe", ("swipe", "slide", "scroll", "open", "close", "drawer", "minimize", "toggle", "switch")),
    ("pop", ("pop", "bubble", "drop", "plop", "bloop", "blip")),
    ("click", ("click", "tick", "tap", "select", "button", "press", "mouse")),
    ("impact", ("impact", "hit", "punch", "thud", "slam", "knock", "explosion", "boom", "crash", "smash", "bang", "kick", "metal", "clang")),
    ("laser", ("laser", "zap", "shot", "shoot", "phaser", "blaster", "gun", "weapon")),
    ("foley", ("footstep", "step", "door", "cloth", "paper", "page", "book", "card", "dice", "chip", "coin", "glass", "chop", "handle", "creak")),
    ("ambience", ("ambience", "ambient", "ambiance", "loop", "engine", "hum", "drone", "room", "machine", "water", "rain", "forest", "space")),
]
UI_WORDS = {"click", "pop", "swipe", "notification", "typing"}


def words(path: str) -> list[str]:
    """camelCase, snake_case and numbers all split into plain words, so `impactPunch_heavy_002` reads as impact punch heavy."""
    stem = str(PurePosixPath(path).with_suffix(""))
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", stem)
    return [word for word in re.split(r"[^a-z0-9]+", spaced.lower()) if word and not word.isdigit()]


def categorise(path: str, hints: list[str] = ()) -> tuple[str, list[str]]:
    """A clear category from the file's own name first, then its folders, then what the pack says it holds. Never a guess past that."""
    name, folders = words(PurePosixPath(path).name), words(str(PurePosixPath(path).parent))
    for found in (name, folders, list(hints)):
        joined = "-".join(found)
        for category, keys in CATEGORIES:
            if any(key in found or (("-" in key) and key in joined) for key in keys):
                tags = [category, *(["ui"] if category in UI_WORDS else [])]
                return category, list(dict.fromkeys(tags + name + folders))
    return "other", list(dict.fromkeys(name + folders))


def title_of(path: str) -> str:
    """`impactPunch_heavy_002.ogg` becomes Impact punch heavy 002, so a title says what the file is and which take."""
    stem = PurePosixPath(path).stem
    numbers = re.findall(r"\d+", stem)
    title = " ".join(words(PurePosixPath(path).name)).capitalize() or "Sound"
    return f"{title} {numbers[-1]}" if numbers else title


def slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def sha256_of(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hashed.update(block)
    return hashed.hexdigest()


def cache_dir() -> Path:
    return Path.home() / ".altocue" / "sfx" / "packs"


def download(url: str, dest: Path) -> Path:
    """One place that touches the network, so tests swap it for a local copy."""
    return http.download(url, dest, timeout=600, limit=MAX_PACK)


USER_FIX = "The maker may have published a new version of the pack. Run `altocue library update` for a catalog that matches it, or pick another sound."


def archive(pack: str, url: str, sha256: str, folder: Path | None = None, fix: str = USER_FIX) -> Path:
    """The pack from the cache when it is there and still matches its hash, otherwise downloaded once and checked."""
    folder = folder or cache_dir()
    path = folder / f"{slug(pack)}-{sha256[:16]}.zip"
    if path.is_file() and sha256_of(path) == sha256:
        return path
    path.unlink(missing_ok=True)
    download(url, path)
    found = sha256_of(path)
    if found != sha256:
        path.unlink(missing_ok=True)
        raise AltocueError(
            f"The pack {pack} from {url} does not match its recorded sha256 ({found[:12]}... instead of {sha256[:12]}...).",
            fix=fix,
        )
    return path


def member(path: Path, inner: str) -> bytes:
    """Read one file from a pack by its exact name. A missing name is an error, never a near match."""
    try:
        with zipfile.ZipFile(path) as pack:
            return pack.read(inner)
    except KeyError as error:
        raise AltocueError(f"{inner} is not in the pack {path.name}.", fix="Run `altocue library update` for a catalog that matches the pack.") from error
    except zipfile.BadZipFile as error:
        path.unlink(missing_ok=True)
        raise AltocueError(f"The pack {path.name} is not a zip file, so it was removed from the cache.", fix="Run the command again to download it afresh.") from error


def notice(row: dict[str, Any]) -> str:
    credit = row.get("credit") or "No credit is required. Naming the maker is welcome."
    return (
        f"{row['title']} ({row['path']})\n"
        f"From the pack {row.get('pack_title') or row['pack']} by {row.get('author') or 'its maker'}: {row['url']}\n"
        f"Licence: {row['licence']}, {row['licence_url']}\n"
        f"Credit: {credit}\n"
        f"sha256 of this file: {row['sha256']}\n"
        f"Fetched by Altocue. The pack itself was not copied into this project.\n"
    )


def fetch(project: Project, row: dict[str, Any]) -> str:
    """Extract one indexed sound into `sfx/`, with its licence notice beside it, and record it in credits.json."""
    pack = archive(row["pack"], row["archive"], row["archive_sha256"])
    data = member(pack, row["path"])
    found = hashlib.sha256(data).hexdigest()
    if found != row["sha256"]:
        raise AltocueError(f"{row['path']} in the pack {row['pack']} does not match its recorded sha256.", fix="Run `altocue library update`, then try again.")
    suffix = PurePosixPath(row["path"]).suffix.lower()
    if suffix not in AUDIO:
        raise AltocueError(f"{row['path']} is not a sound file Altocue reads.", fix="Pick another sound with `altocue library search --kind sfx <words>`.")
    folder = project.root / FOLDER
    folder.mkdir(exist_ok=True)
    relative = f"{FOLDER}/{row['id']}{suffix}"
    (project.root / relative).write_bytes(data)
    (folder / f"{row['id']}.LICENSE.txt").write_text(notice(row))
    credits.record(
        project, file=relative, source=row.get("pack_source") or row["pack"], source_url=row["url"], licence=row["licence"],
        licence_url=row["licence_url"], credit=row.get("credit") or "", kind="audio", title=row["title"],
    )  # fmt: skip
    return relative


def indexed(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    from ..library import SOUND_SOURCE

    return [row for row in catalog["entries"] if row.get("source") == SOUND_SOURCE]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None
