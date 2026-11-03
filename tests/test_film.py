"""Films with no footage: one storyboard, one shared timeline, checked before anything renders."""

from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from altocue import brand as brands
from altocue import cli, ffmpeg, film, flash, score, storyboard, study
from altocue.errors import AltocueError
from altocue.motion import compositions, formats, hyperframes, scenes
from altocue.motion.formats import kit, product
from altocue.project import Project
from altocue.qa import film as film_qa

REPO = Path(__file__).resolve().parents[1]
TYPES = ("launch", "explainer", "demo", "ad", "gauge", "case", "line", "music")
LAUNCH_VARIANTS = ("15", "20", "24", "30")


def board(**over) -> dict:
    made = {
        "type": "launch",
        "scenes": [
            {"id": "hook", "beat": "hook", "seconds": 4, "items": [{"type": "word_slam", "text": "Too many tabs", "start": 0.2}], "sound": [{"name": "tick", "at": 1.0}]},
            {
                "id": "problem",
                "beat": "problem",
                "seconds": 7,
                "items": [{"type": "marker", "text": "Nobody reads *the report*", "start": 0.5, "duration": 4}],
                "sound": [{"name": "pop", "at": 2.0}],
            },
            {"id": "product", "beat": "product", "seconds": 8, "items": [{"type": "marker", "text": "One page, *always fresh*"}]},
            {
                "id": "payoff",
                "beat": "payoff",
                "seconds": 6,
                "items": [{"type": "marker", "text": "Ten hours back"}],
                "sound": [{"name": "ding", "at": {"scene": "payoff", "offset": 0.4}}],
            },
            {"id": "close", "beat": "close", "seconds": 5, "items": [{"type": "marker", "text": "Try it today"}]},
        ],
    }
    return {**made, **over}


def brief(**over) -> dict:
    made = {
        "company": "Fernwick makes one calm page for busy teams.",
        "audience": "Team leads",
        "goal": "Sign-ups",
        "message": "One page replaces the Monday report.",
        "must_show": ["one page"],
        "tone": "Calm and exact",
        "brand": {"colors": {"accent": "#ff7a59"}},
        "deliverables": ["16:9", "9:16", "1:1"],
        "length": 30,
    }
    return {**made, **over}


def errors(problems: list[storyboard.Problem]) -> list[str]:
    return [p.message for p in problems if p.level == "error"]


# One shared timeline


def test_scenes_run_in_order_and_times_count_from_each_scene():
    done = storyboard.resolve(board(), fps=30)
    assert [(s["id"], s["start"], s["end"]) for s in done["scenes"]] == [("hook", 0, 4), ("problem", 4, 11), ("product", 11, 19), ("payoff", 19, 25), ("close", 25, 30)]
    assert done["total"] == 30
    first = next(i for i in done["items"] if i["scene"] == "problem")
    assert (first["start"], first["end"]) == (4.5, 8.5)


def test_an_item_with_no_length_holds_to_the_end_of_its_scene():
    done = storyboard.resolve(board(), fps=30)
    held = next(i for i in done["items"] if i["scene"] == "hook")
    assert held["end"] == 4 and held["hold"] is True


def test_changing_one_scene_moves_every_later_scene_and_every_later_cue():
    before = storyboard.resolve(board(), fps=60)
    longer = board()
    longer["scenes"][2]["seconds"] = 9.5
    after = storyboard.resolve(longer, fps=60)
    shift = 1.5
    for old, new in zip(before["scenes"][3:], after["scenes"][3:], strict=True):
        assert new["start"] == pytest.approx(old["start"] + shift)
    for old, new in zip(before["scenes"][:3], after["scenes"][:3], strict=True):
        assert new["start"] == old["start"]

    def ding(done):
        return next(c["at"] for c in done["cues"] if c.get("name") == "ding" and c.get("planned"))

    assert ding(after) == pytest.approx(ding(before) + shift)
    assert after["total"] == pytest.approx(before["total"] + shift)


def test_a_time_can_name_another_scene():
    made = board()
    made["scenes"][1]["sound"] = [{"name": "pop", "at": {"scene": "close", "offset": 0.5}}]
    cue = next(c for c in storyboard.resolve(made, fps=30)["cues"] if c.get("name") == "pop" and c.get("planned"))
    assert cue["at"] == 25.5


def test_a_time_naming_a_missing_scene_says_which():
    made = board()
    made["scenes"][0]["sound"] = [{"name": "pop", "at": {"scene": "outro", "offset": 0}}]
    with pytest.raises(AltocueError, match="outro"):
        storyboard.resolve(made, fps=30)


def test_times_land_on_whole_frames():
    made = board()
    made["scenes"][0]["seconds"] = 4.012
    done = storyboard.resolve(made, fps=60)
    assert done["scenes"][1]["start"] * 60 == pytest.approx(round(done["scenes"][1]["start"] * 60))


def test_row_times_follow_the_scene():
    made = board()
    made["scenes"][1]["items"] = [{"type": "stepped_list", "items": [{"text": "One", "at": 1.0}, {"text": "Two", "at": 2.0}], "duration": 4}]
    item = next(i for i in storyboard.resolve(made, fps=30)["items"] if i["type"] == "stepped_list")
    assert [row["at"] for row in item["items"]] == [5.0, 6.0]


def test_a_time_deep_inside_a_row_follows_the_timeline():
    made = board()
    states = [{"value": "1", "at": 0}, {"value": "2", "at": {"scene": "payoff", "offset": 1}}]
    made["scenes"][0]["items"] = [{"type": "state_gauge", "rows": [{"text": "Charge", "states": states}], "carry": True, "end": {"scene": "close", "offset": 0}}]
    item = next(i for i in storyboard.resolve(made, fps=30)["items"] if i["type"] == "state_gauge")
    assert [state["at"] for state in item["rows"][0]["states"]] == [0.0, 20.0]


def test_a_take_with_no_picture_still_sets_its_scene(tmp_path):
    made = film.example(film.load_type("explainer"))["storyboard"]
    project = Project.create(tmp_path / "film")
    take = project.root / made["scenes"][0]["voice"]
    take.parent.mkdir(parents=True)
    ffmpeg.write_wav(take, np.zeros(48000 * 2), 48000)
    assert film.voice_lengths(project, made) == {made["scenes"][0]["id"]: pytest.approx(2.0)}


