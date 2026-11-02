"""Every format is held to the same rules, so adding one needs no new test to be safe."""

from __future__ import annotations

import json
import re

import pytest

from altocue import brand as brands
from altocue import render
from altocue.errors import AltocueError
from altocue.motion import compositions, formats
from altocue.motion.basic import BasicRenderer, card_frame
from altocue.motion.html import compose
from altocue.motion.placement import estimated_height
from altocue.project import Project
from altocue.qa import rules
from altocue.timeline import resolve_items

ALL = list(formats.load().values())
WORDS = [{"text": "hello", "start": 1.0, "end": 1.4}, {"text": "runway", "start": 2.0, "end": 2.4}]
CSS_WORDS = set("infinite both forwards backwards none linear ease ease-in ease-out ease-in-out var calc steps cubic-bezier alternate reverse".split())
CSS_WORDS |= set("alternate-reverse running paused normal step-end step-start jump-end jump-none jump-start jump-both start end min max clamp s ms".split())
CORE_KEYFRAMES = {"rise", "leave", "capin", "growy", "endin", "lineup", "stepon", "fmtout"}
SVG_TAGS = "svg|path|circle|rect|line|polyline|polygon|ellipse|g|text|textPath|use"
UNSEEKED = re.compile(rf"(?:::(?:before|after)|(?:^|[\s>])(?:{SVG_TAGS})(?:[.:\[][^{{\s]*)?)\s*\{{[^}}]*animation", re.M)
CLOCKS = re.compile(r"Date\.now|Math\.random|requestAnimationFrame|setTimeout|setInterval|performance\.now")


@pytest.fixture
def brand():
    return brands.Brand(json.loads(brands.data_path("brands", "default.json").read_text()), brands.data_path("brands"))


def placed(fmt: formats.Format) -> dict:
    items, _ = resolve_items([{**fmt.example, "type": fmt.id, "start": 0.5, "duration": fmt.seconds}], WORDS, 30.0)
    return items[0]


def page(fmt: formats.Format, brand) -> str:
    item = placed(fmt)
    return compose({"width": 1080, "height": 1920, "fps": 30, "duration": 30.0, "tail": 0, "total": 30.0, "captions": [], "items": [item]}, brand)


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_a_format_is_described_properly(fmt):
    assert re.fullmatch(r"[a-z][a-z0-9_]*", fmt.id)
    assert fmt.group in formats.GROUPS and fmt.area in formats.AREAS and set(fmt.jobs) <= set(formats.JOBS) and fmt.jobs
    assert 12 <= len(fmt.summary) <= 110 and fmt.summary.endswith(".")
    assert "\u2014" not in fmt.summary and fmt.shortest <= fmt.seconds <= fmt.longest


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_a_format_is_time_pure_and_self_contained(fmt, brand):
    html = page(fmt, brand)
    assert f"f-{fmt.id}" in html and f".f-{fmt.id}" in fmt.css
    body = html[html.index("<body>") :]
    assert "<script" not in html and "gsap" not in html.lower() and not CLOCKS.search(html)
    assert not re.search(r"""(?:src|href)=["']?https?:""", body) and "url(http" not in html
    assert "%%" not in fmt.css


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_animations_sit_on_html_elements(fmt):
    """HyperFrames 0.8.222 seeks SVG and pseudo-element animations to film time, so they look finished in any clip after 0."""
    assert not UNSEEKED.search(fmt.css), "Animate an HTML wrapper or a real span instead"


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_keyframes_belong_to_the_format(fmt):
    """Two formats in one film must not overwrite each other's keyframes, or lean on one that is not loaded."""
    css = formats.scoped_css(fmt)
    own = set(formats.KEYFRAMES.findall(css))
    assert all(name.startswith(f"{fmt.id}--") for name in own)
    used = set()
    for block in formats.ANIMATED.findall(css):
        used |= {word for word in formats.WORD.findall(block) if word.startswith(f"{fmt.id}--")}
    assert used <= own, "This format animates with keyframes it never defines"
    undefined = {word for block in formats.ANIMATED.findall(css) if block.startswith("animation") for word in formats.WORD.findall(block.split(":", 1)[1])}
    assert not undefined - own - CSS_WORDS - CORE_KEYFRAMES, "Keyframes from another format would be missing when that format is not in the film"


