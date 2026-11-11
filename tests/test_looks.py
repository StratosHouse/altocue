from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from test_grade import bars_hdr, make_project, scene, write_clip, write_hdr

from altocue import grade, looks
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import Project


def graded_project(tmp_path: Path) -> Project:
    return make_project(tmp_path, [write_clip(tmp_path / "a.mp4", [scene(wall=(150, 150, 150))] * 30)])


RAMP = np.stack([np.linspace(0, 1, 64)] * 3, axis=-1)
SKIN = np.tile(np.array([[0.78, 0.58, 0.47]]), (400, 1))
# Five skin colours from light to deep. Fixtures, not measurements of people: a tint moves weak colour furthest, and deep
# skin has the weakest colour, so a guard proven on one light swatch is not proven.
SWATCHES = {"light": (0.86, 0.70, 0.60), "fair": (0.78, 0.58, 0.47), "medium": (0.60, 0.42, 0.32), "brown": (0.42, 0.28, 0.21), "deep": (0.25, 0.17, 0.14)}
MONO = ("mono_contrast", "mono_silver")
BEFORE = ("warm_film", "cool_film", "faded_matte", "mono_contrast", "bleach_bypass", "teal_orange", "soft_pastel", "neutral_show")
NEW = ("clean_commercial", "product_white", "warm_lifestyle", "airy_bright", "earthy_doc", "moody_low", "print_film", "vintage_print", "mono_silver")
NEW = (*NEW, "night_blue", "neon_night")
# Where each new or changed look puts black and white, as luma. A tint in the shadows shows as a black that is not zero.
POINTS = {
    "teal_orange": (0.0, 1.0), "mono_contrast": (0.0, 1.0), "clean_commercial": (0.0, 1.0), "product_white": (0.0, 1.0), "warm_lifestyle": (0.012, 0.982),
    "airy_bright": (0.038, 0.990), "earthy_doc": (0.011, 0.975), "moody_low": (0.039, 0.900), "print_film": (0.035, 0.975), "vintage_print": (0.040, 0.956),
    "mono_silver": (0.020, 0.975), "night_blue": (0.034, 0.970), "neon_night": (0.008, 0.998),
}  # fmt: skip
S_CURVE = ((0, 0), (0.25, 0.2), (0.5, 0.52), (0.75, 0.82), (1, 1))


def face(tone: str) -> np.ndarray:
    """A face is never one colour: the same skin, lit a little more and a little less, with some grain in it."""
    rng = np.random.default_rng(7)
    base = np.array(SWATCHES[tone])
    return np.clip(base * rng.normal(1.0, 0.06, (600, 1)) + base * rng.normal(0, 0.02, (600, 3)), 0, 1)


def hue(rgb) -> float:
    lab = grade.rgb_to_lab(np.atleast_2d(np.array(rgb, float)))[0]
    return float(np.degrees(np.arctan2(lab[2], lab[1])) % 360)


def colour(rgb) -> float:
    lab = grade.rgb_to_lab(np.atleast_2d(np.array(rgb, float)))[0]
    return float(np.hypot(lab[1], lab[2]))


def test_there_are_nineteen_film_looks_and_none_is_named_after_a_film_stock():
    assert len(looks.IDS) == 19 and len(set(looks.ALL)) == len(looks.ALL)
    assert not {"kodak", "fuji", "portra", "velvia", "cinestill"} & set("_".join(looks.IDS).split("_"))


def test_every_look_that_shipped_before_still_answers_to_its_name():
    assert looks.IDS == (*BEFORE, *NEW)
    assert all(looks.choose(name).name == name for name in looks.ALL)


@pytest.mark.parametrize("name", looks.IDS)
def test_every_look_keeps_its_range_and_never_reverses_the_grey_scale(name):
    grid = grade.identity_grid(9)
    out = looks.render(name, grid)
    assert out.shape == grid.shape and out.min() >= 0 and out.max() <= 1 and np.isfinite(out).all()
    grey = looks.render(name, RAMP) @ grade.LUMA_709
    assert (np.diff(grey) > -1e-3).all()


