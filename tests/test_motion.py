from __future__ import annotations

import json

import pytest

from altocue import brand as brands
from altocue.errors import AltocueError
from altocue.motion.basic import BasicRenderer
from altocue.motion.html import compose, geometry
from altocue.project import Project
from altocue.timeline import resolve_items, resolve_time

WORDS = [{"text": "Hello,", "start": 1.0, "end": 1.4}, {"text": "runway", "start": 2.0, "end": 2.4}, {"text": "runway", "start": 5.0, "end": 5.4}]


@pytest.fixture
def brand():
    return brands.Brand(json.loads(brands.data_path("brands", "default.json").read_text()), brands.data_path("brands"))


def timeline(**over):
    base = {"width": 1080, "height": 1920, "fps": 30, "duration": 8.0, "tail": 0, "total": 8.0, "captions": [], "items": []}
    return {**base, **over}


def page():
    return {"start": 0.5, "end": 1.5, "segment": "a", "words": [{"text": "two", "start": 0.5, "end": 0.9}, {"text": "words", "start": 0.9, "end": 1.4}]}


def test_time_can_follow_a_word():
    assert resolve_time({"word": "hello"}, WORDS) == 1.0
    assert resolve_time({"word": "runway", "occurrence": 2, "offset": 0.1}, WORDS) == pytest.approx(5.1)


def test_a_cut_word_is_an_error():
    with pytest.raises(AltocueError):
        resolve_time({"word": "gone"}, WORDS)


def test_unknown_graphic_is_an_error():
    with pytest.raises(AltocueError):
        resolve_items([{"type": "sparkle"}], WORDS, 8.0)


def test_end_card_extends_the_film():
    items, tail = resolve_items([{"type": "endcard", "headline": "Bye", "duration": 2.5}], WORDS, 8.0)
    assert tail == 2.5 and items[0]["start"] == 8.0 and items[0]["end"] == 10.5


def test_compose_highlights_one_word_per_clip(brand):
    html = compose(timeline(captions=[page()]), brand)
    assert html.count('class="on"') == 2
    assert 'data-composition-id="root"' in html and "gsap" not in html.lower()


def test_compose_escapes_copy(brand):
    html = compose(timeline(items=[{"type": "chip", "text": "<b>x</b>", "start": 0, "end": 1}]), brand)
    assert "<b>x</b>" not in html


@pytest.mark.parametrize("kind", ["hook", "chip", "lower_third", "stat", "quote", "steps", "title", "endcard"])
def test_every_graphic_compiles_and_draws(brand, kind, tmp_path):
    copy = {"text": "Hello *there*", "title": "Name", "value": "41%", "label": "of it", "headline": "Bye"}
    item = {"type": kind, "start": 0.0, "end": 2.0, **copy, "items": [{"text": "One", "at": 0.5}]}
    tl = timeline(items=[item], captions=[page()])
    assert 'class="clip' in compose(tl, brand)
    layer = BasicRenderer().render(tl, brand, tmp_path)
    assert layer.concat and layer.path.exists()
    assert "duration" in layer.path.read_text()


def test_tall_video_keeps_clear_of_app_buttons():
    tall = geometry(1080, 1920)
    assert tall["rail"] > 0 and tall["cap_y"] < 1920 - 480


def branded(tmp_path, **over):
    project = Project.create(tmp_path / "video")
    (project.root / "brand.json").write_text(json.dumps(over))
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace('name = "default"', 'name = "brand.json"'))
    return brands.load(Project.open(project.root))


def test_a_brand_colour_cannot_carry_markup(tmp_path):
    with pytest.raises(AltocueError, match="colour"):
        branded(tmp_path, colors={"ink": "</style><script>fetch('/secret')</script>"})


def test_a_brand_logo_cannot_reach_outside_its_folder(tmp_path):
    with pytest.raises(AltocueError, match="logo"):
        _ = branded(tmp_path, logo="../../.ssh/id_rsa").logo


def test_a_brand_logo_must_be_an_image(tmp_path):
    with pytest.raises(AltocueError, match="logo"):
        _ = branded(tmp_path, logo="notes.txt").logo


def test_a_brand_font_cannot_reach_outside_its_folder(tmp_path):
    with pytest.raises(AltocueError, match="font"):
        _ = branded(tmp_path, font={"file": "../../fonts/secret.ttf"}).font_file


def test_a_graphic_position_must_be_a_number(brand):
    with pytest.raises(ValueError):
        compose(timeline(items=[{"type": "chip", "text": "x", "start": 0, "end": 1, "y": '0"><script>'}]), brand)


def test_the_graphics_page_can_only_load_from_itself(brand):
    assert "Content-Security-Policy" in compose(timeline(), brand)


def test_a_word_time_handles_curly_quotes_and_bad_input():
    words = [{"text": "don't", "start": 1.0}, {"text": "don't", "start": 2.0}]
    assert resolve_time({"word": "don’t", "occurrence": 2}, words) == 2.0
    with pytest.raises(AltocueError):
        resolve_time({"word": "don't", "occurrence": 0}, words)
    with pytest.raises(AltocueError):
        resolve_time("soon", words)
