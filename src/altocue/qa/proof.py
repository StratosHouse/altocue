"""Product Proof Spot's own checks: the label reads what the real one says, the product stays in frame, and hands are clear at the end.

`altocue qa --check proof` runs them on the render, never as part of a normal run. They read a truth
list the person writes from the real product, `product.json` in the project:

    {"name": "Hedgerow jam", "label": ["Blackberry 55%", "300 g"], "hold": 1.5}

A generated angle or stunt is a claim, never proof (c309, c280, b011, k005). Text that belongs to the
real product is product truth, not edit text, so it is held to the real label.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..models import GateResult
from ..project import Project
from . import gates, hands, ocr, rules

PRODUCT = "product.json"
EDGE = 0.01  # a box this close to the frame's edge has been cut by it
HOLD = 1.5  # seconds of the final product hold, when product.json names none
HAND_SAMPLES = 4
FIELDS = {"name", "label", "hold"}


def load_truth(project: Project) -> dict[str, Any] | None:
    path = project.root / PRODUCT
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except ValueError as error:
        raise AltocueError(f"{PRODUCT} is not valid JSON: {error}", fix="Fix the file, or remove it.") from error
    unknown = set(data) - FIELDS if isinstance(data, dict) else {"(not an object)"}
    if unknown:
        raise AltocueError(f"Unknown keys in {PRODUCT}: {', '.join(sorted(unknown))}.", fix=f"Use only {', '.join(sorted(FIELDS))}.")
    if not (isinstance(data.get("label"), list) and data["label"] and all(isinstance(line, str) and line.strip() for line in data["label"])):
        raise AltocueError(f"{PRODUCT} has no label lines.", fix='List the lines on the real label, as printed: "label": ["Blackberry 55%", "300 g"].')
    return data


def one_off(token: str, wanted: str) -> bool:
    """A misprint a generated label makes: one letter or digit changed, added or dropped. 300 g against 800 g counts."""
    return token != wanted and len(wanted) >= 2 and abs(len(token) - len(wanted)) <= 1 and rules.edit_distance(token, wanted) <= 1


def read_lines(records: list[dict[str, Any]], lines: list[str]) -> dict[str, dict[str, list[Any]]]:
    """For each truth line: when it was read exactly, and every reading that is the line with something changed."""
    found: dict[str, dict[str, list[Any]]] = {line: {"exact": [], "warped": []} for line in lines}
    for record in records:
        seen = rules.tokens(record["text"])
        for line in lines:
            wanted = rules.tokens(line)
            for start in range(len(seen) - len(wanted) + 1 if wanted else 0):
                run = seen[start : start + len(wanted)]
                if run == wanted:
                    found[line]["exact"].append(record["time"])
                    break
                if all(token == want or one_off(token, want) for token, want in zip(run, wanted, strict=True)):
                    found[line]["warped"].append(record)
                    break
    return found


def cropped(records: list[dict[str, Any]], words: set[str]) -> list[dict[str, Any]]:
    """Whole-frame readings of the product's own words whose box touches the edge of the frame."""
    hits = []
    for record in records:
        if not Path(record["file"]).name.startswith("full_") or not words & set(rules.tokens(record["text"])):
            continue
        x, y, w, h = record["box"]
        if x <= EDGE or y <= EDGE or x + w >= 1 - EDGE or y + h >= 1 - EDGE:
            hits.append(record)
    return hits


def gate_label(truth: dict[str, Any], records: list[dict[str, Any]], step: float) -> GateResult:
    found = read_lines(records, [str(line) for line in truth["label"]])
    missing = [line for line, seen in found.items() if not seen["exact"]]
    firm, doubtful = [f"{line!r} is never read" for line in missing], []
    for line, seen in found.items():
        if seen["warped"]:
            times = ocr.merge_ranges([record["time"] for record in seen["warped"]], step)
            text = f"{line!r} reads as {seen['warped'][0]['text']!r} at {gates.spans([(a, b) for a, b in times])}"
            (firm if ocr.confidence(seen["warped"]) == "high" else doubtful).append(text)
    detail = "; ".join(firm) or f"{len(found)} label lines read as printed"
    if doubtful:
        detail += f". Needs a human look: {'; '.join(doubtful)}"
    return GateResult(id="product_label", passed=not firm, value={"findings": len(firm), "review": len(doubtful)}, threshold=0, detail=detail)


def gate_framed(truth: dict[str, Any], records: list[dict[str, Any]], step: float) -> GateResult:
    words = {token for line in [*truth["label"], str(truth.get("name", ""))] for token in rules.tokens(line) if len(token) >= 3}
    hits = cropped(records, words)
    times = ocr.merge_ranges([record["time"] for record in hits], step)
    detail = f"the product's own words touch the frame's edge at {gates.spans([(a, b) for a, b in times])}" if hits else "the label stays inside the frame"
    return GateResult(id="product_framed", passed=not hits, value=len(times), threshold=0, detail=f"{detail}, measured by where its label is read")


def gate_hands(c: gates.Context, hold: float) -> GateResult:
    ready, why = hands.detector()
    if not ready:
        return gates.skipped("hands_clear", why)
    end = c.footage_end
    moments = [max(end - hold + hold * (index + 0.5) / HAND_SAMPLES, 0.0) for index in range(HAND_SAMPLES)]
    with tempfile.TemporaryDirectory(prefix="altocue-hands-") as folder:
        grabbed = [(frame, at) for index, at in enumerate(moments) if (frame := gates.grab(c.file, at, Path(folder) / f"end_{index}.jpg"))]
        found = hands.find([frame for frame, _ in grabbed])
    if not found:
        return gates.skipped("hands_clear", "No frame of the final hold could be read for hands.")
    seen = [at for frame, at in grabbed if found.get(frame.name)]
    detail = f"a hand in the final hold at {', '.join(f'{at:.2f} s' for at in seen)}" if seen else f"no hand in the last {hold:g} s"
    return GateResult(id="hands_clear", passed=not seen, value=len(seen), threshold=0, detail=detail)


def run(c: gates.Context) -> list[GateResult]:
    truth = load_truth(c.project)
    ids = ("product_label", "product_framed", "hands_clear")
    if truth is None:
        return [gates.not_applicable(name, f"No {PRODUCT} in this project. Write the real label's lines there.") for name in ids]
    hold = float(truth.get("hold", HOLD))
    chosen, reason = ocr.reader()
    if chosen is None:
        results = [gates.skipped("product_label", reason), gates.skipped("product_framed", reason)]
    else:
        step = 1 / c.ocr_fps
        with tempfile.TemporaryDirectory(prefix="altocue-proof-") as folder:
            records = ocr.read_video(c.file, c.ocr_fps, [shot.start for shot in c.shots[1:]], Path(folder))
        results = [gate_label(truth, records, step), gate_framed(truth, records, step)]
    return [*results, gate_hands(c, hold)]
