from __future__ import annotations

import re

import pytest

from altocue.models import Clip, Segment
from altocue.plate import Canvas, eased_place, fit_crop, origin_of, push_filter, push_travel, segment_filter, zoomed

CANVAS = Canvas(1080, 1920, 30, 16, "medium")


def clip(width=1920, height=1080, rotation=0):
    return Clip(id="a", path="/a.mov", duration=10, fps=30, width=width, height=height, rotation=rotation)


def test_fit_crop_centres_the_target_aspect():
    x, y, width, height = fit_crop((1920, 1080), (1080, 1920), None)
    assert (x, y) == ((1920 - 607.5) / 2, 0.0) and height == 1080 and round(width, 1) == 607.5


def test_tall_source_for_square_output_keeps_the_top_third():
    _, y, _, height = fit_crop((1080, 1920), (1080, 1080), None)
    assert height == 1080 and 0 < y < (1920 - 1080) / 2


def test_given_crop_is_respected():
    assert fit_crop((1920, 1080), (1080, 1920), [10, 20, 225, 400]) == (10.0, 20.0, 225.0, 400.0)


def test_a_crop_from_an_older_aspect_is_not_stretched():
    assert fit_crop((1920, 1080), (1920, 1080), [10, 20, 225, 400]) == (0.0, 0.0, 1920.0, 1080.0)


def test_zoom_narrows_the_crop():
    _, _, width, height = zoomed((0, 0, 1000, 2000), 1.25)
    assert width == 800 and height == 1600


def test_a_zoom_holds_its_origin_still_and_stays_inside_the_crop():
    crop = (100.0, 50.0, 1000.0, 2000.0)
    assert zoomed((0, 0, 1000, 2000), 1.25) == (100.0, 168.0, 800.0, 1600.0), "with no point named, the middle and 42 percent down, as before"
    for origin in ((0.2, 0.3), (0.0, 0.0), (1.0, 1.0)):
        x, y, width, height = zoomed(crop, 1.25, origin)
        point = (crop[0] + crop[2] * origin[0], crop[1] + crop[3] * origin[1])
        assert ((point[0] - x) / width, (point[1] - y) / height) == pytest.approx(origin)
        assert crop[0] <= x and x + width <= crop[0] + crop[2] + 1e-9 and crop[1] <= y and y + height <= crop[1] + crop[3] + 1e-9


def test_a_cut_from_before_the_zoom_origin_plays_as_it_did():
    old = Segment.from_dict({"id": "s", "clip": "a", "start": 0, "end": 2, "zoom_from": 1.12, "zoom_to": 1.12, "crop": [656.0, 0.0, 608.0, 1080.0]})
    assert old.origin is None and old.ease is None
    x, y, width, height = 656 + (608 - 608 / 1.12) / 2, (1080 - 1080 / 1.12) * 0.42, 608 / 1.12, 1080 / 1.12
    assert f"crop={int(width / 2) * 2}:{int(height / 2) * 2}:{int(x)}:{int(y)},scale=1080:1920" in segment_filter(clip(), old, CANVAS, None)
    assert push_filter(CANVAS, 60, 1.0, 1.06).startswith("perspective=((W-(W/(1.0+(1.06-1.0)*in/59)))/2):((H-(H/(1.0+(1.06-1.0)*in/59)))*0.42):")
    crop = (656.0, 0.0, 608.0, 1080.0)
    assert origin_of(old, crop) == (0.5, 0.42)
    assert origin_of(Segment(id="s", clip="a", start=0, end=2, origin=[808.0, 270.0]), crop) == (0.25, 0.25)
    assert origin_of(Segment(id="s", clip="a", start=0, end=2, origin=[5000.0, -20.0]), crop) == (1.0, 0.0), "a point outside the crop would take the window outside it"


def test_a_new_zoom_origin_is_a_new_recipe():
    """The plate caches each segment by its filter chain, so the chain must change when the point does."""

    def chain(origin, zoom_to=1.12):
        return segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.12, zoom_to=zoom_to, origin=origin), CANVAS, None)

    assert len({chain(None), chain([840.0, 380.0]), chain([1020.0, 380.0])}) == 3
    assert len({chain(None, 1.18), chain([840.0, 380.0], 1.18), chain([840.0, 430.0], 1.18)}) == 3
    assert chain([960.0, 453.6]) == chain(None), "the old fixed point, named in pixels, is the old picture"
    wide = [segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, origin=origin), CANVAS, None) for origin in (None, [840.0, 380.0])]
    assert wide[0] == wide[1], "at 1.0 the point changes nothing, so nothing is encoded again"


def place_at(expression: str, frame: int) -> float:
    """What ffmpeg works out for one frame of a piece, counted from 0. Its own counter, `in`, starts at 1."""
    names = {"clip": lambda value, low, high: min(max(value, low), high), "pow": pow, "max": max, "n": frame + 1}
    return eval(re.sub(r"\bin\b", "n", expression.replace("\\,", ",")), {"__builtins__": {}}, names)


