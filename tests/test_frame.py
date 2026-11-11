from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from altocue import frame as framing
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.ffmpeg import probe_clip
from altocue.frame import (
    PUNCH_ZOOM,
    Face,
    choose_subject,
    crop_size,
    detect_faces,
    find_tracks,
    frame_project,
    hold_through_gaps,
    place_crop,
    plan_push,
    plan_shots,
    plan_zooms,
    safe_zoom,
    settle_crop,
)
from altocue.models import Edl, Removed, Segment, Word
from altocue.plate import ZOOM_ORIGIN, origin_of, push_travel, zoomed
from altocue.project import Project, size_for


def draw_face(image: np.ndarray, cx: int, cy: int, s: int, skin: tuple[int, int, int] = (205, 150, 120)) -> None:
    """Paint a shaded head on an RGB image. YuNet accepts it, so tests need no photographs."""
    h, w = image.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = (xx - cx) / (s * 0.78), (yy - cy) / s
    r2 = nx**2 + ny**2
    mask = np.clip((1 - r2) * 12, 0, 1)[..., None]
    shade = (0.55 + 0.45 * np.sqrt(np.clip(1 - r2, 0, 1)) - 0.08 * nx)[..., None]
    image[...] = np.clip(image * (1 - mask) + np.array(skin, np.float32) * shade * mask, 0, 255)
    hair, dark, lip = (40, 30, 30), (60, 50, 40), (150, 60, 70)
    cv2.ellipse(image, (cx, cy - int(s * 0.28)), (int(s * 0.80), int(s * 0.78)), 0, 180, 360, hair, -1, cv2.LINE_AA)
    for side in (-1, 1):
        ex, ey = cx + side * int(s * 0.33), cy - int(s * 0.12)
        cv2.ellipse(image, (ex, ey), (int(s * 0.16), int(s * 0.085)), 0, 0, 360, (240, 235, 235), -1, cv2.LINE_AA)
        cv2.circle(image, (ex, ey), int(s * 0.075), dark, -1, cv2.LINE_AA)
        cv2.circle(image, (ex, ey), int(s * 0.035), (5, 5, 5), -1, cv2.LINE_AA)
        cv2.ellipse(image, (ex, ey - int(s * 0.13)), (int(s * 0.2), int(s * 0.05)), 0, 180, 360, hair, max(2, int(s * 0.04)), cv2.LINE_AA)
    nose = (int(skin[0] * 0.75), int(skin[1] * 0.72), int(skin[2] * 0.75))
    cv2.line(image, (cx, cy - int(s * 0.05)), (cx - int(s * 0.06), cy + int(s * 0.22)), nose, max(1, int(s * 0.03)), cv2.LINE_AA)
    cv2.line(image, (cx - int(s * 0.06), cy + int(s * 0.22)), (cx + int(s * 0.06), cy + int(s * 0.24)), nose, max(1, int(s * 0.03)), cv2.LINE_AA)
    cv2.ellipse(image, (cx, cy + int(s * 0.5)), (int(s * 0.28), int(s * 0.1)), 0, 0, 180, lip, max(2, int(s * 0.06)), cv2.LINE_AA)


def face_image(
    width: int = 640,
    height: int = 480,
    faces: tuple[tuple[int, int, int], ...] = ((320, 230, 95),),
    background: tuple[int, int, int] = (130, 140, 150),
    skin: tuple[int, int, int] = (205, 150, 120),
) -> np.ndarray:
    image = np.zeros((height, width, 3), np.uint8)
    image[...] = background
    for cx, cy, s in faces:
        draw_face(image, cx, cy, s, skin)
    return cv2.GaussianBlur(image, (0, 0), 1.2)


def write_clip(path: Path, frames: list[np.ndarray], *, fps: int = 30, rotation: int | None = None) -> Path:
    """Encode RGB frames. Rotation is stored as metadata, as a phone would."""
    height, width = frames[0].shape[:2]
    target = path if rotation is None else path.with_suffix(".raw.mp4")
    args = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-"]
    args += [
        "-c:v",
        "libx264",
        "-crf",
        "12",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-vf",
        "scale=out_color_matrix=bt709:out_range=tv",
        str(target),
    ]
    subprocess.run(args, input=b"".join(f.tobytes() for f in frames), check=True)
    if rotation is not None:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(target), "-c", "copy", str(path)], check=True)
    return path


def make_project(tmp_path: Path, clips: list[Path], segments: list[Segment], *, aspect: str = "9:16") -> Project:
    project = Project.create(tmp_path / "video", aspect=aspect)
    project.save_clips([probe_clip(path, clip_id=path.stem) for path in clips])
    project.save_edl(Edl(fps=30, segments=segments))
    return project


