"""Log footage: each curve against its maker's published numbers, the gamut matrices, the render, and the one setting."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from test_grade import make_project, scene, to_yuv10

from altocue import grade, logs, looks
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.ffmpeg import probe_clip
from altocue.models import Clip
from altocue.project import Project

# Where each curve puts black, an 18% grey card and 90% white, as a share of the code range, and the scene light at
# the top code. Sony's technical summary for S-Log3 and Panasonic's V-Log reference manual print the first three as
# 10 bit code values: 95, 420, 598 and 128, 433, 602. The other rows are what each maker's published formula gives
# (Apple Log Profile White Paper, Canon Log Gamma Curves, F-Log2 Data Sheet, DJI's D-Log white paper). All six rows
# were also compared with colour-science 0.4.7 (BSD-3-Clause), which gives the same numbers.
PUBLISHED = {
    "apple_log": (0.1505, 0.4883, 0.6817, 12.0),
    "slog3": (95 / 1023, 420 / 1023, 598 / 1023, 38.4),
    "vlog": (128 / 1023, 433 / 1023, 602 / 1023, 46.1),
    "clog3": (0.1251, 0.3434, 0.5645, 14.7),
    "flog2": (0.0929, 0.3910, 0.5571, 58.3),
    "dlog": (0.0929, 0.3988, 0.5729, 42.0),
}
# Camera primaries to BT.709, by rows, as computed from the published primaries with the D65 white point.
MATRICES = {
    "bt2020": [[1.660491, -0.587641, -0.072850], [-0.124550, 1.132900, -0.008349], [-0.018151, -0.100579, 1.118730]],
    "s_gamut3_cine": [[1.626947, -0.540139, -0.086809], [-0.178516, 1.417941, -0.239425], [-0.044436, -0.195920, 1.240356]],
    "v_gamut": [[1.806574, -0.695698, -0.110879], [-0.170089, 1.305955, -0.135865], [-0.025206, -0.154468, 1.179674]],
    "cinema_gamut": [[1.923861, -0.798761, -0.125101], [-0.204311, 1.495899, -0.291588], [-0.023685, -0.420127, 1.443812]],
    "d_gamut": [[1.674842, -0.579967, -0.094927], [-0.098125, 1.334046, -0.235877], [-0.041009, -0.243022, 1.283755]],
}
GREY = np.stack([np.linspace(0, 1, 2001)] * 3, axis=-1)


def test_the_six_curves_the_setting_names():
    assert tuple(logs.CURVES) == ("apple_log", "slog3", "vlog", "clog3", "flog2", "dlog") == tuple(PUBLISHED)
    assert logs.INPUTS == ("auto", "video", *logs.CURVES)
    assert all(curve.gamut in logs.PRIMARIES for curve in logs.CURVES.values())


@pytest.mark.parametrize("name", PUBLISHED)
def test_each_curve_puts_black_grey_and_white_where_its_maker_says(name):
    curve = logs.CURVES[name]
    black, grey, white, top = PUBLISHED[name]
    assert curve.encode(np.array([0.0, 0.18, 0.90])) == pytest.approx([black, grey, white], abs=6e-4)
    assert curve.decode(np.array([black, grey, white])) == pytest.approx([0.0, 0.18, 0.90], abs=3e-3)
    assert float(curve.decode(np.array(1.0))) == pytest.approx(top, rel=5e-3)


def test_sony_and_panasonic_code_values_come_out_whole():
    for name, codes in (("slog3", [95, 420, 598]), ("vlog", [128, 433, 602])):
        assert np.round(logs.CURVES[name].encode(np.array([0.0, 0.18, 0.90])) * 1023).tolist() == codes


@pytest.mark.parametrize("name", PUBLISHED)
def test_each_curve_round_trips_and_never_reverses(name):
    curve = logs.CURVES[name]
    black = PUBLISHED[name][0]
    code = np.linspace(black, 1, 4001)  # from black up: below it a camera records noise, and Apple's curve is flat there
    assert np.abs(curve.encode(curve.decode(code)) - code).max() < 1e-6
    light = np.concatenate((np.linspace(0, 0.02, 400), np.geomspace(0.02, 10, 800)))
    assert np.abs(curve.decode(curve.encode(light)) - light).max() < 1e-6 * 10
    steps = np.diff(curve.decode(np.linspace(black, 1, 20001)))
    assert (steps > 0).all()
    assert steps.max() < 0.05 * float(curve.decode(np.array(1.0)))  # no jump where the pieces of the curve meet


def test_apple_log_is_continuous_where_its_two_pieces_meet():
    """The white paper gives the join as a signal level computed from the other constants. A typo in any of them opens a gap."""
    below, above = logs.apple_log_decode(np.array([logs.APPLE_PT - 1e-9, logs.APPLE_PT + 1e-9]))
    assert below == pytest.approx(logs.APPLE_RT, abs=1e-6) and above == pytest.approx(logs.APPLE_RT, abs=1e-5)
    assert float(logs.apple_log_decode(np.array(0.0))) == pytest.approx(logs.APPLE_R0)


@pytest.mark.parametrize("gamut", MATRICES)
def test_each_gamut_matrix_matches_its_published_primaries_and_keeps_white(gamut):
    matrix = logs.to_bt709(gamut)
    assert np.allclose(matrix.sum(axis=1), 1.0, atol=1e-9)
    assert np.abs(matrix - np.array(MATRICES[gamut])).max() < 3e-4
    assert logs.scene_to_sdr(np.full((1, 3), 0.5), gamut)[0] == pytest.approx(logs.scene_to_sdr(np.full((1, 3), 0.5))[0], abs=1e-9)


def test_the_matrices_agree_with_the_one_the_hdr_path_uses():
    assert np.abs(logs.to_bt709("bt2020") - grade.BT2020_TO_BT709).max() < 1e-6
    assert np.allclose(logs.to_bt709("bt709"), np.eye(3), atol=1e-12)


def test_a_decoded_frame_goes_back_on_the_code_value_scale():
    assert float(logs.code_value(np.array((95 - 64) / 876), False)) == pytest.approx(95 / 1023)
    assert float(logs.code_value(np.array(0.4), True)) == 0.4
    grey = np.full((1, 3), (420 - 64) / 876)  # an S-Log3 grey card, as ffmpeg decodes a limited range file
    assert logs.log_to_sdr(grey, "slog3")[0] == pytest.approx(logs.log_to_sdr(np.full((1, 3), 420 / 1023), "slog3", full_range=True)[0])
    apple = np.full((1, 3), 0.4883)  # Apple writes its curve on the decoded signal, so the range does not move it
    assert np.array_equal(logs.log_to_sdr(apple, "apple_log"), logs.log_to_sdr(apple, "apple_log", full_range=True))


def test_the_render_puts_grey_and_a_face_where_ordinary_video_has_them():
    """BT.709 itself gives the numbers: its camera curve takes 18% to 0.409 and one stop more to 0.595."""
    levels = np.repeat(np.array([0.0, 0.18, 0.36, 0.90, 1.0, 12.0])[:, None], 3, axis=1)
    out = logs.scene_to_sdr(levels)[:, 0]
    assert out[0] == 0 and out[1] == pytest.approx(1.099 * 0.18**0.45 - 0.099, abs=1e-9) and out[2] == pytest.approx(0.595, abs=1e-3)
    assert 0.85 < out[3] < out[4] < 0.92 and 0.98 < out[5] < 1.0  # white lands under the top, and the top is never reached


def test_the_render_never_reverses_and_never_leaves_range():
    ramp = np.repeat(np.linspace(0, 40, 4001)[:, None], 3, axis=1)
    out = logs.scene_to_sdr(ramp)
    assert (np.diff(out[:, 0]) >= 0).all() and out.min() == 0 and out.max() < 1
    wild = np.random.default_rng(1).uniform(-0.5, 4.0, (20000, 3))  # colours far outside BT.709, and below black
    for gamut in logs.PRIMARIES:
        mapped = logs.scene_to_sdr(wild, gamut)
        assert np.isfinite(mapped).all() and mapped.min() >= 0 and mapped.max() <= 1


@pytest.mark.parametrize("name", PUBLISHED)
def test_colours_outside_bt709_stay_in_range_with_their_hue(name):
    """The BT.2020 primaries are real lights, close to single wavelengths, and far outside BT.709.

    Each must come out as that colour, and as the same picture whichever camera's primaries describe it. A camera's
    own primaries are no use here: several are not real colours, and a blue with less than no brightness is noise.
    """
    gamut = logs.CURVES[name].gamut
    lasers = np.array([[0.4, 0.0, 0.0], [0.0, 0.4, 0.0], [0.0, 0.0, 0.4], [0.0, 0.3, 0.3]])
    seen = lasers @ logs.to_bt709("bt2020").T @ np.linalg.inv(logs.to_bt709(gamut)).T  # the same lights, in this camera's numbers
    mapped = logs.scene_to_sdr(seen, gamut)
    assert mapped.min() >= 0 and mapped.max() <= 1
    assert mapped.argmax(axis=1)[:3].tolist() == [0, 1, 2] and mapped[3, 0] < mapped[3, 1:].min()
    assert np.abs(mapped - logs.scene_to_sdr(lasers, "bt2020")).max() < 1e-9


@pytest.mark.parametrize("name", PUBLISHED)
def test_a_log_picture_comes_back_as_the_scene(name):
    """Encode a scene the way the camera would, read it the way ffmpeg would, and the render gives the scene's own picture."""
    rng = np.random.default_rng(3)
    light = 10 ** rng.uniform(-2.0, 0.4, (5000, 1)) * 10 ** rng.uniform(-0.15, 0.15, (5000, 3))  # BT.709 scene light
    assert np.abs(logs.log_to_sdr(as_camera(light, name), name) - logs.scene_to_sdr(light)).max() < 1e-4  # DJI rounds its inverse


