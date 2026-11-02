"""Build the library catalog: built-in formats, the pinned HyperFrames registry, gl-transitions and the source records."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from altocue import library
from altocue.errors import AltocueError
from altocue.generate import http
from altocue.motion import compositions, hyperframes

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "library" / "sources"
CATALOG = ROOT / "library" / "catalog.json"
BUNDLED = ROOT / "src" / "altocue" / "data" / "library" / "catalog.json"
REPORT = ROOT / "library" / "report.json"

REGISTRY_PAGE = "https://github.com/heygen-com/hyperframes/tree"
NPM_INDEX = "https://registry.npmjs.org/gl-transitions/latest"
SHADER_INDEX = "https://cdn.jsdelivr.net/npm/gl-transitions@{version}/gl-transitions.json"
SHADER_PAGE = "https://github.com/gl-transitions/gl-transitions/blob/master/transitions"
SHADER_LICENCES = {"MIT": "MIT", "BSD 3 Clause": "BSD-3-Clause", "BSD 2 Clause": "BSD-2-Clause"}
FOLDERS = {"block": "blocks", "component": "components"}
SUMMARY_LENGTH = 240
WORKERS = 8

SHIP_FETCH = "Fetched into your project by `altocue library add`. Licence files travel with it. Nothing from it ships in Altocue."
SHIP_SNIPPET = "A snippet to paste into a composition you write, with no timeline of its own. `altocue library add` cannot mount it."
SHIP_SHADER = "Listed here, not shipped. Keep the licence header in the shader. It needs both clips, so it belongs in the picture edit, not the graphics layer."
SHIP_VERDICT = {
    "BUNDLE": "Cleared for bundling item by item, with notices kept. Anything not already in Altocue is a link for now.",
    "RUNTIME": "Reached at run time with your own account or key where it needs one. Nothing from it ships in Altocue.",
    "DIRECTORY": "Link only. A person opens it and follows its terms. Altocue indexes none of its items.",
}
JOB_WORDS = {
    "hook": {"emphasize", "agitate", "ask", "reveal", "title-card", "headline", "impact"},
    "prove": {"prove", "compare", "exhibit", "data", "chart", "social-proof", "proof", "testimonial", "stat", "leaderboard"},
    "explain": {"demonstrate", "orient", "tour", "feature-tour", "connect", "lower-third", "annotation", "callout", "product-demo", "mock-ui", "code", "flowchart"},
    "transition": {"bridge", "transition", "transition-primitive", "wipe"},
    "close": {"exit", "celebrate", "cta", "end-card", "outro"},
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)).strip()


def plain_summary(text: str) -> str:
    """Our catalog names no brand, and a passing `TikTok-style` says nothing about what an item does."""
    text = clean(library.drop_style(text))
    text = text[:1].upper() + text[1:]
    if len(text) > SUMMARY_LENGTH:
        text = text[:SUMMARY_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


def kind_of(name: str, tags: list[str]) -> str:
    if {"transition", "transition-primitive"} & set(tags) or name.startswith("transitions-"):
        return "transition"
    if "media-treatment-overlay" in tags:
        return "overlay"
    if "map" in tags:
        return "map"
    return "3d" if {"3d", "3d-motion"} & set(tags) else "motion"


def jobs_of(item: dict[str, Any]) -> list[str]:
    words = set(item.get("jobs", [])) | set(item.get("tags", [])) | {item.get("family") or ""}
    return [job for job, wanted in JOB_WORDS.items() if words & wanted]


def slots_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    """What a person fills in. Sample text is kept as an example, sample paths are not."""
    slots = []
    for variable in item.get("variables", []):
        if variable.get("role", "content") != "content":
            continue
        slot = {"name": variable["id"], "kind": variable.get("type", "string"), "label": clean(variable.get("label", ""))}
        default = variable.get("default")
        if isinstance(default, str) and default and len(default) <= 80 and "/" not in default and not library.refusal("x", description=default):
            slot["default"] = clean(default)
        slots.append(slot)
    return slots


def registry_entries(items: list[dict[str, Any]], version: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries, refused = [], []
    for item in items:
        name, title, description = item["name"], clean(item.get("title", "")), item.get("description", "")
        tags = item.get("tags", [])
        files = [file.get("target", file.get("path", "")) for file in item.get("files", []) if not str(file.get("path", "")).endswith(".html")]
        licence = item.get("license") or "Apache-2.0"  # 408 items state nothing and inherit the repo's licence
        found = library.refusal(name, title, description, tags, files)
        if found is None and not library.licence_allowed(licence):
            found = {"rule": "licence", "reason": f"Its licence, {licence}, is not on the allow-list."}
        if found:
            refused.append({"id": name, "source": "hyperframes", "title": title, **found})
            continue
        folder = FOLDERS[item["type"].rsplit(":", 1)[-1]]
        entry = {
            "id": name,
            "kind": kind_of(name, tags),
            "tier": "fetch" if item.get("mountable", True) else "link",
            "title": title,
            "summary": plain_summary(description),
            "tags": [tag for tag in tags if not library.refusal(tag, tag)],
            "jobs": jobs_of(item),
            "licence": licence,
            "url": f"{REGISTRY_PAGE}/v{version}/registry/{folder}/{name}",
            "ship": SHIP_FETCH if item.get("mountable", True) else SHIP_SNIPPET,
            "source": "hyperframes",
            "version": version,
        }
        size = item.get("dimensions") or {}
        extras = {"seconds": item.get("duration"), "width": size.get("width"), "height": size.get("height"), "gsap": item.get("gsap")}
        extras.update(slots=slots_of(item), author=item.get("author"))
        entries.append({**entry, **{key: value for key, value in extras.items() if value}})
    return entries, refused


def _kebab(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).replace("_", "-").lower()


def transition_entries(rows: list[dict[str, Any]], version: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries, refused = [], []
    for row in rows:
        id = f"gl-{_kebab(row['name'])}"
        licence = SHADER_LICENCES.get(row.get("license", ""))
        found = library.refusal(id, row["name"], row.get("description", ""))
        if found is None and licence is None:
            found = {"rule": "licence", "reason": f"Its licence, {row.get('license')}, is not MIT or BSD."}
        if found:
            refused.append({"id": id, "source": "gl-transitions", "title": row["name"], **found})
            continue
        author = clean(str(row.get("author", "")))
        entry = {
            "id": id,
            "kind": "transition",
            "tier": "fetch",
            "title": row["name"],
            "summary": plain_summary(row.get("description") or f"A GLSL transition by {author}."),
            "tags": ["shader", "glsl", *row.get("tags", [])],
            "jobs": ["transition"],
            "licence": licence,
            "url": f"{SHADER_PAGE}/{row['name']}.glsl",
            "ship": SHIP_SHADER,
            "source": "gl-transitions",
            "version": version,
            "author": author,
        }
        entries.append(entry)
    return entries, refused


def read_sources(folder: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(folder.glob("*.json")):
        record = json.loads(path.read_text())
        if record["id"] != path.stem:
            raise AltocueError(f"{path.name}: the id {record['id']} does not match its file.")
        records.append(record)
    return records


def source_entries(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per source, however large it is. An excluded source stays in the records with its reason and is never a row."""
    rows = []
    for record in sources:
        if record["verdict"] == "EXCLUDE":
            continue
        kinds = list(dict.fromkeys(library.CLASS_KINDS[name] for name in record["classes"]))
        licence = record["licence"]
        row = {
            "id": record["id"],
            "kind": kinds[0],
            "also": kinds[1:],
            "tier": "search" if record["verdict"] == "RUNTIME" else "link",
            "title": record["name"],
            "summary": record["holds"],
            "tags": [*record["classes"], record["verdict"].lower()],
            "jobs": [],
            "licence": licence.get("id") or licence["scope"].replace("_", " "),
            "url": record["url"],
            "ship": SHIP_VERDICT[record["verdict"]],
            "source": record["id"],
        }
        rows.append(row)
    return rows


