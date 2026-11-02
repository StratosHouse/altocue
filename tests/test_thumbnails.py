"""Thumbnails come from real frames, and a sharp face with open eyes wins."""

from __future__ import annotations

import numpy as np
from conftest import make_clip
from PIL import Image

from altocue import thumbnails
from altocue.frame import Face
from altocue.thumbnails import Moment, eye_openness, moments, pick, sharpness


def skin(height=240, width=240):
    return np.full((height, width), 170.0, dtype=np.float32)


def face_at():
    return Face(40, 40, 160, 160, 0.9, 100, ((90.0, 100.0), (150.0, 100.0)))


def open_eyes():
    image = skin()
    for x in (90, 150):
        yy, xx = np.ogrid[:240, :240]
        image[(yy - 100) ** 2 + (xx - x) ** 2 <= 49] = 40.0
    return image


def shut_eyes():
    image = skin()
    for x in (90, 150):
        image[100:102, x - 16 : x + 16] = 40.0
    return image


def test_an_open_eye_measures_above_the_line_and_a_shut_one_below():
    face = face_at()
    assert eye_openness(open_eyes(), face) > thumbnails.OPEN_EYES > eye_openness(shut_eyes(), face)


def test_a_face_with_no_eye_points_cannot_be_called_open():
    assert eye_openness(open_eyes(), Face(40, 40, 160, 160)) is None


def test_a_blurred_picture_reads_softer():
    rng = np.random.default_rng(1)
    crisp = rng.integers(0, 255, (200, 200), dtype=np.uint8).astype(np.float32)
    soft = np.array(Image.fromarray(crisp.astype(np.uint8)).resize((50, 50)).resize((200, 200), Image.BILINEAR), dtype=np.float32)
    assert sharpness(crisp) > sharpness(soft)


def test_moments_stay_away_from_the_cuts():
    times = moments([(0.0, 2.0), (2.0, 4.0)])
    assert times and all(min(abs(t - c) for c in (0.0, 2.0, 4.0)) >= thumbnails.CUT_MARGIN for t in times)


def test_a_short_shot_gets_enough_looks_to_miss_a_blink():
    assert len(moments([(0.0, 2.5)])) >= 5


def test_moments_cover_a_long_film_without_reading_every_frame():
    times = moments([(float(i), float(i + 1)) for i in range(400)])
    assert len(times) <= thumbnails.MOST and times[0] < 10 and times[-1] > 390


def test_open_eyes_beat_a_sharper_blink():
    blink = Moment(at=1.0, face=True, eyes=0.1, sharp=900.0, size=0.2)
    awake = Moment(at=5.0, face=True, eyes=0.3, sharp=300.0, size=0.2)
    assert pick([blink, awake], 1) == [awake]


def test_any_face_beats_no_face_and_the_sharpest_wall_comes_last():
    wall = Moment(at=9.0, face=False, eyes=None, sharp=2000.0, size=0.0)
    blink = Moment(at=1.0, face=True, eyes=0.1, sharp=100.0, size=0.2)
    awake = Moment(at=5.0, face=True, eyes=0.3, sharp=300.0, size=0.2)
    assert pick([wall, blink, awake], 3) == [awake, blink, wall]


def test_candidates_are_spread_out():
    near = [Moment(at=2.0 + i * 0.1, face=True, eyes=0.3, sharp=500.0 - i, size=0.2) for i in range(5)]
    far = Moment(at=9.0, face=True, eyes=0.3, sharp=100.0, size=0.2)
    assert [m.at for m in pick([*near, far], 2)] == [2.0, 9.0]


def test_a_film_with_no_face_still_gets_candidates_and_a_sheet(tmp_path):
    film = make_clip(tmp_path / "film.mp4", seconds=3.0, size="320x568")
    done = thumbnails.write(film, [(0.0, 3.0)], tmp_path / "thumbs", count=2)
    assert len(done["candidates"]) == 2
    assert all(not c["face"] for c in done["candidates"])
    assert all((tmp_path / "thumbs" / c["file"]).exists() for c in done["candidates"])
    with Image.open(tmp_path / "thumbs" / done["sheet"]) as sheet:
        assert sheet.width > 320


def test_a_painted_face_is_found_on_a_real_frame(tmp_path):
    from test_frame import face_image, write_clip

    film = write_clip(tmp_path / "film.mp4", [face_image(640, 480, ((320, 230, 150),))] * 30)
    done = thumbnails.write(film, [(0.0, 1.0)], tmp_path / "thumbs", count=1)
    assert done["candidates"][0]["face"] is True