@pytest.mark.parametrize("name", looks.IDS)
def test_every_look_bakes_to_a_smooth_cube(name):
    """A big step between two neighbouring points of the cube is where a sky or a wall breaks into bands."""
    cube = looks.render(name, grade.identity_grid(grade.LUT_SIZE)).reshape(grade.LUT_SIZE, grade.LUT_SIZE, grade.LUT_SIZE, 3)
    assert max(np.abs(np.diff(cube, axis=axis)).max() for axis in range(3)) <= 0.25


@pytest.mark.parametrize("name", POINTS)
def test_black_and_white_land_where_the_recipe_puts_them(name):
    black, white = looks.render(name, np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])) @ grade.LUMA_709
    assert black == pytest.approx(POINTS[name][0], abs=0.004) and white == pytest.approx(POINTS[name][1], abs=0.004)


@pytest.mark.parametrize("name", ["neutral_show", "clean_commercial", "product_white", "teal_orange", *MONO])
def test_a_look_with_no_opinion_about_greys_keeps_them_grey(name):
    lab = grade.rgb_to_lab(looks.render(name, RAMP))
    assert np.abs(lab[:, 1:]).max() < (0.8 if name == "neutral_show" else 0.05)


@pytest.mark.parametrize("name", MONO)
def test_mono_has_no_colour_in_it(name):
    out = looks.render(name, grade.identity_grid(9))
    assert np.abs(out[:, 0] - out[:, 1]).max() < 1e-6 and np.abs(out[:, 1] - out[:, 2]).max() < 1e-6


def test_mono_mixes_light_the_way_a_filter_over_the_lens_does():
    """A red and the grey that sends the same light through the filter come out as one tone."""
    red = np.array([[0.8, 0.2, 0.1]])
    light = (red**grade.DISPLAY_GAMMA) @ np.array([0.34, 0.58, 0.08])
    grey = np.full((1, 3), light[0] ** (1 / grade.DISPLAY_GAMMA))
    assert np.allclose(looks.render("mono_contrast", red), looks.render("mono_contrast", grey), atol=1e-9)
    soft, hard = (looks.render(name, RAMP)[:, 0] for name in ("mono_silver", "mono_contrast"))
    assert soft[4] > hard[4] and soft[-5] < hard[-5]  # the softer one keeps more in both ends


def test_matte_lifts_the_blacks_and_holds_the_whites():
    out = looks.render("faded_matte", np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]))
    assert out[0].min() > 0.05 and out[1].max() < 0.96


def test_teal_and_orange_splits_cool_shadows_from_warm_highlights():
    out = grade.rgb_to_lab(looks.render("teal_orange", np.array([[0.12, 0.13, 0.15], [0.85, 0.72, 0.6]])))
    assert out[0, 1] < 0 and out[0, 2] < 0  # teal: less red, less yellow
    assert out[1, 1] > 0 and out[1, 2] > 0  # orange


@pytest.mark.parametrize("warm", [(0.72, 0.30, 0.34), (0.80, 0.16, 0.14), (0.90, 0.82, 0.20), (0.78, 0.58, 0.47)], ids=["lips", "a brand red", "yellow", "skin"])
def test_teal_and_orange_leaves_warm_colours_their_own_hue(warm):
    """The old recipe pulled every warm hue toward one orange, so lips, a red logo and a yellow all drifted together."""
    assert abs(hue(looks.render("teal_orange", np.array([warm]))[0]) - hue(warm)) < 1.0


def test_teal_and_orange_leans_only_what_already_has_colour():
    sky, wall, shirt = (0.45, 0.62, 0.85), (0.5, 0.5, 0.5), (0.93, 0.93, 0.93)
    out = looks.render("teal_orange", np.array([sky, wall, shirt]))
    assert looks.TEAL < hue(out[0]) < hue(sky) - 10  # a blue sky turns toward teal
    assert colour(out[1]) < 0.05 and colour(out[2]) < 0.05  # a grey wall and a white shirt take no colour