def assemble(
    entries: list[dict[str, Any]], sources: list[dict[str, Any]], refused: list[dict[str, Any]], pins: dict[str, str], previous: dict[str, Any] | None
) -> dict[str, Any]:
    """The date only moves when something else did, so an idle week commits nothing."""
    body = {
        "pins": pins,
        "entries": sorted(entries, key=lambda row: (library.TIERS.index(row["tier"]), row["id"])),
        "sources": sorted(sources, key=lambda row: row["id"]),
        "refused": sorted(refused, key=lambda row: (row["source"], row["id"])),
    }
    unchanged = previous and all(previous.get(key) == value for key, value in body.items())
    generated = previous["generated"] if unchanged else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog = {"schema": library.SCHEMA, "generated": generated, **body}
    library.validate(catalog)
    return catalog


def format_entries() -> list[dict[str, Any]]:
    return library.builtins()


def renderer_version() -> str:
    package = json.loads(library.bundled_path().parent.parent.joinpath("renderer", "package.json").read_text())
    return package["dependencies"]["hyperframes"]


def _get_json(url: str) -> Any:
    reply = http.call("GET", url, timeout=30, attempts=4)
    if not reply.ok:
        raise AltocueError(f"{url} answered {reply.status}.")
    return json.loads(reply.body)


