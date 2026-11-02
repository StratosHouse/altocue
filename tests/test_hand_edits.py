"""Gain, crop, zoom and order set by hand stay through a re-cut, and anything lost is reported."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from test_cut import silent_project, spoken, timed
from test_frame import face_image, write_clip

from altocue import deliver
from altocue.cli import main
from altocue.cut import apply_review, assemble, propose, rebuild, removal_id, remove_segments, validate
from altocue.cut.hand import carry, hand_set
from altocue.cut.script import from_script, write_script
from altocue.ffmpeg import probe_clip
from altocue.frame import frame_project
from altocue.models import Edl, Removed, Segment
from altocue.platforms import find
from altocue.project import Project
from altocue.transcribe import classify

TALK = "First we plan the work. Then we film the idea. Next we cut the pauses. Finally we ship the thing."


@pytest.fixture
def landscape(tmp_path: Path) -> Path:
    return write_clip(tmp_path / "wide.mp4", [face_image(1280, 720, ((360, 330, 150),))] * 90)


def framed_project(tmp_path: Path, path: Path) -> Project:
    """Three shots of one take, cut by pauses, so jump cuts get punch-ins."""
    project = Project.create(tmp_path / "video", aspect="9:16")
    project.save_clips([probe_clip(path, clip_id="wide")])
    words = [*timed("We plan.", start=0.2, step=0.2), *timed("We film.", start=1.2, step=0.2), *timed("We ship.", start=2.2, step=0.2)]
    project.save_words("wide", classify(words), "test")
    project.save_edl(propose(project))
    assert len(project.edl().segments) == 3
    return project


def apart() -> list:
    """Four sentences with long pauses, so the cut holds four segments."""
    return [
        *timed("First we plan the work.", start=0.0),
        *timed("Then we film the idea.", start=4.0),
        *timed("Next we cut the pauses.", start=8.0),
        *timed("Finally we ship the thing.", start=12.0),
    ]


def cut_project(tmp_path: Path):
    project = silent_project(tmp_path, apart())
    project.save_edl(propose(project))
    assert len(project.edl().segments) == 4
    return project


def edit(project, **changes):
    edl = project.edl()
    for segment_id, values in changes.items():
        segment = next(s for s in edl.segments if s.id == segment_id)
        for key, value in values.items():
            setattr(segment, key, value)
    project.save_edl(edl)
    return edl


def by_label(edl: Edl, opening: str) -> Segment:
    return next(s for s in edl.segments if s.label.startswith(opening))


def test_gain_survives_a_removal(tmp_path):
    project = cut_project(tmp_path)
    edit(project, s003={"gain": 1.4})
    edl = remove_segments(project, ["s001"], "too slow")
    assert by_label(edl, "Next").gain == 1.4
    assert by_label(edl, "Next").held == ["gain"]
    assert edl.lost == []


def test_crop_and_zoom_survive_a_removal(tmp_path):
    project = cut_project(tmp_path)
    edit(project, s002={"crop": [10.0, 0.0, 406.0, 720.0], "zoom_from": 1.0, "zoom_to": 1.06})
    edl = remove_segments(project, ["s004"], "too slow")
    kept = by_label(edl, "Then")
    assert (kept.crop, kept.zoom_from, kept.zoom_to) == ([10.0, 0.0, 406.0, 720.0], 1.0, 1.06)
    assert sorted(kept.held) == ["crop", "zoom"]


def test_hand_edits_survive_a_ruling(tmp_path):
    words = [*timed("Then we film the idea.", start=0.0), *timed("well um that works today.", start=4.0)]
    project = silent_project(tmp_path, words)
    project.save_edl(propose(project))
    edit(project, s002={"gain": 0.8})
    item = next(r for r in project.edl().removed if r.kind == "filler")
    rulings = tmp_path / "rulings.json"
    rulings.write_text(json.dumps({removal_id(item): "keep"}))
    edl = apply_review(project, rulings)
    assert "um" in spoken(project, edl)
    assert by_label(edl, "well").gain == 0.8


def test_a_segment_that_splits_passes_its_edit_to_both_halves(tmp_path):
    project = silent_project(tmp_path, timed("We plan the work with real care today."))
    project.save_edl(propose(project))
    edit(project, s001={"gain": 1.3})
    edl = project.edl()
    edl.removed.append(Removed("a", 1.2, 1.5, "manual", "by hand", "auto"))
    fresh = rebuild(project, edl, edl.removed)
    assert len(fresh.segments) == 2
    assert [s.gain for s in fresh.segments] == [1.3, 1.3]


def test_two_segments_that_merge_keep_the_larger_and_report_the_other(tmp_path):
    words = timed("We plan the work carefully today.", start=0.0)
    project = silent_project(tmp_path, words)
    edl = Edl(fps=30, segments=[Segment("s001", "a", 0.0, 0.6, gain=1.2), Segment("s002", "a", 0.6, words[-1].end + 0.1, gain=0.7)])
    fresh = Edl(fps=30, segments=[Segment("s001", "a", 0.0, words[-1].end + 0.1)])
    carry(project, edl, fresh)
    assert fresh.segments[0].gain == 0.7
    assert len(fresh.lost) == 1 and "s001" in fresh.lost[0] and "gain 1.2" in fresh.lost[0]


def test_an_edit_on_a_removed_segment_is_reported(tmp_path, capsys):
    project = cut_project(tmp_path)
    edit(project, s002={"gain": 1.4})
    assert main(["cut", "--project", str(project.root), "--remove", "s002"]) == 0
    assert any("gain 1.4" in note for note in project.edl().lost)
    output = capsys.readouterr().out
    assert "Could not keep" in output and "gain 1.4" in output
    assert "gain 1.4" in (project.work / "cut-report.md").read_text()


def test_the_report_lists_what_was_kept(tmp_path, capsys):
    project = cut_project(tmp_path)
    edit(project, s002={"gain": 1.4})
    assert main(["cut", "--project", str(project.root), "--remove", "s003"]) == 0
    assert "Kept 1 hand edit" in capsys.readouterr().out
    report = (project.work / "cut-report.md").read_text()
    assert "Hand edits" in report and "gain" in report


def test_a_fresh_proposal_says_what_it_leaves_behind(tmp_path, capsys):
    project = cut_project(tmp_path)
    edit(project, s002={"gain": 1.4})
    assert main(["cut", "--project", str(project.root)]) == 0
    assert "edl-previous.json" in capsys.readouterr().out
    assert all(s.gain == 1.0 for s in project.edl().segments)


def test_a_clean_recut_reports_nothing(tmp_path, capsys):
    project = cut_project(tmp_path)
    assert main(["cut", "--project", str(project.root), "--remove", "s003"]) == 0
    assert "hand edit" not in capsys.readouterr().out.lower()
    assert project.edl().lost == []


def test_an_edit_lost_once_is_not_reported_for_ever(tmp_path):
    project = cut_project(tmp_path)
    edit(project, s002={"gain": 1.4})
    project.save_edl(remove_segments(project, ["s002"], "out"))
    assert project.edl().lost
    assert remove_segments(project, ["s001"], "out").lost == []


def test_moved_segments_stay_moved_through_a_removal(tmp_path):
    project = cut_project(tmp_path)
    write_script(project, project.edl())
    lines = [line for line in project.script_path.read_text().splitlines() if line.startswith("[L")]
    project.script_path.write_text("\n".join([lines[3], lines[0], lines[1], lines[2]]))
    project.save_edl(from_script(project, project.script_path))
    edl = remove_segments(project, [project.edl().segments[2].id], "out")
    assert spoken(project, edl) == "Finally we ship the thing. First we plan the work. Next we cut the pauses."
    assert validate(project, edl) == []


def test_moved_neighbours_stay_apart_when_the_cut_between_them_goes(tmp_path):
    project = silent_project(tmp_path, timed(TALK, step=0.3))
    project.save_edl(propose(project))
    write_script(project, project.edl())
    lines = [line for line in project.script_path.read_text().splitlines() if line.startswith("[L")]
    project.script_path.write_text("\n".join([lines[1], lines[0], lines[2], lines[3]]))
    project.save_edl(from_script(project, project.script_path))
    project.save_edl(remove_segments(project, [project.edl().segments[-1].id], "out"))
    assert spoken(project, project.edl()).startswith("Then we film the idea. First we plan the work.")


def test_moved_order_takes_back_a_restored_word(tmp_path):
    words = [*timed("Well um that works.", start=0.0), *timed("We ship the thing.", start=4.0)]
    project = silent_project(tmp_path, words)
    project.save_edl(propose(project))
    write_script(project, project.edl())
    lines = [line for line in project.script_path.read_text().splitlines() if line.startswith("[L")]
    project.script_path.write_text("\n".join([lines[1], lines[0]]))
    project.save_edl(from_script(project, project.script_path))
    item = next(r for r in project.edl().removed if r.kind == "filler")
    rulings = tmp_path / "rulings.json"
    rulings.write_text(json.dumps({removal_id(item): "keep"}))
    assert spoken(project, apply_review(project, rulings)) == "We ship the thing. Well um that works."


def test_a_script_recut_keeps_hand_edits_too(tmp_path):
    project = cut_project(tmp_path)
    write_script(project, project.edl())
    edit(project, s003={"gain": 1.4})
    lines = [line for line in project.script_path.read_text().splitlines() if line.startswith("[L")]
    project.script_path.write_text("\n".join([lines[2], lines[0]]))
    edl = from_script(project, project.script_path)
    assert edl.segments[0].gain == 1.4
    assert edl.lost == []


def test_solved_framing_is_not_a_hand_edit(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    assert hand_set(project, edl) == {}


def test_a_person_changing_a_solved_crop_is_a_hand_edit(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    edl.segments[-1].zoom_from = edl.segments[-1].zoom_to = 1.3
    project.save_edl(edl)
    found = hand_set(project, project.edl())
    assert found[edl.segments[0].id]["crop"] == [10.0, 0.0, 406.0, 720.0]
    assert found[edl.segments[-1].id]["zoom"] == (1.3, 1.3)
    assert len(found) == 2


def test_taking_a_punch_in_off_by_hand_counts(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    punched = next(s for s in edl.segments if s.zoom_from > 1)
    punched.zoom_from = punched.zoom_to = 1.0
    project.save_edl(edl)
    assert hand_set(project, project.edl())[punched.id]["zoom"] == (1.0, 1.0)


def test_the_frame_stage_leaves_held_values_and_solves_the_rest(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    solved = [s.crop for s in edl.segments]
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    fresh = remove_segments(project, [edl.segments[-1].id], "out")
    project.save_edl(fresh)
    assert fresh.segments[1].crop is None
    done = frame_project(project)
    assert done.segments[0].crop == [10.0, 0.0, 406.0, 720.0]
    assert done.segments[0].held == ["crop"]
    assert done.segments[1].crop == solved[1]


def test_a_held_crop_outlives_a_second_recut(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    project.save_edl(remove_segments(project, [edl.segments[-1].id], "out"))
    frame_project(project)
    project.save_edl(remove_segments(project, [project.edl().segments[-1].id], "out"))
    assert frame_project(project).segments[0].crop == [10.0, 0.0, 406.0, 720.0]


def test_a_recut_of_the_same_ranges_still_solves_what_is_blank(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    solved = [s.crop for s in edl.segments]
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    project.save_edl(rebuild(project, project.edl(), project.edl().removed))
    done = frame_project(project)
    assert done.segments[0].crop == [10.0, 0.0, 406.0, 720.0]
    assert done.segments[1].crop == solved[1]


def test_force_clears_what_was_held(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    solved = edl.segments[0].crop
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    frame_project(project)
    done = frame_project(project, force=True)
    assert done.segments[0].crop == solved
    assert done.segments[0].held == []


def test_a_held_crop_for_another_shape_is_solved_again(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    project.config["output"]["aspect"] = "1:1"
    done = frame_project(project)
    x, y, width, height = done.segments[0].crop
    assert width == pytest.approx(height, abs=2)


def test_a_new_shape_clears_held_framing_and_keeps_gain(tmp_path, landscape):
    project = framed_project(tmp_path, landscape)
    edl = frame_project(project)
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    edl.segments[0].gain = 1.2
    edl.segments[0].held = ["crop", "gain"]
    project.save_edl(edl)
    project.timeline_path.write_text("{}")
    version = deliver.open_version(project, find("square"), {"target": -14.0, "true_peak": -1.5}, reuse=False)
    assert version.edl().segments[0].held == ["gain"]
    assert version.edl().segments[0].gain == 1.2


def test_assemble_without_an_order_follows_the_recording(tmp_path):
    project = cut_project(tmp_path)
    assert [s.label for s in assemble(project, project.edl().removed, 30).segments] == [s.label for s in project.edl().segments]
    assert np.diff([s.start for s in project.edl().segments]).min() > 0