@pytest.mark.parametrize("name", PUBLISHED)
def test_a_65_point_cube_follows_the_curve_on_greys(name):
    table = logs.log_to_sdr(grade.identity_grid(grade.HDR_LUT_SIZE), name)
    cube, exact = grade.apply_lut(table, GREY), logs.log_to_sdr(GREY, name)
    assert np.abs(cube - exact).max() < 0.008 and np.allclose(cube[:, 0], cube[:, 1], atol=1e-6)
    coarse = grade.apply_lut(logs.log_to_sdr(grade.identity_grid(grade.LUT_SIZE), name), GREY)
    assert np.abs(coarse - exact).mean() > 2 * np.abs(cube - exact).mean()  # which is why log is baked at 65 points


def as_camera(light: np.ndarray, name: str, *, full_range: bool = False) -> np.ndarray:
    """BT.709 scene light as the coded RGB ffmpeg would hand back from this camera's file."""
    curve = logs.CURVES[name]
    code = curve.encode(np.asarray(light, np.float64) @ np.linalg.inv(logs.to_bt709(curve.gamut)).T)
    return code if full_range or not curve.on_code_values else (code * 1023 - 64) / 876


def log_scene(name: str = "slog3", **kwargs) -> np.ndarray:
    """The face against a wall that the grade tests use, as this camera would record it. Floats from 0 to 1."""
    picture = scene(**kwargs).astype(np.float64) / 255
    light = np.where(picture < 0.081, picture / 4.5, ((picture + 0.099) / 1.099) ** (1 / 0.45))  # the BT.709 curve, backwards
    return np.clip(as_camera(light, name), 0, 1)


