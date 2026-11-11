"""Platform targets: every number has a source, and layout and the gate both follow the zone."""

from __future__ import annotations

import pytest

from altocue import brand as brands
from altocue import caption, master, platforms, timeline
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import Clip, Edl, Segment, Word
from altocue.motion import placement
from altocue.motion.basic import caption_frame
from altocue.motion.html import compose, layout
from altocue.project import SIZES, Project
from altocue.qa import gates


@pytest.fixture
def brand(tmp_path):
    return brands.load(Project.create(tmp_path / "video"))


def test_every_platform_is_a_real_size_with_a_sourced_zone_and_length():
    assert set(platforms.PLATFORMS) == {"reels", "tiktok", "shorts", "youtube", "linkedin", "square", "vertical"}
    for platform in platforms.PLATFORMS.values():
        assert platform.aspect in SIZES
        assert platform.loudness in master.TARGETS
        zone = platform.zone
        assert 0 <= zone["left"] < zone["right"] <= 1 and 0 <= zone["top"] < zone["bottom"] <= 1
        assert "estimate" in platform.zone_source.lower()
        assert platform.length_source


def test_the_published_zones_are_the_published_numbers():
    reels = platforms.PLATFORMS["reels"].zone
    assert reels == {"left": 0.06, "right": 0.94, "top": 0.14, "bottom": 0.65}
    shorts = platforms.PLATFORMS["shorts"].zone
    assert (round(shorts["top"] * 1920), round((1 - shorts["bottom"]) * 1920)) == (288, 672)
    assert (round(shorts["left"] * 1080), round((1 - shorts["right"]) * 1080)) == (48, 192)


def test_a_figure_with_no_primary_source_says_so():
    assert "no primary source" in platforms.PLATFORMS["tiktok"].zone_source.lower()
    assert "no primary source" in platforms.PLATFORMS["linkedin"].zone_source.lower()


def test_for_splits_a_list_and_refuses_an_unknown_name():
    assert [p.name for p in platforms.parse("reels, square")] == ["reels", "square"]
    with pytest.raises(AltocueError, match="myspace"):
        platforms.parse("reels,myspace")


def test_the_vertical_zone_is_worked_out_from_the_table():
    """Whatever the three apps leave alone between them. Nothing here is typed in twice."""
    apps = [platforms.PLATFORMS[name].zone for name in platforms.VERTICAL_APPS]
    zone = platforms.PLATFORMS["vertical"].zone
    assert zone == platforms.overlap(platforms.VERTICAL_APPS)
    assert zone == {
        "left": max(app["left"] for app in apps),
        "right": min(app["right"] for app in apps),
        "top": max(app["top"] for app in apps),
        "bottom": min(app["bottom"] for app in apps),
    }
    assert {side: round(value, 2) for side, value in zone.items()} == {"left": 0.11, "right": 0.82, "top": 0.15, "bottom": 0.65}
    for app in apps:
        assert app["left"] <= zone["left"] < zone["right"] <= app["right"] and app["top"] <= zone["top"] < zone["bottom"] <= app["bottom"]
    assert set(platforms.VERTICAL_APPS) == {name for name, platform in platforms.PLATFORMS.items() if platform.aspect == "9:16" and name != "vertical"}
    assert platforms.PLATFORMS["vertical"].longest == min(platforms.PLATFORMS[name].longest for name in platforms.VERTICAL_APPS)


def test_the_vertical_zone_follows_a_change_to_the_table(monkeypatch):
    lower = platforms.Platform("reels", "9:16", {"left": 0.06, "right": 0.94, "top": 0.14, "bottom": 0.6}, "Estimate.", 180.0, None, "A test zone.")
    monkeypatch.setitem(platforms.PLATFORMS, "reels", lower)
    assert platforms.overlap(platforms.VERTICAL_APPS)["bottom"] == 0.6


def test_a_project_names_its_platform(tmp_path):
    project = Project.create(tmp_path / "video", aspect="16:9")
    assert platforms.zone_of(project) is None
    project.config["output"]["platform"] = "reels"
    assert platforms.zone_of(project) == platforms.PLATFORMS["reels"].zone
    project.config["output"]["platform"] = "myspace"
    with pytest.raises(AltocueError):
        platforms.zone_of(project)


def test_a_tall_project_with_no_platform_gets_the_vertical_zone(tmp_path):
    """The old fallback let captions sit in the band where every vertical app draws its own caption and buttons."""
    vertical = platforms.PLATFORMS["vertical"].zone
    tall = Project.create(tmp_path / "tall")
    assert "platform" not in tall.config["output"]
    assert platforms.zone_of(tall) == vertical and platforms.built_in(tall)
    assert gates.zone_for(FakeContext(tall)) == vertical
    odd = Project.create(tmp_path / "odd", aspect="2:3")
    assert platforms.zone_of(odd) == vertical