def test_product_white_cleans_a_cream_background_and_keeps_the_product():
    cream, product = (0.97, 0.95, 0.90), (0.85, 0.20, 0.25)
    out = looks.render("product_white", np.array([cream, product]))
    assert np.ptp(out[0]) < 0.02 and out[0].min() > 0.97
    assert colour(out[1]) >= 0.95 * colour(product) and abs(hue(out[1]) - hue(product)) < 1.5


def test_clean_commercial_lifts_weak_colour_and_holds_strong_colour():
    weak, strong = (0.55, 0.50, 0.45), (0.95, 0.10, 0.10)
    out = looks.render("clean_commercial", np.array([weak, strong]))
    assert colour(out[0]) / colour(weak) > 1.08 > colour(out[1]) / colour(strong)


def test_night_cools_the_dark_and_leaves_a_lamp_warm():
    for name in ("night_blue", "neon_night"):
        dark, lamp = grade.rgb_to_lab(looks.render(name, np.array([[0.2, 0.2, 0.2], [0.95, 0.75, 0.45]])))
        assert dark[2] < -2 and lamp[2] > 20
    assert looks.render("night_blue", RAMP)[32] @ grade.LUMA_709 < RAMP[32, 0] - 0.05  # and the mids come down


def test_the_lifestyle_and_print_looks_lean_the_way_they_say():
    grey = np.array([[0.5, 0.5, 0.5]])
    assert grade.rgb_to_lab(looks.render("warm_lifestyle", grey))[0, 2] > 3  # golden mids
    assert grade.rgb_to_lab(looks.render("vintage_print", np.array([[1.0, 1.0, 1.0]])))[0, 2] > 3  # cream whites
    shadow, top = grade.rgb_to_lab(looks.render("print_film", np.array([[0.1, 0.1, 0.1], [0.9, 0.9, 0.9]])))
    assert shadow[2] < 0 < top[2]  # cool shadows, warm top
    green = (0.30, 0.60, 0.25)
    for name in ("warm_lifestyle", "earthy_doc", "airy_bright"):
        out = looks.render(name, np.array([green]))[0]
        assert hue(out) < hue(green) - 3 and colour(out) < colour(green)  # greens go toward olive, and quieter


def test_a_curve_goes_through_its_points_and_never_reverses():
    points = ((0, 0.02), (0.1, 0.085), (0.25, 0.24), (0.5, 0.53), (0.75, 0.79), (0.92, 0.93), (1, 0.975))
    curve = looks._curve(points)
    assert np.allclose(curve(np.array([x for x, _ in points])), [y for _, y in points])
    assert (np.diff(curve(np.linspace(0, 1, 2001))) >= 0).all()
    assert (np.diff(looks._curve(((0, 0), (0.4, 0.4), (0.5, 0.9), (0.6, 0.9), (1, 1)))(np.linspace(0, 1, 2001))) >= -1e-12).all()  # a flat step does not ring


def test_the_tone_curve_keeps_hue_where_a_curve_per_channel_bends_it():
    colours = np.array([[0.70, 0.45, 0.35], [0.30, 0.50, 0.70], [0.35, 0.60, 0.30], [0.60, 0.42, 0.32]])
    kept = [abs(hue(a) - hue(b)) for a, b in zip(looks._tone(colours, S_CURVE), colours, strict=True)]
    bent = [abs(hue(a) - hue(b)) for a, b in zip(looks._tone_each(colours, S_CURVE), colours, strict=True)]
    assert max(kept) < 1.5 and max(bent) > 2 * max(kept)
    assert np.allclose(looks._tone(RAMP, S_CURVE)[:, 0], looks._curve(S_CURVE)(RAMP[:, 0]), atol=1e-9)  # and on greys it is the curve