def test_scene_cuts_can_land_on_beats():
    beats = [i * 0.52 for i in range(100)]
    made = {**board(), "snap": True}
    done = storyboard.resolve(made, fps=60, beats=beats)
    for scene in done["scenes"][1:]:
        assert min(abs(scene["start"] - b) for b in beats) < 1 / 60 + 1e-6
    assert done["scenes"][1]["start"] == pytest.approx(4.16, abs=0.02)


def test_a_marked_cue_lands_on_the_nearest_beat():
    beats = [i * 0.5 for i in range(100)]
    made = board()
    made["scenes"][0]["sound"] = [{"name": "tick", "at": 1.2, "snap": True}]
    cue = next(c for c in storyboard.resolve(made, fps=60, beats=beats)["cues"] if c.get("name") == "tick" and c.get("planned"))
    assert cue["at"] == 1.0


def test_voice_lengths_set_scene_lengths_when_a_scene_asks():
    made = board()
    made["scenes"][0]["seconds"] = "voice"
    made["scenes"][0]["voice"] = "assets/voice/hook.wav"
    done = storyboard.resolve(made, fps=30, voice={"hook": 5.2})
    assert done["scenes"][1]["start"] == pytest.approx(5.5, abs=0.04)  # the take plus its pad


def test_layers_fill_the_copy_in_one_place():
    made = board(layers={"cta": "Start free"})
    made["scenes"][4]["items"][0]["text"] = "{{cta}} today"
    filled = storyboard.fill(made)
    assert filled["scenes"][4]["items"][0]["text"] == "Start free today"


def test_a_layer_with_no_value_is_named():
    made = board(layers={})
    made["scenes"][4]["items"][0]["text"] = "{{cta}}"
    with pytest.raises(AltocueError, match="cta"):
        storyboard.fill(made)


def test_an_aspect_can_set_its_own_zone():
    made = board()
    made["scenes"][0]["items"][0].update({"zone": "left", "aspects": {"9:16": {"zone": "top"}}})
    tall = next(i for i in storyboard.resolve(made, fps=30, aspect="9:16")["items"] if i["scene"] == "hook")
    wide = next(i for i in storyboard.resolve(made, fps=30, aspect="16:9")["items"] if i["scene"] == "hook")
    assert (tall["zone"], wide["zone"]) == ("top", "left")


# Checks that run before anything renders


def test_a_good_brief_and_board_pass():
    assert errors(storyboard.check(brief(must_show=["one page"]), board(), film.load_type("launch"))) == []


@pytest.mark.parametrize("field", ["company", "audience", "goal", "message", "must_show", "tone", "deliverables"])
def test_a_brief_missing_a_field_says_which(field):
    made = brief()
    del made[field]
    assert any(field in text for text in errors(storyboard.check(made, board(), film.load_type("launch"))))


def test_a_must_show_nobody_shows_fails_by_name():
    found = errors(storyboard.check(brief(must_show=["the audit trail"]), board(), film.load_type("launch")))
    assert any("the audit trail" in text for text in found)


def test_a_scene_can_cover_a_must_show_by_naming_it():
    made = board()
    made["scenes"][2]["shows"] = ["the audit trail"]
    assert errors(storyboard.check(brief(must_show=["the audit trail"]), made, film.load_type("launch"))) == []


def test_a_scene_with_no_job_fails():
    made = board()
    made["scenes"][1]["beat"] = "vibes"
    found = errors(storyboard.check(brief(), made, film.load_type("launch")))
    assert any("vibes" in text for text in found)


def test_a_film_missing_a_beat_fails():
    made = board()
    made["scenes"][3]["beat"] = "product"
    found = errors(storyboard.check(brief(), made, film.load_type("launch")))
    assert any("payoff" in text for text in found)


def test_an_unknown_format_says_how_to_find_one():
    made = board()
    made["scenes"][0]["items"][0]["type"] = "wobble"
    problems = [p for p in storyboard.check(brief(), made, film.load_type("launch")) if "wobble" in p.message]
    assert problems and "library search" in problems[0].fix


def test_copy_over_a_format_limit_is_listed_not_raised():
    made = board()
    made["scenes"][0]["items"][0]["text"] = "x" * 60
    found = errors(storyboard.check(brief(), made, film.load_type("launch")))
    assert any("48 fit" in text for text in found)


def test_important_text_must_hold_about_a_second_and_a_half():
    made = board()
    made["scenes"][0]["items"][0].update({"start": 0.2, "duration": 1.2})
    found = errors(storyboard.check(brief(), made, film.load_type("launch")))
    assert any("1.5" in text for text in found)


def test_a_passing_label_is_not_held_to_the_rule():
    made = board()
    made["scenes"][1]["items"].append({"type": "chip", "text": "Small note", "start": 1, "duration": 0.8})
    assert errors(storyboard.check(brief(), made, film.load_type("launch"))) == []


def test_four_flashes_in_one_second_break_the_limit():
    made = board()
    made["scenes"][1]["items"] += [{"type": "white_flash", "start": 1 + i * 0.25, "duration": 0.4} for i in range(4)]
    found = errors(storyboard.check(brief(), made, film.load_type("launch")))
    assert any("three flashes" in text for text in found)