CORE_CLASSES = {"cap", "leave", "hook", "chip", "lt", "stat", "quote", "steps", "title", "end"}


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_format_markup_avoids_the_core_class_names(fmt):
    """The core stylesheet is not scoped, so a `.end` inside a format picks up the end card's look."""
    classes = {name for group in re.findall(r'class="([^"]*)"', fmt.draw(placed(fmt))) for name in group.split()}
    assert not classes & CORE_CLASSES


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_copy_goes_through_escaping(fmt, brand):
    text_slots = [slot for slot in fmt.slots if slot.kind == "text"]
    if not text_slots:
        pytest.skip("no free text")
    item = {**placed(fmt), text_slots[0].name: "<b>x</b>"}
    assert "<b>x</b>" not in fmt.draw(item)


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_every_string_reaches_the_qa_rules(fmt):
    item = placed(fmt)
    assert formats.strings(item), "QA would not see this format's copy"
    seen = rules.on_screen({"items": [item]})
    assert all(row["layer"] == fmt.id for row in seen)


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_the_basic_renderer_still_draws_something(fmt, brand):
    canvas = card_frame((1080, 1920), placed(fmt), brand)
    assert canvas.getbbox() is not None


@pytest.mark.parametrize("fmt", ALL, ids=lambda f: f.id)
def test_placement_and_sound_know_the_format(fmt, brand):
    assert estimated_height(placed(fmt), 1080, 1920, brand) > 0
    if fmt.sound:
        assert brands.data_path("sfx", f"{fmt.sound}.ogg").exists()
        at = 0.5 + fmt.seconds / 2 if fmt.group == "wipe" else 0.5
        assert {"name": fmt.sound, "at": at} in render.sfx_cues({"items": [placed(fmt)]})


def test_ids_never_shadow_core_types():
    assert not set(formats.load()) & formats.CORE_TYPES


def test_copy_too_long_for_the_format_is_an_error_not_an_overflow():
    with pytest.raises(AltocueError, match="only 48 fit"):
        resolve_items([{"type": "word_slam", "text": "x" * 49}], WORDS, 8.0)


def test_a_missing_slot_says_which_one():
    with pytest.raises(AltocueError, match='needs "text"'):
        resolve_items([{"type": "word_slam"}], WORDS, 8.0)


def test_only_formats_in_the_film_add_css(brand):
    html = compose({"width": 1080, "height": 1920, "fps": 30, "duration": 8.0, "tail": 0, "total": 8.0, "captions": [], "items": []}, brand)
    assert ".f-word_slam" not in html and "fmt-panel" not in html


COMPOSITION = '<html><body><template><div id="root" data-composition-id="demo-card" data-duration="3"><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script></div></template></body></html>'


@pytest.fixture
def project(tmp_path):
    made = Project.create(tmp_path / "film")
    (made.root / "motion").mkdir()
    (made.root / "motion" / "mine.html").write_text(COMPOSITION)
    return made


def test_a_composition_file_is_found_and_read(project):
    item = compositions.resolve(project, {"type": "composition", "file": "motion/mine.html", "start": 0, "end": 2})
    assert item["own_id"] == "demo-card"


def test_a_missing_composition_says_how_to_fetch_it(project):
    with pytest.raises(AltocueError, match="not in this project") as caught:
        compositions.resolve(project, {"type": "composition", "name": "headline-slam"})
    assert "altocue library add headline-slam" in caught.value.fix


def test_a_composition_cannot_reach_outside_the_project(project):
    with pytest.raises(AltocueError, match="not in this project"):
        compositions.resolve(project, {"type": "composition", "file": "../../secret.html"})


def test_a_plain_page_is_not_a_composition(project):
    (project.root / "motion" / "page.html").write_text("<html><body>hi</body></html>")
    with pytest.raises(AltocueError, match="not a HyperFrames composition"):
        compositions.resolve(project, {"type": "composition", "file": "motion/page.html"})


def test_variables_are_escaped_into_the_mount(project):
    item = compositions.resolve(project, {"type": "composition", "file": "motion/mine.html", "vars": {"text": '"><b>'}, "start": 0, "end": 2})
    assert '"><b>' not in compositions.mount(item, 'data-start="0"')


def test_gsap_comes_from_the_renderer_so_a_render_needs_no_network(project, tmp_path):
    vendor = tmp_path / "renderer/node_modules/hyperframes/dist/vendor/npm/gsap@3.15.0/dist"
    vendor.mkdir(parents=True)
    (vendor / "gsap.min.js").write_text("// gsap")
    item = compositions.resolve(project, {"type": "composition", "file": "motion/mine.html", "start": 0, "end": 2})
    stage = tmp_path / "stage"
    stage.mkdir()
    compositions.install([item], stage, tmp_path / "renderer")
    assert "jsdelivr" not in (stage / "compositions/demo-card.html").read_text() and (stage / "compositions/gsap.min.js").exists()


def test_the_basic_renderer_refuses_compositions_plainly(brand, tmp_path):
    item = {"type": "composition", "start": 0, "end": 1}
    timeline = {"width": 320, "height": 568, "fps": 30, "duration": 2.0, "tail": 0, "total": 2.0, "captions": [], "items": [item]}
    with pytest.raises(AltocueError, match="full renderer"):
        BasicRenderer().render(timeline, brand, tmp_path, draft=True)
