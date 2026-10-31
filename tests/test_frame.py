from __future__ import annotations

import hashlib
import json
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
    plan_zooms,
    safe_zoom,
    settle_crop,
)
from altocue.models import Edl, Segment
from altocue.project import Project


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
    cv2.line(
        image, (cx - int(s * 0.06), cy + int(s * 0.22)), (cx + int(s * 0.06), cy + int(s * 0.24)), nose, max(1, int(s * 0.03)), cv2.LINE_AA
    )
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
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(target), "-c", "copy", str(path)], check=True
        )
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


def test_punch_ins_alternate_on_jump_cuts_only():
    segments = [
        Segment(id="s1", clip="a", start=0, end=2),
        Segment(id="s2", clip="a", start=3, end=5),
        Segment(id="s3", clip="a", start=6, end=7),
        Segment(id="s4", clip="a", start=7, end=8),
        Segment(id="s5", clip="b", start=0, end=2),
        Segment(id="s6", clip="b", start=3, end=4),
    ]
    assert [round(z, 3) for z in plan_zooms(segments, fps=30)] == [1.0, PUNCH_ZOOM, 1.0, 1.0, 1.0, PUNCH_ZOOM]
    assert PUNCH_ZOOM == pytest.approx(1.12)


def test_punch_backs_off_rather_than_cut_the_head():
    crop = [0.0, 0.0, 608.0, 1080.0]
    tall = choose_subject(find_tracks([[face(304, 330, 400, 560)]] * 3), 608, 1080)
    assert 1.0 <= safe_zoom(crop, tall, PUNCH_ZOOM) < PUNCH_ZOOM
    small = choose_subject(find_tracks([[face(304, 400, 100, 120)]] * 3), 608, 1080)
    assert safe_zoom(crop, small, PUNCH_ZOOM) == PUNCH_ZOOM


@pytest.fixture
def landscape(tmp_path: Path) -> Path:
    image = face_image(1280, 720, ((360, 330, 150),))
    return write_clip(tmp_path / "wide.mp4", [image] * 90)


def test_frame_project_crops_to_the_face_and_alternates_zoom(tmp_path, landscape):
    segments = [Segment(id="s1", clip="wide", start=0, end=1), Segment(id="s2", clip="wide", start=1.5, end=2.5)]
    project = make_project(tmp_path, [landscape], segments)
    edl = frame_project(project)
    first, second = edl.segments
    x, y, w, h = first.crop
    assert y == 0 and h == 720 and w == 404
    assert x < 360 < x + w and abs(x + w / 2 - 360) < 30
    assert second.crop == first.crop
    assert first.zoom_from == first.zoom_to == 1.0
    assert second.zoom_from == second.zoom_to == pytest.approx(PUNCH_ZOOM)
    saved = json.loads(project.edl_path.read_text())
    assert saved["segments"][0]["crop"] == first.crop


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


def test_rerun_keeps_a_manual_crop_until_forced(tmp_path, landscape):
    project = make_project(tmp_path, [landscape], [Segment(id="s1", clip="wide", start=0, end=1)])
    edl = frame_project(project)
    edl.segments[0].crop = [10.0, 0.0, 406.0, 720.0]
    project.save_edl(edl)
    assert frame_project(project).segments[0].crop == [10.0, 0.0, 406.0, 720.0]
    assert frame_project(project, force=True).segments[0].crop != [10.0, 0.0, 406.0, 720.0]


def test_push_in_is_optional(tmp_path, landscape):
    project = make_project(tmp_path, [landscape], [Segment(id="s1", clip="wide", start=0, end=1)])
    project.config.setdefault("frame", {})["push_in"] = 0.02
    edl = frame_project(project)
    assert edl.segments[0].zoom_from == 1.0 and edl.segments[0].zoom_to == pytest.approx(1.02)


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