def write_log(path: Path, coded: np.ndarray, *, tags: str = "range=tv:colorspace=bt709", frames: int = 8) -> Path:
    """A lossless 10 bit clip with nothing in its tags that says log, which is how cameras write them."""
    height, width = coded.shape[:2]
    raw = to_yuv10(coded, (0.2126, 0.0722)) * frames
    args = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-s", f"{width}x{height}", "-r", "30", "-i", "-"]
    subprocess.run([*args, "-vf", f"setparams={tags}", "-c:v", "ffv1", str(path)], input=raw, check=True)
    return path


def set_input(project: Project, value: str) -> Project:
    path = project.root / "altocue.toml"
    text = path.read_text()
    line = next(line for line in text.splitlines() if line.startswith("input = "))
    path.write_text(text.replace(line, f"input = {value}", 1))
    return Project.open(project.root)


def clip(**fields) -> Clip:
    return Clip(**{"id": "a", "path": "a.mov", "duration": 1.0, "fps": 30.0, "width": 16, "height": 16, **fields})


def test_a_new_project_reads_the_files_own_tags(tmp_path):
    project = Project.open(Project.create(tmp_path / "video").root)
    assert project.setting("grade", "input") == "auto"
    assert all(name in (project.root / "altocue.toml").read_text() for name in logs.INPUTS)  # the file says what the word can be