def face(cx: float, cy: float, w: float = 100, h: float = 120, score: float = 0.9) -> Face:
    return Face(x=cx - w / 2, y=cy - h / 2, w=w, h=h, score=score, eye_y=cy - h * 0.15)


def test_model_is_vendored_with_its_licence():
    model = framing.MODEL_PATH
    assert hashlib.sha256(model.read_bytes()).hexdigest() == "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0"
    licence = (model.parent / "LICENSE").read_text()
    assert "MIT License" in licence and "Shiqi Yu" in licence


def test_detect_finds_a_painted_face():
    found = detect_faces(face_image())
    assert len(found) == 1
    assert abs(found[0].center[0] - 320) < 30 and abs(found[0].center[1] - 240) < 40
    assert found[0].score > 0.6


def test_detect_maps_back_from_a_large_image():
    found = detect_faces(face_image(2560, 1440, ((1800, 700, 300),)))
    assert len(found) == 1
    assert abs(found[0].center[0] - 1800) < 90 and found[0].w > 250


def test_detect_finds_a_close_up():
    found = detect_faces(face_image(720, 720, ((360, 380, 300),)))
    assert len(found) == 1 and found[0].w > 300


def test_detect_returns_nothing_on_a_flat_image():
    assert detect_faces(np.full((360, 640, 3), 128, np.uint8)) == []


def test_detect_rejects_grey_input():
    with pytest.raises(AltocueError):
        detect_faces(np.zeros((100, 100), np.uint8))


def test_crop_size_keeps_the_most_picture():
    assert crop_size((1920, 1080), (1080, 1920)) == (608, 1080)
    assert crop_size((1080, 1920), (1920, 1080)) == (1080, 608)
    assert crop_size((1080, 1920), (1080, 1080)) == (1080, 1080)
    assert crop_size((2160, 3840), (1080, 1350)) == (2160, 2700)


def test_crop_follows_the_face_and_stays_inside():
    size = crop_size((1920, 1080), (1080, 1920))
    subject = choose_subject(find_tracks([[face(500, 400)]] * 3), *size)
    x, y, w, h = place_crop((1920, 1080), size, subject)
    assert abs((x + w / 2) - 500) <= 2 and y == 0 and (w, h) == size
    assert all(float(v).is_integer() and int(v) % 2 == 0 for v in (x, y, w, h))

    edge = choose_subject(find_tracks([[face(60, 400)]] * 3), *size)
    assert place_crop((1920, 1080), size, edge)[0] == 0
    far = choose_subject(find_tracks([[face(1900, 400)]] * 3), *size)
    assert place_crop((1920, 1080), size, far)[0] + size[0] == 1920


def test_no_face_means_centre():
    size = crop_size((1920, 1080), (1080, 1920))
    assert place_crop((1920, 1080), size, None) == [656.0, 0.0, 608.0, 1080.0]


def test_two_faces_stay_when_they_fit():
    size = (1000, 1080)
    subject = choose_subject(find_tracks([[face(500, 400), face(900, 420)]] * 3), *size)
    x, _, w, _ = place_crop((1920, 1080), size, subject)
    assert x < 450 and x + w > 950


def test_two_faces_that_do_not_fit_keep_the_bigger():
    size = crop_size((1920, 1080), (1080, 1920))
    subject = choose_subject(find_tracks([[face(300, 400, 80, 100), face(1500, 420, 160, 190)]] * 3), *size)
    x, _, w, _ = place_crop((1920, 1080), size, subject)
    assert x < 1500 < x + w and not x < 300 < x + w


def test_a_stray_small_face_does_not_pull_the_crop():
    size = (1000, 1080)
    subject = choose_subject(find_tracks([[face(500, 400, 200, 240), face(950, 300, 40, 50)]] * 3), *size)
    assert abs(subject.center_x - 500) < 2


def test_one_frame_detections_are_dropped():
    samples = [[face(500, 400)], [face(502, 401), face(1500, 200)], [face(498, 399)], [face(500, 400)]]
    tracks = find_tracks(samples)
    assert len(tracks) == 1 and abs(tracks[0].center_x - 500) < 3


def test_a_fast_head_still_beats_the_centre():
    samples = [[face(300, 400)], [face(900, 400)], [], [face(302, 400)], [], []]
    tracks = find_tracks(samples)
    assert len(tracks) == 1 and tracks[0].center_x == pytest.approx(301)


