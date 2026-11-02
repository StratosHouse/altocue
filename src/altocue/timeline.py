"""Resolve the edit, the captions and the graphics into one output timeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import brand as brands
from . import cut, ffmpeg, mix, overlays, platforms, transitions
from .errors import AltocueError
from .models import Edl
from .motion import compositions, formats, placement
from .project import Project, write_json

END_HOLD_TYPES = {"endcard"}
DEFAULT_LENGTHS = {"hook": 2.6, "chip": 2.4, "lower_third": 3.2, "stat": 2.8, "quote": 3.6, "steps": 6.0, "title": 2.4, "endcard": 2.6, "composition": 3.0}
STILL_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
CUT_REACH = 0.75  # A word starts a little after the cut it follows, by up to the pause the cut kept.


def bare(word: str) -> str:
    return word.replace("\u2019", "'").strip(" .,!?;:\"'").lower()


def resolve_time(value: Any, words: list[dict[str, Any]], what: str = "Graphic") -> float:
    """A time is seconds, or `{"word": "...", "occurrence": 1, "offset": 0}` to follow speech."""
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, dict) or "word" not in value:
        raise AltocueError(f"Cannot read the time {value!r}.", fix='Use seconds, or {"word": "...", "occurrence": 1}.')
    wanted = bare(str(value["word"]))
    occurrence = int(value.get("occurrence", 1))
    hits = [w for w in words if bare(w["text"]) == wanted]
    if occurrence < 1 or len(hits) < occurrence:
        raise AltocueError(f'{what} is anchored to "{value["word"]}", which is not in the cut.', fix="Pick a word that is still spoken, or use a time in seconds.")
    return hits[occurrence - 1]["start"] + float(value.get("offset", 0))


def resolve_transitions(raw: list[dict[str, Any]], words: list[dict[str, Any]], edl: Edl) -> list[dict[str, Any]]:
    """Each transition lands on the cut nearest its anchor. A dissolve with no cut under it blends a shot with itself."""
    fps = edl.fps
    lengths = [max(1, round((s.end - s.start) * fps)) for s in edl.segments]
    cuts = [sum(lengths[:index]) for index in range(1, len(lengths))]
    found: dict[int, dict[str, Any]] = {}
    for entry in raw:
        item = transitions.parse(entry)
        at = resolve_time(entry.get("at"), words)
        near = min(range(len(cuts)), key=lambda index: abs(cuts[index] / fps - at), default=None)
        if near is None or abs(cuts[near] / fps - at) > CUT_REACH:
            listed = ", ".join(f"{frame / fps:.2f}" for frame in cuts) or "none"
            raise AltocueError(f"No cut within {CUT_REACH} s of {at:.2f} s for the {item['type']}.", fix=f"Anchor it to a word at a cut. Cuts are at: {listed}.")
        if near + 1 in found:
            raise AltocueError(f"The cut at {cuts[near] / fps:.2f} s has more than one transition.", fix="Keep one.")
        frames = max(2, round(item["duration"] * fps))
        start = cuts[near] - frames // 2
        found[near + 1] = {"cut": near + 1, "frames": frames, "type": item["type"], "start": round(start / fps, 6), "end": round((start + frames) / fps, 6)}
    return [found[cut] for cut in sorted(found)]


def resolve_freezes(raw: list[dict[str, Any]], words: list[dict[str, Any]], fps: int) -> list[dict[str, Any]]:
    """A freeze holds the frame on its anchor. The length is frames of picture, and sound carries on under them."""
    holds = []
    for entry in raw:
        length = float(entry.get("duration", 0))
        if length <= 0:
            raise AltocueError("A freeze needs a duration in seconds.", fix='Use {"at": {"word": "stop"}, "duration": 0.6}.')
        frame = round(resolve_time(entry.get("at"), words) * fps)
        frames = max(1, round(length * fps))
        holds.append({"frame": frame, "frames": frames, "start": round(frame / fps, 6), "end": round((frame + frames) / fps, 6)})
    return holds


def picture_edits(project: Project, edl: Edl, *, words: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """What motion.json asks of the picture edit. The cut and the sound never change for these."""
    path = project.root / "motion.json"
    motion = json.loads(path.read_text()) if path.exists() else {}
    if not motion.get("transitions") and not motion.get("freezes"):
        return {"transitions": [], "freezes": []}
    words = cut.map_words(project, edl) if words is None else words
    return {"transitions": resolve_transitions(motion.get("transitions", []), words, edl), "freezes": resolve_freezes(motion.get("freezes", []), words, edl.fps)}


def resolve_overlays(raw: Any, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlays run the whole film unless they name a `start` or an `end`."""
    items = overlays.parse(raw)
    return [{**item, **{key: resolve_time(item[key], words) for key in ("start", "end") if key in item}} for item in items]


def resolve_items(raw: list[dict[str, Any]], words: list[dict[str, Any]], duration: float) -> tuple[list[dict[str, Any]], float]:
    """Return items with plain seconds, plus how long the film runs past the speech."""
    items, tail = [], 0.0
    for entry in raw:
        kind = entry.get("type")
        fmt = formats.find(kind)
        if kind not in DEFAULT_LENGTHS and fmt is None:
            raise AltocueError(f"Unknown graphic type {kind}.", fix="Run `altocue library search <what you want>` to find a format.")
        item = dict(entry)
        formats.check(item)
        length = float(entry.get("duration", fmt.seconds if fmt else DEFAULT_LENGTHS[kind]))
        if kind in END_HOLD_TYPES:
            item["start"] = duration
            item["end"] = duration + length
            tail = max(tail, length)
        else:
            item["start"] = resolve_time(entry.get("start", 0), words)
            item["end"] = resolve_time(entry["end"], words) if "end" in entry else item["start"] + length
        for key, value in entry.items():
            if isinstance(value, list) and any(isinstance(row, dict) and "at" in row for row in value):
                item[key] = [{**row, "at": resolve_time(row["at"], words)} if isinstance(row, dict) and "at" in row else row for row in value]
        items.append(item)
    return sorted(items, key=lambda i: i["start"]), tail