def test_vertical_can_be_asked_for_by_name(tmp_path):
    project = Project.create(tmp_path / "video", platform="vertical")
    assert project.config["output"]["platform"] == "vertical"
    assert platforms.zone_of(project) == platforms.PLATFORMS["vertical"].zone and not platforms.built_in(project)
    assert [p.name for p in platforms.parse("vertical")] == ["vertical"]
    assert platforms.note(project) == ""


@pytest.mark.parametrize("aspect", ["4:5", "1:1", "16:9"])
def test_a_canvas_no_taller_than_four_by_five_keeps_its_old_zone(tmp_path, aspect):
    project = Project.create(tmp_path / "video", aspect=aspect)
    assert platforms.zone_of(project) is None and not platforms.built_in(project) and platforms.note(project) == ""
    width, height = project.size
    assert gates.zone_for(FakeContext(project)) == (gates.TALL_ZONE if height > width else gates.FLAT_ZONE)


def test_a_film_from_a_storyboard_keeps_its_own_zones(tmp_path):
    """Its text is laid out in zones of its own, which no platform moves, so the gate must not start failing it."""
    project = Project.create(tmp_path / "film")
    (project.root / "storyboard.json").write_text("{}")
    assert platforms.zone_of(project) is None and platforms.note(project) == ""
    assert gates.zone_for(FakeContext(project)) == gates.TALL_ZONE


def test_new_writes_the_vertical_platform_for_a_tall_project_only(tmp_path, clip):
    assert main(["new", str(tmp_path / "tall"), "--input", str(clip)]) == 0
    text = (tmp_path / "tall" / "altocue.toml").read_text()
    assert '\nplatform = "vertical"' in text and "# platform" not in text
    assert Project.open(tmp_path / "tall").config["output"]["platform"] == "vertical"
    for aspect in ("16:9", "4:5"):
        folder = tmp_path / aspect.replace(":", "x")
        assert main(["new", str(folder), "--input", str(clip), "--aspect", aspect]) == 0
        assert "platform" not in Project.open(folder).config["output"]
        assert '# platform = "reels"' in (folder / "altocue.toml").read_text()


def spoken(tmp_path, aspect="9:16"):
    project = Project.create(tmp_path / "talk", aspect=aspect)
    project.save_clips([Clip(id="a", path=str(tmp_path / "gone.mp4"), duration=6.0, fps=30, width=1080, height=1920, has_audio=False)])
    line = "the international committee of experts met again in Geneva yesterday"
    project.save_words("a", [Word(text, 0.3 + n * 0.4, 0.6 + n * 0.4) for n, text in enumerate(line.split())], "test")
    project.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip="a", start=0.0, end=5.0)]))
    return project


def test_the_built_in_zone_is_named_once_and_only_when_nobody_chose_it(tmp_path, capsys):
    project = spoken(tmp_path)
    assert "built-in vertical safe zone" in platforms.note(project)
    assert main(["caption", "-p", str(project.root)]) == 0
    assert capsys.readouterr().out.count("built-in vertical safe zone") == 1
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace('# platform = "reels"   ', 'platform = "tiktok"'))
    assert main(["caption", "-p", str(project.root)]) == 0
    assert "built-in" not in capsys.readouterr().out


def test_a_tall_project_with_no_platform_sets_its_captions_inside_the_vertical_zone(tmp_path, brand):
    project = spoken(tmp_path)
    zone = platforms.PLATFORMS["vertical"].zone
    pages = caption.build(project)
    made = timeline.build(project)
    assert made["zone"] == zone and made["captions"] == pages
    assert max(len(page["lines"]) for page in pages) == 2  # the test means nothing unless a page really takes two lines
    geo = layout(1080, 1920, brand, made["zone"])
    assert geo["gutter"] >= zone["left"] * 1080 and 1080 - geo["gutter"] - geo["rail"] <= zone["right"] * 1080
    for page in pages:
        drawn = caption_frame((1080, 1920), page, 0, brand, made["zone"])
        left, top, right, bottom = drawn.getbbox()
        spread = 40  # the soft shadow spreads a little past the letters
        assert zone["top"] * 1920 <= top and bottom <= zone["bottom"] * 1920 + spread, page
        assert zone["left"] * 1080 - spread <= left and right <= zone["right"] * 1080 + spread, page
    assert f"--cap-y:{geo['cap_y']}px" in compose(made, brand)


def text_box(brand, geo, height, lines=2):
    size = int(brand.caption["size"])
    return geo["cap_y"], geo["cap_y"] + lines * size * 1.14


@pytest.mark.parametrize("name", ["reels", "tiktok", "shorts", "vertical"])
def test_tall_captions_rise_into_the_zone(brand, name):
    zone = platforms.PLATFORMS[name].zone
    plain, moved = layout(1080, 1920, brand), layout(1080, 1920, brand, zone)
    top, bottom = text_box(brand, moved, 1920)
    assert moved["cap_y"] < plain["cap_y"]
    assert bottom <= zone["bottom"] * 1920
    assert top >= zone["top"] * 1920