def test_a_segment_with_no_face_holds_the_clips_subject():
    segments = [Segment(id=f"s{i}", clip=c, start=i, end=i + 0.5) for i, c in enumerate("aaabb")]
    subject = choose_subject(find_tracks([[face(500, 400)]] * 3), 608, 1080)
    held = hold_through_gaps(segments, [None, subject, None, None, None])
    assert held == [subject, subject, subject, None, None]


def test_headroom_when_there_is_room_to_move():
    source, target = (2160, 3840), (1080, 1080)
    size = crop_size(source, target)
    subject = choose_subject(find_tracks([[face(1080, 1500, 500, 620)]] * 3), *size)
    _, y, _, h = place_crop(source, size, subject)
    eyes = (subject.eye_y - y) / h
    assert 0.3 < eyes < 0.46
    assert subject.top - y >= 0


def test_a_small_move_keeps_the_previous_crop():
    previous = [600.0, 0.0, 608.0, 1080.0]
    near = choose_subject(find_tracks([[face(930, 400)]] * 3), 608, 1080)
    assert settle_crop(previous, near, (1920, 1080), (608, 1080)) == previous
    far = choose_subject(find_tracks([[face(1300, 400)]] * 3), 608, 1080)
    assert settle_crop(previous, far, (1920, 1080), (608, 1080)) != previous


def shots(*spans: tuple[float, float], clip: str = "a", first: int = 1) -> list[Segment]:
    return [Segment(id=f"s{number}", clip=clip, start=start, end=end) for number, (start, end) in enumerate(spans, start=first)]


def said(text: str, start: float, step: float = 0.7) -> list[Word]:
    return [Word(token, round(start + i * step, 3), round(start + i * step + 0.5, 3)) for i, token in enumerate(text.split())]


def kinds(segments: list[Segment], **known) -> list[str]:
    return [join["kind"] for join in plan_shots(segments, 30, **known)[1]]


def test_the_size_changes_where_a_beat_opens_and_each_clip_starts_wide():
    """With no words to read, a second or more taken out is a new beat and less is the same thought."""
    segments = [*shots((0, 3), (4, 7), (7.3, 10)), *shots((0, 3), clip="b", first=4)]
    assert [round(z, 3) for z in plan_zooms(segments, fps=30)] == [1.0, PUNCH_ZOOM, PUNCH_ZOOM, 1.0]
    assert kinds(segments) == ["toggle", "bare"]
    assert PUNCH_ZOOM == pytest.approx(1.12)


def test_footage_that_runs_on_is_not_a_jump():
    segments = shots((0, 3), (3, 6), (7, 10))
    assert plan_zooms(segments, fps=30) == [1.0, 1.0, PUNCH_ZOOM]
    assert kinds(segments) == ["toggle"]


def test_the_size_never_changes_inside_a_sentence():
    """An um taken out mid-thought must not flip the shot. The next sentence may."""
    segments = shots((0, 3), (3.5, 6.5), (7, 10))
    words = {"a": [*said("We plan the work", 0.2), Word("um", 3.05, 3.4, kind="filler"), *said("every single day.", 3.6), *said("Then we ship.", 7.1)]}
    removed = [Removed("a", 3.05, 3.4, "filler", "um")]
    tight, joins = plan_shots(segments, 30, words=words, removed=removed)
    assert tight == [False, False, True]
    assert [(join["kind"], join["beat"]) for join in joins] == [("bare", False), ("toggle", True)]
    assert joins[0]["why"] == "inside a beat" and joins[0]["at"] == 3.0 and joins[0]["stamp"] == "00:03.0"


def test_a_retake_or_a_long_pause_opens_a_beat_mid_sentence():
    segments = shots((0, 3), (4.5, 7.5))
    words = {"a": [*said("We plan the work", 0.2), *said("and ship it.", 4.6)]}
    assert plan_zooms(segments, 30, words=words) == [1.0, 1.0]
    assert plan_zooms(segments, 30, words=words, removed=[Removed("a", 2.9, 4.6, "silence", "1.7 s pause")]) == [1.0, PUNCH_ZOOM]
    assert plan_zooms(segments, 30, words=words, removed=[Removed("a", 2.9, 3.3, "silence", "0.4 s pause")]) == [1.0, 1.0]
    assert plan_zooms(segments, 30, words=words, removed=[Removed("a", 3.0, 4.5, "retake", "said again")]) == [1.0, PUNCH_ZOOM]
    assert plan_zooms(segments, 30, words=words, removed=[Removed("a", 3.0, 4.5, "retake", "said again", decision="review")]) == [1.0, 1.0]


