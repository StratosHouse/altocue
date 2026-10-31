from __future__ import annotations

from altocue.models import Clip, Segment
from altocue.plate import Canvas, fit_crop, push_filter, segment_filter, zoomed

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
