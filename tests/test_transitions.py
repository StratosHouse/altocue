"""Transitions and freezes change picture only, so every test holds the frame count and the frames around them."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_frame import write_clip

from altocue import plate, timeline, transitions
from altocue.errors import AltocueError
from altocue.models import Edl, Segment
from altocue.motion import shaders
from altocue.plate import Canvas, Piece, layout
from altocue.project import Project
from altocue.qa import gates

C = Canvas(96, 96, 30, 16, "veryfast")


def freeze(frame: int, frames: int) -> dict:
    return {"frame": frame, "frames": frames}


def blend(cut: int, frames: int, name: str = "dissolve") -> dict:
    return {"cut": cut, "frames": frames, "type": name}


def covers(pieces: list[Piece], total: int) -> bool:
    """Every frame of the film belongs to exactly one piece, in order."""
    at = 0
    for piece in pieces:
        if piece.start != at or piece.frames < 1:
            return False
        at += piece.frames
    return at == total


def test_all_fifty_eight_names_are_ffmpegs_own():
    done = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True)
    theirs = set(re.findall(r"^\s{5}(\w+)\s+\d+\s+\.\.FV", done.stdout, re.MULTILINE))
    assert len(transitions.XFADE) == 58 and set(transitions.XFADE) == theirs


def test_a_dissolve_is_a_smooth_mix_and_the_stipple_keeps_a_name(tmp_path):
    a, b = counting_clip(tmp_path / "a.mp4", 10, base=40), counting_clip(tmp_path / "b.mp4", 10, base=200)

    def middle(name: str) -> float:
        dest = transitions.blend(a, b, name, 10, 30, plate.encode_args(C), tmp_path / f"{name}.mp4")
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
        return float(np.frombuffer(done.stdout, np.uint8).reshape(-1, 96, 96)[5].std())

    assert middle("dissolve") < 2 < middle("stipple")


def test_a_name_alone_takes_the_default_length():
    assert transitions.parse("dissolve") == {"type": "dissolve", "duration": transitions.DEFAULT_SECONDS}


def test_a_length_is_kept():
    assert transitions.parse({"type": "wipeleft", "duration": 0.3}) == {"type": "wipeleft", "duration": 0.3}


@pytest.mark.parametrize("raw", ["warp", {"type": "dissolve", "duration": 0}, {"type": "dissolve", "duration": 9}, {"duration": 0.4}, 3])
def test_what_cannot_be_played_is_refused_with_a_fix(raw):
    with pytest.raises(AltocueError) as caught:
        transitions.parse(raw)
    assert caught.value.fix


def test_with_no_edits_each_segment_is_one_piece():
    pieces = layout([10, 20], [], [])
    assert [(p.start, p.frames, p.segment, p.first) for p in pieces] == [(0, 10, 0, 0), (10, 20, 1, 0)]


def test_a_transition_sits_across_the_cut_and_runs_past_both_ends():
    pieces = layout([30, 30], [blend(1, 10)], [])
    assert covers(pieces, 60)
    join = next(p for p in pieces if p.blend)
    assert (join.start, join.frames, join.first) == (25, 10, 25)
    assert [(p.start, p.frames, p.first) for p in pieces if not p.blend] == [(0, 25, 0), (35, 25, 5)]


def test_an_odd_length_still_keeps_every_frame():
    pieces = layout([30, 30], [blend(1, 9)], [])
    assert covers(pieces, 60) and sum(p.frames for p in pieces) == 60


def test_one_segment_can_carry_a_transition_at_each_end():
    assert covers(layout([30, 30, 30], [blend(1, 10), blend(2, 10)], []), 90)


def test_a_segment_too_short_for_its_transitions_is_refused():
    with pytest.raises(AltocueError, match="too short"):
        layout([4, 30], [blend(1, 12)], [])


def test_a_freeze_overwrites_frames_and_the_film_keeps_its_length():
    pieces = layout([30, 30], [], [freeze(10, 12)])
    assert covers(pieces, 60)
    hold = next(p for p in pieces if p.hold)
    assert (hold.start, hold.frames, hold.segment, hold.first) == (10, 12, 0, 10)
    after = next(p for p in pieces if p.start == 22)
    assert after.first == 22  # picture comes back exactly where it would have been


def test_a_freeze_can_run_over_a_cut():
    pieces = layout([30, 30], [], [freeze(25, 12)])
    assert covers(pieces, 60)
    after = next(p for p in pieces if p.start == 37)
    assert (after.segment, after.first) == (1, 7)


def test_a_freeze_inside_a_transition_is_refused():
    with pytest.raises(AltocueError, match="transition"):
        layout([30, 30], [blend(1, 10)], [freeze(28, 6)])


def test_freezes_that_touch_are_refused():
    with pytest.raises(AltocueError, match="overlap"):
        layout([60], [], [freeze(10, 10), freeze(15, 10)])


def test_a_freeze_past_the_end_is_refused():
    with pytest.raises(AltocueError, match="past the end"):
        layout([30], [], [freeze(25, 10)])


def test_a_freeze_before_the_start_is_refused_with_a_fix():
    with pytest.raises(AltocueError, match="before the film starts") as caught:
        layout([30, 30], [], [freeze(-3, 5)])
    assert caught.value.fix


def edl_of(*spans: tuple[float, float]) -> Edl:
    return Edl(fps=30, segments=[Segment(id=f"s{i}", clip="a", start=a, end=b) for i, (a, b) in enumerate(spans)])


WORDS = [{"text": "first", "start": 0.1}, {"text": "next", "start": 1.05}, {"text": "stop", "start": 1.5}, {"text": "last", "start": 1.9}]


def test_a_word_finds_the_cut_beside_it():
    found = timeline.resolve_transitions([{"at": {"word": "next"}, "type": "dissolve", "duration": 0.4}], WORDS, edl_of((0, 1), (2, 3)))
    assert found == [{"cut": 1, "frames": 12, "type": "dissolve", "start": 0.8, "end": 1.2}]


def test_a_transition_with_no_cut_nearby_names_the_cuts():
    with pytest.raises(AltocueError) as caught:
        timeline.resolve_transitions([{"at": {"word": "last"}, "type": "dissolve"}], WORDS, edl_of((0, 1), (2, 4)))
    assert "1.00" in caught.value.fix


def test_two_transitions_on_one_cut_are_refused():
    with pytest.raises(AltocueError, match="more than one"):
        timeline.resolve_transitions([{"at": 1.0, "type": "dissolve"}, {"at": 1.1, "type": "fade"}], WORDS, edl_of((0, 1), (2, 3)))


def test_a_freeze_starts_on_its_word():
    assert timeline.resolve_freezes([{"at": {"word": "stop"}, "duration": 0.6}], WORDS, 30) == [{"frame": 45, "frames": 18, "start": 1.5, "end": 2.1}]


def counting_clip(path: Path, frames: int, *, base: int = 16) -> Path:
    """Each frame's level says which frame it is, so a test can read the order back."""
    return write_clip(path, [np.full((96, 96, 3), base + index * 2, np.uint8) for index in range(frames)])


