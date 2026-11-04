"""Pixabay, the best route for generic footage. A free key. The API asks that every search is cached for a day."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from . import base
from .base import Result

VIDEOS = "https://pixabay.com/api/videos/"
PHOTOS = "https://pixabay.com/api/"
LICENCE = "Pixabay Content License"
LICENCE_URL = "https://pixabay.com/service/license-summary/"
PER_PAGE = 20  # the video API cannot filter on shape, so rank from a wider set
MIN_SIDE = {"portrait": ("min_height", 1080), "landscape": ("min_width", 1280), "square": ("min_width", 1080)}
ORIENTATION = {"portrait": "vertical", "landscape": "horizontal", "square": "all"}


def hits(endpoint: str, params: dict[str, Any], key: str, cache_key: str) -> list[dict[str, Any]]:
    saved = base.cache_read("pixabay", cache_key)
    if saved is not None:
        return saved
    url = f"{endpoint}?{urlencode({'key': key, 'per_page': PER_PAGE, 'safesearch': 'true', **params})}"
    found = base.answer("Pixabay", base.fetch("pixabay", url), fix="Check PIXABAY_API_KEY.").get("hits", [])
    base.cache_write("pixabay", cache_key, found)
    return found


def rendition(videos: dict[str, Any], shape: str) -> dict[str, Any]:
    """The smallest file that fills the canvas, else the largest there is. `large` can come back empty."""
    files = [row for row in (videos.get(name, {}) for name in ("tiny", "small", "medium", "large")) if row.get("url")]
    return next((row for row in files if base.fills(int(row.get("width", 0)), int(row.get("height", 0)), shape)), files[-1] if files else {})


def video(hit: dict[str, Any], shape: str) -> Result | None:
    chosen = rendition(hit.get("videos", {}), shape)
    if not chosen:
        return None
    number = hit.get("id")
    return Result(
        str(number), f"Pixabay {number}", "video", chosen["url"], str(chosen.get("thumbnail", "")), float(hit.get("duration") or 0),
        int(chosen.get("width", 0)), int(chosen.get("height", 0)), LICENCE, LICENCE_URL, "", "pixabay", str(hit.get("pageURL", "")),
        author=base.credit_field(hit.get("user", "")), size=int(chosen.get("size", 0)), warnings=[base.PEOPLE_WARNING],
    )  # fmt: skip


def photo(hit: dict[str, Any]) -> Result | None:
    url = hit.get("largeImageURL")
    if not url:
        return None
    number = hit.get("id")
    return Result(
        str(number), f"Pixabay {number}", "photo", url, str(hit.get("webformatURL", "")), 0.0, int(hit.get("imageWidth", 0)), int(hit.get("imageHeight", 0)),
        LICENCE, LICENCE_URL, "", "pixabay", str(hit.get("pageURL", "")), author=base.credit_field(hit.get("user", "")), warnings=[base.PEOPLE_WARNING],
    )  # fmt: skip


def search(query: str, *, kind: str = "video", shape: str = "landscape", limit: int = 4, key: str = "", endpoint: str = "") -> list[Result]:
    words = query[:100]
    if kind == "photo":
        found = hits(endpoint or PHOTOS, {"q": words, "image_type": "photo", "orientation": ORIENTATION[shape]}, key, f"photo|{shape}|{words}")
        return [result for result in map(photo, found) if result][:limit]
    side, size = MIN_SIDE[shape]
    found = hits(endpoint or VIDEOS, {"q": words, side: size}, key, f"video|{shape}|{words}")
    return base.rank([result for result in (video(hit, shape) for hit in found) if result], shape)[:limit]