def test_three_flashes_in_one_second_are_allowed():
    made = board()
    made["scenes"][1]["items"] += [{"type": "white_flash", "start": 1 + i * 0.3, "duration": 0.4} for i in range(3)]
    assert not any("three flashes" in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"))))


def test_two_scenes_cannot_share_an_id():
    made = board()
    made["scenes"][1]["id"] = "hook"
    assert any("hook" in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"))))


def test_an_item_past_its_scene_fails_unless_it_says_it_carries():
    made = board()
    made["scenes"][0]["items"][0].update({"start": 1, "duration": 4})
    assert any("past the end" in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"))))
    made["scenes"][0]["items"][0]["carry"] = True
    assert not any("past the end" in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"))))


def test_the_wrong_length_for_the_brief_fails():
    found = errors(storyboard.check(brief(length=20), board(), film.load_type("launch")))
    assert any("20" in text for text in found)


def test_nothing_may_drop_or_reorder_an_approved_scene():
    approved = ["hook", "problem", "product", "payoff", "close"]
    made = board()
    made["scenes"].pop(2)
    assert any("product" in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"), approved=approved)))
    swapped = board()
    swapped["scenes"][1], swapped["scenes"][2] = swapped["scenes"][2], swapped["scenes"][1]
    assert any("order" in text for text in errors(storyboard.check(brief(), swapped, film.load_type("launch"), approved=approved)))


def test_a_pan_that_flips_direction_across_a_cut_is_a_warning():
    made = board()
    made["scenes"][1]["camera"] = {"move": "pan", "x": -0.05}
    made["scenes"][2]["camera"] = {"move": "pan", "x": 0.05}
    warned = [p.message for p in storyboard.check(brief(), made, film.load_type("launch")) if p.level == "warning"]
    assert any("direction" in text for text in warned)


def test_an_unknown_camera_or_link_is_an_error():
    made = board()
    made["scenes"][0]["camera"] = {"move": "spin"}
    made["scenes"][1]["link"] = {"type": "wobble"}
    found = errors(storyboard.check(brief(), made, film.load_type("launch")))
    assert any("spin" in text for text in found) and any("wobble" in text for text in found)


def test_a_wipe_is_held_to_its_format_like_any_item():
    made = board()
    made["scenes"][0]["link"] = {"type": "torn_wipe", "why": "A tear."}
    assert any('needs "text"' in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"))))


def test_a_slot_in_the_wrong_shape_fails_the_check_not_the_render():
    made = board()
    made["scenes"][1]["items"] = [{"type": "bar_build", "title": "Sample", "bars": [{"label": "Mon", "value": 2}], "start": 0, "duration": 4}]
    assert any("cannot be drawn" in text or "needs" in text for text in errors(storyboard.check(brief(), made, film.load_type("launch"))))


def test_a_film_past_its_cap_fails_the_check():
    found = errors(storyboard.check(brief(cap_seconds=29.5), board(), film.load_type("launch")))
    assert any("past its cap" in text for text in found)


def test_a_film_exactly_on_its_cap_passes():
    assert not any("past its cap" in text for text in errors(storyboard.check(brief(cap_seconds=30.0), board(), film.load_type("launch"))))


def test_a_finished_file_past_its_cap_is_refused(tmp_path, renderer, capsys, monkeypatch):
    project = small_film(tmp_path / "film")
    path = project.root / "brief.json"
    made = json.loads(path.read_text())
    made["cap_seconds"] = 15.0
    path.write_text(json.dumps(made))
    real = ffmpeg.probe_clip

    def long(file, **kwargs):
        clip = real(file, **kwargs)
        clip.duration = 15.5
        return clip

    monkeypatch.setattr(ffmpeg, "probe_clip", long)
    assert altocue("film", "render", "-p", str(project.root), "--draft") == 1
    assert "past its cap" in capsys.readouterr().err


def test_footage_keeps_its_place_and_its_label():
    made = board()
    made["scenes"][0]["items"].append({"type": "footage", "file": "assets/take.mp4", "zone": "phone_a", "label": "Raw take", "start": 0.2, "duration": 3})
    line = timeline_for(made)
    page = scenes.compose(line, default_brand())
    assert "<video" in page and "muted" in page and "Raw take" in page


def test_example_copy_left_in_is_a_warning():
    warned = [p.message for p in storyboard.check(brief(example=True), board(), film.load_type("launch")) if p.level == "warning"]
    assert any("invented" in text for text in warned)


# Film types


def test_every_type_is_one_full_record():
    for kind in TYPES:
        record = film.load_type(kind)
        assert record["id"] == kind and record["promise"].endswith(".") and record["cost"]
        assert record["beats"] and record["brief_fields"] and record["formats"] and record["generation"] in ("none", "optional")
        assert all(formats.find(name) or name in formats.CORE_TYPES for name in record["formats"]), kind


@pytest.mark.parametrize("kind", TYPES)
def test_every_type_ships_an_example_that_passes_its_own_check(kind):
    record = film.load_type(kind)
    for name in film.variants_of(record):
        made = film.example(record, name)
        found = errors(storyboard.check(made["brief"], made["storyboard"], record, voice=film.estimated_voice(made["storyboard"])))
        assert found == [], f"{kind} {name}: {found}"


def test_launch_ships_the_four_lengths():
    record = film.load_type("launch")
    assert set(LAUNCH_VARIANTS) <= set(film.variants_of(record))
    for name in LAUNCH_VARIANTS:
        made = film.example(record, name)
        assert storyboard.resolve(made["storyboard"], fps=60)["total"] == pytest.approx(float(name), abs=0.02)


def test_examples_name_no_real_brand_and_stay_in_house_style():
    for path in sorted((REPO / "src/altocue/data/films").glob("*.json")):
        text = path.read_text()
        assert chr(0x2014) not in text, path.name
        assert not re.search(r"remotion|higgsfield|vox|netflix|apple|google|openai|anthropic", text, re.I), path.name


def test_policy_limited_types_stay_out():
    ids = set(film.type_ids())
    assert not ids & {"host", "persona", "ranking", "swap", "ambient"}


# Commands


def altocue(*args: str) -> int:
    return cli.main(list(args))


def test_film_types_lists_promise_and_cost(capsys):
    assert altocue("film", "types") == 0
    out = capsys.readouterr().out
    for kind in TYPES:
        assert kind in out
    assert "$0" in out


def test_film_new_makes_a_working_project(tmp_path, capsys):
    folder = tmp_path / "launch-film"
    assert altocue("film", "new", "launch", str(folder)) == 0
    for name in ("brief.json", "storyboard.json", "altocue.toml"):
        assert (folder / name).is_file()
    for name in ("assets", "generated", "motion", "out"):
        assert (folder / name).is_dir()
    project = Project.open(folder)
    assert project.size == (1920, 1080) and project.fps == 60
    assert altocue("film", "check", "-p", str(folder)) == 0


def test_launch_is_film_new_with_a_product_name(tmp_path, capsys):
    folder = tmp_path / "launch"
    assert altocue("launch", str(folder), "--product", "Fernwick") == 0
    assert "Fernwick" in (folder / "storyboard.json").read_text()
    assert json.loads((folder / "storyboard.json").read_text())["type"] == "launch"
    assert altocue("film", "check", "-p", str(folder)) == 0


def test_a_new_film_will_not_overwrite_a_project(tmp_path, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "launch", str(folder))
    assert altocue("film", "new", "launch", str(folder)) == 1


def test_an_unknown_type_lists_the_real_ones(tmp_path, capsys):
    assert altocue("film", "new", "vlog", str(tmp_path / "x")) == 1
    assert "launch" in capsys.readouterr().err


def test_film_check_explains_what_to_fix(tmp_path, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "launch", str(folder))
    path = folder / "storyboard.json"
    made = json.loads(path.read_text())
    made["scenes"][0]["items"][0]["type"] = "wobble"
    path.write_text(json.dumps(made))
    capsys.readouterr()
    assert altocue("film", "check", "-p", str(folder)) == 1
    out = capsys.readouterr().out
    assert "wobble" in out and "Fix:" in out


def test_approving_a_new_order_clears_the_guard(tmp_path, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "launch", str(folder))
    path = folder / "storyboard.json"
    made = json.loads(path.read_text())
    made["scenes"][1], made["scenes"][2] = made["scenes"][2], made["scenes"][1]
    path.write_text(json.dumps(made))
    assert altocue("film", "check", "-p", str(folder)) == 1
    assert altocue("film", "approve", "-p", str(folder)) == 0
    assert altocue("film", "check", "-p", str(folder)) == 0


# Variant Pack


def test_a_variant_swaps_layers_and_nothing_else(tmp_path):
    made = film.example(film.load_type("launch"), "30")["storyboard"]
    changed = film.apply_variant(made, {"id": "hook-b", "set": {"cta": "Book a demo"}})
    assert changed["layers"]["cta"] == "Book a demo" and made["layers"]["cta"] != "Book a demo"
    assert [s["id"] for s in changed["scenes"]] == [s["id"] for s in made["scenes"]]
    assert [s["seconds"] for s in changed["scenes"]] == [s["seconds"] for s in made["scenes"]]


def test_a_variant_naming_a_missing_layer_fails():
    made = film.example(film.load_type("launch"), "30")["storyboard"]
    with pytest.raises(AltocueError, match="flavour"):
        film.apply_variant(made, {"id": "x", "set": {"flavour": "lime"}})


def test_variant_copy_too_long_fails_before_any_render(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "launch", str(folder))
    plan = tmp_path / "variants.json"
    plan.write_text(json.dumps({"variants": [{"id": "long", "set": {"cta": "x" * 300}}]}))
    monkeypatch.setattr(film, "render_film", lambda *a, **k: pytest.fail("rendered"))
    assert altocue("film", "variants", "-p", str(folder), str(plan)) == 1


def test_variants_write_a_copy_table_before_rendering(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "launch", str(folder))
    plan = tmp_path / "variants.json"
    plan.write_text(json.dumps({"variants": [{"id": "demo", "set": {"cta": "Book a demo"}}, {"id": "price", "set": {"price": "$9 a seat", "cta": "Start at $9"}}]}))
    made: list[str] = []

    def fake(project, *, board, name, **_):
        made.append(name)
        return project.out / f"{name}.mp4"

    monkeypatch.setattr(film, "render_film", fake)
    assert altocue("film", "variants", "-p", str(folder), str(plan)) == 0
    assert made == ["variants/demo", "variants/price"]
    table = (folder / "review" / "variants.md").read_text()
    assert "Book a demo" in table and "$9 a seat" in table
    assert "more than one layer" in capsys.readouterr().out


# The page


def timeline_for(board_: dict, size=(1920, 1080), fps=60) -> dict:
    return scenes.timeline(storyboard.resolve(board_, fps=fps, aspect=f"{size[0]}x{size[1]}"), size=size, fps=fps, brand=default_brand())


def default_brand() -> brands.Brand:
    return brands.Brand(json.loads(brands.data_path("brands", "default.json").read_text()), brands.data_path("brands"))


def test_a_camera_is_css_on_an_html_wrapper():
    made = board()
    made["scenes"][0]["camera"] = {"move": "push", "amount": 0.1}
    page = scenes.compose(timeline_for(made), default_brand())
    assert '<div class="cam" id="cam-hook"' in page
    assert "@keyframes cam-hook" in page
    assert "<script src" not in page


def test_the_camera_never_animates_svg_or_pseudo_elements():
    page = scenes.compose(timeline_for(board()), default_brand())
    css = page[page.index("<style>") : page.index("</style>")]
    assert not re.search(r"::(before|after)[^{]*\{[^}]*animation", css)


def test_every_scene_keeps_moving_with_no_camera_set():
    page = scenes.compose(timeline_for(board()), default_brand())
    for scene in ("hook", "problem", "product", "payoff", "close"):
        assert f"@keyframes cam-{scene}" in page


def test_a_wipe_link_sits_centred_on_the_cut():
    made = board()
    made["scenes"][0]["link"] = {"type": "iris", "seconds": 1.0}
    links = [i for i in timeline_for(made)["links"] if i["type"] == "iris"]
    assert links and links[0]["start"] == pytest.approx(3.5) and links[0]["end"] == pytest.approx(4.5)


def test_a_camera_link_overlaps_the_two_scenes():
    made = board()
    made["scenes"][0]["link"] = {"type": "through", "seconds": 0.6}
    page = scenes.compose(timeline_for(made), default_brand())
    hook = re.search(r'id="cam-hook" style="[^"]*animation:cam-hook ([\d.]+)s linear ([\d.]+)s', page)
    assert float(hook.group(1)) == pytest.approx(4.3)
    problem = re.search(r'id="cam-problem" style="[^"]*animation:cam-problem ([\d.]+)s linear ([\d.]+)s', page)
    assert float(problem.group(2)) == pytest.approx(3.7)


def test_a_cutdown_comes_from_the_same_storyboard_at_its_own_size():
    made = board()
    wide = timeline_for(made)
    tall = timeline_for(made, size=(1080, 1920))
    assert (wide["width"], tall["width"]) == (1920, 1080)
    assert [i["start"] for i in wide["items"]] == [i["start"] for i in tall["items"]]
    page = scenes.compose(tall, default_brand())
    assert 'data-width="1080"' in page and 'data-height="1920"' in page


def test_two_column_scenes_stack_on_a_tall_canvas():
    wide = scenes.zone_box("left", (1920, 1080))
    tall_left = scenes.zone_box("left", (1080, 1920))
    tall_right = scenes.zone_box("right", (1080, 1920))
    assert wide[2] - wide[0] < 0.6
    assert tall_left[2] - tall_left[0] > 0.8 and tall_left[3] <= tall_right[1]


def test_a_panel_grows_to_fill_its_zone_but_stays_inside_it():
    fmt = formats.find("recap_check")
    size = (1920, 1080)
    scale = scenes.fit_scale(fmt, "center", size, 1.0)
    left, top, right, bottom = scenes.zone_box("center", size)
    assert fmt.tall * scale <= (bottom - top) * size[1] + 1
    assert 880 * scale <= (right - left) * size[0] + 1


def test_the_page_registers_for_the_renderer_checks():
    page = scenes.compose(timeline_for(board()), default_brand())
    assert "__timelines" in page and "data-motion-blur" in page


def test_every_film_page_has_a_solid_ground():
    page = scenes.compose(timeline_for(board()), default_brand())
    assert 'class="ground"' in page


# Rendering, with the renderer stubbed


@pytest.fixture
def renderer(monkeypatch):
    """Writes a real short picture, so the sound, mastering and naming are exercised for real."""
    calls: list[list[str]] = []

    def fake(stage: Path, output: Path, *, fps: int, draft: bool, total: float) -> Path:
        calls.append([stage.parent.name, str(fps), str(draft)])
        page = (stage / "index.html").read_text()
        width = re.search(r'data-width="(\d+)"', page).group(1)
        height = re.search(r'data-height="(\d+)"', page).group(1)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x101020:s={width}x{height}:r={fps}:d={total}", "-pix_fmt", "yuv420p", str(output)], check=True
        )
        return output

    monkeypatch.setattr(film, "render_picture", fake)
    return calls


def small_film(folder: Path) -> Project:
    altocue("film", "new", "launch", str(folder), "--variant", "15")
    return Project.open(folder)


def test_film_render_makes_a_master_and_real_cutdowns(tmp_path, renderer, capsys):
    project = small_film(tmp_path / "film")
    assert altocue("film", "render", "-p", str(project.root), "--cutdowns", "9:16,1:1", "--draft") == 0
    assert [call[0] for call in renderer] == ["16x9", "9x16", "1x1"]
    for name in ("final.mp4", "final-9x16.mp4", "final-1x1.mp4"):
        assert (project.out / name).is_file()
    assert (project.timeline_path).is_file()
    assert json.loads(project.timeline_path.read_text())["scenes"]
    credits_ = (project.root / "credits.txt").read_text() if (project.root / "credits.txt").exists() else ""
    report = json.loads((project.review_dir / "film.json").read_text())
    assert report["score"]["made_by"] == "altocue" and "code" in report["score"]["label"]
    assert "listen" in report["score"]["listen"]
    assert isinstance(credits_, str)


def test_film_render_refuses_a_board_that_fails_its_check(tmp_path, renderer, capsys):
    project = small_film(tmp_path / "film")
    path = project.root / "storyboard.json"
    made = json.loads(path.read_text())
    made["scenes"][0]["items"][0]["type"] = "wobble"
    path.write_text(json.dumps(made))
    assert altocue("film", "render", "-p", str(project.root), "--draft") == 1
    assert renderer == []


def test_a_film_project_masters_loudly_enough(tmp_path, renderer, capsys):
    project = small_film(tmp_path / "film")
    altocue("film", "render", "-p", str(project.root), "--draft")
    report = json.loads((project.review_dir / "film.json").read_text())
    assert report["master"]["passed"] is True


def test_film_render_keeps_the_storyboard_untouched(tmp_path, renderer, capsys):
    project = small_film(tmp_path / "film")
    before = (project.root / "storyboard.json").read_bytes()
    altocue("film", "render", "-p", str(project.root), "--draft")
    assert (project.root / "storyboard.json").read_bytes() == before


def test_the_render_arguments_ask_for_a_finished_picture(monkeypatch, tmp_path):
    seen = {}

    def run(cmd, **_):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("--output") + 1]).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(hyperframes, "render_workers", lambda: 2)
    film.render_picture(tmp_path, tmp_path / "picture.mp4", fps=60, draft=False, total=30)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--format") + 1] == "mp4" and cmd[cmd.index("--fps") + 1] == "60" and cmd[cmd.index("--quality") + 1] == "delivery"


# The bed


def test_the_bed_is_exactly_as_long_as_the_film_and_not_silent(tmp_path):
    made = storyboard.resolve(board(), fps=60)
    path, info = score.build(tmp_path / "bed.wav", made["scenes"], made["total"])
    with wave.open(str(path)) as handle:
        assert handle.getnframes() / handle.getframerate() == pytest.approx(30.0, abs=0.01)
        raw = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    assert np.abs(raw).max() > 3000
    assert info["made_by"] == "altocue" and "code" in info["label"] and info["bpm"] > 0


def test_the_bed_is_the_same_every_time(tmp_path):
    made = storyboard.resolve(board(), fps=60)
    one, _ = score.build(tmp_path / "a.wav", made["scenes"], made["total"])
    two, _ = score.build(tmp_path / "b.wav", made["scenes"], made["total"])
    assert one.read_bytes() == two.read_bytes()


def test_the_bed_gets_louder_towards_the_payoff(tmp_path):
    made = storyboard.resolve(board(), fps=60)
    path, _ = score.build(tmp_path / "bed.wav", made["scenes"], made["total"])
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        raw = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(float).reshape(-1, 2).mean(axis=1)

    def level(a, b):
        return float(np.sqrt(np.mean(raw[int(a * rate) : int(b * rate)] ** 2)))

    assert level(20, 24) > level(1, 3)


def test_the_music_turns_on_every_scene_mark():
    made = storyboard.resolve(board(), fps=60)
    laid = score.grid(made["scenes"], style="pulse", seed=4)
    for scene in made["scenes"]:
        assert min(abs(beat - scene["start"]) for beat in laid["beats"]) < 1e-6


def test_a_longer_scene_moves_the_music_with_it():
    before = storyboard.resolve(board(), fps=60)
    longer = board()
    longer["scenes"][1]["seconds"] = 9
    after = storyboard.resolve(longer, fps=60)
    assert 4.0 + 9 in [round(b, 3) for b in score.grid(after["scenes"], style="pulse", seed=4)["beats"]]
    assert 11.0 in [round(b, 3) for b in score.grid(before["scenes"], style="pulse", seed=4)["beats"]]


def test_tempo_stays_in_the_style_and_the_stretch_is_small():
    made = storyboard.resolve(board(), fps=60)
    for name, style in score.STYLES.items():
        laid = score.grid(made["scenes"], style=name, seed=2)
        assert style.tempo[0] <= laid["bpm"] <= style.tempo[1]
        for section in laid["sections"]:
            assert abs(60 / section["step"] / laid["bpm"] - 1) < 0.04


def test_several_styles_ship_and_an_unknown_one_says_so():
    assert len(score.STYLES) >= 4
    with pytest.raises(AltocueError, match="style"):
        score.grid([], style="polka")


def test_the_same_seed_is_the_same_sound_and_a_new_seed_is_not(tmp_path):
    made = storyboard.resolve(board(), fps=60)
    one, _ = score.compose(made["scenes"], made["total"], style="glass", seed=11)
    two, _ = score.compose(made["scenes"], made["total"], style="glass", seed=11)
    three, _ = score.compose(made["scenes"], made["total"], style="glass", seed=12)
    assert np.array_equal(one, two) and not np.array_equal(one, three)


def test_every_score_says_a_person_must_listen(tmp_path):
    made = storyboard.resolve(board(), fps=60)
    _, info = score.build(tmp_path / "s.wav", made["scenes"], made["total"], style="night", seed=1)
    assert "listen" in info["listen"].lower()


def test_the_score_goes_quiet_where_the_storyboard_plans_a_silence(tmp_path):
    made = storyboard.resolve(board(), fps=60)
    quiet = [{"from": 18.0, "to": 18.6}]
    samples, _ = score.compose(made["scenes"], made["total"], style="pulse", seed=3, silences=quiet)
    inside = samples[int(18.1 * score.RATE) : int(18.5 * score.RATE)]
    around = samples[int(16.0 * score.RATE) : int(17.5 * score.RATE)]
    assert np.abs(inside).max() < 0.1 * np.abs(around).max()


def test_two_films_that_sound_the_same_get_a_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    made = storyboard.resolve(board(), fps=60)
    _, info = score.build(tmp_path / "a.wav", made["scenes"], made["total"], style="pulse", seed=5)
    assert score.remember("film-one", info) is None
    warned = score.remember("film-two", info)
    assert warned and "film-one" in warned


def test_a_different_style_gets_no_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    made = storyboard.resolve(board(), fps=60)
    _, one = score.build(tmp_path / "a.wav", made["scenes"], made["total"], style="pulse", seed=5)
    _, two = score.build(tmp_path / "b.wav", made["scenes"], made["total"], style="glass", seed=5)
    assert score.remember("film-one", one) is None and score.remember("film-two", two) is None


def test_re_rendering_one_film_is_not_a_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    made = storyboard.resolve(board(), fps=60)
    _, info = score.build(tmp_path / "a.wav", made["scenes"], made["total"], style="pulse", seed=5)
    score.remember("film-one", info)
    assert score.remember("film-one", info) is None


# Flash limit, measured on the picture


def strobe(path: Path, rate: float, seconds: float = 2.0) -> Path:
    """Alternating black and white at `rate` full flashes a second."""
    half = 1 / (2 * rate)
    expr = f"if(lt(mod(T,{2 * half}),{half}),235,16)"
    source = f"color=c=black:s=320x180:r=60:d={seconds},format=yuv420p,geq=lum='{expr}':cb=128:cr=128"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, str(path)], check=True)
    return path


def test_a_strobe_breaks_the_flash_limit(tmp_path):
    found = flash.measure(strobe(tmp_path / "fast.mp4", 6))
    assert found["peak_per_second"] > 3 and not found["passed"]


def test_a_slow_pulse_stays_inside_the_flash_limit(tmp_path):
    found = flash.measure(strobe(tmp_path / "slow.mp4", 2))
    assert found["peak_per_second"] <= 3 and found["passed"]


# A track someone brought


def test_a_chosen_track_moves_the_cuts_to_its_beats(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "music", str(folder))
    project = Project.open(folder)
    track = folder / "assets" / "music" / "track.wav"
    track.write_bytes(b"not really audio")
    path = folder / "storyboard.json"
    made = json.loads(path.read_text())
    made["music"] = {"path": "assets/music/track.wav"}
    made["snap"] = True
    path.write_text(json.dumps(made))
    beats = [round(index * 0.52, 3) for index in range(80)]
    calls = []

    def fake(command, **_):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"bpm": 115, "beatTimes": beats}), "")

    monkeypatch.setattr(hyperframes, "binary", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake)
    done = film.timing(film.load(project), "16:9")
    for scene in done["scenes"][1:]:
        assert min(abs(scene["start"] - beat) for beat in beats) < 0.04
    film.timing(film.load(project), "16:9")
    assert len(calls) == 1, "The beat map is kept until the file changes"
    assert "beats" in calls[0]


