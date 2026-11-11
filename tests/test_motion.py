from __future__ import annotations

import json
import re

import pytest

from altocue import brand as brands
from altocue import caption
from altocue.errors import AltocueError
from altocue.motion import basic, breaks
from altocue.motion.basic import BasicRenderer
from altocue.motion.html import caption_html, caption_lines, compose, geometry
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


def test_adjacent_tagged_words_share_one_label_per_caption_frame():
    from altocue.motion.html import caption_html

    tagged = page()
    for word in tagged["words"]:
        word["tag"] = "stutter"
    frames = caption_html(tagged, "boxed", 1).splitlines()
    assert len(frames) == 2
    assert all(frame.count("<i>stutter</i>") == 1 for frame in frames)
    assert frames[-1].count('class="tg') == 2


def test_time_can_follow_a_word():
    assert resolve_time({"word": "hello"}, WORDS) == 1.0
    assert resolve_time({"word": "runway", "occurrence": 2, "offset": 0.1}, WORDS) == pytest.approx(5.1)


def test_a_cut_word_is_an_error():
    with pytest.raises(AltocueError):
        resolve_time({"word": "gone"}, WORDS)


def test_unknown_graphic_is_an_error():
    with pytest.raises(AltocueError):
        resolve_items([{"type": "sparkle"}], WORDS, 8.0)


def test_the_page_says_css_animates_it(brand):
    """Without this HyperFrames lint reports a missing timeline, and a producer that trusts it waits for one."""
    html = compose(timeline(items=resolve_items([{"type": "chip", "text": "One", "start": 1.0}], WORDS, 8.0)[0]), brand)
    assert re.search(r'<div id="root" data-composition-id="root" data-no-timeline[ >]', html)


def test_end_card_extends_the_film():
    items, tail = resolve_items([{"type": "endcard", "headline": "Bye", "duration": 2.5}], WORDS, 8.0)
    assert tail == 2.5 and items[0]["start"] == 8.0 and items[0]["end"] == 10.5


def test_compose_highlights_one_word_per_clip(brand):
    html = compose(timeline(captions=[page()]), brand)
    assert html.count('class="on"') == 2
    assert 'data-composition-id="root"' in html and "gsap" not in html.lower()


def test_a_word_that_pops_keeps_the_space_beside_it(brand):
    """The spoken word grows by a transform, which never moves its neighbours, so a long word swelled into the gap.

    The old take B caption read "ACOMPLETELY" while "completely" was lit. Every word now holds the room its popped
    size needs: an unseen copy at that size sets its width, and only the visible word inside it grows.
    """
    said = [("A", 4.3, 4.45), ("completely", 4.45, 4.95), ("separate", 4.95, 5.4)]
    words = [{"text": text, "start": start, "end": end} for text, start, end in said]
    html = compose(timeline(captions=[{"start": 4.3, "end": 5.6, "segment": "a", "words": words}]), brand)
    shown = re.findall(r'<b(?: class="on")? data-room="([^"]*)"><span>([^<]*)</span></b>', html)
    assert len(shown) == 9 and all(room == text for room, text in shown)
    assert html.count('<b class="on" data-room="completely">') == 1
    pop = re.search(r"\.cap\{[^}]*--pop:([\d.]+)", html)
    assert pop and float(pop.group(1)) > 1
    assert re.search(r"\.cap b::after\{content:attr\(data-room\);[^}]*font-size:calc\(var\(--pop\) \* 1em\)", html)
    assert re.search(r"\.cap b\.on>span\{[^}]*transform:scale\(var\(--pop\)\)", html)
    assert not re.search(r"\.cap b\.on\{[^}]*transform", html)  # the word's own box never grows past its room


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


# Two lines, chosen before the page is drawn


def said(text):
    words = text.split()
    return {"start": 0.0, "end": 2.0, "segment": "a", "words": [{"text": word, "start": n * 0.4, "end": n * 0.4 + 0.3} for n, word in enumerate(words)]}


def html_lines(drawn):
    """The words on each line of every state of a page, as the browser is told to set them."""
    states = []
    for state in drawn.splitlines():
        rows = re.findall(r'<span class="ln">(.*?)</span>(?=<span class="ln">|</span>|</div>)', state)
        states.append([re.findall(r"<span>([^<]*)</span>", row) for row in rows])
    return states