def test_an_eased_push_draws_the_curve_the_frame_stage_planned():
    canvas = Canvas(1080, 1920, 30, 16, "medium")
    whole = eased_place(canvas, 60, 0, [0.0, 2.0])
    assert place_at(whole, 0) == 0 and place_at(whole, 60) == pytest.approx(1.0)
    assert place_at(whole, 30) == pytest.approx(0.5), "it speeds up and slows down by the same amount"
    assert place_at(whole, 3) < 3 / 60 / 2 and 1 - place_at(whole, 57) < 3 / 60 / 2, "and it is slow at both ends"
    later = eased_place(canvas, 30, 0, [1.0, 2.0])  # the second half of that push, as the next segment
    for frame in (0, 10, 29, 30):
        expected = (push_travel(1.0 + frame / 30, 2.0) - push_travel(1.0, 2.0)) / (push_travel(2.0, 2.0) - push_travel(1.0, 2.0))
        assert place_at(later, frame) == pytest.approx(expected, abs=1e-4)
    assert place_at(eased_place(canvas, 30, 12, [1.0, 2.0]), 0) == pytest.approx(place_at(later, 12)), "a piece that starts part way in carries on from there"
    assert eased_place(canvas, 30, 0, [5.0, 2.0]) is None, "past the end of its push a segment does not move"
    moving = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.0, zoom_to=1.017, ease=[0.0, 2.0]), CANVAS, None)
    assert "pow(" in moving and "pow(" not in segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.0, zoom_to=1.017), CANVAS, None)


def test_ffmpeg_takes_an_eased_push_about_a_point(tmp_path):
    from altocue import ffmpeg

    canvas = Canvas(64, 96, 10, 30, "ultrafast")
    move = push_filter(canvas, 10, 1.0, 1.05, origin=(0.3, 0.35), ease=[0.0, 1.0])
    out = tmp_path / "push.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc=s=64x96:r=10:d=1", "-vf", move, "-frames:v", "10", "-c:v", "libx264", "-preset", "ultrafast", str(out)])
    assert probe(out, "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames").strip().strip(",") == "10"


def test_segment_filter_uses_even_dimensions_and_ends_tagged():
    chain = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2), CANVAS, None)
    crop = chain.split("crop=")[1].split(":")
    assert int(crop[0]) % 2 == 0 and int(crop[1]) % 2 == 0
    assert "scale=1080:1920" in chain and "fps=30" in chain


def test_push_uses_sub_pixel_filter_only_when_moving():
    still = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.1, zoom_to=1.1), CANVAS, None)
    moving = segment_filter(clip(), Segment(id="s", clip="a", start=0, end=2, zoom_from=1.0, zoom_to=1.06), CANVAS, None)
    assert "perspective" not in still and "perspective" in moving
    assert "eval=frame" in push_filter(CANVAS, 60, 1.0, 1.06)


def test_an_odd_source_never_asks_for_more_than_it_has():
    clip = Clip(id="c", path="c.mp4", duration=2.0, fps=30.0, width=1079, height=1919)
    chain = segment_filter(clip, Segment(id="s", clip="c", start=0, end=1), Canvas(1080, 1920, 30, 16, "medium"), None)
    assert "crop=1078:" in chain


def probe(path, *fields: str) -> str:
    import subprocess

    return subprocess.run(["ffprobe", "-v", "error", *fields, "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout


def test_parts_join_with_no_gap_when_the_source_has_a_chapter_track(tmp_path):
    """Zoom files carry a chapter track that starts before the picture. Each part must hold picture only, or every join adds a frame."""
    from altocue import ffmpeg, plate
    from altocue.project import Project

    chapters = tmp_path / "chapters.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=6000\ntitle=Recording\n")
    source = tmp_path / "call.mov"
    ffmpeg.run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x180:r=25:d=6",
            "-i",
            str(chapters),
            "-map",
            "0:v",
            "-map_chapters",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(source),
        ]
    )
    assert len(probe(source, "-show_entries", "stream=codec_type").split()) > 1, "the test source should carry a chapter track, as Zoom's do"
    project = Project.create(tmp_path / "video")
    source_clip = Clip(id="a", path=str(source), duration=6.0, fps=25, width=320, height=180, has_audio=False)
    folder = tmp_path / "parts"
    folder.mkdir()
    canvas = Canvas(180, 320, 25, 30, "veryfast")  # B-frames, as the default preset makes: they start the picture a frame in
    parts = [
        plate.build_segment(project, source_clip, Segment(id=f"s{n}", clip="a", start=start, end=start + 1.0), canvas, folder) for n, start in enumerate((0.5, 2.0, 4.0))
    ]
    for part in parts:
        assert probe(part, "-show_entries", "stream=codec_type").split() == ["video"]
    joined = tmp_path / "joined.mp4"
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", str(ffmpeg.concat_list(tmp_path / "list.ffconcat", parts)), "-c", "copy", str(joined)])
    times = [float(line.strip(",")) for line in probe(joined, "-select_streams", "v:0", "-show_entries", "frame=pts_time").split()]
    assert len(times) == 75
    assert max(b - a for a, b in zip(times, times[1:], strict=False)) < 0.05