def test_the_setting_is_one_word_or_a_table_and_anything_else_is_refused(tmp_path):
    project = Project.create(tmp_path / "video")
    assert logs.asked(project) == "auto" and logs.mark(project) == ""
    assert logs.asked(set_input(project, '"slog3"')) == "slog3"
    assert logs.asked(set_input(project, '{ a = "vlog", b = "video" }')) == {"a": "vlog", "b": "video"}
    for wrong in ('"slog2"', '"S-Log3"', "3", '{ a = "log" }', '""'):
        with pytest.raises(AltocueError, match="input") as caught:
            logs.asked(set_input(project, wrong))
        assert all(name in caught.value.fix for name in logs.INPUTS)


def test_one_word_goes_to_every_clip_the_tags_do_not_settle(tmp_path):
    project = set_input(Project.create(tmp_path / "video"), '"slog3"')
    plain, phone = clip(id="a"), clip(id="b", transfer="arib-std-b67", primaries="bt2020")
    assert [c.input for c in logs.tagged(project, [plain, phone])] == ["slog3", ""]  # HDR is tagged, so it keeps its own path
    for word in ('"auto"', '"video"'):
        assert [c.input for c in logs.tagged(set_input(project, word), [plain, phone])] == ["", ""]


def test_a_table_names_clips_and_refuses_what_cannot_be_true(tmp_path):
    project = set_input(Project.create(tmp_path / "video"), '{ a = "vlog" }')
    plain, other, phone = clip(id="a"), clip(id="b"), clip(id="c", transfer="smpte2084")
    assert [c.input for c in logs.tagged(project, [plain, other, phone])] == ["vlog", "", ""]
    with pytest.raises(AltocueError, match="no clip has that id") as caught:
        logs.tagged(set_input(project, '{ z = "vlog" }'), [plain, other])
    assert "a, b" in caught.value.fix
    with pytest.raises(AltocueError, match="HDR") as caught:
        logs.tagged(set_input(project, '{ c = "vlog" }'), [plain, phone])
    assert caught.value.fix


@pytest.mark.parametrize("name", PUBLISHED)
def test_an_unconverted_log_picture_reads_as_flat(name):
    assert logs.flat(log_scene(name).reshape(-1, 3)).startswith("a flat picture, with nothing darker than 0.")
    ordinary = scene()
    ordinary[:40], ordinary[-40:] = 8, 246  # a dark jumper and a window
    assert logs.flat(ordinary.reshape(-1, 3).astype(np.float64) / 255) == ""
    colourful = scene(wall=(60, 140, 200)).reshape(-1, 3).astype(np.float64) / 255
    assert logs.flat(colourful) == ""  # a picture with real colour in it is not a wide gamut read wrongly


