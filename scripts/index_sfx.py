"""Index every sound in the open packs listed in library/sfx/packs.json, so the catalog can offer each one by name.

Run it on a machine with internet and ffprobe:

    uv run python scripts/index_sfx.py
    uv run python scripts/build_library.py --offline

The first run reads each pack's page for its download link, downloads the pack, and pins the address, size and sha256 in
packs.json. Every later run checks the download against that pin and stops on a mismatch, so a changed pack is a
reviewed change, never a silent one. It writes library/sfx/index.json: one row per sound with its category, tags,
length, path inside the archive and sha256. No audio is written to the repo. Packs are cached in ~/.altocue/sfx/packs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

from altocue import ffmpeg, library
from altocue.errors import AltocueError
from altocue.generate import http
from altocue.stock import sfxpacks

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "library" / "sfx" / "packs.json"
INDEX = ROOT / "library" / "sfx" / "index.json"
SCHEMA = 1


def read_packs(path: Path = PACKS) -> dict[str, Any]:
    packs = json.loads(path.read_text())
    seen = set()
    for pack in packs["packs"]:
        if not library.SAFE_ID.fullmatch(pack["id"]) or pack["id"] in seen:
            raise AltocueError(f"Pack {pack['id']!r} needs a unique id of lower-case letters, digits and dashes.")
        seen.add(pack["id"])
        if not library.licence_allowed(pack["licence"]):
            raise AltocueError(
                f"Pack {pack['id']} is {pack['licence']}, which is not on the allow-list.", fix="Only CC0 and the open licences in library.py may be indexed."
            )
        for key in ("page", "licence_url"):
            if not str(pack[key]).startswith("https://"):
                raise AltocueError(f"Pack {pack['id']} has a {key} that is not https.")
    return packs


def page_link(pack: dict[str, Any]) -> str:
    """The maker's own page names the current archive. Kenney's links change with each upload, so they are read, never typed."""
    reply = http.call("GET", pack["page"], timeout=60, attempts=3)
    if not reply.ok:
        raise AltocueError(
            f"{pack['page']} answered {reply.status}.", fix=f"Open the page, copy the archive link into packs.json as download for {pack['id']}, and run again."
        )
    found = re.search(pack["find_download"], reply.body.decode(errors="replace"))
    if not found:
        raise AltocueError(
            f"No archive link matching {pack['find_download']} on {pack['page']}.",
            fix=f"Open the page, copy the archive link into packs.json as download for {pack['id']}, and run again.",
        )
    return urljoin(pack["page"], found.group(0).replace("&amp;", "&"))


def fetch_pack(pack: dict[str, Any], cache: Path, repin: bool = False) -> tuple[Path, bool]:
    """The archive on disk, and whether its pin changed. A pinned pack that no longer matches stops the run unless --repin says so."""
    url = pack.get("download") or page_link(pack)
    if pack.get("sha256") and not repin:
        fix = f"Run `uv run python scripts/index_sfx.py --pack {pack['id']} --repin`, review the new pin and index in the diff, then rebuild the catalog."
        return sfxpacks.archive(pack["id"], url, pack["sha256"], cache, fix=fix), False
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{sfxpacks.slug(pack['id'])}-unpinned.zip"
    sfxpacks.download(url, path)
    sha256 = sfxpacks.sha256_of(path)
    pinned = cache / f"{sfxpacks.slug(pack['id'])}-{sha256[:16]}.zip"
    path.replace(pinned)
    changed = pack.get("sha256") != sha256 or pack.get("download") != url
    pack.update(download=url, sha256=sha256, bytes=pinned.stat().st_size, pinned_on=date.today().isoformat())
    return pinned, changed


def seconds_of(data: bytes, suffix: str) -> float | None:
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / f"sound{suffix}"
        path.write_bytes(data)
        try:
            found = ffmpeg.probe_json(path).get("format", {}).get("duration")
        except AltocueError:
            return None
    return round(float(found), 3) if found not in (None, "N/A") else None


def wanted(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return PurePosixPath(name).suffix.lower() in sfxpacks.AUDIO and not any(part.startswith((".", "__MACOSX")) for part in parts)


def index_pack(pack: dict[str, Any], archive: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """One row per sound file. A name that trips the brand rules is left out with its reason, the way the catalog refuses an item."""
    rows, left_out, taken = [], [], set()
    with zipfile.ZipFile(archive) as opened:
        names = sorted(name for name in opened.namelist() if wanted(name))
        for name in names:
            data = opened.read(name)
            category, tags = sfxpacks.categorise(name, pack.get("hints", []))
            stem = sfxpacks.slug(PurePosixPath(name).stem) or "sound"
            id = f"{pack['id']}-{stem}"
            if id in taken:
                id = f"{pack['id']}-{sfxpacks.slug(str(PurePosixPath(name).with_suffix('')))}"
            title = sfxpacks.title_of(name)
            found = library.refusal(id, title, "", tags)
            if found:
                left_out.append({"path": name, **found})
                continue
            taken.add(id)
            rows.append({
                "id": id, "name": title, "category": category, "tags": tags, "seconds": seconds_of(data, PurePosixPath(name).suffix.lower()),
                "pack": pack["id"], "path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "licence": pack["licence"],
                "credit": pack.get("credit"),
            })  # fmt: skip
    return rows, left_out


def write_index(rows: list[dict[str, Any]], left_out: dict[str, list[dict[str, str]]], path: Path = INDEX) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    body = {"schema": SCHEMA, "indexed_on": date.today().isoformat(), "sounds": len(rows), "categories": dict(sorted(counts.items())),
            "left_out": left_out, "rows": sorted(rows, key=lambda row: row["id"])}  # fmt: skip
    lines = ",\n".join("    " + json.dumps(row, ensure_ascii=False) for row in body["rows"])
    head = {key: value for key, value in body.items() if key != "rows"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(head, indent=2, ensure_ascii=False)[:-2] + ',\n  "rows": [\n' + lines + "\n  ]\n}\n")
    return body


def run(only: list[str], cache: Path, repin: bool, packs_path: Path = PACKS, index_path: Path = INDEX) -> dict[str, Any]:
    packs = read_packs(packs_path)
    previous = sfxpacks.read_json(index_path) or {"rows": [], "left_out": {}}
    rows = [row for row in previous["rows"] if only and row["pack"] not in only]
    left_out = {key: value for key, value in previous.get("left_out", {}).items() if only and key not in only}
    old_rows = [row for row in previous["rows"] if not only or row["pack"] in only]
    changed, failed = False, []
    for pack in packs["packs"]:
        if only and pack["id"] not in only:
            continue
        try:
            archive, moved = fetch_pack(pack, cache, repin)
        except AltocueError as error:
            # One dead link must not empty the index: say so, keep the pack's last rows, and fail the run at the end.
            failed.append({"pack": pack["id"], "error": error.args[0], "fix": error.fix})
            rows += [row for row in old_rows if row["pack"] == pack["id"]]
            if pack["id"] in previous.get("left_out", {}):
                left_out[pack["id"]] = previous["left_out"][pack["id"]]
            print(f"{pack['id']}: NOT INDEXED. {error}", file=sys.stderr)
            continue
        changed |= moved
        found, dropped = index_pack(pack, archive)
        rows += found
        if dropped:
            left_out[pack["id"]] = dropped
        print(f"{pack['id']}: {len(found)} sounds" + (f", {len(dropped)} left out" if dropped else "") + (", pin written" if moved else ""))
    if changed:
        packs_path.write_text(json.dumps(packs, indent=2, ensure_ascii=False) + "\n")
    body = write_index(rows, left_out, index_path)
    return {**body, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack", action="append", default=[], help="Index only this pack id. Repeat for more. Other packs keep their rows")
    parser.add_argument("--cache", type=Path, default=sfxpacks.cache_dir(), help="Where packs are kept between runs")
    parser.add_argument("--repin", action="store_true", help="Accept a new download and write its sha256. Review the diff before committing")
    args = parser.parse_args(argv)
    body = run(args.pack, args.cache, args.repin)
    print(f"Indexed {body['sounds']} sounds: " + ", ".join(f"{name} {count}" for name, count in body["categories"].items()))
    if body["failed"]:
        print(f"{len(body['failed'])} pack(s) could not be fetched: " + ", ".join(item["pack"] for item in body["failed"]), file=sys.stderr)
        print("Their last indexed rows were kept. Fix each one above and run again.", file=sys.stderr)
        return 1
    print("Now run `uv run python scripts/build_library.py --offline` to put them in the catalog.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AltocueError as error:
        print(f"index_sfx: {error}", file=sys.stderr)
        sys.exit(1)
