"""NASA Images and Video. No key. The API gives no licence field, so third-party material is screened by its words."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from . import base
from .base import Result

SEARCH = "https://images-api.nasa.gov/search"
ASSET = "https://images-api.nasa.gov/asset"
DETAILS = "https://images.nasa.gov/details"
LICENCE = "NASA media guidelines"
LICENCE_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
VIDEO_ORDER = ("~medium.mp4", "~mobile.mp4", "~small.mp4", "~preview.mp4", "~orig.mp4")
THIRD_PARTY = re.compile(
    r"copyright|courtesy|©|\breuters\b|\bgetty\b|associated press|\bap photo\b|\bafp\b|shutterstock|all rights reserved|used (?:with|by) permission", re.IGNORECASE
)


def third_party(data: dict[str, Any]) -> bool:
    """NASA says material it does not own names its holder, so any such word rules an item out."""
    keywords = data.get("keywords") or []
    words = " ".join(str(data.get(key, "")) for key in ("title", "description", "description_508", "photographer", "secondary_creator"))
    return bool(THIRD_PARTY.search(f"{words} {' '.join(keywords) if isinstance(keywords, list) else keywords}"))


def credit_for(data: dict[str, Any]) -> str:
    named = base.credit_field(data.get("photographer", ""))
    return named if named.upper().startswith("NASA") else "NASA"


def warnings_for(data: dict[str, Any]) -> list[str]:
    also = base.credit_field(data.get("secondary_creator", ""), 100)
    return [base.PEOPLE_WARNING, *([f"Also credited on the page: {also}. Check it is NASA's to share."] if also else [])]


def preview_of(links: list[dict[str, Any]]) -> str:
    previews = [link for link in links if link.get("render") == "image"]
    chosen = next((link for link in previews if link.get("rel") == "preview"), previews[0] if previews else {})
    return base.secure(str(chosen.get("href", "")))


def video_file(nasa_id: str) -> str:
    listing = base.answer("NASA", base.fetch("nasa", f"{ASSET}/{nasa_id}")).get("collection", {}).get("items", [])
    links = [base.secure(str(item.get("href", ""))) for item in listing]
    return next((link for tail in VIDEO_ORDER for link in links if link.endswith(tail)), "")


def measure(href: str) -> tuple[int, int, float]:
    """Width, height and length from the file's metadata. A clip without them still works, it just ranks lower."""
    data = base.fetch("nasa", href.rsplit("/", 1)[0] + "/metadata.json", attempts=1).json()
    wide, _, tall = str(data.get("Composite:ImageSize", "")).partition("x") if isinstance(data, dict) else ("", "", "")
    length = base.seconds(data.get("QuickTime:Duration", "")) if isinstance(data, dict) else 0.0
    return (int(wide), int(tall), length) if wide.isdigit() and tall.isdigit() else (0, 0, length)


def video_result(item: dict[str, Any], data: dict[str, Any]) -> Result | None:
    nasa_id = data["nasa_id"]
    url = video_file(nasa_id)
    if not url:
        return None
    width, height, length = measure(str(item.get("href", "")))
    if length > base.LONGEST:
        return None
    return Result(
        nasa_id, base.credit_field(data.get("title", nasa_id)), "video", url, preview_of(item.get("links", [])), length, width, height,
        LICENCE, LICENCE_URL, credit_for(data), "nasa", f"{DETAILS}/{nasa_id}", author="NASA", warnings=warnings_for(data),
    )  # fmt: skip


def photo_result(item: dict[str, Any], data: dict[str, Any], shape: str) -> Result | None:
    links = [link for link in item.get("links", []) if str(link.get("href", "")).lower().endswith((".jpg", ".jpeg", ".png")) and link.get("width") and link.get("height")]
    if not links:
        return None
    links.sort(key=lambda link: int(link["width"]) * int(link["height"]))
    chosen = next((link for link in links if base.fills(int(link["width"]), int(link["height"]), shape)), links[-1])
    nasa_id = data["nasa_id"]
    return Result(
        nasa_id, base.credit_field(data.get("title", nasa_id)), "photo", base.secure(chosen["href"]), preview_of(item["links"]), 0.0,
        int(chosen["width"]), int(chosen["height"]),
        LICENCE, LICENCE_URL, credit_for(data), "nasa", f"{DETAILS}/{nasa_id}", author="NASA", size=int(chosen.get("size", 0)), warnings=warnings_for(data),
    )  # fmt: skip


def search(query: str, *, kind: str = "video", shape: str = "landscape", limit: int = 4, key: str = "") -> list[Result]:
    params = {"q": query, "media_type": "image" if kind == "photo" else "video", "page_size": limit * 4, "page": 1}
    items = base.answer("NASA", base.fetch("nasa", f"{SEARCH}?{urlencode(params)}")).get("collection", {}).get("items", [])
    results: list[Result] = []
    for item in items:
        data = (item.get("data") or [{}])[0]
        if not data.get("nasa_id") or third_party(data):
            continue
        found = photo_result(item, data, shape) if kind == "photo" else video_result(item, data)
        if found:
            results.append(found)
        if len(results) == limit:
            break
    return base.rank(results, shape)
