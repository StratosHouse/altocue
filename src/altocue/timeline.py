"""Resolve the edit, the captions and the graphics into one output timeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import brand as brands
from . import cut, ffmpeg
from .errors import AltocueError
from .motion import placement
from .project import Project, write_json

END_HOLD_TYPES = {"endcard"}
DEFAULT_LENGTHS = {"hook": 2.6, "chip": 2.4, "lower_third": 3.2, "stat": 2.8, "quote": 3.6, "steps": 6.0, "title": 2.4, "endcard": 2.6}
KNOWN_TYPES = set(DEFAULT_LENGTHS)
STILL_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


def bare(word: str) -> str:
    return word.replace("\u2019", "'").strip(" .,!?;:\"'").lower()


def resolve_time(value: Any, words: list[dict[str, Any]]) -> float:
    """A time is seconds, or `{"word": "...", "occurrence": 1, "offset": 0}` to follow speech."""
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, dict) or "word" not in value:
        raise AltocueError(f"Cannot read the time {value!r}.", fix='Use seconds, or {"word": "...", "occurrence": 1}.')
    wanted = bare(str(value["word"]))
    occurrence = int(value.get("occurrence", 1))
    hits = [w for w in words if bare(w["text"]) == wanted]
    if occurrence < 1 or len(hits) < occurrence:
        raise AltocueError(f'Graphic is anchored to "{value["word"]}", which is not in the cut.', fix="Pick a word that is still spoken, or use a time in seconds.")
    return hits[occurrence - 1]["start"] + float(value.get("offset", 0))


def resolve_items(raw: list[dict[str, Any]], words: list[dict[str, Any]], duration: float) -> tuple[list[dict[str, Any]], float]:
    """Return items with plain seconds, plus how long the film runs past the speech."""
    items, tail = [], 0.0
    for entry in raw:
        kind = entry.get("type")
        if kind not in KNOWN_TYPES:
            raise AltocueError(f"Unknown graphic type {kind}.", fix=f"Use one of {', '.join(sorted(KNOWN_TYPES))}.")
        item = dict(entry)
        length = float(entry.get("duration", DEFAULT_LENGTHS[kind]))
        if kind in END_HOLD_TYPES:
            item["start"] = duration
            item["end"] = duration + length
            tail = max(tail, length)
        else:
            item["start"] = resolve_time(entry.get("start", 0), words)
            item["end"] = resolve_time(entry["end"], words) if "end" in entry else item["start"] + length
        if kind == "steps":
            item["items"] = [{**step, "at": resolve_time(step["at"], words)} for step in entry.get("items", [])]
        items.append(item)
    return sorted(items, key=lambda i: i["start"]), tail


def resolve_inserts(project: Project, raw: list[dict[str, Any]], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An insert is a file or a generated shot id, placed by time or by a spoken word."""
    inserts = []
    for entry in raw:
        if "shot" in entry:
            from . import generate

            asset = generate.generated_asset(project, entry["shot"])
            file = str(Path(asset).relative_to(project.root))
        else:
            file = entry["file"]
        path = project.root / file
        if path.suffix.lower() not in STILL_TYPES and ffmpeg.probe_clip(path).is_hdr:
            raise AltocueError(f"{file} is HDR, so it would look washed out here.", fix="Place it with `altocue broll --pick b1=file:...`, which converts it.")
        start = resolve_time(entry["start"], words)
        end = resolve_time(entry["end"], words) if "end" in entry else start + float(entry.get("duration", 2.5))
        inserts.append({"file": file, "start": start, "end": end})
    return inserts


def chosen_broll(project: Project) -> list[dict[str, Any]]:
    """B-roll sits on words, so it is only true for the cut it was chosen on."""
    path = project.root / "broll.json"
    if not path.exists():
        return []
    chosen = json.loads(path.read_text())
    if chosen.get("inserts") and chosen.get("cut") != project.cut_mark():
        raise AltocueError("The cut changed after the B-roll was chosen.", fix="Run `altocue broll`, then pick again. Or delete broll.json.")
    return chosen.get("inserts", [])


def build(project: Project) -> dict[str, Any]:
    edl = project.edl()
    words = cut.map_words(project, edl)
    captions = json.loads((project.work / "captions.json").read_text())["pages"] if (project.work / "captions.json").exists() else []
    motion_file = project.root / "motion.json"
    motion = json.loads(motion_file.read_text()) if motion_file.exists() else {}
    duration = round(sum(round((s.end - s.start) * edl.fps) for s in edl.segments) / edl.fps, 6)
    items, tail = resolve_items(motion.get("items", []), words, duration)
    width, height = project.size
    brand = brands.load(project)
    picture = next((path for path in (project.work / "plate.mp4", project.work / "plate-draft.mp4") if path.exists()), None)
    items = placement.place(items, picture, (width, height), brand)
    timeline = {
        "width": width,
        "height": height,
        "fps": edl.fps,
        "duration": duration,
        "tail": tail,
        "total": round(duration + tail, 6),
        "brand": brand.data,
        "captions": captions,
        "items": items,
        "music": motion.get("music"),
        "sfx": motion.get("sfx", True),
        "inserts": sorted(resolve_inserts(project, motion.get("inserts", []), words) + chosen_broll(project), key=lambda i: i["start"]),
    }
    write_json(project.timeline_path, timeline)
    return timeline