def test_dye_makes_colour_deeper_where_video_makes_it_brighter():
    sky = np.array([[0.45, 0.62, 0.85]])
    light = [float(((looks._dye(sky, amount, 1.25) ** grade.DISPLAY_GAMMA) @ grade.LUMA_709)[0]) for amount in (0.0, 0.5, 1.0)]
    assert light[0] == pytest.approx(float(((sky**grade.DISPLAY_GAMMA) @ grade.LUMA_709)[0]), abs=1e-6)  # video: the light is held
    assert light[0] > light[1] + 0.03 and light[1] > light[2] + 0.03  # dye: more colour, less light
    assert all(colour(looks._dye(sky, amount, 1.25)[0]) > colour(sky[0]) for amount in (0.0, 0.5, 1.0))
    assert np.allclose(looks._dye(RAMP, 1.0, 1.25), RAMP, atol=1e-9)  # greys do not move


def test_the_chroma_ceiling_has_a_knee_and_is_never_passed():
    lab = grade.rgb_to_lab(grade.identity_grid(9))
    before = np.hypot(lab[:, 1], lab[:, 2])
    after_lab = looks._ceiling(lab, 80)
    after = np.hypot(after_lab[:, 1], after_lab[:, 2])
    assert before.max() > 120 and after.max() < 80
    assert np.allclose(after[before <= 48], before[before <= 48]) and (after[before > 48] < before[before > 48]).all()
    order = np.argsort(before)
    assert (np.diff(after[order]) >= -1e-9).all()  # a stronger colour never comes out weaker than a weaker one
    assert np.allclose(np.arctan2(after_lab[:, 2], after_lab[:, 1])[before > 1], np.arctan2(lab[:, 2], lab[:, 1])[before > 1])


def test_vibrance_and_punchy_lift_weak_colour_more_than_strong():
    weak, strong = (0.55, 0.50, 0.45), (0.95, 0.10, 0.10)
    lab = looks._vibrance(grade.rgb_to_lab(np.array([weak, strong])), 0.2)
    assert np.hypot(*lab[0, 1:]) / colour(weak) > np.hypot(*lab[1, 1:]) / colour(strong) > 1
    out = grade.apply_look(np.array([weak, SWATCHES["fair"], strong]), "punchy")
    gains = [colour(a) / colour(b) for a, b in zip(out, (weak, SWATCHES["fair"], strong), strict=True)]
    assert gains[0] > gains[1] > gains[2] and gains[0] > 1.08 and gains[2] < 1.02


def test_strength_scales_a_look_from_nothing_to_all_of_it():
    grid = grade.identity_grid(5)
    full = looks.fit(looks.choose("warm_film"), None)
    half = looks.fit(looks.choose("warm_film", strength=0.5), None)
    none = looks.fit(looks.choose("warm_film", strength=0.0), None)
    assert np.allclose(none.apply(grid), grid)
    assert np.allclose(half.apply(grid), grid + (full.apply(grid) - grid) * 0.5)


def test_skin_is_held_back_while_the_rest_takes_the_look():
    wall = np.tile(np.array([[0.45, 0.5, 0.55]]), (400, 1))
    guarded = looks.fit(looks.choose("warm_film"), SKIN)
    bare = looks.fit(looks.choose("warm_film"), None)
    moved = grade._hue_gap(grade.median_hue(SKIN), grade.median_hue(guarded.apply(SKIN)))
    assert moved <= grade.SKIN_HUE_LIMIT
    assert moved < grade._hue_gap(grade.median_hue(SKIN), grade.median_hue(bare.apply(SKIN)))
    assert np.allclose(guarded.apply(wall), bare.apply(wall), atol=0.02)


@pytest.mark.parametrize("tone", SWATCHES)
@pytest.mark.parametrize("name", [name for name in looks.ALL if name not in MONO])
def test_the_skin_guard_holds_on_every_skin_tone(name, tone):
    """Skin hue within its limit and the face within its budget, from light skin to deep, on every look that keeps colour."""
    skin = face(tone)
    fitted = looks.fit(looks.choose(name), skin)
    after = fitted.apply(skin)
    assert grade._hue_gap(grade.median_hue(skin), grade.median_hue(after)) <= grade.SKIN_HUE_LIMIT
    assert abs(grade.median_luma(after) / grade.median_luma(skin) - 1) <= grade.FACE_BUDGET
    assert fitted.strength >= looks.FLOOR


