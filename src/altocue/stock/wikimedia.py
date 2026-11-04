"""Wikimedia Commons. No key. The server filters on licence templates, then each file's own licence is read."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from ..errors import AltocueError
from . import base
from .base import Result

API = "https://commons.wikimedia.org/w/api.php"
TEMPLATES = "Cc-zero|PD-Layout|Cc-by-layout|Cc-by-sa-layout"
METADATA = "Artist|LicenseShortName|License|LicenseUrl|UsageTerms|Attribution|Copyrighted|NonFree|Restrictions"
GAP = 0.25  # the robot policy asks for one Action API call at a time, under 5 per second
SLOW = 5.0  # and for a pause after a slow reply
MIN_WIDTH = 720
PHOTO_WIDTH = 2160
MOST_BYTES = 300_000_000


def ask(params: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    query = {"action": "query", "format": "json", "formatversion": 2, "maxlag": 5, **params}
    data = base.answer("Wikimedia Commons", base.fetch("wikimedia", f"{API}?{urlencode(query)}", gap=GAP))
    if time.monotonic() - started > SLOW:
        time.sleep(SLOW)
    if isinstance(data, dict) and data.get("error"):
        raise AltocueError(f"Wikimedia Commons refused the search: {data['error'].get('code', 'error')}.", fix="Try again in a minute.")
    return data


def meta(info: dict[str, Any], name: str) -> str:
    return base.strip_html(info.get("extmetadata", {}).get(name, {}).get("value", ""))


def title_of(page: dict[str, Any]) -> str:
    name = str(page.get("title", "")).removeprefix("File:")
    return base.credit_field(name.rsplit(".", 1)[0] if "." in name else name)


def video_file(info: dict[str, Any]) -> str:
    """A transcode near 1080 costs far less than a 4K original. Only WebM and MP4 count, because the old .mov ones are poor."""
    files = [row for row in info.get("derivatives", []) if str(row.get("type", "")).startswith(("video/webm", "video/mp4")) and row.get("src")]
    chosen = base.nearest_1080(files)
    if chosen and min(int(chosen["width"]), int(chosen["height"])) >= 480:
        return base.bare(chosen["src"])
    return base.bare(str(info.get("url", ""))) if int(info.get("size", 0)) <= MOST_BYTES else ""


def result(page: dict[str, Any], kind: str) -> Result | None:
    info = (page.get("videoinfo") or page.get("imageinfo") or [{}])[0]
    licence = base.licence_id(meta(info, "LicenseShortName")) or base.licence_id(meta(info, "License"))
    if not licence or meta(info, "NonFree").lower() == "true":
        return None
    length = float(info.get("duration") or 0.0)
    if kind == "video" and not (str(info.get("mime", "")).startswith("video/") and length <= base.LONGEST):
        return None
    if kind == "photo" and not str(info.get("mime", "")).startswith("image/"):
        return None
    url = video_file(info) if kind == "video" else base.bare(str(info.get("thumburl") or info.get("url", "")))
    page_url = str(info.get("descriptionurl", ""))
    author = base.credit_field(meta(info, "Attribution") or meta(info, "Artist"))
    if not url or not page_url or (base.needs_credit(licence) and not author):
        return None
    title = title_of(page)
    warnings = [base.PEOPLE_WARNING]
    if base.share_alike(licence):
        warnings.append(base.SHARE_ALIKE_WARNING)
    if restrictions := meta(info, "Restrictions"):
        warnings.append(f"Commons marks this file: {base.credit_field(restrictions, 80)}.")
    licence_url = base.secure(meta(info, "LicenseUrl")) or (base.CC0_URL if licence == base.CC0 else page_url)
    credit = base.credit_line(title, author, licence, page_url) if base.needs_credit(licence) else ""
    return Result(
        title, title, kind, url, base.bare(str(info.get("thumburl", ""))), length, int(info.get("width", 0)), int(info.get("height", 0)),
        licence, licence_url, credit, "wikimedia", page_url, author=author,
        size=int(info.get("size", 0)) if url == base.bare(str(info.get("url", ""))) else 0, warnings=warnings,
    )  # fmt: skip


def search(query: str, *, kind: str = "video", shape: str = "landscape", limit: int = 4, key: str = "") -> list[Result]:
    filetype = "video" if kind == "video" else "bitmap"
    words = f'{query} filetype:{filetype} filew:>{MIN_WIDTH - 1} hastemplate:"{TEMPLATES}"'
    prop = "videoinfo" if kind == "video" else "imageinfo"
    short = "vi" if kind == "video" else "ii"
    wanted = "url|size|mime|extmetadata" + ("|derivatives" if kind == "video" else "")
    params = {
        "generator": "search", "gsrnamespace": 6, "gsrlimit": min(limit * 4, 50), "gsrsearch": words, "prop": prop,
        f"{short}prop": wanted, f"{short}urlwidth": 640 if kind == "video" else PHOTO_WIDTH, f"{short}extmetadatafilter": METADATA,
    }  # fmt: skip
    pages = sorted(ask(params).get("query", {}).get("pages", []), key=lambda page: page.get("index", 0))
    results = [found for found in (result(page, kind) for page in pages) if found]
    return base.rank(results, shape)[:limit]