def levels(path: Path) -> np.ndarray:
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True
    )
    return np.frombuffer(done.stdout, np.uint8).astype(float)


def picture_project(tmp_path: Path, *segments: Segment) -> tuple[Project, Edl]:
    project = Project.create(tmp_path / "video")
    a = counting_clip(tmp_path / "a.mp4", 100)
    from altocue.ffmpeg import probe_clip

    project.save_clips([probe_clip(a, clip_id="a")])
    return project, Edl(fps=30, segments=list(segments))


def seconds(frame: int) -> float:
    return frame / 30


TWO = (Segment(id="s1", clip="a", start=0, end=seconds(30)), Segment(id="s2", clip="a", start=seconds(50), end=seconds(80)))


def test_a_dissolve_changes_only_its_own_frames_and_no_frame_moves(tmp_path):
    project, edl = picture_project(tmp_path, *TWO)
    plain = levels(plate.assemble(project, edl, C, layout([30, 30], [], []), tmp_path / "plain", tmp_path / "plain.mp4"))
    mixed = levels(plate.assemble(project, edl, C, layout([30, 30], [blend(1, 10)], []), tmp_path / "mixed", tmp_path / "mixed.mp4"))
    assert len(plain) == len(mixed) == 60
    assert np.abs(plain[:25] - mixed[:25]).max() <= 1.5 and np.abs(plain[35:] - mixed[35:]).max() <= 1.5
    assert np.abs(plain[25:35] - mixed[25:35]).max() > 5  # past the cut the outgoing shot keeps playing