# Motion checks, each with a planted fault


def clip_with(path: Path, source: str, seconds: float = 3.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            str(seconds),
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


MOVING = "testsrc2=size=320x180:rate=30"


def line_for(**over) -> dict:
    return {"scenes": [{"id": "a", "start": 0.0, "end": 3.0}], "silences": [], **over}


def gate(rows: list[dict], name: str) -> dict:
    return next(row for row in rows if row["id"] == name)


def test_a_moving_film_passes_the_motion_gates(tmp_path):
    rows = film_qa.gate_rows(clip_with(tmp_path / "ok.mp4", MOVING), line_for())
    assert gate(rows, "frame_zero")["passed"] and gate(rows, "dead_air")["passed"] and gate(rows, "flash_rate")["passed"]


def test_canary_a_black_first_frame_fails(tmp_path):
    source = f"{MOVING},drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(t,0.05)'"
    rows = film_qa.gate_rows(clip_with(tmp_path / "black.mp4", source), line_for())
    assert not gate(rows, "frame_zero")["passed"]


def test_canary_dead_air_in_the_middle_fails(tmp_path):
    source = "testsrc2=size=320x180:rate=30,split[fa][fb];[fa][fb]freezeframes=first=20:last=70:replace=20[out0]"
    rows = film_qa.gate_rows(clip_with(tmp_path / "held.mp4", source), line_for())
    assert not gate(rows, "dead_air")["passed"]


def test_canary_a_strobe_fails_the_flash_gate(tmp_path):
    rows = film_qa.gate_rows(strobe(tmp_path / "fast.mp4", 6), line_for())
    assert not gate(rows, "flash_rate")["passed"]


def test_canary_a_planned_silence_with_sound_in_it_fails(tmp_path):
    rows = film_qa.gate_rows(clip_with(tmp_path / "loud.mp4", MOVING), line_for(silences=[{"from": 1.0, "to": 1.6}]))
    assert not gate(rows, "planned_silence")["passed"]


def test_canary_a_cut_off_plan_fails(tmp_path):
    parts = "color=c=red:s=320x180:r=30:d=1.5[a];color=c=blue:s=320x180:r=30:d=1.5[b];[a][b]concat=n=2:v=1[out0]"
    rows = film_qa.gate_rows(
        clip_with(tmp_path / "cut.mp4", parts), {"scenes": [{"id": "a", "start": 0.0}, {"id": "b", "start": 0.6}], "silences": [], "route": "montage"}
    )
    assert not gate(rows, "cuts_to_plan")["passed"]


def test_canary_a_loop_with_a_seam_fails(tmp_path):
    rows = film_qa.gate_rows(clip_with(tmp_path / "seam.mp4", MOVING), line_for(loop=True))
    assert not gate(rows, "loop_seam")["passed"]


def test_cuts_compare_against_the_plan_in_frames():
    found = film_qa.compare_cuts([1.0, 2.53], [1.0, 2.5], 30)
    assert found["max_error_frames"] == pytest.approx(0.9) and not found["missing"]
    assert film_qa.compare_cuts([1.0], [1.0, 4.0], 30)["missing"] == [4.0]
    assert film_qa.compare_cuts([1.0, 3.0], [1.0], 30)["unplanned"] == [3.0]


def test_text_size_and_contrast_are_measured_from_pixels():
    canvas = Image.new("RGB", (1080, 400), (10, 10, 20))
    ImageDraw.Draw(canvas).text((60, 60), "Hook", fill=(250, 250, 250), font=ImageFont.truetype(str(brands.data_path("fonts", "Geist-Variable.ttf")), 120))
    found = film_qa.measure_text(canvas, [40, 40, 400, 200], 1080)
    assert 90 <= found["font_px"] <= 140 and found["contrast"] > 12


def test_faint_text_measures_low_contrast():
    canvas = Image.new("RGB", (1080, 400), (40, 40, 50))
    ImageDraw.Draw(canvas).text((60, 60), "Hook", fill=(70, 70, 80), font=ImageFont.truetype(str(brands.data_path("fonts", "Geist-Variable.ttf")), 120))
    assert film_qa.measure_text(canvas, [40, 40, 400, 200], 1080)["contrast"] < 3


def test_text_on_a_phone_is_worked_out_from_the_canvas():
    wide = film_qa.read_size(1920, 1080, 36)
    tall = film_qa.read_size(1080, 1920, 36)
    assert wide["phone_px"] == pytest.approx(6.8, abs=0.1) and tall["phone_px"] == pytest.approx(12.0, abs=0.1)


def test_hold_time_grows_with_the_words():
    assert film_qa.hold_needed(3) == pytest.approx(1.0) and film_qa.hold_needed(10) == pytest.approx(2.4)


def test_the_seek_check_flags_a_frame_that_depends_on_history(tmp_path):
    def snapshot(stage, times, out):
        out.mkdir(parents=True, exist_ok=True)
        made = []
        for at in times:
            shade = 200 if len(times) == 1 else 10
            path = out / f"{at}.png"
            Image.new("L", (32, 18), shade).save(path)
            made.append(path)
        return made

    assert not film_qa.seek_check(tmp_path, [1.0, 2.0], snapshot)["passed"]


def test_the_seek_check_passes_a_pure_film(tmp_path):
    def snapshot(stage, times, out):
        out.mkdir(parents=True, exist_ok=True)
        made = []
        for at in times:
            path = out / f"{at}.png"
            Image.new("L", (32, 18), int(at * 40)).save(path)
            made.append(path)
        return made

    assert film_qa.seek_check(tmp_path, [1.0, 2.0], snapshot)["passed"]


def test_a_cutdown_alone_leaves_the_master_and_says_so(tmp_path, renderer, capsys):
    project = small_film(tmp_path / "film")
    assert altocue("film", "render", "-p", str(project.root), "--draft") == 0
    before = (project.out / "final.mp4").read_bytes()
    assert altocue("film", "render", "-p", str(project.root), "--cutdowns", "9:16", "--only-cutdowns", "--draft") == 0
    assert (project.out / "final.mp4").read_bytes() == before
    report = json.loads((project.review_dir / "film.json").read_text())
    assert report["master_unchanged"] == {"frames_checked": 30, "same": True}
    assert "did not change" in capsys.readouterr().out


def test_a_film_project_reports_its_own_next_step(tmp_path, capsys):
    folder = tmp_path / "film"
    altocue("film", "new", "launch", str(folder))
    capsys.readouterr()
    assert altocue("status", "-p", str(folder)) == 0
    assert "altocue film render" in capsys.readouterr().out


# Curves and the cursor


def test_a_flow_passes_through_every_pose():
    curve = kit.flow([0, 1, 3], [0.0, 2.0, 1.0])
    assert curve(0) == 0 and curve(1) == pytest.approx(2.0) and curve(3) == 1.0


def test_a_flow_never_overshoots():
    curve = kit.flow([0, 1, 2, 3], [0.0, 1.0, 1.0, 4.0])
    samples = [curve(i / 40) for i in range(121)]
    assert min(samples) >= -1e-9 and max(samples) <= 4 + 1e-9
    assert all(b >= a - 1e-9 for a, b in zip(samples, samples[1:], strict=False))


def test_a_flow_keeps_its_speed_through_a_pose():
    curve = kit.flow([0, 1, 2], [0.0, 1.0, 2.0])
    assert curve(1.01) - curve(1.0) == pytest.approx(0.01, abs=0.002)


def test_a_flow_easing_is_a_css_linear_function():
    made = kit.flow_easing([0, 1, 2], [0.0, 0.8, 1.0], steps=8)
    assert made.startswith("linear(0.0000 0.00%") and made.endswith("1.0000 100.00%)")


def test_the_cursor_travels_a_curve_and_waits_before_it_presses():
    made = product._pointer((100.0, 100.0), [(500.0, 300.0, 2.0), (700.0, 120.0, 3.0)])
    paths = re.findall(r"path\('M0 0Q([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)'\)", made)
    assert len(paths) == 2
    cx, cy, dx, dy = (float(v) for v in paths[-1])
    off_line = abs(cx * dy - cy * dx) / (dx**2 + dy**2) ** 0.5
    assert off_line > 10, "The first leg should bend away from a straight line"
    starts = [float(v) for v in re.findall(r"--t:([\d.]+)s;--n:", made)]
    spans = [float(v) for v in re.findall(r"--n:([\d.]+)s", made)]
    last_press = 3.0
    assert max(start + span for start, span in zip(starts, spans, strict=True)) <= last_press - product.CURSOR_DWELL + 0.01


def test_the_cursor_is_the_same_on_every_render():
    stops = [(500.0, 300.0, 2.0)]
    assert product._pointer((100.0, 100.0), stops) == product._pointer((100.0, 100.0), stops)


# Compositions we ship


def shipped() -> list[Path]:
    return sorted((REPO / "src/altocue/data/compositions").glob("*.html"))


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.stem)
def test_a_shipped_composition_draws_from_time_alone(path):
    text = path.read_text()
    assert "<template>" in text and "hf-seek" in text and f'data-composition-id="{path.stem}"' in text and "gsap" not in text.lower()
    assert not re.search(r"Date\.now|Math\.random|requestAnimationFrame|setTimeout|setInterval|performance\.now", text)


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.stem)
def test_a_shipped_composition_loads_with_no_project_copy(tmp_path, path):
    project = Project.create(tmp_path / "film")
    item = compositions.resolve(project, {"type": "composition", "name": path.stem, "start": 0, "end": 2})
    assert item["own_id"] == path.stem and item["path"] == str(path.resolve()) or Path(item["path"]).name == path.name


