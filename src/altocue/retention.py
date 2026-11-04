"""Data with a time limit, and the purge every `altocue` command runs before anything else.

YouTube's Developer Policies cap how long API statistics may be kept. Altocue has no background
process, so the deadline is kept by whichever command runs next, wherever it runs. This module
uses the standard library only, so checking costs every command almost nothing.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

YOUTUBE_LIST = "youtube-list.json"
YOUTUBE_KEEP_DAYS = 30


def today() -> date:
    return date.today()


def register_path() -> Path:
    """Every saved YouTube list, by path, so a command run anywhere can find one that is past its date."""
    return Path.home() / ".altocue" / "youtube-lists.json"


def _registered() -> dict[str, str]:
    path = register_path()
    try:
        return dict(json.loads(path.read_text()).get("lists", {})) if path.exists() else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _write(lists: dict[str, str]) -> None:
    path = register_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lists": lists}, indent=2) + "\n")


def remember(saved: Path, expires: date) -> None:
    """Record a saved list, so it is deleted after `expires` whatever runs next."""
    _write({**_registered(), str(saved.resolve()): expires.isoformat()})


def purge_expired(folder: Path, when: date) -> None:
    path = folder / YOUTUBE_LIST
    if not path.exists():
        return
    try:
        expires = date.fromisoformat(str(json.loads(path.read_text()).get("expires", ""))[:10])
    except (OSError, ValueError, AttributeError):
        expires = None
    if expires is None or when > expires:
        path.unlink()
        print(f"Deleted {path}: YouTube statistics may not be kept past {YOUTUBE_KEEP_DAYS} days.")


def purge_everywhere(project: str | Path | None = None, as_of: date | None = None) -> None:
    """Delete every saved YouTube list past its date: the project's own, and each one recorded on this machine."""
    when = as_of or today()
    lists = _registered()
    folders = {Path(saved).parent for saved in lists}
    if project is not None:
        folders.add(Path(project).expanduser().resolve() / "bench")
    for folder in sorted(folders):
        purge_expired(folder, when)
    kept = {saved: expires for saved, expires in lists.items() if Path(saved).exists()}
    if kept != lists:
        _write(kept)
