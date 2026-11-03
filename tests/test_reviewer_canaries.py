"""Reviewer canaries: the scoring is exact, and the evidence reaches a reviewer who cannot hear.

The live measurement needs the real demo take, a real transcriber and a fresh agent, so it runs by hand. See
`reviewer_canaries.py` for how, and for exactly what a run proves and what it does not.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from canaries import Case, Recipe, build, planted
from PIL import Image
from reviewer_canaries import CONTROL, FAULTS, LATE, between, delayed, named, overlaps, score
from test_review_pack import folder, lanes_of, sheet_of, x_of

from altocue.project import write_json
from altocue.review import pack, rubric


def note(dimension: str, start: str, end: str | None = None, severity: str = "major") -> dict:
    return {"start": start, "end": end, "severity": severity, "dimension": dimension, "observed": "seen", "suggested_fix": "fix it"}


def verdict(notes: list[dict]) -> dict:
    return {"scores": {name: {"score": None if name == "inserts" else 4, "why": ""} for name in rubric.DIMENSIONS}, "notes": notes}


def key_file(root, *films: tuple[str, dict]) -> None:
    write_json(root / "key.json", dict(films))


CALM = ("calm", {"name": "clean control", "dimensions": [], "window": [0.0, 25.0], "what": "", "duration": 25.0})
HOOK = ("aa", {"name": "weak hook", "dimensions": ["hook"], "window": [0.0, 4.0], "what": "", "duration": 25.0})


def give(root, name: str, notes: list[dict]) -> None:
    write_json(root / "films" / name / "film" / "review" / "verdict.json", verdict(notes))


def test_the_right_note_in_the_right_place_is_a_catch(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "aa", [note("hook", "00:01")])
    result = score(tmp_path)
    assert result["caught"] == 1 and result["rows"][0]["caught"]


def test_the_clean_films_mark_sits_beside_the_faults(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "aa", [note("hook", "00:01")])
    give(tmp_path, "calm", [])
    row = score(tmp_path)["rows"][0]
    assert row["dimension"] == "hook" and row["scored"] == 4 and row["control_scored"] == 4


def test_the_right_dimension_at_the_wrong_moment_is_a_miss(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "aa", [note("hook", "00:20")])
    assert score(tmp_path)["caught"] == 0


def test_the_wrong_dimension_at_the_right_moment_is_a_miss(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "aa", [note("picture", "00:01")])
    assert score(tmp_path)["caught"] == 0


def test_a_note_with_no_timecode_counts_for_nothing(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "aa", [note("hook", "around the start")])
    assert score(tmp_path)["caught"] == 0


def test_no_verdict_is_not_reviewed_and_never_a_miss(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    result = score(tmp_path)
    assert result["reviewed"] == 0 and result["planted"] == 1 and not result["rows"][0]["reviewed"]
    assert not result["control_reviewed"]


def test_the_control_flagged_in_a_faults_place_is_a_false_alarm(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "calm", [note("hook", "00:02")])
    assert len(score(tmp_path)["false_alarms"]) == 1


def test_a_minor_nit_on_the_control_is_not_a_false_alarm(tmp_path):
    key_file(tmp_path, CALM, HOOK)
    give(tmp_path, "calm", [note("hook", "00:02", severity="minor")])
    assert score(tmp_path)["false_alarms"] == []


def test_a_window_allows_for_whole_second_timecodes():
    assert overlaps({"start_seconds": 11.0, "end_seconds": None}, [12.0, 14.0])
    assert not overlaps({"start_seconds": 9.0, "end_seconds": 10.0}, [12.0, 14.0])
    assert overlaps({"start_seconds": 9.0, "end_seconds": 20.0}, [12.0, 14.0])


def test_named_reads_the_kept_notes_only():
    assert named([{"dimension": "hook", "start_seconds": 1.0, "end_seconds": None}], ["hook"], [0.0, 2.0])


def test_every_fault_has_something_to_catch_it_by():
    assert len(FAULTS) == 5 and not CONTROL.dimensions
    for fault in FAULTS:
        assert fault.dimensions and set(fault.dimensions) <= set(rubric.DIMENSIONS) and fault.what


def test_the_clean_film_has_nothing_planted():
    assert not (CONTROL.keep_false_start or CONTROL.stop_early or CONTROL.drag or CONTROL.quiet or CONTROL.late)


def test_every_fault_plants_something():
    for fault in FAULTS:
        assert fault.keep_false_start or fault.stop_early or fault.drag or fault.quiet or fault.late, fault.name


def test_every_film_starts_from_the_published_take():
    """A reader who runs these sees the films, so they use no picture or voice of their own."""
    source = Path("tests/reviewer_canaries.py").read_text()
    assert "from canaries" not in source and "PIL" not in source and "demo.fetch_sample" in source


def test_a_phrase_is_found_where_the_cut_put_it():
    words = [{"text": word, "start": float(at), "end": at + 0.5} for at, word in enumerate(["This", "is", "Altocue.", "It's", "an", "agent."])]
    assert between(words, "it's", "agent") == (3.0, 5.5)


def test_delayed_moves_every_word_and_stops_at_the_end():
    first = {"start": 1.0, "end": 2.0, "words": [{"text": "a", "start": 1.0, "end": 2.0}]}
    pages = [first, {"start": 9.5, "end": 10.0, "words": [{"text": "b", "start": 9.5, "end": 10.0}]}]
    moved = delayed(pages, LATE, 10.0)
    assert moved[0]["start"] == 1.9 and moved[0]["words"][0]["start"] == 1.9
    assert moved[1]["end"] == 10.0


def test_late_captions_stand_clear_of_the_speech_lane_on_the_sheet(tmp_path):
    """The evidence for the late captions fault: speech bars start well before the first caption block."""
    item = build(Case("t", "", Recipe(burn_captions=False, face=False)), tmp_path)
    timeline = json.loads(item.project.timeline_path.read_text())
    timeline["captions"] = delayed(timeline["captions"], LATE, 6.0)
    write_json(item.project.timeline_path, timeline)
    with planted(item):
        pack.build(item.project, item.file, [])
    picture = np.asarray(Image.open(sheet_of(item)).convert("L")).astype(float)
    lanes = lanes_of(item)
    empty = picture[lanes["captions"][0] : lanes["captions"][1], x_of(0.05) : x_of(0.8)]
    filled = picture[lanes["speech"][0] : lanes["speech"][1], x_of(0.05) : x_of(0.8)]
    assert empty.std() < 3 and filled.mean() > empty.mean() + 30


def test_the_pack_states_the_rhythm_of_a_film_with_one_long_shot(tmp_path):
    """The evidence for the dull stretch fault: a reviewer gets the shot lengths as numbers, and the longest stands out."""
    item = build(Case("t", "", Recipe(burn_captions=False, face=False)), tmp_path)
    edl = item.project.edl()
    edl.segments[0].end, edl.segments[1].start = 5.0, 5.0
    edl.segments = edl.segments[:2]
    edl.segments[1].end = 6.0
    item.project.save_edl(edl)
    with planted(item):
        pack.build(item.project, item.file, [])
    pacing = json.loads((folder(item) / "numbers.json").read_text())["pacing"]
    assert pacing["longest_shot"] == 5.0 and pacing["shots"] == 2
