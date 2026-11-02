from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from test_grade import make_project, scene, write_clip

from altocue import grade, looks
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import Project


def graded_project(tmp_path: Path) -> Project:
    return make_project(tmp_path, [write_clip(tmp_path / "a.mp4", [scene(wall=(150, 150, 150))] * 30)])


RAMP = np.stack([np.linspace(0, 1, 64)] * 3, axis=-1)
SKIN = np.tile(np.array([[0.78, 0.58, 0.47]]), (400, 1))


def test_there_are_eight_new_looks_and_none_is_named_after_a_film_stock():
    assert len(looks.IDS) == 8
    assert not {"kodak", "fuji", "portra", "velvia", "cinestill"} & set("_".join(looks.IDS).split("_"))


@pytest.mark.parametrize("name", looks.IDS)
def test_every_look_keeps_its_range_and_never_reverses_the_grey_scale(name):
    grid = grade.identity_grid(9)
    out = looks.render(name, grid)
    assert out.shape == grid.shape and out.min() >= 0 and out.max() <= 1
    grey = looks.render(name, RAMP) @ grade.LUMA_709
    assert (np.diff(grey) > -1e-3).all()


def test_mono_has_no_colour_in_it():
    out = looks.render("mono_contrast", grade.identity_grid(9))
    assert np.abs(out[:, 0] - out[:, 1]).max() < 1e-6 and np.abs(out[:, 1] - out[:, 2]).max() < 1e-6


def test_matte_lifts_the_blacks_and_holds_the_whites():
    out = looks.render("faded_matte", np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]))
    assert out[0].min() > 0.05 and out[1].max() < 0.96


def test_teal_and_orange_splits_cool_shadows_from_warm_highlights():
    out = grade.rgb_to_lab(looks.render("teal_orange", np.array([[0.12, 0.13, 0.15], [0.85, 0.72, 0.6]])))
    assert out[0, 1] < 0 and out[0, 2] < 0  # teal: less red, less yellow
    assert out[1, 1] > 0 and out[1, 2] > 0  # orange


def test_the_neutral_show_lut_keeps_greys_grey():
    lab = grade.rgb_to_lab(looks.render("neutral_show", RAMP))
    assert np.abs(lab[:, 1:]).max() < 0.8


def test_strength_scales_a_look_from_nothing_to_all_of_it():
    grid = grade.identity_grid(5)
    full = looks.fit(looks.choose("warm_film"), None)
    half = looks.fit(looks.choose("warm_film", strength=0.5), None)
    none = looks.fit(looks.choose("warm_film", strength=0.0), None)
    assert np.allclose(none.apply(grid), grid)
    assert np.allclose(half.apply(grid), grid + (full.apply(grid) - grid) * 0.5)


def test_skin_is_held_back_while_the_rest_takes_the_look():
    wall = np.tile(np.array([[0.45, 0.5, 0.55]]), (400, 1))
    guarded = looks.fit(looks.choose("teal_orange"), SKIN)
    bare = looks.fit(looks.choose("teal_orange"), None)
    moved = grade._hue_gap(grade.median_hue(SKIN), grade.median_hue(guarded.apply(SKIN)))
    assert moved <= grade.SKIN_HUE_LIMIT
    assert moved < grade._hue_gap(grade.median_hue(SKIN), grade.median_hue(bare.apply(SKIN)))
    assert np.allclose(guarded.apply(wall), bare.apply(wall), atol=0.02)


def test_a_look_that_removes_colour_is_not_guarded():
    finish = looks.fit(looks.choose("mono_contrast"), SKIN)
    out = finish.apply(SKIN)
    assert np.abs(out[:, 0] - out[:, 2]).max() < 1e-6


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


def test_the_comparison_sheet_is_written(tmp_path):
    project = graded_project(tmp_path)
    dest = looks.sheet(project)
    assert dest.exists() and dest.stat().st_size > 1000