def test_the_size_is_held_for_min_hold():
    segments = shots((0, 1.5), (2.5, 4), (5, 8))
    assert plan_zooms(segments, 30, min_hold=0.5) == [1.0, PUNCH_ZOOM, 1.0]
    assert plan_zooms(segments, 30, min_hold=2.0) == [1.0, 1.0, PUNCH_ZOOM]
    assert plan_zooms(segments, 30) == plan_zooms(segments, 30, min_hold=2.0)
    held = plan_shots(segments, 30, min_hold=2.0)[1][0]
    assert held["kind"] == "bare" and "min_hold" in held["why"]


def test_no_new_size_for_a_beat_that_is_over_at_once():
    segments = shots((0, 3), (4, 4.8), (5.8, 9))
    tight, joins = plan_shots(segments, 30)
    assert tight == [False, False, True]
    assert joins[0]["kind"] == "bare" and "next beat" in joins[0]["why"]


def test_a_join_under_an_insert_never_changes_size():
    segments = shots((0, 3), (4, 7))
    assert plan_zooms(segments, 30) == [1.0, PUNCH_ZOOM]
    assert plan_zooms(segments, 30, inserts=[(2.5, 3.5)]) == [1.0, 1.0]
    assert kinds(segments, inserts=[(2.5, 3.5)]) == ["covered"]
    assert plan_zooms(segments, 30, inserts=[(2.95, 3.5)]) == [1.0, PUNCH_ZOOM], "an insert must run four frames past the join on both sides"


def test_a_join_nobody_would_see_never_changes_size():
    segments = shots((0, 3), (4, 7))
    assert plan_zooms(segments, 30, shows={1: False}) == [1.0, 1.0]
    assert kinds(segments, shows={1: False}) == ["invisible"]
    assert plan_zooms(segments, 30, shows={1: True}) == [1.0, PUNCH_ZOOM]


def test_a_join_shows_when_the_face_moves_or_changes_size():
    still = [face(500, 400, 200, 240)]
    assert not framing.jump_shows(still, [face(505, 402, 200, 244)], 608)
    assert framing.jump_shows(still, [face(520, 400, 200, 240)], 608)
    assert framing.jump_shows(still, [face(500, 400, 200, 250)], 608)
    assert framing.jump_shows(still, [], 608) and framing.jump_shows([], still, 608), "with no face to compare, nobody can call it hidden"


def test_an_emphasised_line_plays_tight_whatever_the_hold():
    segments = shots((0, 1), (2, 3), (4, 5))
    assert plan_zooms(segments, 30) == [1.0, 1.0, 1.0]
    assert plan_zooms(segments, 30, emphasis={"s2"}) == [1.0, PUNCH_ZOOM, PUNCH_ZOOM]
    assert plan_zooms(segments, 30, emphasis={"s1"}) == [PUNCH_ZOOM, PUNCH_ZOOM, PUNCH_ZOOM]


def test_emphasis_finds_the_segments_that_say_a_phrase():
    segments = shots((0, 3), (3.5, 6.5), (7, 10))
    words = {"a": [*said("We plan the work", 0.2), *said("every single day.", 3.6), *said("Then we ship.", 7.1)]}
    assert framing.emphasised(["Single day"], segments, words) == ({"s2"}, [])
    assert framing.emphasised(["the work, every"], segments, words) == ({"s1", "s2"}, [])
    assert framing.emphasised(["never said"], segments, words) == (set(), ["never said"])


def test_punch_backs_off_rather_than_cut_the_head():
    crop = [0.0, 0.0, 608.0, 1080.0]
    tall = choose_subject(find_tracks([[face(304, 330, 400, 560)]] * 3), 608, 1080)
    assert 1.0 <= safe_zoom(crop, tall, PUNCH_ZOOM) < PUNCH_ZOOM
    small = choose_subject(find_tracks([[face(304, 400, 100, 120)]] * 3), 608, 1080)
    assert safe_zoom(crop, small, PUNCH_ZOOM) == PUNCH_ZOOM


