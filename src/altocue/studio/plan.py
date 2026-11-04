"""Portable, local-input direction contract. All times are integer output frames."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image

from ..errors import AltocueError

RECIPES = {
    "character-sweep": "Animate the actual product character with a short sweep on the spoken noun.",
    "voice-sculpture": "Drive a lit metallic surface and voice trace from real PCM.",
    "logo-dock": "Close and reopen independently supplied mark parts as they dock into an actual page capture.",
    "logo-roster": "Illuminate genuine supplied marks in spoken order.",
    "typing": "Reveal one character per frame cue; trim one local tap to that same event.",
    "file-restore": "One continuous document journey to a destination and back.",
    "before-after": "Carry one continuous comparison over a dialogue repair.",
    "repo-tour": "Show the captured masthead, scroll, hold a real diagram, then continue.",
    "file-handoff": "Move a document between two owners with actual marks.",
    "parallel-jobs": "Show three simultaneous roles from the same clock.",
    "asset-reveal": "Introduce a supplied transparent product mark or character on its spoken word.",
    "brand-open": "Open the genuine house mark and hold a single keyword.",
}


def need(ok, message):
    if not ok:
        raise AltocueError(message, fix="Correct studio.json. Run 'altocue studio example' for a complete local example.")


def local(root, rel):
    need(isinstance(rel, str) and rel and not Path(rel).is_absolute(), "Studio assets must be relative local files.")
    p = (root / rel).resolve()
    need(p.is_relative_to(root.resolve()) and p.is_file(), f"Missing or outside-project studio asset: {rel!r}.")
    return p


def integer(x):
    return type(x) is int


def textok(x):
    return isinstance(x, str) and bool(x.strip())


def load(root, total=None, fps=None):
    try:
        plan = json.loads((root / "studio.json").read_text())
    except (OSError, ValueError) as e:
        raise AltocueError(f"Cannot read studio.json: {e}", fix="Create the manifest or remove it to use ordinary motion.") from e
    need(isinstance(plan, dict) and plan.get("schema") == 1, "Expected Studio schema 1.")
    need(integer(plan.get("fps")) and 12 <= plan["fps"] <= 120, "Frame rate must be an integer from 12 to 120.")
    need(fps is None or fps == plan["fps"], "Studio and project frame rates must agree; do not silently retime word cues.")
    need(textok(plan.get("house")) and textok(plan.get("product")), "Set the house and product label.")
    need(plan.get("theme", "ink") in {"ink", "forest"}, "Unknown Studio palette.")
    files = plan.get("assets")
    need(isinstance(files, dict) and len(files) > 0, "Supply local artwork and optional sounds.")
    assets = {str(k): str(local(root, v)) for k, v in files.items()}

    def art(k):
        need(isinstance(k, str) and k in assets, f"Missing artwork key {k!r}.")
        try:
            with Image.open(assets[k]) as im:
                im.verify()
        except (OSError, ValueError) as e:
            raise AltocueError(f"Artwork {k!r} cannot be read.", fix="Use a valid local raster image with transparency where appropriate.") from e

    art(plan.get("mark"))
    sc = plan.get("scenes")
    need(isinstance(sc, list) and 0 < len(sc) <= 200, "Expected 1 to 200 scenes.")
    last = 0
    for row in sc:
        need(isinstance(row, dict), "Each scene must be an object.")
        kind = row.get("recipe")
        need(isinstance(kind, str) and kind in RECIPES, f"Unknown scene: {kind!r}.")
        a, b = row.get("start"), row.get("end")
        need(integer(a) and integer(b) and last <= a < b, "Scenes must be ordered, nonoverlapping output frame ranges.")
        need(total is None or b <= total, "A studio scene runs beyond the final output.")
        last = b
        if kind != "brand-open":
            need(textok(row.get("title")), "Each explanatory scene needs a title.")
        if kind in {"asset-reveal", "repo-tour", "character-sweep"}:
            art(row.get("asset"))
        if kind == "voice-sculpture":
            need(isinstance(row.get("voice"), str) and row["voice"] in assets, "Bring a local voice track for the sculpture.")
            need(integer(row.get("voice_start", 0)) and row.get("voice_start", 0) >= 0, "Invalid sculpture voice start frame.")
        if kind == "logo-dock":
            art(row.get("page"))
            parts = row.get("parts")
            need(isinstance(parts, list) and 2 <= len(parts) <= 8, "Supply two to eight actual transparent mark pieces.")
            sizes = set()
            for k in parts:
                art(k)
                with Image.open(assets[k]) as part:
                    need("A" in part.getbands(), "Export mark pieces with a transparent alpha channel.")
                    sizes.add(part.size)
            need(len(sizes) == 1, "Export mark pieces on one common canvas to preserve registration.")
            target = row.get("target")
            need(
                isinstance(target, list) and len(target) == 3 and all(type(v) in (int, float) and 0 < v < 1 for v in target),
                "Supply the page target x, y and width as fractions.",
            )
        if kind == "brand-open":
            need(textok(row.get("keyword")), "Missing final keyword.")
            art(row.get("asset", plan["mark"]))
        if kind in {"logo-roster", "parallel-jobs", "file-handoff", "before-after"}:
            items = row.get("items")
            expected = 2 if kind in {"file-handoff", "before-after"} else 3
            need(isinstance(items, list) and len(items) == expected, f"{kind} requires {expected} items.")
            for item in items:
                need(isinstance(item, dict) and textok(item.get("name")), "Each item needs a name.")
                if kind != "before-after":
                    art(item.get("asset"))
                if kind in {"before-after", "parallel-jobs"}:
                    need(textok(item.get("detail")), "Missing item detail.")
                if kind == "logo-roster":
                    need(integer(item.get("at")) and 0 <= item["at"] < b - a, "Logo cue falls outside its scene.")
                    hold = item.get("hold", 0.8)
                    need(type(hold) in (int, float) and math.isfinite(hold) and hold >= 0, "Invalid logo hold.")
            if kind == "file-handoff":
                need(textok(row.get("file_label")), "Missing display filename.")
        if kind == "typing":
            keys = row.get("keys")
            line = row.get("text")
            need(textok(line) and len(line) <= 24 and isinstance(keys, list) and len(keys) == len(line), "Supply one reveal frame per character (up to 24 characters).")
            need(all(integer(v) and 0 <= v < b - a for v in keys) and keys == sorted(set(keys)), "Keystrokes must be distinct ascending frames inside the scene.")
            if "sound" in row:
                need(isinstance(row["sound"], str) and row["sound"] in assets, "Keystroke audio asset is missing.")
        if kind == "file-restore":
            times = row.get("travel")
            labels = row.get("labels")
            need(isinstance(labels, list) and len(labels) == 2 and all(map(textok, labels)), "Name the two file locations.")
            need(
                isinstance(times, list) and len(times) == 4 and all(integer(x) and 0 <= x < b - a for x in times) and times == sorted(set(times)),
                "Supply four ordered travel frames.",
            )
        if kind == "repo-tour":
            times = row.get("scroll_frames")
            offsets = row.get("scroll_y")
            need(textok(row.get("source_label")), "Identify the captured source.")
            need(isinstance(times, list) and len(times) == 3 and all(integer(v) for v in times), "Supply scroll arrival, hold end, and scroll end frames.")
            need(0 < times[0] < times[1] < times[2] <= b - a and times[1] - times[0] >= 2 * plan["fps"], "Hold the real diagram for at least two seconds.")
            need(
                isinstance(offsets, list) and len(offsets) == 3 and all(type(v) in (int, float) and 0 <= v <= 1 for v in offsets),
                "Scroll positions must be fractions from 0 to 1.",
            )
            if "detail" in row:
                art(row["detail"])
                box = row.get("detail_box", [0, 0, 1, 1])
                need(
                    isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) for v in box) and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1,
                    "Invalid diagram detail crop.",
                )
    need(last <= plan["fps"] * 3600, "Studio plan exceeds one hour.")
    return {**plan, "_assets": assets}


def check_speech(words, rules, fps):
    """Independently transcribed final words; count phrases within an explicit time range, never the whole shoot."""

    def norm(s):
        return re.findall(r"[^\W_]+", s.casefold().replace("’", "'"), flags=re.UNICODE)

    result = []
    for r in rules:
        phrase = norm(r["phrase"])
        need(bool(phrase), "Empty phrase check.")
        tokens = [t for w in words if r["start"] / fps <= w["start"] < r["end"] / fps for t in norm(w["text"])]
        count = sum(tokens[n : n + len(phrase)] == phrase for n in range(len(tokens) - len(phrase) + 1))
        result.append({"phrase": r["phrase"], "expected": r["count"], "observed": count, "passed": count == r["count"]})
    return result