def test_lower_thirds_stay_above_the_lifted_captions(brand):
    moved = layout(1080, 1920, brand, platforms.PLATFORMS["reels"].zone)
    assert moved["lt_y"] + 132 < moved["cap_y"]


def test_a_wide_right_margin_widens_the_rail(brand):
    zone = platforms.PLATFORMS["shorts"].zone
    moved = layout(1080, 1920, brand, zone)
    assert moved["gutter"] >= zone["left"] * 1080
    assert moved["gutter"] + moved["rail"] >= round((1 - zone["right"]) * 1080, 6)


def test_text_moved_for_a_zone_sits_clear_of_its_edge(brand):
    zone = platforms.PLATFORMS["tiktok"].zone
    moved = layout(1080, 1920, brand, zone)
    assert moved["gutter"] >= zone["left"] * 1080 + 8  # a text reader's box runs a few pixels past the letters


def test_a_narrower_zone_scales_graphics_so_a_title_keeps_its_line_breaks(brand):
    plain = layout(1080, 1920, brand)
    moved = layout(1080, 1920, brand, platforms.PLATFORMS["tiktok"].zone)
    area = moved["scale"] / plain["scale"]
    assert area == pytest.approx((1080 - 2 * moved["gutter"] - moved["rail"]) / (1080 - 2 * plain["gutter"] - plain["rail"]), abs=0.002)
    assert layout(1080, 1920, brand, platforms.PLATFORMS["reels"].zone)["scale"] == plain["scale"]


def test_a_graphic_can_sit_right_under_the_top_of_the_zone(brand):
    zone = platforms.PLATFORMS["reels"].zone
    geo = layout(1080, 1920, brand, zone)
    face = [(300.0, 560.0, 480.0, 500.0)]
    top = placement.best_top({"type": "hook"}, 230.0, face, (1080, 1920), geo["cap_y"], geo=geo, zone=zone)
    assert top == pytest.approx(zone["top"] * 1920 + 12, abs=1)


def test_a_narrower_zone_is_counted_when_guessing_a_graphics_height(brand, monkeypatch):
    widths = []
    monkeypatch.setattr(placement, "line_count", lambda text, size, width, brand: widths.append(width) or 1)
    item = {"type": "hook", "text": "Any words"}
    placement.estimated_height(item, 1080, 1920, brand)
    placement.estimated_height(item, 1080, 1920, brand, platforms.PLATFORMS["tiktok"].zone)
    assert widths[1] < widths[0]


def test_a_zone_the_layout_already_clears_changes_nothing(brand):
    assert layout(1920, 1080, brand, platforms.PLATFORMS["youtube"].zone) == layout(1920, 1080, brand)


def test_the_html_layer_uses_the_zone(brand):
    timeline = {"width": 1080, "height": 1920, "total": 1.0, "captions": [], "items": [], "zone": platforms.PLATFORMS["reels"].zone}
    assert f"--cap-y:{layout(1080, 1920, brand, timeline['zone'])['cap_y']}px" in compose(timeline, brand)


def test_the_basic_layer_uses_the_zone(brand):
    page = {"start": 0.0, "end": 1.0, "words": [{"text": "Hello", "start": 0.0, "end": 1.0}]}
    zone = platforms.PLATFORMS["reels"].zone
    drawn = caption_frame((1080, 1920), page, 0, brand, zone)
    _, top, _, bottom = drawn.getbbox()
    assert bottom <= zone["bottom"] * 1920 + 40  # the soft shadow spreads a little past the letters


def test_a_graphic_dodging_a_face_stays_inside_the_zone(brand):
    zone = platforms.PLATFORMS["reels"].zone
    item = {"type": "stat", "value": "41%", "label": "of it"}
    face = [(200.0, 300.0, 700.0, 500.0)]
    geo = layout(1080, 1920, brand, zone)
    top = placement.best_top(item, 300.0, face, (1080, 1920), geo["cap_y"], geo=geo, zone=zone)
    assert top is None or zone["top"] * 1920 <= top


class FakeContext:
    def __init__(self, project, safe_zone=None):
        self.project = project
        self.rules = type("Rules", (), {"safe_zone": safe_zone})() if safe_zone else None


def test_the_gate_reads_the_platform_zone(tmp_path):
    project = Project.create(tmp_path / "video")
    assert gates.zone_for(FakeContext(project)) == platforms.PLATFORMS["vertical"].zone
    project.config["output"]["platform"] = "reels"
    assert gates.zone_for(FakeContext(project)) == platforms.PLATFORMS["reels"].zone


def test_a_rules_file_still_beats_the_platform(tmp_path):
    project = Project.create(tmp_path / "video")
    project.config["output"]["platform"] = "reels"
    mine = {"left": 0.1, "right": 0.9, "top": 0.1, "bottom": 0.9}
    assert gates.zone_for(FakeContext(project, mine)) == mine