def test_a_punch_in_holds_the_eyes_where_they_were():
    """A speaker at the edge of the picture: the crop is clamped there, so the eyes are nowhere near the old fixed point."""
    size = crop_size((1920, 1080), (1080, 1920))
    subject = choose_subject(find_tracks([[face(150, 500, 200, 240)]] * 3), *size)
    crop = place_crop((1920, 1080), size, subject)
    origin = framing.eye_origin(crop, subject)
    assert crop[0] == 0 and origin[0] < 0.3
    assert origin_of(Segment(id="s", clip="a", start=0, end=1, origin=framing.eye_point(subject)), tuple(crop)) == tuple(origin), "as the plate reads it"

    def eyes(zoom: float, about) -> tuple[float, float]:
        x, y, width, height = zoomed(tuple(crop), zoom, about)
        assert crop[0] <= x and x + width <= crop[0] + crop[2] + 1e-6 and crop[1] <= y and y + height <= crop[1] + crop[3] + 1e-6
        return ((subject.center_x - x) / width, (subject.eye_y - y) / height)

    wide, punched = eyes(1.0, origin), eyes(PUNCH_ZOOM, origin)
    assert abs(punched[0] - wide[0]) < 0.01 and abs(punched[1] - wide[1]) < 0.01
    assert abs(eyes(PUNCH_ZOOM, ZOOM_ORIGIN)[0] - wide[0]) > 0.02, "about the old fixed point the same eyes slide sideways"
    assert safe_zoom(crop, subject, PUNCH_ZOOM, origin) == PUNCH_ZOOM, "about the eyes the punch-in cannot push the face out"
    assert safe_zoom(crop, subject, PUNCH_ZOOM) == 1.0, "about the fixed point it had to be dropped"


def test_an_eye_point_outside_the_crop_is_pulled_to_its_edge():
    subject = choose_subject(find_tracks([[face(900, 400)]] * 3), 608, 1080)
    assert framing.eye_origin([0.0, 0.0, 608.0, 1080.0], subject)[0] == 1.0
    assert framing.eye_origin([0.0, 0.0, 608.0, 1080.0], None) is None


@pytest.fixture
def landscape(tmp_path: Path) -> Path:
    image = face_image(1280, 720, ((360, 330, 150),))
    return write_clip(tmp_path / "wide.mp4", [image] * 90)


@pytest.fixture(scope="module")
def walkabout(tmp_path_factory) -> Path:
    """A head that keeps still for a shot and sits somewhere else for the next, so every join between the three shows."""
    stills = [face_image(800, 640, ((x, 300, 120),)) for x in (300, 360, 420)]
    frames = [stills[0]] * 17 + [stills[1]] * 18 + [stills[2]] * 19  # at 10 frames a second: 1.7 s, 1.8 s and 1.9 s
    return write_clip(tmp_path_factory.mktemp("moving") / "moving.mp4", frames, fps=10)


def walked(tmp_path: Path, clip: Path, aspect: str) -> Project:
    """Three shots, one at each place the head sits. With no transcript, 0.3 s cut out is one beat and 1 s is a new one."""
    project = make_project(tmp_path, [clip], shots((0, 1.5), (1.8, 2.6), (3.6, 5.2), clip="moving"), aspect=aspect)
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace("# min_hold = 2.0", "min_hold = 1.0", 1))
    return Project.open(project.root)


def report_of(project: Project) -> dict:
    return json.loads((project.work / "frame.json").read_text())


def test_frame_project_crops_to_the_face_and_leaves_a_join_nobody_sees(tmp_path, landscape):
    segments = [Segment(id="s1", clip="wide", start=0, end=1), Segment(id="s2", clip="wide", start=1.5, end=2.5)]
    project = make_project(tmp_path, [landscape], segments)
    edl = frame_project(project)
    first, second = edl.segments
    x, y, w, h = first.crop
    assert y == 0 and h == 720 and w == 404
    assert x < 360 < x + w and abs(x + w / 2 - 360) < 30
    assert second.crop == first.crop
    assert first.zoom_from == first.zoom_to == second.zoom_from == second.zoom_to == 1.0
    assert abs(first.origin[0] - 360) < 20 and 250 < first.origin[1] < 330, "the zoom point is the eyes, in the picture's own pixels"
    saved = json.loads(project.edl_path.read_text())
    assert saved["segments"][0]["crop"] == first.crop and saved["segments"][0]["origin"] == first.origin
    report = report_of(project)
    assert [join["kind"] for join in report["joins"]] == ["invisible"] and report["bare_jumps"] == []


def test_a_new_beat_gets_the_punch_in_and_a_jump_inside_one_is_reported_bare(tmp_path, walkabout):
    project = walked(tmp_path, walkabout, "1:1")
    edl = frame_project(project)
    assert [s.zoom_from for s in edl.segments] == [1.0, 1.0, pytest.approx(PUNCH_ZOOM)]
    assert all(s.zoom_to == s.zoom_from and s.origin and s.ease is None for s in edl.segments)
    report = report_of(project)
    assert report["min_hold"] == 1.0
    assert report["bare_jumps"] == [{"at": 1.5, "stamp": "00:01.5", "before": "s1", "after": "s2", "why": "inside a beat"}]
    assert [(join["kind"], join["beat"], join["face_moved"]) for join in report["joins"]] == [("bare", False, True), ("toggle", True, True)]


