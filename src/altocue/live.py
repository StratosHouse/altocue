"""Numbers a film prints that must match the tool as it ships.

A film built from the tool writes its storyboard with tokens such as `{live:formats}`. `fill` reads each number
now, puts it in, and records the text it was shown as and every place that shows it, in `live.json`. `check` reads
every number again, so `altocue film check` fails a film whose numbers have moved since it was built, and nothing
renders until it is built again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import ffmpeg, storyboard
from .errors import AltocueError

FILE = "live.json"
TOKEN = re.compile(r"\{live:([\w.-]+)\}")
COUNTS = ("formats", "groups", "library", "gates", "looks")
REBUILD = "Build the film again from the tool, so every number is read fresh. Never type a number in by hand."


def count(name: str) -> int:
    """A count of what ships, read now. `groups.<id>` counts one group of formats."""
    from . import library, looks
    from .motion import formats
    from .qa import gates

    if name == "formats":
        return len(formats.load())
    if name.startswith("groups."):
        return sum(1 for fmt in formats.load().values() if fmt.group == name.split(".", 1)[1])
    if name == "library":
        return int(library.stats(library.current())["entries"])
    if name == "gates":
        return len(gates.gate_ids())
    if name == "looks":
        return len(looks.ALL)
    raise AltocueError(f"No live count called {name}.", fix=f"Use one of {', '.join(COUNTS)}.")


def _sha(path: Path) -> str:
    from .qa import sha256

    return sha256(path)


def _qa(folder: Path, read: str) -> Any:
    from .qa import gates

    path = folder / "review" / "qa.json"
    if not path.is_file():
        raise AltocueError(f"{folder.name} has no QA report.", fix=f"Run `altocue qa -p {folder}`.")
    report = json.loads(path.read_text())
    ran, ships = [gate["id"] for gate in report["gates"]], gates.gate_ids()
    if set(ran) != set(ships):
        raise AltocueError(
            f"The QA of {folder.name} is older than the gates that ship: it ran {len(ran)}, and {len(ships)} ship now.",
            fix=f"Run `altocue qa -p {folder}` again, then build the film again.",
        )
    file = Path(report["file"])
    if not file.is_file() or _sha(file) != report["sha256"]:
        raise AltocueError(f"{file.name} in {folder.name} has changed since its QA.", fix=f"Run `altocue qa -p {folder}` again, then build the film again.")
    rows = report["gates"]
    if read == "passed":
        return sum(1 for gate in rows if gate["passed"] and not gate.get("skipped"))
    if read == "failed":
        return sum(1 for gate in rows if not gate["passed"] and not gate.get("skipped"))
    if read == "gates":
        return len(rows)
    if read == "not_applicable":
        return sum(1 for gate in rows if gate.get("value") == gates.NOT_APPLICABLE)
    found = next((gate for gate in rows if gate["id"] == read), None)
    if found is None:
        raise AltocueError(f"No gate called {read} in the QA of {folder.name}.")
    return found["value"]


def _review(folder: Path, number: int, read: str) -> Any:
    path = folder / "review" / "rounds.json"
    if not path.is_file():
        raise AltocueError(f"{folder.name} has no review rounds.", fix=f"Review it with `altocue review -p {folder}`.")
    rounds = json.loads(path.read_text())["rounds"]
    chosen = rounds[number]
    final = folder / "out" / "final.mp4"
    if number in (-1, len(rounds) - 1) and (not final.is_file() or _sha(final) != chosen["file_sha256"]):
        raise AltocueError(f"The film in {folder.name} has changed since its last review.", fix=f"Review it again with `altocue review -p {folder}`.")
    return chosen[read]


def _file(path: Path, read: str) -> Any:
    if not path.is_file():
        raise AltocueError(f"No file at {path}.")
    if read == "seconds":
        return float(ffmpeg.probe_json(path)["format"]["duration"])
    return ffmpeg.loudness(path)[read]


def value(source: dict[str, Any], root: Path) -> Any:
    """One number, read now. Raises when what it is read from is out of date, so a stale number is never shown."""
    if "count" in source:
        return count(str(source["count"]))
    if "qa" in source:
        return _qa(Path(source["qa"]).expanduser(), str(source["read"]))
    if "review" in source:
        return _review(Path(source["review"]).expanduser(), int(source.get("round", -1)), str(source["read"]))
    if "file" in source:
        return _file(root / str(source["file"]), str(source["read"]))
    raise AltocueError(f"Cannot read a live number from {source}.", fix="Name a count, qa, review or file.")


def shown(found: Any, spec: str = "") -> str:
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return str(found)
    if spec:
        return format(found, spec)
    return str(found) if isinstance(found, int) else f"{found:g}"


def fill(board: dict[str, Any], numbers: dict[str, dict[str, Any]], root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Put every live number into the storyboard, and record where each one landed."""
    read = {name: value(spec["source"], root) for name, spec in numbers.items()}
    places: dict[str, list[list[Any]]] = {name: [] for name in numbers}

    def put(node: Any, path: list[Any]) -> Any:
        if isinstance(node, dict):
            return {key: put(child, [*path, key]) for key, child in node.items()}
        if isinstance(node, list):
            return [put(child, [*path, index]) for index, child in enumerate(node)]
        if not isinstance(node, str) or not TOKEN.search(node):
            return node
        for name in TOKEN.findall(node):
            if name not in numbers:
                raise AltocueError(f"The storyboard asks for {{live:{name}}}, which nothing reads.", fix=f"Add {name} to the numbers the film reads.")
            places[name].append(path)
        whole = TOKEN.fullmatch(node)
        if whole and not numbers[whole.group(1)].get("format") and isinstance(read[whole.group(1)], (int, float)):
            return read[whole.group(1)]
        return TOKEN.sub(lambda match: shown(read[match.group(1)], numbers[match.group(1)].get("format", "")), node)

    filled = put(board, [])
    record = {
        "numbers": [
            {"name": name, "source": spec["source"], "format": spec.get("format", ""), "shown": shown(read[name], spec.get("format", "")), "places": places[name]}
            for name, spec in numbers.items()
        ]
    }
    return filled, record


def _at(board: Any, path: list[Any]) -> Any:
    node = board
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def _shows(text: Any, wanted: str, spec: str) -> bool:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return shown(text, spec) == wanted
    return isinstance(text, str) and re.search(rf"(?<![\w.]){re.escape(wanted)}(?![\w])", text) is not None


def check(root: Path, board: dict[str, Any], record: dict[str, Any] | None = None) -> list[storyboard.Problem]:
    """Every recorded number, read again and found again in its places. Nothing to do for a film with no live numbers."""
    if record is None:
        path = root / FILE
        if not path.is_file():
            return []
        record = json.loads(path.read_text())
    problems = []
    for entry in record.get("numbers", []):
        where = f"live/{entry['name']}"
        try:
            now = shown(value(entry["source"], root), entry.get("format", ""))
        except AltocueError as error:
            problems.append(storyboard.Problem("error", where, error.args[0], error.fix or REBUILD))
            continue
        if now != entry["shown"]:
            problems.append(storyboard.Problem("error", where, f"The film shows {entry['shown']} for {entry['name']}, and the tool reads {now} now.", REBUILD))
            continue
        for place in entry["places"]:
            if not _shows(_at(board, place), now, entry.get("format", "")):
                spot = "/".join(str(part) for part in place)
                problems.append(storyboard.Problem("error", where, f"{spot} no longer shows {entry['name']} as {now}.", REBUILD))
    return problems
