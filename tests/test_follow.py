"""A crop that follows: a walking head, and a wide shot of people taking turns."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from test_frame import face_image, make_project, write_clip

from altocue import follow, plate
from altocue.follow import Look, Person
from altocue.frame import Face, detect_faces, frame_project
from altocue.models import Segment, Word
from altocue.project import Project


def face(cx: float, cy: float = 330, w: float = 150, mouth: np.ndarray | None = None) -> Face:
    return Face(cx - w / 2, cy - w * 0.6, w, w * 1.2, 0.9, cy - w * 0.2)


def looks_of(centres: list[list[float]], rate: float = 5.0, start: float = 0.0) -> list[Look]:
    return [Look(start + i / rate, tuple(face(x) for x in xs), tuple(None for _ in xs)) for i, xs in enumerate(centres)]


def test_a_still_head_needs_no_path():
    looks = looks_of([[640.0]] * 15)
    path, note = follow.solve(looks, [""] * 15, (1280, 720), (404, 720), 0.0)
    assert path is None and note["people"] == 1


def test_a_walking_head_gets_a_smooth_path_that_stays_in_the_picture():
    xs = [300 + 680 * i / 19 for i in range(20)]
    path, _ = follow.solve(looks_of([[x] for x in xs], start=2.0), [""] * 20, (1280, 720), (404, 720), 2.0)
    assert path is not None and 2 <= len(path) <= follow.MAX_KEYS
    assert path[0][0] == 0.0  # seconds into the segment, not the clip
    lefts = [x for _, x, _ in path]
    assert lefts == sorted(lefts) and lefts[-1] - lefts[0] > 300
    assert all(0 <= x <= 1280 - 404 for x in lefts)


def test_a_camera_change_is_found_and_a_steady_shot_is_not():
    rng = np.random.default_rng(5)
    wide = rng.integers(0, 80, (18, 32), dtype=np.uint8)
    close = rng.integers(150, 255, (18, 32), dtype=np.uint8)
    moving = np.roll(wide, 1, axis=1)
    assert follow.shots([wide, moving, None, wide, close, close]) == [0, 0, 0, 0, 1, 1]


def test_a_camera_change_inside_a_segment_does_not_keep_the_old_place():
    # A close-up on the right, then the cut to a wide shot where nobody's face is found.
    looks = [Look(i / 5, (face(1500.0),), (None,), 0) for i in range(10)]
    looks += [Look(2 + i / 5, (), (), 1) for i in range(15)]
    path, note = follow.solve(looks, [""] * 25, (1920, 1080), (606, 1080), 0.0)
    assert note["shots"] == 2 and path is not None and note["faceless"] == 3.0
    after = [x for t, x, _ in path if t >= 2.0]
    assert after and all(abs(x - (1920 - 606) / 2) < 2 for x in after)  # the centre, not the close-up's place
    assert any(x > 1100 for t, x, _ in path if t < 2.0)


def test_a_pan_off_the_subject_is_counted_as_seconds_with_no_face():
    xs = [300 + 40 * i for i in range(20)]
    looks = looks_of([[x] for x in xs])
    looks[8:20] = [Look(look.t, (), ()) for look in looks[8:20]]
    _, note = follow.solve(looks, [""] * 20, (1920, 1080), (606, 1080), 0.0)
    assert note["faceless"] == 2.4


def test_a_cut_the_tones_miss_is_found_by_the_faces():
    # Same wall either side of the cut: a mid shot on the right, then a close-up on the left.
    looks = [Look(i / 5, (face(1500.0, w=120),), (None,)) for i in range(10)]
    looks += [Look(2 + i / 5, (face(500.0, 540, w=420),), (None,)) for i in range(10)]
    path, note = follow.solve(looks, [""] * 20, (1920, 1080), (606, 1080), 0.0)
    assert note["shots"] == 2 and path is not None
    assert min(x for t, x, _ in path if t >= 2.0) < 400


def test_stray_background_faces_still_count_as_no_face():
    looks = [Look(i / 5, (face(1500.0),), (None,), 0) for i in range(10)]
    looks += [Look(2 + i / 5, (face(200.0 + 97 * i, 1000, w=30),), (None,), 1) for i in range(15)]
    _, note = follow.solve(looks, [""] * 25, (1920, 1080), (606, 1080), 0.0)
    assert note["faceless"] == 3.0


def test_a_short_shot_of_someone_new_is_not_counted_as_faceless():
    looks = [Look(i / 5, (face(1500.0),), (None,), 0) for i in range(20)]
    looks += [Look(4 + i / 5, (face(400.0),), (None,), 1) for i in range(5)]
    path, note = follow.solve(looks, [""] * 25, (1920, 1080), (606, 1080), 0.0)
    assert note["faceless"] == 0.0 and path is not None
    assert min(x for t, x, _ in path if t >= 4.0) < 300


def test_one_stray_detection_is_not_followed():
    looks = [Look(i / 5, (), ()) for i in range(15)]
    looks[7] = Look(7 / 5, (face(1500.0),), (None,))
    path, note = follow.solve(looks, [""] * 15, (1920, 1080), (606, 1080), 0.0)
    assert path is None and note["people"] == 0


def test_each_shot_frames_the_face_it_has():
    looks = [Look(i / 5, (face(400.0),), (None,), 0) for i in range(10)]
    looks += [Look(2 + i / 5, (face(1500.0),), (None,), 1) for i in range(10)]
    path, note = follow.solve(looks, [""] * 20, (1920, 1080), (606, 1080), 0.0)
    assert note["shots"] == 2 and path is not None
    before = [x for t, x, _ in path if t < 1.99]
    after = [x for t, x, _ in path if t >= 2.0]
    assert max(before) < 300 and min(after) > 1100  # a cut, not a pan across the room


def test_turns_shorter_than_the_hold_are_dropped():
    times = [i / 5 for i in range(30)]
    choice = [0] * 10 + [1] * 3 + [0] * 7 + [1] * 10
    assert follow.steady_turns(choice, times) == [0] * 20 + [1] * 10


def test_simplify_keeps_corners_and_drops_straight_runs():
    points = [(t / 10, t * 2.0, 0.0) for t in range(10)] + [(1 + t / 10, 18.0, 0.0) for t in range(1, 10)]
    kept = follow.simplify(points)
    assert len(kept) == 3 and kept[1][1] == 18.0


def test_the_speaker_under_each_sample_is_found():
    words = [Word("hi", 0.0, 0.4, speaker="S1"), Word("there", 1.0, 1.3, speaker="S2")]
    assert follow.speaking(words, [0.2, 0.7, 1.1, 2.0]) == ["S1", "S1", "S2", "S2"]


def test_voices_go_to_the_face_whose_mouth_moves():
    rng = np.random.default_rng(3)
    voices = ["S1"] * 10 + ["S2"] * 10
    talking = [rng.normal(size=follow.PATCH[::-1]) for _ in range(20)]
    still = np.zeros(follow.PATCH[::-1])
    left = Person({i: face(300) for i in range(20)}, {i: talking[i] if i < 10 else still for i in range(20)})
    right = Person({i: face(980) for i in range(20)}, {i: talking[i] if i >= 10 else still for i in range(20)})
    assert follow.match_voices([left, right], voices) == {"S1": 0, "S2": 1}


def test_two_people_who_do_not_fit_get_a_cut_to_whoever_speaks(monkeypatch):
    looks = looks_of([[300.0, 980.0]] * 30)
    monkeypatch.setattr(follow, "match_voices", lambda people, voices: {"S1": 0, "S2": 1})
    voices = ["S1"] * 15 + ["S2"] * 15
    path, note = follow.solve(looks, voices, (1280, 720), (404, 720), 0.0)
    assert note["switches"] == 1 and path is not None
    first, last = path[0][1] + 202, path[-1][1] + 202
    assert abs(first - 300) < 40 and abs(last - 980) < 40
    cut = next(i for i in range(1, len(path)) if path[i][1] - path[i - 1][1] > 300)
    assert path[cut][0] - path[cut - 1][0] < 0.25  # a cut, not a slow pan across the gap


def test_follow_modes():
    assert follow.needed("auto", True, False) and follow.needed("auto", False, True)
    assert not follow.needed("auto", False, False) and follow.needed("on", False, False)
    assert not follow.needed("off", True, True)


def walking_clip(tmp_path: Path) -> Path:
    frames = [face_image(1280, 720, ((int(260 + 760 * i / 89), 330, 140),)) for i in range(90)]
    return write_clip(tmp_path / "walk.mp4", frames)


def test_framing_a_walk_gives_a_path_and_the_render_keeps_the_head_centred(tmp_path):
    clip_path = walking_clip(tmp_path)
    project = make_project(tmp_path, [clip_path], [Segment(id="s1", clip="walk", start=0, end=3)])
    segment = frame_project(project).segments[0]
    assert segment.path and segment.crop and segment.crop[2] == 404
    assert segment.path[-1][1] - segment.path[0][1] > 300
    clip = project.clip("walk")
    canvas = plate.Canvas(270, 480, 30, 18, "veryfast")
    out = plate.build_segment(project, clip, segment, canvas, tmp_path)
    centres = []
    for at in (0.3, 1.5, 2.7):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True,
            check=True,
        ).stdout
        image = np.frombuffer(raw, np.uint8).reshape(480, 270, 3).copy()
        found = detect_faces(image)
        assert found, f"no face in the rendered frame at {at} s"
        centres.append(found[0].center[0])
    assert all(abs(x - 135) < 60 for x in centres), centres


def test_a_hand_crop_stops_the_path(tmp_path):
    clip_path = walking_clip(tmp_path)
    project = make_project(tmp_path, [clip_path], [Segment(id="s1", clip="walk", start=0, end=3)])
    edl = frame_project(project)
    edl.segments[0].crop = [0.0, 0.0, 404.0, 720.0]
    project.save_edl(edl)
    again = frame_project(project).segments[0]
    assert again.crop == [0.0, 0.0, 404.0, 720.0]
    assert not plate.fits_path(project.clip("walk"), again, plate.Canvas(270, 480, 30, 18, "veryfast"))
    assert frame_project(project, force=True).segments[0].path


def test_follow_off_keeps_one_crop(tmp_path):
    clip_path = walking_clip(tmp_path)
    project = make_project(tmp_path, [clip_path], [Segment(id="s1", clip="walk", start=0, end=3)])
    toml = project.root / "altocue.toml"
    toml.write_text(toml.read_text().replace('follow = "auto"', 'follow = "off"', 1))
    assert frame_project(Project.open(project.root)).segments[0].path is None


def test_the_slide_reads_straight_lines_held_at_both_ends():
    text = plate.line_through([(0.0, 10.0), (30.0, 40.0)], "in")
    assert "\\," in text and "clip(in\\,0.0000\\,30.0000)" in text