def resolve_sfx(raw: Any, words: list[dict[str, Any]], total: float, root: Path) -> tuple[bool, list[dict[str, Any]]]:
    """Whether the house sounds play, and the project's own cues in plain seconds.

    `sfx` is true or false for the house sounds, a list of cues to add to them, or
    `{"house": false, "cues": [...]}` to hear only the cues.
    """
    if raw is None or isinstance(raw, bool):
        return raw is not False, []
    house, cues = (raw.get("house", True), raw.get("cues", [])) if isinstance(raw, dict) else (True, raw)
    if not isinstance(cues, list):
        raise AltocueError(f"Cannot read the sfx setting {raw!r}.", fix='Use true, false, or a list such as [{"name": "whoosh", "at": {"word": "next"}}].')
    return bool(house), [_resolve_cue(cue, words, total, root) for cue in cues]


def _resolve_cue(cue: dict[str, Any], words: list[dict[str, Any]], total: float, root: Path) -> dict[str, Any]:
    if "at" not in cue:
        raise AltocueError(f"Effect {cue} has no time.", fix='Add "at": seconds, or {"word": "..."}.')
    if "name" in cue:
        mix.sfx_path(cue["name"])
    elif "file" in cue:
        path = (root / cue["file"]).resolve()
        if not path.exists():
            raise AltocueError(f"No effect at {path}.", fix="Check the path. It is relative to the project folder.")
        cue = {**cue, "file": str(path)}
    else:
        raise AltocueError(f"Effect {cue} has no sound.", fix=f'Add "file", or a "name" from {", ".join(mix.sfx_names())}.')
    at = resolve_time(cue["at"], words, "Effect")
    if not 0 <= at < total:
        raise AltocueError(f"Effect at {at:.2f}s is outside the {total:.2f}s film.", fix="Move it inside the film, or remove it.")
    if not mix.SFX_UNDER_RANGE[0] <= float(cue.get("under_db", mix.SFX_UNDER_DB)) <= mix.SFX_UNDER_RANGE[1]:
        raise AltocueError(f"Effect level {cue['under_db']:g} dB under the voice is out of range.", fix="Use 8 to 12 dB under the voice.")
    return {**cue, "at": at}


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
        inserts.append({"file": file, "start": start, "end": end, **edges(entry)})
    return inserts


def edges(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """An insert may arrive or leave by a transition. By default it cuts."""
    return {edge: transitions.parse(entry[edge]) for edge in ("in", "out") if entry.get(edge)}


def chosen_broll(project: Project) -> list[dict[str, Any]]:
    """B-roll sits on words, so it is only true for the cut it was chosen on."""
    path = project.root / "broll.json"
    if not path.exists():
        return []
    chosen = json.loads(path.read_text())
    if chosen.get("inserts") and chosen.get("cut") != project.cut_mark():
        raise AltocueError("The cut changed after the B-roll was chosen.", fix="Run `altocue broll`, then pick again. Or delete broll.json.")
    return [{**insert, **edges(insert)} for insert in chosen.get("inserts", [])]


def build(project: Project) -> dict[str, Any]:
    edl = project.edl()
    words = cut.map_words(project, edl)
    captions = json.loads((project.work / "captions.json").read_text())["pages"] if (project.work / "captions.json").exists() else []
    motion_file = project.root / "motion.json"
    motion = json.loads(motion_file.read_text()) if motion_file.exists() else {}
    duration = round(sum(round((s.end - s.start) * edl.fps) for s in edl.segments) / edl.fps, 6)
    edits = picture_edits(project, edl, words=words)
    items, tail = resolve_items(motion.get("items", []), words, duration)
    items = [compositions.resolve(project, item) if item["type"] == "composition" else formats.place_images(project.root, item) for item in items]
    width, height = project.size
    brand = brands.load(project)
    picture = next((path for path in (project.work / "plate.mp4", project.work / "plate-draft.mp4") if path.exists()), None)
    zone = platforms.zone_of(project)
    items = placement.place(items, picture, (width, height), brand, zone)
    house, cues = resolve_sfx(motion.get("sfx"), words, duration + tail, project.root)
    timeline = {
        "width": width,
        "height": height,
        "zone": zone,
        "fps": edl.fps,
        "duration": duration,
        "tail": tail,
        "total": round(duration + tail, 6),
        "brand": brand.data,
        "captions": captions,
        "items": items,
        "music": motion.get("music"),
        "sfx": house,
        "cues": cues,
        "inserts": sorted(resolve_inserts(project, motion.get("inserts", []), words) + chosen_broll(project), key=lambda i: i["start"]),
        "transitions": edits["transitions"],
        "freezes": edits["freezes"],
        "overlay": resolve_overlays(motion.get("overlay"), words),
    }
    write_json(project.timeline_path, timeline)
    return timeline
