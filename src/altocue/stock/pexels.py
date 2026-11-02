"""Pexels. Needs an existing key, because Pexels has paused new ones."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from . import base
from .base import Result

VIDEOS = "https://api.pexels.com/v1/videos/search"
PHOTOS = "https://api.pexels.com/v1/search"
LICENCE = "Pexels License"
LICENCE_URL = "https://www.pexels.com/license/"


def video(item: dict[str, Any]) -> Result | None:
    files = [row for row in item.get("video_files", []) if row.get("file_type") == "video/mp4" and row.get("width") and row.get("height")]
    best = base.nearest_1080(files)
    if not best:
        return None
    number = item.get("id")
    return Result(
        str(number), f"Pexels {number}", "video", best["link"], str(item.get("image", "")), float(item.get("duration") or 0), int(best["width"]), int(best["height"]),
        LICENCE, LICENCE_URL, "", "pexels", str(item.get("url", "")), author=base.credit_field(item.get("user", {}).get("name", "")), warnings=[base.PEOPLE_WARNING],
    )  # fmt: skip


def photo(item: dict[str, Any]) -> Result | None:
    sources = item.get("src", {})
    url = sources.get("large2x") or sources.get("original")
    if not url:
        return None
    number = item.get("id")
    return Result(
        str(number), f"Pexels {number}", "photo", url, str(sources.get("medium", "")), 0.0, int(item.get("width", 0)), int(item.get("height", 0)),
        LICENCE, LICENCE_URL, "", "pexels", str(item.get("url", "")), author=base.credit_field(item.get("photographer", "")), warnings=[base.PEOPLE_WARNING],
    )  # fmt: skip


def search(query: str, *, kind: str = "video", shape: str = "landscape", limit: int = 4, key: str = "", endpoint: str = "") -> list[Result]:
    url = f"{endpoint or (PHOTOS if kind == 'photo' else VIDEOS)}?{urlencode({'query': query, 'orientation': shape, 'size': 'medium', 'per_page': limit})}"
    data = base.answer("Pexels", base.fetch("pexels", url, headers={"Authorization": key}), fix="Check PEXELS_API_KEY.")
    if kind == "photo":
        return [result for result in map(photo, data.get("photos", [])) if result]
    return [result for result in map(video, data.get("videos", [])) if result]