def test_a_handle_past_the_clip_holds_its_last_frame(tmp_path):
    project, edl = picture_project(tmp_path, Segment(id="s1", clip="a", start=seconds(80), end=seconds(100)), Segment(id="s2", clip="a", start=0, end=seconds(30)))
    done = plate.assemble(project, edl, C, layout([20, 30], [blend(1, 10)], []), tmp_path / "parts", tmp_path / "out.mp4")
    assert len(levels(done)) == 50


def test_a_freeze_holds_then_comes_back_in_step(tmp_path):
    project, edl = picture_project(tmp_path, Segment(id="s1", clip="a", start=0, end=seconds(60)))
    plain = levels(plate.assemble(project, edl, C, layout([60], [], []), tmp_path / "plain", tmp_path / "plain.mp4"))
    held = levels(plate.assemble(project, edl, C, layout([60], [], [freeze(20, 12)]), tmp_path / "held", tmp_path / "held.mp4"))
    assert len(held) == 60
    assert np.ptp(held[20:32]) <= 1 and abs(held[20] - plain[20]) <= 1.5
    assert np.abs(held[:20] - plain[:20]).max() <= 1.5 and np.abs(held[32:] - plain[32:]).max() <= 1.5


def test_the_plate_stage_reads_motion_json(tmp_path):
    project, edl = picture_project(tmp_path, *TWO)
    project.save_edl(edl)
    (project.root / "motion.json").write_text(json.dumps({"transitions": [{"at": 1.0, "type": "wipeleft", "duration": 0.2}], "freezes": [{"at": 0.2, "duration": 0.2}]}))
    edits = timeline.picture_edits(project, edl, words=[])
    assert edits["transitions"][0]["cut"] == 1 and edits["freezes"][0]["frame"] == 6


SHADER = """// Author: Someone
// License: MIT
uniform float strength; // = 0.4
uniform vec2 direction /* = vec2(1.0, 0.0) */;
uniform bool flip; // = true // a note
vec4 transition(vec2 uv) { return mix(getFromColor(uv), getToColor(uv), progress); }
"""


def test_settings_become_constants_at_their_defaults():
    body = shaders.prepare(SHADER)
    assert "const float strength = 0.4;" in body and "const vec2 direction = vec2(1.0, 0.0);" in body and "const bool flip = true;" in body
    assert "uniform" not in body and "License: MIT" in body


def test_a_shader_with_a_texture_input_is_refused():
    with pytest.raises(AltocueError, match="cannot supply"):
        shaders.prepare(SHADER + "uniform sampler2D luma;\n")


def test_a_shader_with_no_licence_is_refused():
    with pytest.raises(AltocueError, match="licence"):
        shaders.licence_of("// Author: Someone\nvec4 transition(vec2 uv) { return vec4(0.0); }")


def test_a_name_maps_to_the_upstream_file():
    assert shaders.file_name("gl-cross-zoom") == "CrossZoom"
    with pytest.raises(AltocueError):
        shaders.file_name("gl-../../etc")


def test_the_page_cannot_be_closed_by_the_shader_and_blocks_the_network():
    html = shaders.page(SHADER + "// </script><script>alert(1)</script>\n", 12, 30, 1080, 1920)
    assert "</script><script>alert" not in html and "default-src 'self'" in html and "connect-src" not in html


def test_every_bundled_shader_carries_its_licence_and_prepares():
    names = shaders.bundled()
    assert len(names) == 12
    for name in names:
        text = shaders.source(None, name)
        assert shaders.licence_of(text).upper() == "MIT"
        shaders.prepare(text)


