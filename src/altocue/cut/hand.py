"""Hand edits: what a person set on a segment, as opposed to what a stage solved.

The frame stage records what it solved in work/frame.json, so anything in edl.json that differs
came from a person. A re-cut rebuilds the segments, so these edits travel with the words they sat on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import Edl, Segment, Word
from ..project import Project
from .snap import plays


@dataclass
class Claim:
    seconds: float
    value: Any
    source: str


def blank(segment: Segment) -> bool:
    return segment.crop is None and segment.zoom_from == 1.0 and segment.zoom_to == 1.0


def solved_rows(project: Project) -> dict[str, dict[str, Any]]:
    path = project.work / "frame.json"
    return {row["id"]: row for row in json.loads(path.read_text()).get("segments", [])} if path.exists() else {}


def same_shot(row: dict[str, Any] | None, segment: Segment) -> bool:
    return bool(row) and (row["clip"], row.get("start", segment.start), row.get("end", segment.end)) == (segment.clip, segment.start, segment.end)


def hand_set(project: Project, edl: Edl) -> dict[str, dict[str, Any]]:
    """Each segment's hand-set `gain`, `crop` and `zoom`.

    A re-cut leaves segments blank, so a blank one only counts as a choice when the frame report
    was written for exactly this cut. A cut that is blank all through has not been framed yet.
    """
    rows = solved_rows(project)
    framed = not all(blank(s) and not s.held for s in edl.segments)
    current = framed and len(rows) == len(edl.segments) and all(same_shot(rows.get(s.id), s) for s in edl.segments)
    found: dict[str, dict[str, Any]] = {}
    for segment in edl.segments:
        row = rows.get(segment.id) if framed and same_shot(rows.get(segment.id), segment) else None
        crop, zoom = (row["crop"], (row["zoom_from"], row["zoom_to"])) if row else (None, (1.0, 1.0))
        chosen = (segment.zoom_from, segment.zoom_to)
        edits: dict[str, Any] = {}
        if segment.gain != 1.0 or "gain" in segment.held:
            edits["gain"] = segment.gain
        if "crop" in segment.held or (segment.crop != crop and (segment.crop is not None or current)):
            edits["crop"] = segment.crop
        if "zoom" in segment.held or (chosen != zoom and (chosen != (1.0, 1.0) or current)):
            edits["zoom"] = chosen
        if edits:
            found[segment.id] = edits
    return found


def describe(field: str, value: Any) -> str:
    if field == "zoom":
        return f"zoom {value[0]:g}" if value[0] == value[1] else f"zoom {value[0]:g} to {value[1]:g}"
    if field == "crop":
        return "crop " + " ".join(f"{v:g}" for v in value) if value else "no crop"
    return f"gain {value:g}"


def shared(words: list[Word], a: Segment, b: Segment) -> float:
    """Seconds of speech both segments play. A stretch with no words goes by time."""
    spoken = [word for word in words if plays(word, a.start, a.end)]
    if spoken:
        return sum(word.end - word.start for word in spoken if plays(word, b.start, b.end))
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def place(segment: Segment, field: str, value: Any) -> None:
    if field == "zoom":
        segment.zoom_from, segment.zoom_to = value
    else:
        setattr(segment, field, value)
    segment.held = sorted({*segment.held, field})


def carry(project: Project, old: Edl, new: Edl) -> None:
    """Move hand edits onto the segments that still play their words, and note any that cannot go.

    A segment that splits gives its edit to both halves. Where two merge, the one with more speech wins.
    """
    shots = [(s.id, s.clip, s.start, s.end) for s in new.segments]
    if shots == [(s.id, s.clip, s.start, s.end) for s in old.segments]:
        for before, after in zip(old.segments, new.segments, strict=True):
            after.gain, after.crop, after.zoom_from, after.zoom_to, after.held = before.gain, before.crop, before.zoom_from, before.zoom_to, before.held
            after.path = before.path
        return
    edits = hand_set(project, old)
    words = {clip: project.words(clip) for clip in {s.clip for s in old.segments if s.id in edits}}
    claims: dict[tuple[str, str], Claim] = {}
    lost: list[str] = []
    for source in old.segments:
        takers = [(shared(words[source.clip], source, s), s) for s in new.segments if source.id in edits and s.clip == source.clip]
        takers = [(seconds, s) for seconds, s in takers if seconds > 0]
        for field, value in edits.get(source.id, {}).items():
            if not takers:
                lost.append(f"{source.id}: {describe(field, value)} went with the words it was set on.")
            for seconds, target in takers:
                claim = claims.get((target.id, field))
                if claim is None or (seconds > claim.seconds and claim.value != value):
                    if claim:
                        lost.append(f"{claim.source}: {describe(field, claim.value)} lost to {source.id} when the two merged.")
                    claims[(target.id, field)] = Claim(seconds, value, source.id)
                elif claim.value != value:
                    lost.append(f"{source.id}: {describe(field, value)} lost to {claim.source} when the two merged.")
    by_id = {segment.id: segment for segment in new.segments}
    for (segment_id, field), claim in claims.items():
        place(by_id[segment_id], field, claim.value)
    new.lost = lost
