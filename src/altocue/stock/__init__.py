"""Licensed stock. One result shape, one small module per source, and no key needed to start."""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .base import Result

# Each source's module, what it holds, and the key it needs. Excluded sources stay out of this table on purpose.
SOURCES: dict[str, tuple[tuple[str, ...], str]] = {
    "nasa": (("video", "photo"), ""),
    "wikimedia": (("video", "photo"), ""),
    "met": (("photo",), ""),
    "cleveland": (("photo",), ""),
    "pixabay": (("video", "photo"), "PIXABAY_API_KEY"),
    "pexels": (("video", "photo"), "PEXELS_API_KEY"),
    "archive": (("video",), ""),
}
FREE_VIDEO = ("nasa", "wikimedia")
FREE_PHOTOS = ("met", "cleveland", "nasa", "wikimedia")


def plan(choice: str, kind: str | None, env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """Which source to ask for what, in order. `auto` leads with Pixabay when a key exists, and never uses the Internet Archive."""
    env = os.environ if env is None else env
    if choice != "auto":
        return [(choice, held) for held in SOURCES[choice][0] if kind in (None, held)]
    keyed = ["pixabay"] if env.get("PIXABAY_API_KEY") else []
    video = [(name, "video") for name in (*keyed, *FREE_VIDEO)]
    photos = [(name, "photo") for name in (*keyed, *FREE_PHOTOS)]
    return [step for step in [*video, *photos] if kind in (None, step[1])]


def search(name: str, query: str, *, kind: str, shape: str, limit: int) -> list[Result]:
    module = importlib.import_module(f"altocue.stock.{name}")
    key = os.environ.get(SOURCES[name][1], "") if SOURCES[name][1] else ""
    return module.search(query, kind=kind, shape=shape, limit=limit, key=key)


def option(found: Result) -> dict[str, Any]:
    """A result as a B-roll option. The option's own `id` is its place in the review, so the source's id moves aside."""
    data = asdict(found)
    data["item"] = data.pop("id")
    data["page"] = found.source_url
    return data
