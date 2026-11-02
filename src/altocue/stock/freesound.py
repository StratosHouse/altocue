"""Freesound with the user's own key. CC0 and CC BY sounds only. Originals need a sign-in flow, so previews are used."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from ..errors import AltocueError
from . import base
from .base import Result

SEARCH = "https://freesound.org/apiv2/search/"
SOUND = "https://freesound.org/apiv2/sounds"
KEY = "FREESOUND_API_KEY"
FILTER = 'license:("Creative Commons 0" OR "Attribution") duration:[0 TO 30]'
FIELDS = "id,name,username,license,duration,previews,url"
TERMS = "Freesound's API terms keep commercial use for negotiation, so check them before using this in anything sold: https://freesound.org/help/tos_api/"


def result(row: dict[str, Any]) -> Result | None:
    licence = base.licence_id(str(row.get("license", "")))
    audio = row.get("previews", {}).get("preview-hq-mp3", "")
    if licence is None or base.share_alike(licence) or not audio:
        return None
    number = str(row.get("id"))
    title, author = base.credit_field(row.get("name", number)), base.credit_field(row.get("username", ""))
    page = base.secure(str(row.get("url", "")))
    credit = base.credit_line(title, author, licence, page) if base.needs_credit(licence) else ""
    return Result(
        number, title, "audio", audio, audio, float(row.get("duration") or 0), 0, 0, licence, base.secure(str(row.get("license", ""))), credit, "freesound", page,
        author=author, warnings=[TERMS],
    )  # fmt: skip


def token(key: str = "") -> str:
    found = key or os.environ.get(KEY, "")
    if not found:
        raise AltocueError(f"{KEY} is not set.", fix="Get a free key at https://freesound.org/apiv2/apply, then set it. The bundled sounds need no key.")
    return found


def search(query: str, *, kind: str = "audio", shape: str = "", limit: int = 6, key: str = "") -> list[Result]:
    params = {"query": query, "filter": FILTER, "fields": FIELDS, "page_size": limit}
    reply = base.fetch("freesound", f"{SEARCH}?{urlencode(params)}", gap=1.0, headers={"Authorization": f"Token {token(key)}"})
    rows = base.answer("Freesound", reply, fix=f"Check {KEY}.").get("results", [])
    return [found for found in map(result, rows) if found]


def find(number: str, key: str = "") -> Result | None:
    reply = base.fetch("freesound", f"{SOUND}/{number}/?{urlencode({'fields': FIELDS})}", gap=1.0, headers={"Authorization": f"Token {token(key)}"})
    return result(base.answer("Freesound", reply, fix=f"Check {KEY}."))
