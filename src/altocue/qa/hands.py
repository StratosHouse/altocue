"""Where the hands are in a frame, through Apple Vision on a Mac. Elsewhere there is no detector, and the check says so."""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from .. import swift

SWIFT = Path(__file__).with_name("hands.swift")
NO_DETECTOR = "No hand detector on this machine. It uses Apple Vision, so it runs on a Mac with the Xcode command line tools."


@lru_cache(maxsize=1)
def binary() -> Path | None:
    return swift.compiled(SWIFT, "hands")


def detector() -> tuple[bool, str]:
    return (True, "") if binary() else (False, NO_DETECTOR)


def find(images: list[Path]) -> dict[str, list[list[float]]]:
    """Hand boxes by image name, as x, y, w, h from the top left, 0 to 1. An image Vision could not read is left out."""
    tool = binary()
    if tool is None or not images:
        return {}
    done = subprocess.run([str(tool), *[str(path) for path in images]], capture_output=True, text=True, errors="replace")
    found: dict[str, list[list[float]]] = {}
    for line in done.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "hands" in record:
            found[Path(record["file"]).name] = record["hands"]
    return found
