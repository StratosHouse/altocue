"""The Metropolitan Museum of Art. No key. The search filter is loose, so each object's own flag decides."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from . import base
from .base import Result

SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
OBJECTS = "https://collectionapi.metmuseum.org/public/collection/v1/objects"
GAP = 0.02  # the API allows 80 calls a second


def result(item: dict[str, Any]) -> Result | None:
    if item.get("isPublicDomain") is not True or not str(item.get("primaryImage", "")).startswith("https://"):
        return None
    title = base.credit_field(item.get("title") or f"Object {item.get('objectID')}")
    return Result(
        str(item.get("objectID")), title, "photo", item["primaryImage"], str(item.get("primaryImageSmall", "")), 0.0, 0, 0,
        base.CC0, base.CC0_URL, "", "met", str(item.get("objectURL", "")), author=base.credit_field(item.get("artistDisplayName", "")),
    )  # fmt: skip


def search(query: str, *, kind: str = "photo", shape: str = "landscape", limit: int = 4, key: str = "") -> list[Result]:
    params = {"q": query, "hasImages": "true", "isPublicDomain": "true", "limit": limit * 4, "offset": 0}
    ids = base.answer("The Met", base.fetch("met", f"{SEARCH}?{urlencode(params)}")).get("objectIDs") or []
    results: list[Result] = []
    for number in ids[: limit * 4]:
        reply = base.fetch("met", f"{OBJECTS}/{number}", gap=GAP)
        found = result(reply.json()) if reply.ok else None
        if found:
            results.append(found)
        if len(results) == limit:
            break
    return results