# Studying a reference


@pytest.fixture
def reference(tmp_path) -> Path:
    """Three hard cuts on purpose: red, blue, green, white, one second each, with a tone."""
    parts = []
    for index, colour in enumerate(("red", "blue", "green", "white")):
        parts.append(tmp_path / f"{index}.mp4")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={colour}:s=320x180:r=30:d=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:sample_rate=48000:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(parts[-1]),
            ],
            check=True,
        )
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    dest = tmp_path / "reference.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)], check=True)
    return dest


def test_a_study_measures_pacing_palette_and_loudness(reference, tmp_path):
    card = study.study(reference, read_text=False)
    assert card["cuts"] == pytest.approx([1.0, 2.0, 3.0], abs=0.1)
    assert card["shots"] == 4 and card["average_shot"] == pytest.approx(1.0, abs=0.1)
    assert card["cuts_per_10s"] == pytest.approx(7.5, abs=0.5)
    assert len(card["palette"]) >= 3 and all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in card["palette"])
    assert card["loudness"]["integrated"] < 0
    assert "rights" in card["note"]


def test_the_command_writes_a_style_card_and_says_the_rule_plainly(reference, tmp_path, capsys):
    out = tmp_path / "card.json"
    assert altocue("film", "study", str(reference), "--output", str(out), "--no-text") == 0
    assert json.loads(out.read_text())["shots"] == 4
    printed = capsys.readouterr().out
    assert "own or have rights" in printed