def test_auto_never_picks_a_curve_and_a_hint_names_the_setting(tmp_path):
    project = Project.create(tmp_path / "video")
    apple = clip(pix_fmt="yuv422p10le", primaries="bt2020", transfer="unknown")  # no file behind it, so only its tags can speak
    assert [c.input for c in logs.tagged(project, [clip(), apple])] == ["", ""]
    said = logs.hint(project, apple)
    assert said.startswith("may be log footage (10 bit BT.2020 with no transfer function, as Apple Log is tagged)")
    assert "input under [grade]" in said and all(name in said for name in logs.CURVES) and 'input = "video"' in said
    assert logs.hint(project, clip(pix_fmt="yuv420p10le", primaries="bt2020", transfer="arib-std-b67")) == ""  # tagged HDR is settled
    assert logs.hint(project, clip(pix_fmt="yuv420p", primaries="bt2020")) == ""  # log is not recorded in 8 bits often enough to ask
    assert [logs.bit_depth(clip(pix_fmt=name)) for name in ("yuv420p", "yuvj420p", "yuv422p10le", "p010le", "yuv444p12le")] == [8, 8, 10, 10, 12]


def test_saying_what_the_clip_is_ends_the_hint(tmp_path):
    project = Project.create(tmp_path / "video")
    apple = clip(pix_fmt="yuv422p10le", primaries="bt2020")
    assert logs.hint(set_input(project, '"video"'), apple) == ""
    assert logs.hint(set_input(project, '{ a = "video" }'), apple) == ""
    assert logs.hint(set_input(project, '{ b = "video" }'), apple) != ""  # the other clip was named, not this one
    named = logs.tagged(set_input(project, '"apple_log"'), [apple])[0]
    assert named.input == "apple_log" and logs.hint(set_input(project, '"apple_log"'), named) == ""


def flagged(path: Path, span: str) -> Path:
    """One 10 bit frame whose range is in the picture's own stream, as a camera writes it, so every ffmpeg reads it back.

    10 bit has no full range format of its own, so the flag is all there is to go on. A flag kept only by the container
    did not survive ffmpeg 8 and 9 on a lossless clip, and no camera file relies on one.
    """
    source = ["-f", "lavfi", "-i", "color=c=gray:s=64x64:r=30", "-frames:v", "1"]
    chain = f"format=yuv420p10le,setparams=range={span}:colorspace=bt709"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *source, "-vf", chain, "-color_range", span, "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


def test_how_ffmpeg_reads_the_range_comes_from_the_file(tmp_path):
    limited = probe_clip(flagged(tmp_path / "tv.mkv", "tv"), clip_id="tv")
    full = probe_clip(flagged(tmp_path / "pc.mkv", "pc"), clip_id="pc")
    assert limited.pix_fmt == full.pix_fmt == "yuv420p10le"  # the format says nothing, so the flag decides
    assert not logs.full_range(limited) and logs.full_range(full)
    assert logs.full_range(clip(pix_fmt="yuvj420p", path=str(tmp_path / "tv.mkv")))


def log_project(tmp_path: Path, name: str = "slog3") -> Project:
    return make_project(tmp_path, [write_log(tmp_path / "a.mkv", log_scene(name))])


def test_a_log_clip_left_on_auto_is_not_converted_and_the_summary_says_why(tmp_path, capsys):
    project = log_project(tmp_path)
    assert main(["grade", "--project", str(project.root)]) == 0
    printed = capsys.readouterr().out
    entry = json.loads((project.lut_dir / "report.json").read_text())["clips"]["a"]
    assert entry["changes"]["input"] is None and "may be log footage" in entry["input_hint"]
    assert "a: may be log footage" in printed and "input under [grade]" in printed and "slog3" in printed
    table = grade.read_cube(project.lut_dir / "a.cube")
    assert len(table) == grade.LUT_SIZE**3 and np.abs(table - grade.identity_grid(grade.LUT_SIZE)).max() < 0.12  # corrected at most, never decoded