def test_a_shader_not_in_the_project_says_how_to_get_it(tmp_path):
    project = Project.create(tmp_path / "video")
    with pytest.raises(AltocueError) as caught:
        shaders.source(project, "gl-wind")
    assert "altocue library add gl-wind" in caught.value.fix


def test_a_fetched_shader_is_kept_with_the_project_and_wins(tmp_path, monkeypatch):
    from altocue.generate import http

    project = Project.create(tmp_path / "video")
    seen = []
    monkeypatch.setattr(http, "call", lambda method, url, **kwargs: seen.append(url) or http.Response(200, SHADER.encode()))
    record = shaders.fetch(project, "gl-wind")
    assert seen == ["https://cdn.jsdelivr.net/npm/gl-transitions@2.2.0/transitions/Wind.glsl"]
    assert record["licence"] == "MIT" and record["author"] == "Someone" and len(record["sha256"]) == 64
    assert shaders.source(project, "gl-wind") == SHADER


def test_a_fetch_with_no_licence_keeps_nothing(tmp_path, monkeypatch):
    from altocue.generate import http

    project = Project.create(tmp_path / "video")
    monkeypatch.setattr(http, "call", lambda method, url, **kwargs: http.Response(200, b"vec4 transition(vec2 uv) { return vec4(0.0); }"))
    with pytest.raises(AltocueError, match="licence"):
        shaders.fetch(project, "gl-wind")
    assert not (project.root / "motion" / "fetched" / "gl-wind.glsl").exists()


def test_with_no_browser_a_shader_becomes_a_dissolve_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shaders, "ready", lambda: False)
    project, edl = picture_project(tmp_path, *TWO)
    done = plate.assemble(project, edl, C, layout([30, 30], [blend(1, 10, "gl-cross-zoom")], []), tmp_path / "parts", tmp_path / "out.mp4")
    assert len(levels(done)) == 60 and "dissolve" in capsys.readouterr().out


@pytest.mark.skipif(not shaders.ready(), reason="needs the renderer")
def test_a_shader_renders_on_the_stage_and_comes_back_in_step(tmp_path):
    project, edl = picture_project(tmp_path, *TWO)
    plain = levels(plate.assemble(project, edl, C, layout([30, 30], [], []), tmp_path / "plain", tmp_path / "plain.mp4"))
    done = levels(plate.assemble(project, edl, C, layout([30, 30], [blend(1, 10, "gl-cross-zoom")], []), tmp_path / "shader", tmp_path / "shader.mp4"))
    assert len(done) == 60 and np.abs(plain[:25] - done[:25]).max() <= 1.5 and np.abs(plain[35:] - done[35:]).max() <= 1.5
    assert done[25:35].min() > plain[25] - 8  # the page really drew the clips, not a black frame


def test_a_named_transition_and_freeze_reach_the_timeline(tmp_path, monkeypatch):
    project, edl = picture_project(tmp_path, *TWO)
    project.save_edl(edl)
    asked = {"transitions": [{"at": 1.0, "type": "fadeblack"}], "freezes": [{"at": 0.2, "duration": 0.2}], "overlay": {"type": "grain"}}
    (project.root / "motion.json").write_text(json.dumps(asked))
    monkeypatch.setattr(timeline.cut, "map_words", lambda project, edl: [])
    built = timeline.build(project)
    assert built["transitions"][0]["type"] == "fadeblack" and built["freezes"][0]["end"] == pytest.approx(0.4)
    assert built["overlay"][0]["type"] == "grain"


def gate_context(timeline: dict) -> SimpleNamespace:
    def unplanned(ranges, planned):
        return [run for run in ranges if not any(run[0] >= a - 0.04 and run[1] <= b + 0.04 for a, b in planned)]

    return SimpleNamespace(file="x", footage_end=10.0, timeline=timeline, planned=lambda *args: [], inserts=lambda: [], unplanned=unplanned)