@pytest.mark.parametrize("name", ["night_blue", "neon_night", "cool_film"])
def test_a_shadow_tint_that_would_turn_deep_skin_is_caught(name):
    """Unguarded, the same tint that moves light skin by a degree moves deep skin by many. The guard has to see that."""
    light, deep = face("light"), face("deep")
    bare = looks.choose(name)
    turned = [grade._hue_gap(grade.median_hue(skin), grade.median_hue(bare.apply(skin))) for skin in (light, deep)]
    assert turned[1] > 15 and turned[1] > 5 * turned[0]
    held = looks.fit(bare, deep)
    assert grade._hue_gap(grade.median_hue(deep), grade.median_hue(held.apply(deep))) <= grade.SKIN_HUE_LIMIT


def test_a_hard_look_does_not_crush_a_dark_face():
    """Bleach bypass left so little colour in deep skin that it passed for black and white, and the face lost half its level."""
    deep = face("deep")
    bare = looks.choose("bleach_bypass")
    assert grade.median_luma(bare.apply(deep)) < 0.7 * grade.median_luma(deep)
    assert not looks.removes_colour(bare)
    held = looks.fit(bare, deep)
    assert held.skin_center is not None and abs(grade.median_luma(held.apply(deep)) / grade.median_luma(deep) - 1) <= grade.FACE_BUDGET


@pytest.mark.parametrize("tone", SWATCHES)
@pytest.mark.parametrize("name", MONO)
def test_a_look_that_removes_colour_is_not_guarded(name, tone):
    skin = face(tone)
    finish = looks.fit(looks.choose(name), skin)
    out = finish.apply(skin)
    assert finish.skin_center is None and np.abs(out[:, 0] - out[:, 2]).max() < 1e-6


def cube(path: Path, table: np.ndarray, *head: str) -> Path:
    size = round(len(table) ** (1 / 3))
    lines = ["# made by a test", 'TITLE "Test"', *head, f"LUT_3D_SIZE {size}", ""]
    path.write_text("\n".join([*lines, *(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table)]) + "\n")
    return path


def test_reads_a_three_dimensional_cube(tmp_path):
    table = 1 - grade.identity_grid(5)
    read = looks.read_cube(cube(tmp_path / "invert.cube", table))
    assert np.allclose(read.apply(np.array([[0.25, 0.5, 0.75]])), [[0.75, 0.5, 0.25]], atol=1e-5)


def test_reads_a_one_dimensional_cube_and_a_domain(tmp_path):
    path = tmp_path / "half.cube"
    path.write_text("LUT_1D_SIZE 3\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n0 0 0\n0.25 0.25 0.25\n0.5 0.5 0.5\n")
    assert np.allclose(looks.read_cube(path).apply(np.array([[1.0, 0.5, 2.0]])), [[0.25, 0.125, 0.5]])


@pytest.mark.parametrize(
    "text",
    ["", "LUT_3D_SIZE 2\n0 0 0\n", "LUT_3D_SIZE 1\n0 0 0\n", "not a lut at all\n", "LUT_3D_SIZE 2\n" + "nan 0 0\n" * 8],
)
def test_a_bad_cube_is_refused_with_a_fix(tmp_path, text):
    path = tmp_path / "bad.cube"
    path.write_text(text)
    with pytest.raises(AltocueError) as error:
        looks.read_cube(path)
    assert error.value.fix


def test_a_missing_lut_is_refused(tmp_path):
    with pytest.raises(AltocueError, match="No LUT"):
        looks.choose("natural", lut=tmp_path / "gone.cube")