def test_a_soft_picture_is_measured_and_said(tmp_path, walkabout):
    """800 pixels across for a 1080 square: enlarged 1.69 times, and 1.89 in the punch-in. Soft, but the punch-in stays."""
    project = walked(tmp_path, walkabout, "1:1")
    frame_project(project)
    report = report_of(project)
    assert [row["upscale"] for row in report["segments"]] == [pytest.approx(1080 / 640, abs=0.001)] * 2 + [pytest.approx(1080 * PUNCH_ZOOM / 640, abs=0.001)]
    assert report["upscale"] == {"warn_over": 1.5, "drop_over": 2.0, "worst": report["segments"][2]["upscale"], "soft": ["s1", "s2", "s3"], "punch_dropped": []}
    assert len(report["warnings"]) == 1 and report["warnings"][0].startswith("moving: the picture is enlarged 1.89 times")
    assert "4:5" not in report["warnings"][0], "a 4:5 crop of this source is narrower still, so it is not offered"


def test_a_source_without_the_pixels_loses_its_punch_in_and_says_so(tmp_path, walkabout, capsys):
    project = walked(tmp_path, walkabout, "9:16")
    assert main(["frame", "--project", str(project.root)]) == 0
    said_out = capsys.readouterr().out
    assert all(s.zoom_from == s.zoom_to == 1.0 for s in project.edl().segments)
    report = report_of(project)
    assert report["upscale"]["punch_dropped"] == ["moving"] and report["upscale"]["worst"] == pytest.approx(3.0)
    assert [(join["kind"], join["why"]) for join in report["joins"]] == [("bare", "inside a beat"), ("bare", "the punch-in was dropped on this clip")]
    assert "past 2, so this clip plays without one" in report["warnings"][0] and 'aspect = "4:5"' in report["warnings"][0]
    assert "Warning: moving:" in said_out and "enlarged 3.00 times" in said_out
    assert "2 jump cuts left bare" in said_out and "00:01.5 (s1 to s2, inside a beat)" in said_out and "work/frame.json" in said_out


def test_chosen_broll_over_a_join_keeps_the_size(tmp_path, walkabout):
    project = walked(tmp_path, walkabout, "1:1")
    assert frame_project(project).segments[2].zoom_from > 1
    broll = project.root / "broll.json"
    broll.write_text(json.dumps({"cut": "another cut", "inserts": [{"file": "b.mp4", "start": 2.0, "end": 2.8}]}))
    assert frame_project(project).segments[2].zoom_from > 1, "B-roll chosen on another cut covers nothing here"
    broll.write_text(json.dumps({"cut": project.cut_mark(), "inserts": [{"file": "b.mp4", "start": 2.6, "end": 3.4}]}))
    assert framing.up_to_date(project), "B-roll clear of every jump changes nothing here"
    broll.write_text(json.dumps({"cut": project.cut_mark(), "inserts": [{"file": "b.mp4", "start": 2.0, "end": 2.8}]}))
    assert not framing.up_to_date(project)
    assert frame_project(project).segments[2].zoom_from == 1.0
    assert [join["kind"] for join in report_of(project)["joins"]] == ["bare", "covered"]


def test_an_insert_in_motion_json_covers_a_join_too(tmp_path, walkabout):
    project = walked(tmp_path, walkabout, "1:1")
    (project.root / "motion.json").write_text(json.dumps({"inserts": [{"file": "b.mp4", "start": 1.2, "duration": 0.6}]}))
    frame_project(project)
    assert [join["kind"] for join in report_of(project)["joins"]] == ["covered", "toggle"]


def test_min_hold_follows_the_pace_unless_it_is_set(tmp_path):
    project = Project.create(tmp_path / "video")
    assert framing.min_hold_of(project) == 2.0
    for pace, seconds in (("tight", 1.2), ("natural", 2.0), ("relaxed", 3.0)):
        project.config["cut"]["pace"] = pace
        assert framing.min_hold_of(project) == seconds
    project.config["frame"]["min_hold"] = 0.8
    assert framing.min_hold_of(project) == 0.8
    project.config["frame"]["min_hold"] = -1
    with pytest.raises(AltocueError) as caught:
        framing.min_hold_of(project)
    assert caught.value.fix
    del project.config["frame"]["min_hold"]
    project.config["cut"]["pace"] = "brisk"
    with pytest.raises(AltocueError) as caught:
        framing.min_hold_of(project)
    assert "tight" in caught.value.fix