def test_a_missing_reference_says_so(tmp_path, capsys):
    assert altocue("film", "study", str(tmp_path / "nope.mp4")) == 1


def test_a_board_can_follow_a_style_card():
    card = {"average_shot": 2.0}
    warned = [p.message for p in storyboard.check(brief(), board(), film.load_type("launch"), card=card) if p.level == "warning"]
    assert any("style card" in text for text in warned)


# Skills and onboarding


def skill(name: str) -> str:
    return (REPO / "skills" / name / "SKILL.md").read_text()


def test_the_launch_skill_triggers_on_every_way_people_ask():
    head = skill("altocue-launch").split("---")[1]
    for phrase in ("launch video", "product launch", "SaaS launch", "product video", "launch film"):
        assert phrase in head


def test_the_launch_skill_runs_through_to_the_finished_film():
    body = skill("altocue-launch")
    for command in ("altocue launch", "altocue film check", "altocue film stills", "altocue film render", "altocue qa"):
        assert command in body
    assert "Never stop at a storyboard" in body


def test_the_router_sends_launches_first():
    router = skill("altocue")
    assert "altocue-launch" in router and "altocue-film" in router
    assert router.index("altocue-launch") < router.index("## The workflow")


def test_onboarding_names_the_launch_feature():
    assert "altocue launch" in (REPO / "INSTALL.md").read_text()


def test_the_film_skills_name_no_other_video_tool():
    for name in ("altocue-launch", "altocue-film"):
        assert not re.search(r"remotion|higgsfield|sanji|\u2014", skill(name), re.I)


def test_parked_types_are_explained_with_the_platform_rule():
    body = skill("altocue-film")
    assert "AI persona" in body and "mass-produced" in body


def test_film_commands_load_lazily():
    assert "altocue.film" in {module for module, _ in cli.STAGES.values()}
