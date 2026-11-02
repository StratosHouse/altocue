"""ccMixter. No key. Public-domain and CC BY tracks only, read per track."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from . import base
from .base import Result

QUERY = "https://ccmixter.org/api/query"


def result(row: dict[str, Any]) -> Result | None:
    licence = base.licence_id(str(row.get("license_url", "")))
    extra = row.get("upload_extra") if isinstance(row.get("upload_extra"), dict) else {}
    if licence is None or base.share_alike(licence) or extra.get("nsfw"):
        return None
    audio = next((item for item in row.get("files", []) if str(item.get("download_url", "")).lower().endswith(".mp3")), None)
    if audio is None:
        return None
    number = str(row.get("upload_id"))
    title = base.credit_field(row.get("upload_name", number))
    author = base.credit_field(row.get("user_real_name") or row.get("user_name", ""))
    page = base.secure(str(row.get("file_page_url", "")))
    featuring = base.credit_field(extra.get("featuring", ""))
    credit = ""
    if base.needs_credit(licence):
        credit = base.credit_line(title, f"{author}, feat. {featuring}" if featuring else author, licence, page)
    info = audio.get("file_format_info", {})
    return Result(
        number, title, "audio", base.secure(audio["download_url"]), base.secure(audio["download_url"]), base.seconds(info.get("ps")), 0, 0,
        licence, base.secure(str(row.get("license_url", ""))), credit, "ccmixter", page, author=author, size=int(audio.get("file_rawsize") or 0),
    )  # fmt: skip


def search(query: str, *, kind: str = "audio", shape: str = "", limit: int = 6, key: str = "") -> list[Result]:
    results: list[Result] = []
    for licence in ("by", "pd"):
        params = {"f": "json", "lic": licence, "search": query, "search_type": "all", "sort": "rank", "ord": "desc", "limit": limit}
        rows = base.answer("ccMixter", base.fetch("ccmixter", f"{QUERY}?{urlencode(params)}", gap=0.5))
        results += [found for found in map(result, rows if isinstance(rows, list) else []) if found]
    return results


def find(number: str) -> Result | None:
    rows = base.answer("ccMixter", base.fetch("ccmixter", f"{QUERY}?{urlencode({'f': 'json', 'ids': number})}"))
    return next((found for found in map(result, rows if isinstance(rows, list) else []) if found), None)
