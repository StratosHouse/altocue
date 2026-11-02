"""Platform targets: every number has a source, and layout and the gate both follow the zone."""

from __future__ import annotations

import pytest

from altocue import brand as brands
from altocue import master, platforms
from altocue.errors import AltocueError
from altocue.motion import placement
from altocue.motion.basic import caption_frame
from altocue.motion.html import compose, layout
from altocue.project import SIZES, Project
from altocue.qa import gates


@pytest.fixture
def brand(tmp_path):
    return brands.load(Project.create(tmp_path / "video"))


def test_every_platform_is_a_real_size_with_a_sourced_zone_and_length():
    assert set(platforms.PLATFORMS) == {"reels", "tiktok", "shorts", "youtube", "linkedin", "square"}
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


def test_a_project_names_its_platform(tmp_path):
    project = Project.create(tmp_path / "video")
    assert platforms.zone_of(project) is None
    project.config["output"]["platform"] = "reels"
    assert platforms.zone_of(project) == platforms.PLATFORMS["reels"].zone
    project.config["output"]["platform"] = "myspace"
    with pytest.raises(AltocueError):
        platforms.zone_of(project)


def text_box(brand, geo, height, lines=2):
    size = int(brand.caption["size"])
    return geo["cap_y"], geo["cap_y"] + lines * size * 1.14


@pytest.mark.parametrize("name", ["reels", "tiktok", "shorts"])
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
    assert gates.zone_for(FakeContext(project)) == gates.TALL_ZONE
    project.config["output"]["platform"] = "reels"
    assert gates.zone_for(FakeContext(project)) == platforms.PLATFORMS["reels"].zone


def test_a_rules_file_still_beats_the_platform(tmp_path):
    project = Project.create(tmp_path / "video")
    project.config["output"]["platform"] = "reels"
    mine = {"left": 0.1, "right": 0.9, "top": 0.1, "bottom": 0.9}
    assert gates.zone_for(FakeContext(project, mine)) == mine
