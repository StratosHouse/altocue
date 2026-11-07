"""Point callouts: graphics that unfold from where the presenter points."""

from __future__ import annotations

import json
import re

import pytest

from altocue import brand as brands
from altocue.errors import AltocueError
from altocue.motion import formats
from altocue.motion.formats import point
from altocue.motion.html import compose
from altocue.timeline import resolve_items

WORDS = [{"text": "protect", "start": 1.0, "end": 1.3}, {"text": "sleep", "start": 1.4, "end": 1.8}, {"text": "schedule", "start": 3.1, "end": 3.6}]


def made(**over):
    raw = {"type": "point_callout", "start": 0.5, "duration": 5.0, "title": "Protect your *sleep*", **over}
    items, _ = resolve_items([raw], WORDS, 30.0)
    return items[0]


def test_both_formats_load_and_paint_over_the_presenter():
    for name in ("point_callout", "point_stat"):
        fmt = formats.find(name)
        assert fmt and fmt.area == "full" and fmt.needs_alpha and not fmt.leaves


def test_a_pointed_card_starts_at_the_fingertip_and_draws_to_the_card():
    html = point.POINT.draw(made(ax=34, ay=45, cx=50, cy=29))
    assert "pc-pointed" in html and "pc-spark" in html and "pc-link" in html
    assert "--ax:34.000;--ay:45.000;--cx:50.000;--cy:29.000" in html
    assert 'd="M34.00 45.00C34.00 37.00 34.00 37.00 34.00 29.00"' in html  # straight up, to the nearest point of the card
    far = point.POINT.draw(made(ax=4, ay=45, cx=50, cy=29, w=60))
    assert 'd="M4.00 45.00C4.00 37.00 27.00 37.00 27.00 29.00"' in far  # a fingertip beyond the card meets its corner


def test_without_a_fingertip_there_is_no_spark_or_connector():
    html = point.POINT.draw(made(cx=50, cy=29))
    assert "pc-open" in html and "pc-spark" not in html and "pc-link" not in html


def test_cover_mode_paints_a_backdrop_and_drops_the_pointer():
    html = point.POINT.draw(made(stage="cover", ax=30, ay=40))
    assert "pc-cover" in html and "pc-backdrop" in html and "pc-spark" not in html


def test_positions_stay_inside_the_frame():
    html = point.POINT.draw({**made(cy=29), "ax": -20, "ay": 140, "cx": "nonsense"})
    assert "--ax:0.000;--ay:100.000;--cx:50.000" in html


def test_rows_land_on_their_spoken_words():
    item = made(items=[{"text": "Keep the schedule", "icon": "clock", "at": {"word": "schedule"}}])
    assert item["items"][0]["at"] == pytest.approx(3.1)
    html = point.POINT.draw(item)
    assert "--d:2.6000s" in html and "pc-mini-glyph" in html


def test_an_unknown_icon_is_refused_and_every_icon_is_drawn():
    with pytest.raises(AltocueError):
        made(icon="not-an-icon")
    for name in point.ICONS:
        svg = point.icon(name)
        assert svg.startswith("<svg") and 'viewBox="0 0 24 24"' in svg and any(tag in svg for tag in ("<path", "<circle", "<rect"))


def test_a_walk_gives_the_badge_legs_and_walks_it_off_before_the_end():
    html = point.POINT.draw(made(act="walk", icon="bottle"))
    assert "pc-walk" in html and html.count("pc-leg") >= 2
    assert re.search(r"pc-walk-off [\d.]+s .*calc\(var\(--dur\) - 2\.15s\)", point.POINT.css)


def test_a_stat_draws_its_value_on_a_dial():
    item, _ = resolve_items([{"type": "point_stat", "start": 0, "duration": 4, "title": "of *sleep*", "value": "3", "unit": "hours", "fill": 0.25}], WORDS, 30.0)
    html = point.STAT.draw(item[0])
    assert "pc-dial" in html and "--fill:0.2500" in html and ">3</b>" in html and ">hours</i>" in html


def test_alert_tone_is_its_own_colour():
    assert "pc-alert" in point.POINT.draw(made(tone="alert"))
    assert ".f-point_callout .pc-alert{--pc-tone:" in point.POINT.css


def test_copy_that_cannot_fit_is_refused():
    with pytest.raises(AltocueError):
        made(title="A title far too long to sit on one card")
    with pytest.raises(AltocueError):
        made(items=[{"text": "one"}, {"text": "two"}, {"text": "three"}, {"text": "four"}, {"text": "five"}])


def test_point_graphics_sit_beneath_the_captions():
    brand = brands.Brand(json.loads(brands.data_path("brands", "default.json").read_text()), brands.data_path("brands"))
    caption = {"start": 0.5, "end": 2.0, "words": [{"text": "protect", "start": 0.5, "end": 1.0}, {"text": "sleep", "start": 1.0, "end": 2.0}]}
    page = compose({"width": 1080, "height": 1920, "fps": 30, "duration": 6.0, "tail": 0, "total": 6.0, "captions": [caption],
                    "items": [made(stage="cover")]}, brand)
    body = page[page.index("<body>"):]
    assert body.index("f-point_callout") < body.index('class="clip cap')