def _read_item(row: dict[str, Any], version: str) -> dict[str, Any]:
    kind = row["type"]
    base = f"{library.REGISTRY_REPO}/v{version}/registry/{FOLDERS[kind]}/{row['name']}"
    manifest = _get_json(f"{base}/registry-item.json")
    pages = [file["path"] for file in manifest.get("files", []) if file["path"].endswith(".html") and "url" not in file]
    text = "".join(http.call("GET", f"{base}/{page}", timeout=30, attempts=4).body.decode(errors="replace") for page in pages)
    stated = re.search(r'data-duration="([\d.]+)"', text)
    length = {"duration": float(stated[1])} if stated and not row.get("duration") else {}
    return {**manifest, **row, **length, "gsap": "gsap" in text.lower(), "mountable": compositions.own_id(text) is not None}


def fetch_registry(version: str) -> list[dict[str, Any]]:
    """Every item the pinned renderer lists, read from upstream's tag for that version and never from its main branch."""
    binary = hyperframes.binary()
    if not binary.exists():
        raise AltocueError("The renderer is not installed here.", fix="Set ALTOCUE_RENDERER_DIR, or run `npm ci` in src/altocue/data/renderer.")
    with tempfile.TemporaryDirectory() as temp:
        library.pinned_folder(Path(temp), version)
        done = subprocess.run([str(binary), "catalog", "--json"], check=False, capture_output=True, text=True, cwd=temp, env=hyperframes.quiet_env())
    if done.returncode != 0:
        raise AltocueError(f"The renderer could not list its registry:\n{done.stderr[-400:]}")
    rows = json.loads(done.stdout)
    if not rows:
        raise AltocueError("The renderer listed an empty registry, so the build stops rather than empty the catalog.")
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda row: _read_item(row, version), rows))


def fetch_transitions() -> tuple[list[dict[str, Any]], str]:
    version = _get_json(NPM_INDEX)["version"]
    return _get_json(SHADER_INDEX.format(version=version)), version


def write_catalog(catalog: dict[str, Any], path: Path) -> None:
    """One row per line, so a week's change reads as a short diff."""
    rows = {key: ",\n".join("    " + json.dumps(row, ensure_ascii=False) for row in catalog[key]) for key in ("entries", "sources", "refused")}
    head = f'{{\n  "schema": {catalog["schema"]},\n  "generated": {json.dumps(catalog["generated"])},\n  "pins": {json.dumps(catalog["pins"])},\n'
    body = ",\n".join(f'  "{key}": [\n{rows[key]}\n  ]' for key in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{head}{body}\n}}\n")


