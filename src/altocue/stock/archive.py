"""The Internet Archive. Opt-in only. Uploaders set its licence field, so nothing here counts as cleared."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from . import base
from .base import Result

SEARCH = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata"
FILES = "https://archive.org/download"
WARNING = (
    "Internet Archive licences are set by uploaders and cannot be trusted. Its top public-domain results have included "
    "feature films still under copyright. Check the rights yourself before using this."
)
MOST_BYTES = 300_000_000
LICENCES = r"(licenseurl:*publicdomain* OR licenseurl:*licenses\/by\/* OR licenseurl:*licenses\/by-sa\/*)"  # unescaped slashes break its search


def video_file(item: str) -> dict[str, Any]:
    reply = base.fetch("archive", f"{METADATA}/{quote(item)}", gap=1.0)
    rows = base.answer("Internet Archive", reply).get("files", [])
    files = [
        row
        for row in rows
        if str(row.get("name", "")).lower().endswith(".mp4") and 0 < int(row.get("size") or 0) <= MOST_BYTES and base.seconds(row.get("length")) <= base.LONGEST
    ]
    return min(files, key=lambda row: int(row["size"]), default={})


def result(doc: dict[str, Any]) -> Result | None:
    stated = base.licence_id(doc.get("licenseurl", ""))
    item = str(doc.get("identifier", ""))
    if not stated or not item:
        return None
    chosen = video_file(item)
    if not chosen:
        return None
    title = base.credit_field(doc.get("title", item))
    author = base.credit_field(doc.get("creator", "") if isinstance(doc.get("creator", ""), str) else ", ".join(doc["creator"])) or "Unknown"
    page = f"https://archive.org/details/{item}"
    credit = f"{title} by {author}, uploader states {base.licence_name(stated)}, {page}"
    return Result(
        item, title, "video", f"{FILES}/{quote(item)}/{quote(chosen['name'])}", f"https://archive.org/services/img/{quote(item)}", base.seconds(chosen.get("length")),
        int(chosen.get("width") or 0), int(chosen.get("height") or 0), base.UNVERIFIED, base.secure(str(doc["licenseurl"])), credit, "archive", page, author=author,
        size=int(chosen["size"]), warnings=[WARNING, base.PEOPLE_WARNING],
    )  # fmt: skip


def search(query: str, *, kind: str = "video", shape: str = "landscape", limit: int = 4, key: str = "") -> list[Result]:
    params = [("q", f"({query}) AND mediatype:movies AND {LICENCES}"), ("rows", limit * 3), ("output", "json")]
    params += [("fl[]", name) for name in ("identifier", "title", "creator", "licenseurl")]
    docs = base.answer("Internet Archive", base.fetch("archive", f"{SEARCH}?{urlencode(params)}", gap=1.0)).get("response", {}).get("docs", [])
    results: list[Result] = []
    for doc in docs:
        found = result(doc)
        if found:
            results.append(found)
        if len(results) == limit:
            break
    return results
