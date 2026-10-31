"""Fails if anything private, unlicensed or heavy is about to be published."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = re.compile(
    r"/Users/|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|IMG_\d{4}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}",
    re.IGNORECASE,
)
PRIVATE_LIST = Path.home() / ".altocue" / "blocklist.txt"
ALLOWED_FILES = {"scripts/check_repo.py", "uv.lock", "src/altocue/data/renderer/package-lock.json", "src/altocue/data/models/LICENSE"}
TEXT = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".html", ".css", ".js", ".txt", ".svg", ".cube"}
MAX_FILE_BYTES = 2_000_000
MAX_ASSET_BYTES = 6_000_000
BANNED_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".wav", ".mp3", ".aiff"}


def tracked() -> list[Path]:
    listing = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / line for line in listing.stdout.splitlines()]


def private_names() -> re.Pattern[str] | None:
    """Names that must never ship live outside this public repo, so this file cannot leak them."""
    raw = os.environ.get("ALTOCUE_BLOCKLIST") or (PRIVATE_LIST.read_text() if PRIVATE_LIST.exists() else "")
    terms = [line.strip() for line in raw.splitlines() if line.strip()]
    return re.compile("|".join(terms), re.IGNORECASE) if terms else None


def main() -> int:
    problems = []
    names = private_names()
    if names is None:
        print("No private blocklist here, so names were not checked. Maintainers set ALTOCUE_BLOCKLIST.")
    for path in tracked():
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            continue
        if path.suffix.lower() in BANNED_SUFFIXES and not rel.startswith("src/altocue/data/sfx/"):
            problems.append(f"{rel}: media does not belong in git")
        limit = MAX_ASSET_BYTES if rel.startswith("assets/") else MAX_FILE_BYTES
        if path.stat().st_size > limit:
            problems.append(f"{rel}: over {limit} bytes")
        if path.suffix.lower() not in TEXT:
            continue
        text = path.read_text(errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            found = (None if rel in ALLOWED_FILES else BLOCKED.search(line)) or (names.search(line) if names else None)
            if found:
                problems.append(f"{rel}:{number}: blocked term {found.group(0)!r}")
            if "—" in line and rel not in ALLOWED_FILES:
                problems.append(f"{rel}:{number}: em dash")
    print("\n".join(problems) or "Repo check passed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
