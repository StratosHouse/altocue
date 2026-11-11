"""The weekly sweep's first step: what moved upstream since a person last looked.

Reads `library/watch.toml`, asks PyPI, npm and GitHub for each entry's newest release and licence,
and prints a report. It changes nothing: a person or an agent reads the report and follows docs/SWEEP.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from altocue.errors import AltocueError
from altocue.generate import http

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "library" / "watch.toml"
PYPROJECT = ROOT / "pyproject.toml"
RENDERER = ROOT / "src" / "altocue" / "data" / "renderer" / "package.json"

PLACES = ("pypi", "npm", "github")
ROLES = ("used", "candidate")
ORDER = ("licence changed", "outside the pin", "new", "unreachable", "current")
SPDX = re.compile(r"[A-Za-z0-9.+-]+( (OR|AND|WITH) [A-Za-z0-9.+-]+)*")  # A licence field can hold a whole licence text. Only an id is compared.

Fetch = Callable[[str], Any]


def fetch(url: str) -> Any:
    """One JSON document, or None when the address has none."""
    headers = {"Accept": "application/json"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    reply = http.call("GET", url, headers=headers, timeout=30, attempts=3)
    return reply.json() if reply.ok else None


def numbers(version: str) -> tuple[int, ...]:
    """`v1.13.5` and `1.13.5` compare alike. A pre-release tail is left out."""
    found = re.match(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(part) for part in found.group(1).split(".")) if found else ()


def load(path: Path = WATCH) -> list[dict[str, Any]]:
    entries = tomllib.loads(path.read_text()).get("watch", [])
    for entry in entries:
        missing = [key for key in ("name", "where", "id", "role", "why") if not entry.get(key)]
        if missing:
            raise AltocueError(f"{path.name}: {entry.get('name', 'an entry')} has no {', '.join(missing)}", fix="Give every entry a name, where, id, role and why.")
        if entry["where"] not in PLACES or entry["role"] not in ROLES:
            raise AltocueError(
                f"{path.name}: {entry['name']} has where = {entry['where']!r}, role = {entry['role']!r}",
                fix=f"where is one of {', '.join(PLACES)}. role is one of {', '.join(ROLES)}.",
            )
    return entries


def python_pins(path: Path = PYPROJECT) -> dict[str, tuple[int, ...]]:
    """The upper bound of each Python dependency: `<0.6` means a 0.6 release is outside the pin."""
    project = tomllib.loads(path.read_text()).get("project", {})
    lines = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        lines += extra
    pins: dict[str, tuple[int, ...]] = {}
    for line in lines:
        name = re.match(r"[A-Za-z0-9._-]+", line)
        ceiling = re.search(r"<\s*(\d+(?:\.\d+)*)", line.split(";")[0])
        if name and ceiling:
            pins[name.group(0).lower()] = numbers(ceiling.group(1))
    return pins


def node_pins(path: Path = RENDERER) -> dict[str, str]:
    return dict(json.loads(path.read_text()).get("dependencies", {}))


def newest(entry: dict[str, Any], get: Fetch) -> dict[str, str]:
    """The newest release, its date and its licence, as upstream states them."""
    where, ident = entry["where"], entry["id"]
    if where == "pypi":
        data = get(f"https://pypi.org/pypi/{ident}/json")
        if not data:
            return {}
        info = data.get("info", {})
        files = data.get("urls") or [{}]
        return {
            "version": info.get("version", ""),
            "date": str(files[0].get("upload_time_iso_8601", ""))[:10],
            "licence": info.get("license_expression") or info.get("license") or "",
        }
    if where == "npm":
        data = get(f"https://registry.npmjs.org/{ident}/latest")
        return {"version": data.get("version", ""), "date": "", "licence": str(data.get("license", ""))} if data else {}
    release = get(f"https://api.github.com/repos/{ident}/releases/latest")
    if release and release.get("tag_name"):
        version, date = release["tag_name"], str(release.get("published_at", ""))[:10]
    else:
        tags = get(f"https://api.github.com/repos/{ident}/tags?per_page=1") or []
        version, date = (tags[0].get("name", ""), "") if tags else ("", "")
    licence = get(f"https://api.github.com/repos/{ident}/license") or {}
    return {"version": version, "date": date, "licence": (licence.get("license") or {}).get("spdx_id", "")} if version else {}


def judge(entry: dict[str, Any], found: dict[str, str], python: dict[str, tuple[int, ...]], node: dict[str, str]) -> tuple[str, str]:
    """The status of one entry, and what it was measured against."""
    if not found.get("version"):
        return "unreachable", ""
    stated, expected = found.get("licence", "").strip(), entry.get("licence", "")
    if expected and stated and SPDX.fullmatch(stated) and stated not in ("NOASSERTION", "UNKNOWN") and stated.lower() != expected.lower():
        return "licence changed", f"was {expected}"
    latest = numbers(found["version"])
    if entry["role"] == "used" and entry["where"] == "pypi" and entry["id"].lower() in python:
        ceiling = python[entry["id"].lower()]
        against = "under " + ".".join(map(str, ceiling))
        return ("outside the pin" if latest >= ceiling else "current"), against
    pinned = entry.get("pin") or (node.get(entry["id"], "") if entry["where"] == "npm" else "")
    if entry["role"] == "used" and pinned:
        return ("outside the pin" if latest != numbers(pinned) else "current"), f"pinned {pinned}"
    seen = entry.get("seen", "")
    return ("current" if seen and numbers(seen) == latest else "new"), f"seen {seen}" if seen else "never reviewed"


def sweep(
    entries: list[dict[str, Any]], get: Fetch = fetch, python: dict[str, tuple[int, ...]] | None = None, node: dict[str, str] | None = None
) -> list[dict[str, str]]:
    python = python_pins() if python is None else python
    node = node_pins() if node is None else node
    rows = []
    for entry in entries:
        try:
            found = newest(entry, get)
        except AltocueError:
            found = {}
        status, against = judge(entry, found, python, node)
        rows.append(
            {
                "name": entry["name"],
                "role": entry["role"],
                "where": entry["where"],
                "status": status,
                "latest": found.get("version", ""),
                "date": found.get("date", ""),
                "licence": found.get("licence", "")[:40],
                "against": against,
                "why": entry["why"],
            }
        )
    return sorted(rows, key=lambda row: (ORDER.index(row["status"]), row["role"] != "used", row["name"].lower()))


def report(rows: list[dict[str, str]], today: str) -> str:
    moved = [row for row in rows if row["status"] not in ("current", "unreachable")]
    lines = [f"# Altocue sweep, {today}", "", f"{len(rows)} watched. {len(moved)} moved. {sum(row['status'] == 'unreachable' for row in rows)} could not be reached.", ""]
    lines += ["| Status | Name | Role | Newest | Released | Licence stated | Measured against | Why it is watched |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        newest_cells = [row["latest"] or "unknown", row["date"] or "unknown", row["licence"] or "not stated"]
        cells = [row["status"], row["name"], row["role"], *newest_cells, row["against"] or "nothing", row["why"]]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "A row that moved is a prompt to read the release notes and the licence file, never a reason to bump a pin unread. The steps are in docs/SWEEP.md."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report what moved upstream since the last sweep. Changes nothing.")
    parser.add_argument("--json", action="store_true", help="print the rows as JSON")
    parser.add_argument("--watch", default=str(WATCH), help="the watch list to read")
    args = parser.parse_args(argv)
    try:
        rows = sweep(load(Path(args.watch)))
    except AltocueError as error:
        print(f"sweep: {error}", file=sys.stderr)
        return 1
    print(json.dumps(rows, indent=2) if args.json else report(rows, datetime.now(UTC).date().isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