def test_a_look_and_a_lut_are_not_both_taken(tmp_path):
    with pytest.raises(AltocueError, match="one or the other"):
        looks.choose("warm_film", lut=cube(tmp_path / "a.cube", grade.identity_grid(3)))


def test_strength_outside_the_range_is_refused():
    with pytest.raises(AltocueError, match="between 0 and 1"):
        looks.choose("warm_film", strength=1.5)


def test_grade_bakes_a_new_look_by_either_spelling(tmp_path, capsys):
    project = graded_project(tmp_path)
    assert main(["grade", "--project", str(project.root), "--look", "warm-film", "--look-strength", "0.6"]) == 0
    report = json.loads((project.lut_dir / "report.json").read_text())
    assert report["look"] == "warm_film" and report["look_strength"] == 0.6
    entry = report["clips"]["a"]["changes"]
    assert entry["look_strength"] <= 0.6
    table = grade.read_cube(project.lut_dir / "a.cube")
    assert not np.allclose(table, grade.identity_grid(grade.LUT_SIZE), atol=0.002)


def test_changing_the_strength_solves_again(tmp_path):
    project = graded_project(tmp_path)
    first = grade.grade_project(project, look="soft_pastel", strength=1.0)
    second = grade.grade_project(project, look="soft_pastel", strength=0.5)
    assert first["stamp"] != second["stamp"]


def test_a_users_lut_goes_through_the_grade(tmp_path):
    project = graded_project(tmp_path)
    path = cube(tmp_path / "mine.cube", grade.identity_grid(9) ** 1.5)
    report = grade.grade_project(project, lut=path)
    assert report["look"] == "lut:mine.cube"
    assert not np.allclose(grade.read_cube(project.lut_dir / "a.cube"), grade.identity_grid(grade.LUT_SIZE), atol=0.002)


def test_a_changed_lut_file_solves_again(tmp_path):
    project = graded_project(tmp_path)
    path = cube(tmp_path / "mine.cube", grade.identity_grid(9) ** 1.5)
    first = grade.grade_project(project, lut=path)
    cube(path, grade.identity_grid(9) ** 2)
    assert grade.grade_project(project, lut=path)["stamp"] != first["stamp"]


def test_a_lut_named_in_the_config_is_read_from_the_project(tmp_path):
    project = graded_project(tmp_path)
    cube(project.root / "show.cube", grade.identity_grid(9) ** 1.5)
    text = (project.root / "altocue.toml").read_text().replace('lut = ""', 'lut = "show.cube"', 1)
    (project.root / "altocue.toml").write_text(text)
    assert grade.grade_project(Project.open(project.root))["look"] == "lut:show.cube"


def test_a_look_bakes_on_hdr_footage(tmp_path):
    project = make_project(tmp_path, [write_hdr(tmp_path / "a.mov", bars_hdr())])
    report = grade.grade_project(project, look="bleach_bypass")
    table = grade.read_cube(project.lut_dir / "a.cube")
    assert report["clips"]["a"]["changes"]["hdr"] and report["look"] == "bleach_bypass"
    plain = grade.hdr_to_sdr(grade.identity_grid(grade.LUT_SIZE), "arib-std-b67")
    assert len(table) == grade.LUT_SIZE**3 and not np.allclose(table, plain, atol=0.01)


def test_the_comparison_sheet_is_written(tmp_path):
    project = graded_project(tmp_path)
    dest = looks.sheet(project)
    assert dest.exists() and dest.stat().st_size > 1000


def test_the_comparison_also_keeps_each_look_alone_and_the_raw_frame(tmp_path):
    """A film shows one take raw against graded, then its looks one by one, so each needs its own picture."""
    project = graded_project(tmp_path)
    dest = looks.sheet(project)
    singles = dest.parent / "looks"
    assert {path.stem for path in singles.glob("*.jpg")} == {"raw", *looks.ALL}
    with Image.open(singles / "raw.jpg") as raw, Image.open(singles / "natural.jpg") as natural:
        assert raw.size == natural.size and raw.width >= 320