def test_a_replaced_source_is_framed_again(tmp_path):
    clip = write_clip(tmp_path / "tall.mp4", [face_image(720, 1280, ((360, 500, 170),))] * 45)
    project = make_project(tmp_path, [clip], [Segment(id="s1", clip="tall", start=0, end=1)])
    frame_project(project)
    before = framing._fingerprint(project, project.edl(), project.size)
    assert framing.up_to_date(project)
    write_clip(clip, [face_image(720, 1280, ((300, 700, 170),))] * 45)  # a re-export of the same length
    assert framing._fingerprint(project, project.edl(), project.size) != before
    assert not framing.up_to_date(project)


def test_source_that_matches_stays_uncropped(tmp_path):
    clip = write_clip(tmp_path / "tall.mp4", [face_image(720, 1280, ((360, 500, 170),))] * 45)
    project = make_project(tmp_path, [clip], [Segment(id="s1", clip="tall", start=0, end=1)])
    assert frame_project(project).segments[0].crop is None


def test_rotation_metadata_gives_display_pixels(tmp_path):
    # Coded upright, then flagged so the player turns it: the head ends up off centre in a landscape display.
    image = face_image(720, 1280, ((360, 400, 170),))
    turned = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    clip = write_clip(tmp_path / "phone.mp4", [turned] * 45, rotation=-90)
    probed = probe_clip(clip, clip_id="phone")
    assert abs(probed.rotation) == 90 and probed.display_size == (720, 1280)
    project = make_project(tmp_path, [clip], [Segment(id="s1", clip="phone", start=0, end=1)], aspect="1:1")
    x, y, w, h = frame_project(project).segments[0].crop
    assert (x, w) == (0, 720) and h == 720
    assert y < 400 < y + h


def test_no_face_clip_is_centred(tmp_path, clip):
    project = make_project(tmp_path, [clip], [Segment(id="s1", clip="take one", start=0, end=1)], aspect="1:1")
    x, y, w, h = frame_project(project).segments[0].crop
    assert (w, h) == (320, 320) and x == 0 and abs(y - (568 - 320) * 0.42) <= 2


def test_a_rerun_that_changes_nothing_leaves_the_cut_file_alone(tmp_path):
    """Every render frames first, and a rewritten edl.json made a finished film look older than its cut."""
    clip = write_clip(tmp_path / "tall.mp4", [face_image(720, 1280, ((360, 500, 170),))] * 45)
    project = make_project(tmp_path, [clip], [Segment(id="s1", clip="tall", start=0, end=1)])
    frame_project(project)
    os.utime(project.edl_path, (1_000_000, 1_000_000))
    frame_project(project)
    assert project.edl_path.stat().st_mtime == 1_000_000


def test_rerun_keeps_a_manual_crop_until_forced(tmp_path, landscape):
    project = make_project(tmp_path, [landscape], [Segment(id="s1", clip="wide", start=0, end=1)])
    edl = frame_project(project)
    solved = edl.segments[0].origin
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    kept = frame_project(project).segments[0]
    assert kept.crop == [10.0, 0.0, 406.0, 720.0]
    assert kept.origin == solved, "the eyes are where they were"
    assert origin_of(kept, tuple(kept.crop))[0] == pytest.approx((360 - 10) / 406, abs=0.05), "so a zoom in the crop a person chose still holds them"
    assert frame_project(project, force=True).segments[0].crop != [10.0, 0.0, 406.0, 720.0]


def test_push_in_is_optional(tmp_path, landscape):
    """The older key: one amount for each shot, at an even speed. It still works while `push_rate` is 0."""
    project = make_project(tmp_path, [landscape], [Segment(id="s1", clip="wide", start=0, end=1)])
    project.config.setdefault("frame", {})["push_in"] = 0.02
    edl = frame_project(project)
    assert edl.segments[0].zoom_from == 1.0 and edl.segments[0].zoom_to == pytest.approx(1.02) and edl.segments[0].ease is None


def drawn(move: tuple[float, float, list[float] | None], seconds: float, length: float) -> float:
    """The zoom the plate draws `seconds` into a segment `length` long."""
    first, last, (at, over) = move
    return first + (last - first) * (push_travel(at + seconds, over) - push_travel(at, over)) / (push_travel(at + length, over) - push_travel(at, over))


