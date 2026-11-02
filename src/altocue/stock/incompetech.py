"""Incompetech, Kevin MacLeod's library. No key. CC BY 4.0, with a fixed credit he asks for."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from . import base
from .base import Result

PIECES = "https://incompetech.com/music/royalty-free/pieces.json"
FILES = "https://incompetech.com/music/royalty-free/mp3-royaltyfree"
PAGE = "https://incompetech.com/music/royalty-free/music.html"
LICENCE = "CC-BY-4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
CREDIT = "{title} Kevin MacLeod (incompetech.com)\nLicensed under Creative Commons: By Attribution 4.0\nhttps://creativecommons.org/licenses/by/4.0/"


def pieces() -> list[dict[str, Any]]:
    saved = base.cache_read("incompetech", PIECES)
    if saved is not None:
        return saved
    rows = base.answer("Incompetech", base.fetch("incompetech", PIECES))
    rows = rows if isinstance(rows, list) else []
    base.cache_write("incompetech", PIECES, rows)
    return rows


def result(row: dict[str, Any]) -> Result:
    title = base.credit_field(row.get("title", ""))
    url = f"{FILES}/{quote(str(row.get('filename', '')))}"
    return Result(
        base.slug(title), title, "audio", url, url, base.seconds(row.get("length")), 0, 0, LICENCE, LICENCE_URL, CREDIT.format(title=title),
        "incompetech", PAGE, author="Kevin MacLeod",
    )  # fmt: skip


def score(row: dict[str, Any], words: set[str]) -> int:
    text = " ".join(str(row.get(key) or "") for key in ("title", "feel", "instruments", "description", "genre")).lower()
    return len(words & set(re.findall(r"[a-z0-9]+", text)))


def search(query: str, *, kind: str = "audio", shape: str = "", limit: int = 6, key: str = "") -> list[Result]:
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    rows = pieces()
    ranked = sorted((row for row in rows if score(row, words)), key=lambda row: -score(row, words))
    return [result(row) for row in ranked[:limit]]


def find(number: str) -> Result | None:
    return next((result(row) for row in pieces() if base.slug(str(row.get("title", ""))) == number), None)