def test_a_draft_places_graphics_on_the_draft_plate(tmp_path, monkeypatch):
    """A final plate left from an earlier render is an older cut, so a draft must not sample it."""
    project, edl = picture_project(tmp_path, *TWO)
    project.save_edl(edl)
    for name in ("plate.mp4", "plate-draft.mp4"):
        (project.work / name).write_bytes(b"picture")
    seen = []
    monkeypatch.setattr(timeline.cut, "map_words", lambda project, edl: [])
    monkeypatch.setattr(timeline.placement, "place", lambda items, picture, *rest: seen.append(picture) or items)
    timeline.build(project, draft=True)
    timeline.build(project)
    assert [path.name for path in seen] == ["plate-draft.mp4", "plate.mp4"]


def test_captions_made_for_another_cut_stop_the_timeline(tmp_path, monkeypatch):
    project, edl = picture_project(tmp_path, *TWO)
    project.save_edl(edl)
    monkeypatch.setattr(timeline.cut, "map_words", lambda project, edl: [])
    (project.work / "captions.json").write_text(json.dumps({"pages": [{"text": "an older cut", "start": 0.0, "end": 1.0}]}))
    with pytest.raises(AltocueError, match="captions") as caught:
        timeline.build(project)
    assert "altocue caption" in caught.value.fix
    (project.work / "captions.json").write_text(json.dumps({"cut": project.cut_mark(), "pages": []}))
    assert timeline.build(project)["captions"] == []


def test_a_freeze_the_edit_asked_for_is_not_a_fault(monkeypatch):
    monkeypatch.setattr(gates, "frozen_runs", lambda file, end: [(2.0, 2.6)])
    assert gates.gate_frozen(gate_context({"freezes": [{"start": 2.0, "end": 2.6}]})).passed
    assert not gates.gate_frozen(gate_context({"freezes": []})).passed
    assert not gates.gate_frozen(gate_context({"freezes": [{"start": 5.0, "end": 5.6}]})).passed


def test_a_dip_through_black_the_edit_asked_for_is_not_a_fault(monkeypatch):
    monkeypatch.setattr(gates, "black_runs", lambda file, end: [(3.1, 3.3)])
    assert gates.gate_black(gate_context({"transitions": [{"start": 3.0, "end": 3.4}]})).passed
    assert not gates.gate_black(gate_context({"transitions": []})).passed


def test_a_slow_zoom_carries_on_through_a_transition(tmp_path):
    moving = Segment(id="s1", clip="a", start=0, end=seconds(30), zoom_from=1.0, zoom_to=1.2)
    project, edl = picture_project(tmp_path, moving, TWO[1])
    assert "(in+5)/29" in plate.push_filter(C, 30, 1.0, 1.2, 5)
    done = plate.assemble(project, edl, C, layout([30, 30], [blend(1, 10)], []), tmp_path / "parts", tmp_path / "out.mp4")
    assert len(levels(done)) == 60


def test_a_name_this_ffmpeg_lacks_says_so(tmp_path, monkeypatch):
    def fails(args):
        raise AltocueError("ffmpeg failed:\nNo such transition")

    monkeypatch.setattr(transitions.ffmpeg, "run", fails)
    with pytest.raises(AltocueError) as caught:
        transitions.blend(tmp_path / "a.mp4", tmp_path / "b.mp4", "coverleft", 10, 30, [], tmp_path / "c.mp4")
    assert "update ffmpeg" in caught.value.fix


def test_library_add_fetches_a_shader_and_records_it(tmp_path, monkeypatch, capsys):
    from altocue import library
    from altocue.cli import main
    from altocue.generate import http

    project = Project.create(tmp_path / "video")
    monkeypatch.setattr(http, "call", lambda method, url, **kwargs: http.Response(200, SHADER.encode()))
    assert main(["library", "add", "gl-wind", "--project", str(project.root)]) == 0
    assert (project.root / "motion" / "fetched" / "gl-wind.glsl").exists()
    lock = json.loads((project.root / library.LOCK_FILE).read_text())
    assert lock["items"]["gl-wind"]["licence"] == "MIT"
    assert '"type": "gl-wind"' in capsys.readouterr().out


def test_library_add_says_a_bundled_shader_needs_no_fetch(tmp_path, capsys):
    from altocue.cli import main

    project = Project.create(tmp_path / "video")
    assert main(["library", "add", "gl-cross-zoom", "--project", str(project.root)]) == 0
    assert "ships with Altocue" in capsys.readouterr().out