def report(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated": catalog["generated"],
        "pins": catalog["pins"],
        "entries": len(catalog["entries"]),
        "refused_by_rule": dict(sorted(Counter(row["rule"] for row in catalog["refused"]).items())),
        "refused": catalog["refused"],
        "excluded_sources": [{"id": record["id"], "reason": record["exclude_reason"]} for record in catalog["sources"] if record["verdict"] == "EXCLUDE"],
    }


def build() -> dict[str, Any]:
    version = renderer_version()
    previous = json.loads(CATALOG.read_text()) if CATALOG.exists() else None
    sources = read_sources(SOURCES)
    registry, refused = registry_entries(fetch_registry(version), version)
    rows, shader_version = fetch_transitions()
    shaders, shaders_refused = transition_entries(rows, shader_version)
    pins = {"hyperframes": version, "gl-transitions": shader_version}
    catalog = assemble(format_entries() + registry + shaders + source_entries(sources), sources, refused + shaders_refused, pins, previous)
    write_catalog(catalog, CATALOG)
    write_catalog(catalog, BUNDLED)
    REPORT.write_text(json.dumps(report(catalog), indent=2, ensure_ascii=False) + "\n")
    return catalog


def _probe(url: str) -> tuple[str, str]:
    """A site that turns away a robot is not a dead site, so only a clear answer counts as dead."""
    last = "unreachable"
    for method in ("HEAD", "GET"):
        try:
            status = http.call(method, url, timeout=15, attempts=2).status
        except AltocueError:
            continue
        if status < 400:
            return url, "ok"
        last = str(status)
    return url, last


def check_links(sources: list[dict[str, Any]]) -> str:
    """Written for the run summary. It never fails the run, because one flaky site says nothing about the catalog."""
    wanted = sorted({record["url"] for record in sources if record["url"].startswith("https://")})
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(_probe, wanted))
    dead = [(url, why) for url, why in results if why in ("404", "410")]
    shy = [(url, why) for url, why in results if why not in ("ok", "404", "410")]
    answered = len(results) - len(dead) - len(shy)
    headline = f"Checked {len(results)} source addresses on {date.today().isoformat()}: {answered} answered, {len(dead)} dead, {len(shy)} would not say."
    lines = ["## Library link check", "", headline, ""]
    lines += [f"- Dead ({why}): {url}" for url, why in dead]
    lines += [f"- Would not say ({why}): {url}" for url, why in shy]
    return "\n".join(lines)


def registry_drift(version: str) -> str:
    """The catalog follows the pin, so say what upstream has added since."""
    try:
        pinned = {item["name"] for item in _get_json(f"{library.REGISTRY_REPO}/v{version}/registry/registry.json")["items"]}
        latest = {item["name"] for item in _get_json(f"{library.REGISTRY_REPO}/main/registry/registry.json")["items"]}
    except AltocueError as error:
        return f"Could not compare the registry with upstream: {error}"
    added = sorted(latest - pinned)
    return f"Upstream's registry has {len(added)} items newer than the pinned {version}." + (f" First few: {', '.join(added[:12])}." if added else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-links", action="store_true", help="Report dead source addresses for the run summary. Never fails")
    args = parser.parse_args(argv)
    if args.check_links:
        print(check_links(read_sources(SOURCES)))
        print(f"\n{registry_drift(renderer_version())}")
        return 0
    catalog = build()
    refused = dict(Counter(row["rule"] for row in catalog["refused"]))
    print(json.dumps({"entries": len(catalog["entries"]), "refused": refused, "generated": catalog["generated"]}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AltocueError as error:
        print(f"build_library: {error}", file=sys.stderr)
        sys.exit(1)
