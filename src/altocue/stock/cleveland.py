"""The Cleveland Museum of Art. No key. Only works the museum itself marks CC0.

Its search has been seen answering zero for every query while single works stay up, so an
empty search falls back to Wikidata for accession numbers. The museum still decides the licence.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

from . import base
from .base import Result

ARTWORKS = "https://openaccess-api.clevelandart.org/api/artworks/"
WIKIDATA = "https://query.wikidata.org/sparql"
COLLECTION = "Q657415"
FIELDS = "id,accession_number,title,url,share_license_status,images,creators"
TIMEOUT = 25.0  # a sick search hangs for twenty seconds, so one try is enough


def result(item: dict[str, Any]) -> Result | None:
    images = item.get("images") or {}
    chosen = images.get("print") or images.get("web") or {}
    url = str(chosen.get("url", ""))
    if item.get("share_license_status") != "CC0" or not url.lower().endswith((".jpg", ".jpeg", ".png")):
        return None
    creators = item.get("creators") or [{}]
    return Result(
        str(item.get("accession_number", item.get("id"))), base.credit_field(item.get("title", "Untitled")), "photo", url,
        str((images.get("web") or {}).get("url", "")), 0.0, int(chosen.get("width") or 0), int(chosen.get("height") or 0),
        base.CC0, base.CC0_URL, "", "cleveland", str(item.get("url", "")),
        author=base.credit_field(creators[0].get("description", "")), size=int(chosen.get("filesize") or 0),
    )  # fmt: skip


def from_wikidata(query: str, limit: int) -> list[dict[str, Any]]:
    words = re.findall(r"[a-z0-9]+", query.lower())[:3]
    if not words:
        return []
    tests = " ".join(f'FILTER(CONTAINS(LCASE(?label), "{word}"))' for word in words)
    sparql = f'SELECT ?acc WHERE {{ ?item wdt:P195 wd:{COLLECTION}; wdt:P217 ?acc; rdfs:label ?label. FILTER(LANG(?label) = "en") {tests} }} LIMIT {limit}'
    reply = base.fetch("wikidata", f"{WIKIDATA}?{urlencode({'query': sparql})}", headers={"Accept": "application/sparql-results+json"}, timeout=TIMEOUT, attempts=1)
    numbers = [row.get("acc", {}).get("value", "") for row in base.answer("Wikidata", reply).get("results", {}).get("bindings", [])]
    works = []
    for number in filter(None, numbers):
        found = base.fetch("cleveland", f"{ARTWORKS.rstrip('/')}/{quote(number)}?fields={FIELDS}", timeout=TIMEOUT, attempts=1)
        if found.ok and isinstance(found.json().get("data"), dict):
            works.append(found.json()["data"])
    return works


def search(query: str, *, kind: str = "photo", shape: str = "landscape", limit: int = 4, key: str = "") -> list[Result]:
    params = {"q": query, "cc0": 1, "has_image": 1, "limit": limit * 2, "fields": FIELDS}
    listing = base.answer("Cleveland Museum of Art", base.fetch("cleveland", f"{ARTWORKS}?{urlencode(params)}", timeout=TIMEOUT, attempts=1)).get("data") or []
    results = [found for found in map(result, listing or from_wikidata(query, limit * 3)) if found]
    return base.rank(results, shape)[:limit]
