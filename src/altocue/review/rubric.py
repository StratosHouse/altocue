"""Nine dimensions, their weights and the verdict rule. Code decides. The model only scores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import qa
from ..errors import AltocueError
from ..models import GateResult

FLOOR = 3
SHIP_AT = 3.8
NOTE_UNDER = 4
SEVERITIES = ("blocker", "major", "minor")
END_SLACK = 1.0  # MM:SS rounds, so an end time may land just past the last frame
FIX_REVIEW = "Fix the reviewer's output and ingest it again."


@dataclass(frozen=True)
class Dimension:
    weight: int
    ask: str


DIMENSIONS = {
    "hook": Dimension(20, "Would a stranger stay past 3 seconds?"),
    "clarity": Dimension(15, "Is the point obvious and is the order logical?"),
    "pacing": Dimension(15, "Any drag, or any cut that chops a thought or a payoff?"),
    "editing_residue": Dimension(15, "Any retake, stumble, filler or doubled phrase left in?"),
    "picture": Dimension(10, "Skin, exposure, consistency between shots, framing, headroom."),
    "sound_as_speech": Dimension(5, "Is every word easy to follow? Sound quality itself comes from the gates."),
    "captions": Dimension(10, "Readable on a phone, well timed, not covering the face."),
    "inserts": Dimension(5, "Relevant, and free of AI faults in hands, faces and on-screen text. Use null if there are none."),
    "ending": Dimension(5, "Does it land, and is any call to action clear?"),
}


def schema() -> dict[str, Any]:
    """The one output shape, for Gemini, for Codex and for an agent reading the pack."""

    def strict(properties: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}

    def mark(kind: str | list[str]) -> dict[str, Any]:
        return strict({"score": {"type": kind, "minimum": 1, "maximum": 5}, "why": {"type": "string"}})

    note = strict(
        {
            "start": {"type": "string", "description": "MM:SS or MM:SS.ss"},
            "end": {"type": ["string", "null"], "description": "MM:SS or MM:SS.ss, or null for one moment"},
            "severity": {"type": "string", "enum": list(SEVERITIES)},
            "dimension": {"type": "string", "enum": list(DIMENSIONS)},
            "observed": {"type": "string", "description": "What is seen or heard at that moment"},
            "suggested_fix": {"type": "string"},
        }
    )
    return strict(
        {
            "scores": strict({name: mark(["integer", "null"]) if name == "inserts" else mark("integer") for name in DIMENSIONS}),
            "notes": {"type": "array", "items": note},
            "could_not_judge": {"type": "array", "items": {"type": "string"}},
        }
    )


def parse_time(value: Any) -> float | None:
    """Seconds from MM:SS, HH:MM:SS, 12.5s or a plain number. None for anything else."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value >= 0 else None
    text = str(value).strip().removesuffix("s")
    parts = text.split(":")
    if not text or len(parts) > 3:
        return None
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in numbers) or (len(parts) > 1 and any(n >= 60 for n in numbers[1:])):
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def read_scores(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every dimension, each a whole 1 to 5. Anything else is refused, never guessed."""
    given = raw.get("scores")
    if not isinstance(given, dict):
        raise AltocueError("The review has no scores.", fix=FIX_REVIEW)
    marks: dict[str, dict[str, Any]] = {}
    for name in DIMENSIONS:
        entry = given.get(name)
        if not isinstance(entry, dict) or "score" not in entry:
            raise AltocueError(f"The review has no score for {name}.", fix=FIX_REVIEW)
        score = entry["score"]
        if score is None and name == "inserts":
            marks[name] = {"score": None, "why": str(entry.get("why", ""))}
            continue
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise AltocueError(f"The score for {name} is {score!r}, not a whole number from 1 to 5.", fix=FIX_REVIEW)
        marks[name] = {"score": score, "why": str(entry.get("why", ""))}
    return marks


def severity_of(note: dict[str, Any]) -> str:
    return str(note.get("severity", "")).strip().lower()


def read_notes(raw: dict[str, Any], duration: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the notes a person can check. The rest are set aside, with why, for the report."""
    kept, dropped = [], []
    for note in raw.get("notes") or []:
        if not isinstance(note, dict):
            dropped.append({"note": note, "why": "not a note"})
            continue
        start = parse_time(note.get("start"))
        end = parse_time(note.get("end")) if note.get("end") not in (None, "") else None
        why = ""
        if start is None:
            why = "no valid timecode"
        elif start > duration:
            why = "points past the end of the file"
        elif note.get("end") not in (None, "") and end is None:
            why = "the end time is not a timecode"
        elif end is not None and end < start:
            why = "ends before it starts"
        elif end is not None and end > duration + END_SLACK:
            why = "points past the end of the file"
        elif note.get("dimension") not in DIMENSIONS:
            why = "names no dimension in the rubric"
        elif severity_of(note) not in SEVERITIES:
            why = "has no valid severity"
        elif not str(note.get("observed", "")).strip():
            why = "says nothing about what was seen or heard"
        if why:
            dropped.append({"note": note, "why": why})
        else:
            kept.append({**note, "severity": severity_of(note), "start_seconds": start, "end_seconds": end, "suggested_fix": str(note.get("suggested_fix") or "")})
    return kept, dropped


def weighted(marks: dict[str, dict[str, Any]]) -> float:
    """The weighted mean. A dimension marked not applicable drops out of the weights."""
    scored = [(DIMENSIONS[name].weight, entry["score"]) for name, entry in marks.items() if entry["score"] is not None]
    return sum(w * s for w, s in scored) / sum(w for w, _ in scored)


def gate_reasons(gates: list[GateResult]) -> list[str]:
    reasons = [f"Gate failed: {g.id} ({g.detail})" for g in qa.failed(gates)]
    reasons += [f"Gate did not run: {g.id} ({g.detail})" for g in qa.not_run(gates)]
    return reasons


def decide(gates: list[GateResult], marks: dict[str, dict[str, Any]], notes: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """SHIP needs every gate, nothing under 3, 3.8 or more, and no blocker. Everything else is FIX."""
    reasons = gate_reasons(gates)
    reasons += [f"{name} scored {e['score']}, under {FLOOR}" for name, e in marks.items() if e["score"] is not None and e["score"] < FLOOR]
    total = weighted(marks)
    if total < SHIP_AT:
        reasons.append(f"Weighted score {total:.2f}, under {SHIP_AT}")
    reasons += [f"Blocker at {n['start']}: {n['observed']}" for n in notes if n["severity"] == "blocker"]
    return ("FIX" if reasons else "SHIP"), reasons


def score(raw: dict[str, Any], gates: list[GateResult], duration: float) -> dict[str, Any]:
    """Turn a reviewer's raw output into the verdict. Its own opinion of the verdict is ignored."""
    marks = read_scores(raw)
    kept, dropped = read_notes(raw, duration)
    verdict, reasons = decide(gates, marks, kept)
    lost = [d for d in dropped if isinstance(d["note"], dict) and severity_of(d["note"]) == "blocker"]
    if lost:  # a note that points nowhere is thrown away, but a claimed blocker still needs a second look
        verdict, reasons = "FIX", [*reasons, *(f"A blocker could not be placed: {d['why']}" for d in lost)]
    warnings = [f"Ignored the reviewer's own {key}: code decides." for key in ("verdict", "weighted") if key in raw]
    noted = {n["dimension"] for n in kept}
    return {
        "verdict": verdict,
        "reasons": reasons,
        "weighted": round(weighted(marks), 2),
        "scores": marks,
        "notes": kept,
        "dropped_notes": dropped,
        "unsupported_scores": [
            name for name, e in marks.items() if e["score"] is not None and e["score"] < NOTE_UNDER and name not in noted
        ],
        "could_not_judge": [str(item) for item in raw.get("could_not_judge") or []],
        "warnings": warnings,
    }


def gates_only(gates: list[GateResult]) -> dict[str, Any]:
    """A failed fact gate is FIX with no judgement and no model call."""
    return {
        "verdict": "FIX",
        "reasons": gate_reasons(gates),
        "weighted": None,
        "scores": {},
        "notes": [],
        "dropped_notes": [],
        "unsupported_scores": [],
        "could_not_judge": [],
        "warnings": [],
    }