def basic_lines(monkeypatch, page, brand, size=(1080, 1920)):
    """The words on each line as the fallback renderer really draws them, read off its own calls."""
    from PIL import ImageDraw

    seen = []
    plain = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        seen.append((round(xy[1]), text))
        return plain(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    basic.caption_frame(size, page, 0, brand)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", plain)
    return [[text for y, text in seen if y == row] for row in sorted({y for y, _ in seen})]


def greedy(texts, brand, size=(1080, 1920)):
    """What a browser's own wrapping, and the old fallback renderer, made of the same words."""
    from PIL import Image, ImageDraw

    geo = geometry(*size)
    px = int(brand.caption["size"])
    rows = basic._wrap(ImageDraw.Draw(Image.new("RGBA", (8, 8))), texts, basic._font(brand, px, 700), size[0] - 2 * geo["gutter"] - geo["rail"], px * 0.26)
    return [len(row) for row in rows]


ORPHANED = "we absolutely love captions"


def test_the_words_chosen_for_this_test_used_to_leave_one_alone(brand):
    assert greedy(ORPHANED.split(), brand) == [3, 1]


def test_lines_are_the_fewest_that_fit_and_then_as_even_as_they_can_be():
    assert breaks.split([100, 100, 100], 10, 400) == [3]
    assert breaks.split([100, 100, 100, 100], 10, 250) == [2, 2]
    assert breaks.split([100, 100, 100], 10, 250) == [1, 2]  # never [2, 1]: the last line is not left with one word
    assert breaks.split([60, 60, 60, 60, 60, 60], 10, 150) == [2, 2, 2]
    assert breaks.split([50, 50, 300], 10, 320) == [2, 1]  # a long last word has to stand alone, and does
    assert breaks.split([], 10, 100) == [] and breaks.split([500], 10, 100) == [1]


def test_a_line_does_not_end_on_a_word_that_leans_on_the_next():
    widths = [100, 40, 60, 100]  # think of the answer
    assert breaks.split(widths, 10, 240) == [2, 2]
    assert breaks.split(widths, 10, 240, caption.joins("think of the answer".split())) == [1, 3]


def test_a_page_only_keeps_lines_that_count_every_word():
    page = said("one two three")
    assert breaks.given({**page, "lines": [1, 2]}) == [1, 2]
    for wrong in ([1, 1], [3, 0], [2.0, 1], "12", None, []):
        assert breaks.given({**page, "lines": wrong}) is None


@pytest.mark.parametrize("style", ["clean", "bold", "boxed", "rail"])
def test_a_two_line_caption_is_balanced_in_the_browser(brand, style):
    brand.data["caption"] = {**brand.caption, "style": style, "size": 70 if style != "rail" else 110}
    html = compose(timeline(captions=[said(ORPHANED)]), brand)
    states = html_lines("\n".join(line for line in html.splitlines() if 'class="clip cap' in line))
    assert len(states) == 4
    for state in states:
        assert [len(row) for row in state] == [2, 2], state
        assert [word.lower() for row in state for word in row] == ORPHANED.split()
    assert ("plate rows" in html) == (style == "boxed")
    assert re.search(r"\.cap \.ln\{[^}]*flex:0 0 100%[^}]*white-space:nowrap", html)


def test_a_two_line_caption_is_balanced_in_the_fallback_renderer(brand, monkeypatch):
    assert [len(row) for row in basic_lines(monkeypatch, said(ORPHANED), brand)] == [2, 2]


def test_both_renderers_set_a_built_page_on_the_same_lines(brand, monkeypatch):
    """`altocue caption` writes the lines on the page, so neither renderer measures for itself."""
    page = {**said("think of the answer today"), "lines": [1, 4]}
    browser = html_lines(caption_html(page, "clean", 1))
    assert all([len(row) for row in state] == [1, 4] for state in browser)
    assert [len(row) for row in basic_lines(monkeypatch, page, brand)] == [1, 4]
    assert basic_lines(monkeypatch, page, brand) == browser[0]


def test_a_page_that_fits_one_line_stays_on_one(brand, monkeypatch):
    short = said("two words")
    assert caption_lines(["two", "words"], brand, 1080, 1920) == [2]
    assert [len(row) for row in html_lines(compose(timeline(captions=[short]), brand).split('class="clip cap', 1)[1].splitlines()[0])[0]] == [2]
    assert [len(row) for row in basic_lines(monkeypatch, short, brand)] == [2]


@pytest.mark.parametrize("style", ["clean", "boxed"])
def test_a_name_goes_above_the_caption_line_so_the_words_keep_their_place(brand, style):
    """In a browser the name pushed both lines down by its own height, out of the foot of a safe zone."""
    page = said(ORPHANED)
    named = caption_html({**page, "name": "Ada"}, style, 1)
    assert all(f'class="clip cap {style} home named' in line for line in named.splitlines())
    assert all(f'class="clip cap {style} home' in line and "named" not in line for line in caption_html(page, style, 1).splitlines())
    assert "home" not in caption_html({**page, "name": "Ada"}, style, 1, "top:40px")  # a film sets its own captions where it wants them
    css = compose(timeline(captions=[page]), brand)
    name_height = float(re.search(r"\.cap \.who\{[^}]*font-size:([\d.]+)em", css).group(1)) * float(re.search(r"\.cap \.who\{[^}]*line-height:([\d.]+)", css).group(1))
    row_gap = float(re.search(r"\.cap\{[^}]*[;{]gap:([\d.]+)em", css).group(1))
    plate_top = float(re.search(r"\.cap\.boxed \.plate\{[^}]*padding:([\d.]+)em", css).group(1))
    assert float(re.search(r"\.cap\.named\{margin-top:-([\d.]+)em\}", css).group(1)) == pytest.approx(name_height + row_gap)
    assert float(re.search(r"\.cap\.boxed\.home\{margin-top:-([\d.]+)em\}", css).group(1)) == pytest.approx(plate_top)
    assert float(re.search(r"\.cap\.boxed\.named\{margin-top:-([\d.]+)em\}", css).group(1)) == pytest.approx(name_height + row_gap + plate_top)


def test_built_captions_never_leave_one_word_alone_on_the_second_line(brand):
    """Every page of a few real sentences, on the narrow room a vertical safe zone leaves."""
    from altocue import platforms

    zone = platforms.PLATFORMS["vertical"].zone
    lines = (
        "we absolutely love captions that anybody can read without stopping the video",
        "the international committee of experts met again in Geneva yesterday afternoon",
        "nobody remembers complicated explanations after watching something quickly",
    )
    two_line = 0
    for line in lines:
        words = [{"text": text, "start": n * 0.4, "end": n * 0.4 + 0.3, "segment": "a"} for n, text in enumerate(line.split())]
        for page in caption.build_pages(words, max_words=3, max_chars=22):
            texts = [word["text"] for word in page["words"]]
            counts = caption_lines(texts, brand, 1080, 1920, zone, caption.joins(texts))
            assert len(counts) <= 2, texts
            two_line += len(counts) == 2
            assert not (len(counts) == 2 and len(texts) >= 3 and counts[-1] == 1), texts
    assert two_line >= 2  # the check means nothing unless some pages really take two lines


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
