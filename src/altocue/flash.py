"""Flashes in a finished film, reported the way the film reports read. The measuring is `qa.flashes`, which follows WCAG 2.2 criterion 2.3.1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ffmpeg
from .qa import flashes


def measure(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    found = flashes.scan(flashes.read_frames(target), ffmpeg.probe_clip(target).fps or 30.0)
    return {"peak_per_second": found.worst, "at": round(float(found.at), 3), "limit": flashes.MOST, "passed": not found.over}