def test_a_named_curve_is_decoded_ahead_of_the_correction_in_the_clips_one_cube(tmp_path, capsys):
    project = set_input(log_project(tmp_path), '"slog3"')
    assert main(["grade", "--project", str(project.root)]) == 0
    printed = capsys.readouterr().out
    report = json.loads((project.lut_dir / "report.json").read_text())
    entry = report["clips"]["a"]
    assert entry["changes"]["input"] == "slog3" and entry["input_hint"] is None
    assert "log to SDR as slog3" in printed and "may be log" not in printed
    assert sorted(path.name for path in project.lut_dir.glob("*.cube")) == ["a.cube"]

    ordinary = grade.measure_frames("x", [scene().astype(np.float32) / 255])
    assert entry["faces"]["rate"] == 1.0  # the face is found in the picture, not in the log signal
    assert entry["before"]["face_luma"] == pytest.approx(grade.median_luma(ordinary.skin), abs=0.02)  # and measured as the picture it becomes
    table = grade.read_cube(project.lut_dir / "a.cube")
    assert len(table) == grade.HDR_LUT_SIZE**3
    correction = np.abs(grade.apply_lut(table, log_scene()) - scene().astype(np.float64) / 255)
    assert correction.mean() < 0.05  # the scene again, give or take a bounded correction
    assert np.abs(log_scene() - scene().astype(np.float64) / 255).mean() > 2 * correction.mean()


def test_the_render_chain_turns_a_log_file_into_the_picture(tmp_path):
    from test_grade import first_frame_rgb, render

    source = write_log(tmp_path / "a.mkv", log_scene("vlog"))
    found = probe_clip(source, clip_id="a")
    lut = grade.write_cube(tmp_path / "a.cube", logs.log_to_sdr(grade.identity_grid(grade.HDR_LUT_SIZE), "vlog"), "a")
    got = first_frame_rgb(render(source, grade.video_filter(found, lut), tmp_path / "out.mp4"), 640, 480)
    wanted = scene().astype(np.float64) / 255
    for y, x in ((60, 60), (400, 600), (230, 320), (250, 280)):  # wall, wall, and two places on the face
        assert np.abs(got[y, x] - wanted[y, x]).max() < 0.03


def test_changing_the_setting_solves_again_and_grade_off_still_converts(tmp_path):
    project = log_project(tmp_path)
    first = grade.grade_project(project)
    assert grade.up_to_date(project)
    project = set_input(project, '"slog3"')
    assert not grade.up_to_date(project)
    second = grade.grade_project(project)
    assert first["stamp"] != second["stamp"] and grade.up_to_date(project)
    assert grade.grade_project(set_input(project, '{ a = "slog3" }'))["clips"]["a"]["changes"]["input"] == "slog3"

    text = (project.root / "altocue.toml").read_text()
    (project.root / "altocue.toml").write_text(text.replace("enabled = true          # off still converts HDR", "enabled = false         # off still converts HDR", 1))
    off = grade.grade_project(set_input(Project.open(project.root), '"slog3"'))
    assert off["clips"]["a"]["changes"]["input"] == "slog3"
    plain = logs.log_to_sdr(grade.identity_grid(grade.HDR_LUT_SIZE), "slog3")
    assert np.allclose(grade.read_cube(project.lut_dir / "a.cube"), plain, atol=2e-6)  # the conversion and nothing else


def test_a_wrong_setting_stops_the_grade_with_a_fix(tmp_path, capsys):
    project = set_input(log_project(tmp_path), '"s-log"')
    assert main(["grade", "--project", str(project.root)]) != 0
    assert "slog3" in capsys.readouterr().err


def test_an_ordinary_clip_gets_no_hint_and_the_look_sheet_reads_log_too(tmp_path):
    from test_frame import write_clip

    ordinary = make_project(tmp_path / "plain", [write_clip(tmp_path / "b.mp4", [scene()] * 8)])
    assert grade.grade_project(ordinary)["clips"]["b"]["input_hint"] is None
    project = set_input(log_project(tmp_path), '"slog3"')
    sheet = looks.sheet(project)
    from PIL import Image

    with Image.open(sheet.parent / "looks" / "natural.jpg") as natural, Image.open(sheet.parent / "looks" / "raw.jpg") as raw:
        graded, flat = np.asarray(natural, np.float64) / 255, np.asarray(raw, np.float64) / 255
    assert graded.std() > 1.3 * flat.std()  # the graded frame has the contrast the log frame lacks
