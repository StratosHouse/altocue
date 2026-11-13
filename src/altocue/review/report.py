"""REPORT.md: the verdict, the gates, the scores, the fixes in order, and the limits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import facts, qa
from ..models import GateResult
from ..project import Project, write_json
from ..qa.gates import NOT_APPLICABLE, grab
from . import rounds, rubric

SEVERITY_ORDER = {name: index for index, name in enumerate(rubric.SEVERITIES)}
ALWAYS = [
    "Loudness is measured over the whole file, so a short quiet or loud section can still pass.",
    "A transcript shows the words, not tone, delivery or whether a joke lands.",
    "Nothing here checks whether what the video claims is true.",
]
GATE_ACTIONS = {
    "sibilance": 'Set `[audio] deess = "light"`, or run `altocue clean --deess light`, then render again.',
    "music_under_voice": "Raise `[audio] music_under` or choose a calmer track, then render again.",
    "loudness": "Re-run `altocue master`.",
    "true_peak": "Re-run `altocue master`, and check the limiter ceiling.",
    "black_frames": "Find the black frames in the render and correct the cut or the layer.",
    "frozen_frames": "Check the source for that range, or label the segment as a hold.",
    "duration": "Re-render. A mismatch means the render and the EDL have drifted apart.",
    "cut_sound": "Move the cut to where speech starts, or label the shot as b-roll.",
    "word_starts": "Move the shot's in point back to the quiet before its first word, or run `altocue cut` again so the guard places it.",
    "word_ends": "Move the shot's out point on to the quiet after its last word, or run `altocue cut` again so the guard places it.",
    "silence": "Close the gap in the cut, or label the shot as b-roll.",
    "words": "Compare the render with the expected words, then fix the cut.",
    "doubled_phrase": "Remove the repeat, or allow it under `allow_repeats` in rules.json.",
    "captions_match": "Rebuild captions from the transcript. Never caption what was not said.",
    "caption_speed": "Give those pages longer on screen: fewer cuts, a slower read, or a tighter line in the cut. Never drop spoken words from a caption.",
    "brief_coverage": "Say the line, or reword it in brief.md to the words the film uses. A `|` offers other wordings.",
    "safe_zone": "Move the text inside the safe zone.",
    "face_clear": "Move the text off the face.",
    "text_readable": "Hold the text longer, or cut words.",
    "format": "Re-render with the project's output settings.",
    "flash_rate": "Slow the run of cuts or effects to three flashes a second or fewer, or take the swing out of them. See altocue-transitions.",
    "stray_frames": "Trim the insert to whole frames of one shot. Check the source for a cut near its end.",
    "rules_strings": "Change the on-screen text. A human decides how.",
    "rules_ocr": "Look at the frames named. A human decides how to fix them.",
    "claims": "Rewrite the line so the host shows what can be seen, or film a real customer who consents. A label does not cure this.",
    "frame_zero": "Put the subject in the first frame. A thumbnail and muted autoplay show this frame.",
    "dead_air": "Keep something moving across that range, or mark the hold in the storyboard.",
    "pops": "Look at the frames named. Keep the hits you meant, and smooth the rest.",
    "planned_silence": "Clear the sound from that range, or move the silence in the storyboard.",
    "cuts_to_plan": "Check the scene lengths against the storyboard, then render again.",
    "loop_seam": "Match the last frame to the first, in position and in speed.",
    "disclosure": "Add an on-screen label in motion.json, such as AI-generated actor, and flip the platform's AI toggle. See altocue-ugc.",
}


def limits(result: dict[str, Any], gates: list[GateResult]) -> list[str]:
    """Printed in every report. What this review did not do matters as much as what it did."""
    reviewer = result["reviewer"]
    sees = reviewer.get("sees")
    found: list[str] = []
    if sees == "video":
        fps = reviewer.get("sampled_fps") or 1
        found.append(f"The reviewer sampled the picture at {fps} frames a second, so a fault shorter than {1 / fps:.2f}s can fall between frames.")
        found.append(reviewer.get("audio_note", "The reviewer's hearing is limited.") + " Hiss, clipping and harshness come from the gates or not at all.")
    elif sees == "stills":
        found.append(
            "The reviewer saw still frames: contact sheets every 2 seconds, frames around cuts, the first 3 seconds and timeline sheets. "
            "Motion, lip sync and anything between samples were not seen."
        )
        found.append("The reviewer heard nothing. It read a transcript and measurements, and saw level and silence drawn on the timeline sheets.")
        if reviewer.get("images", 0) < reviewer.get("images_in_pack", 0):
            found.append(f"Only {reviewer['images']} of the pack's {reviewer['images_in_pack']} images were sent, so not every cut was shown.")
    else:
        found.append("No reviewer looked at or listened to this. Judgement did not run because a fact gate failed.")
    found += [f"Gate `{g.id}` did not run: {g.detail}" for g in gates if g.skipped and g.value != NOT_APPLICABLE]
    found += [f"Gate `{g.id}`: {g.detail}" for g in gates if "Needs a human look" in str(g.detail)]
    found += [f"The reviewer could not judge: {item}" for item in result.get("could_not_judge", [])]
    return found + ALWAYS


def _cell(text: Any) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def gate_rows(gates: list[GateResult]) -> list[str]:
    rows = ["| Gate | Result | Value | Limit | Detail |", "|---|---|---|---|---|"]
    for gate in gates:
        state = "SKIP" if gate.skipped else "PASS" if gate.passed else "FAIL"
        value = "" if gate.value is None else gate.value
        limit = "" if gate.threshold is None else gate.threshold
        rows.append(f"| {gate.id} | {state} | {_cell(value)} | {_cell(limit)} | {_cell(gate.detail)} |")
    return rows


def score_rows(result: dict[str, Any]) -> list[str]:
    rows = ["| Dimension | Weight | Score | Why |", "|---|---|---|---|"]
    for name, dimension in rubric.DIMENSIONS.items():
        entry = result["scores"].get(name)
        score = "n/a" if entry is None or entry["score"] is None else entry["score"]
        rows.append(f"| {name} | {dimension.weight} | {score} | {_cell(entry['why'] if entry else '')} |")
    return rows


def fix_list(result: dict[str, Any], gates: list[GateResult], frames: dict[int, Path]) -> list[str]:
    rows: list[str] = []
    for gate in qa.failed(gates):
        rows.append(f"{len(rows) + 1}. **Gate {gate.id}.** {gate.detail}. {GATE_ACTIONS.get(gate.id, '')}".rstrip())
    for gate in qa.not_run(gates):
        rows.append(f"{len(rows) + 1}. **Run gate {gate.id}.** {gate.detail}")
    for index, note in enumerate(result["notes"]):
        when = note["start"] if not note.get("end") else f"{note['start']} to {note['end']}"
        frame = f" Frame: `{frames[index].name}`." if index in frames else ""
        rows.append(
            f"{len(rows) + 1}. **{when}, {note['severity']}, {note['dimension']}.** {note['observed']} "
            f"Fix: {note['suggested_fix'].rstrip('.') or 'not suggested'}.{frame}"
        )
    return rows


def note_frames(project: Project, file: Path, notes: list[dict[str, Any]]) -> dict[int, Path]:
    """One frame per note, so a person can check it in a second."""
    folder = project.review_dir / "frames"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.jpg"):
        old.unlink()
    found = {}
    for index, note in enumerate(notes):
        target = grab(file, note["start_seconds"], folder / f"note_{index + 1:02d}.jpg", width=540)
        if target:
            found[index] = target
    return found


def spend_lines(project: Project) -> list[str]:
    """Stated in the report, because a film can pass every gate and still have spent money the cap never saw."""
    from ..generate import spend_report, store

    paid = store.ledger_path(project).exists() or store.reservations_path(project).exists()
    return spend_report(project) if paid else []


def facts_section(measured: dict[str, Any] | None) -> list[str]:
    """Below the verdict and the scores, because the reviewer must never have seen these numbers."""
    if not measured:
        return []
    if "error" in measured:
        return ["", "## Measured facts", "", f"Could not be measured: {measured['error']}"]
    return ["", "## Measured facts", "", facts.AFTER_SCORING, "", *facts.rows(measured)]


def write(project: Project, file: Path, result: dict[str, Any], gates: list[GateResult], log: dict[str, Any], *, measured: dict[str, Any] | None = None) -> Path:
    result["notes"].sort(key=lambda n: (SEVERITY_ORDER[n["severity"]], n["start_seconds"]))
    frames = note_frames(project, file, result["notes"])
    reviewer = result["reviewer"]
    result["limits"] = limits(result, gates)
    write_json(project.review_dir / "report.json", {**result, "gates": [g.to_dict() for g in gates], "rounds": log, "facts": measured})

    lines = [f"# Verdict: {result['verdict']}", ""]
    lines.append(f"- **File:** {file.name}, sha256 {result['file_sha256'][:12]}")
    if reviewer.get("vendor") == "none":
        lines.append("- **Reviewer:** none. Judgement did not run because a fact gate failed.")
    else:
        lines.append(f"- **Reviewer:** {reviewer.get('vendor')}, {reviewer.get('model')}, sees {reviewer.get('sees')}")
    if "independent" in reviewer:
        lines.append(f"- **independent: {str(reviewer['independent']).lower()}.** {reviewer['independent_why']}")
    if result["weighted"] is not None:
        lines.append(f"- **Weighted score:** {result['weighted']:.2f}, ship needs {rubric.SHIP_AT}")
    fixes = sum(1 for r in log["rounds"] if r["verdict"] == "FIX")
    lines.append(f"- **Fix rounds:** {min(fixes, rounds.MAX_FIX_ROUNDS)} of {rounds.MAX_FIX_ROUNDS} used")
    if log["handed_off"]:
        lines += [
            "",
            "**Handed to a human.** Both fix rounds are used. The list below is open, and no further automated review runs on a new render until `--reset-rounds`.",
        ]
    if result["reasons"]:
        lines += ["", "## Why", "", *[f"- {reason}" for reason in result["reasons"]]]
    lines += ["", "## Gates", "", *gate_rows(gates)]
    if result["scores"]:
        lines += ["", "## Scores", "", *score_rows(result)]
        if result["unsupported_scores"]:
            lines += ["", f"Scores under {rubric.NOTE_UNDER} with no timecoded note: {', '.join(result['unsupported_scores'])}."]
    lines += ["", "## Fixes, in order", ""]
    lines += fix_list(result, gates, frames) or ["Nothing to fix."]
    if result["dropped_notes"]:
        lines += ["", "## Notes dropped", "", "These had no checkable timecode, so they count for nothing.", ""]
        for item in result["dropped_notes"]:
            note = item["note"]
            said = note.get("observed", note) if isinstance(note, dict) else note
            lines.append(f"- {said} ({item['why']})")
    if result["warnings"]:
        lines += ["", "## Warnings", "", *[f"- {w}" for w in result["warnings"]]]
    spend = spend_lines(project)
    if spend:
        lines += ["", "## Spend", "", *[f"- {line}" for line in spend]]
    lines += facts_section(measured)
    lines += ["", "## What this review could not see or hear", "", *[f"- {item}" for item in result["limits"]], ""]
    path = project.review_dir / "REPORT.md"
    path.write_text("\n".join(lines))
    return path