def test_a_push_moves_at_its_rate_whatever_the_length_of_the_shot():
    rate = 0.004
    short, long = plan_push(shots((0, 1)), 30, [1.0], rate)[0], plan_push(shots((0, 10)), 30, [1.0], rate)[0]
    for move, length in ((short, 1.0), (long, 10.0)):
        assert (drawn(move, 0.6, length) - drawn(move, 0.4, length)) / 0.2 == pytest.approx(rate, rel=0.01)
        assert (drawn(move, 0.1, length) - move[0]) / 0.1 < rate / 2, "it starts gently"
        assert (move[1] - drawn(move, length - 0.1, length)) / 0.1 < rate / 2, "and it stops gently"
    assert short[1] == pytest.approx(1.0 + rate * 0.7) and long[1] == pytest.approx(1.0 + rate * 9.7)


def test_a_push_carries_across_joins_and_stops_at_its_cap():
    held = plan_push(shots((0, 4), (5, 9), (10, 14)), 30, [1.0, 1.0, 1.0], 0.008)
    assert held[0][0] == 1.0 and held[0][1] == held[1][0] > 1.0, "no snap back at a join inside a held shot"
    assert held[1][1] == pytest.approx(1.0 + framing.PUSH_CAP) and held[2] == (held[1][1], held[1][1], None)
    assert held[0][2] == [0.0, 7.8] and held[1][2] == [4.0, 7.8]
    fresh = plan_push([*shots((0, 4), (5, 9)), *shots((0, 4), clip="b", first=3)], 30, [1.0, 1.12, 1.12], 0.008)
    assert [move[0] for move in fresh] == [1.0, 1.12, 1.12], "a new size, and a new clip, start a push of their own"


def test_push_rate_runs_one_push_over_a_held_shot(tmp_path, landscape):
    segments = [Segment(id="s1", clip="wide", start=0, end=1), Segment(id="s2", clip="wide", start=1.5, end=2.5)]
    project = make_project(tmp_path, [landscape], segments)
    project.config["frame"] |= {"push_rate": 0.01, "push_in": 0.5}
    first, second = frame_project(project).segments
    assert first.zoom_from == 1.0 and first.zoom_to == second.zoom_from == pytest.approx(1.0 + 0.01 * 0.85)
    assert second.zoom_to == pytest.approx(1.0 + 0.01 * 1.7), "the rate wins over the older amount"
    assert first.ease == [0.0, 2.0] and second.ease == [1.0, 2.0]
    project.config["frame"]["push_rate"] = -0.01
    with pytest.raises(AltocueError) as caught:
        frame_project(project)
    assert caught.value.fix


def test_cli_frames_a_project(tmp_path, landscape, capsys):
    project = make_project(tmp_path, [landscape], [Segment(id="s1", clip="wide", start=0, end=1)])
    assert main(["frame", "--project", str(project.root)]) == 0
    assert "s1" in capsys.readouterr().out
    assert Project.open(project.root).edl().segments[0].crop


def test_missing_cut_is_explained(tmp_path):
    project = Project.create(tmp_path / "video")
    with pytest.raises(AltocueError) as caught:
        frame_project(project)
    assert caught.value.fix


def test_a_fresh_cut_of_the_same_ranges_gets_its_framing_back(tmp_path, landscape):
    project = make_project(tmp_path, [landscape], [Segment(id="s1", clip="wide", start=0, end=1)])
    framed = frame_project(project).segments[0].crop
    assert framed
    project.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="wide", start=0, end=1)]))
    assert frame_project(project).segments[0].crop == framed


@pytest.mark.parametrize(
    ("aspect", "size"),
    [("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080)), ("4:5", (1080, 1350)), ("4:3", (1440, 1080)), ("3:4", (1080, 1440)), ("2:3", (1080, 1620)),
     ("21:9", (1920, 822)), ("2.39:1", (1920, 804)), ("32:18", (1920, 1080)), ("4:1", (1920, 480))],
)  # fmt: skip
def test_any_aspect_gets_a_size_on_even_numbers(aspect, size):
    assert size_for(aspect) == size and size[0] % 2 == 0 and size[1] % 2 == 0


@pytest.mark.parametrize("aspect", ["wide", "0:9", "16:0", "5:1", "1:5", "16x9", ""])
def test_an_aspect_that_cannot_be_framed_is_refused(aspect):
    with pytest.raises(AltocueError) as caught:
        size_for(aspect)
    assert "W:H" in caught.value.fix


def test_a_new_aspect_works_for_a_project_and_centres_a_crop(tmp_path):
    project = Project.create(tmp_path / "video", aspect="21:9")
    assert project.size == (1920, 822)
    assert 'aspect = "21:9"' in (project.root / "altocue.toml").read_text()
