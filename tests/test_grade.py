from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from test_frame import face_image, write_clip

from altocue import grade
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.ffmpeg import probe_clip, probe_json
from altocue.frame import frame_project
from altocue.models import Clip, Edl, Segment
from altocue.project import Project


def hlg_encode(linear: np.ndarray) -> np.ndarray:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    linear = np.asarray(linear, np.float64)
    return np.where(linear <= 1 / 12, np.sqrt(3 * np.maximum(linear, 0)), a * np.log(np.maximum(12 * linear - b, 1e-9)) + c)


def to_yuv10(rgb: np.ndarray, matrix: tuple[float, float]) -> bytes:
    """Limited-range 4:2:0 10-bit from non-linear RGB."""
    kr, kb = matrix
    y = kr * rgb[..., 0] + (1 - kr - kb) * rgb[..., 1] + kb * rgb[..., 2]
    u = (rgb[..., 2] - y) / (2 * (1 - kb))
    v = (rgb[..., 0] - y) / (2 * (1 - kr))
    h, w = y.shape
    cu = u.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    cv = v.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    planes = [np.round(64 + 876 * y), np.round(512 + 896 * cu), np.round(512 + 896 * cv)]
    return b"".join(np.clip(p, 0, 1023).astype("<u2").tobytes() for p in planes)


def write_hdr(path: Path, rgb: np.ndarray, *, transfer: str = "arib-std-b67", frames: int = 8, fps: int = 30) -> Path:
    """A lossless 10-bit BT.2020 clip, tagged the way a phone tags it."""
    height, width = rgb.shape[:2]
    raw = to_yuv10(rgb, (0.2627, 0.0593)) * frames
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p10le",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]
    args += ["-vf", f"setparams=range=tv:colorspace=bt2020nc:color_primaries=bt2020:color_trc={transfer}", "-c:v", "ffv1", str(path)]
    subprocess.run(args, input=raw, check=True)
    return path


def render(source: Path, chain: str, dest: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", chain, "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", str(dest)],
        check=True,
    )
    return dest


def first_frame_rgb(path: Path, width: int, height: int) -> np.ndarray:
    """Decode with the BT.709 limited matrix, regardless of what the file claims."""
    chain = "scale=in_color_matrix=bt709:in_range=tv:flags=accurate_rnd+full_chroma_int,format=rgb24"
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", chain, "-f", "rawvideo", "-"], capture_output=True, check=True
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3).astype(np.float32) / 255


def clip_tags(path: Path) -> dict:
    return next(s for s in probe_json(path)["streams"] if s["codec_type"] == "video")


def make_project(tmp_path: Path, clips: list[Path], name: str = "video") -> Project:
    project = Project.create(tmp_path / name)
    project.save_clips([probe_clip(path, clip_id=path.stem) for path in clips])
    return project


def to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.asarray(rgb, np.float32) ** 2.4


def to_signal(linear: np.ndarray) -> np.ndarray:
    return np.clip(linear, 0, 1) ** (1 / 2.4)


def scene(
    *, gains=(1.0, 1.0, 1.0), level: float = 1.0, wall=(150, 150, 150), size=(640, 480), face_at=(320, 230, 95), skin=(205, 150, 120)
) -> np.ndarray:
    """A face against a neutral wall, then a colour cast and level in linear light."""
    image = face_image(size[0], size[1], (face_at,), wall, skin).astype(np.float32) / 255
    return (to_signal(to_linear(image) * np.array(gains, np.float32) * level) * 255).astype(np.uint8)


def test_hlg_reference_white_lands_at_203_nits():
    grey = np.full((1, 3), 0.75)
    assert grade.hlg_to_nits(grey)[0, 0] == pytest.approx(203, abs=1)
    assert grade.hlg_to_nits(np.ones((1, 3)))[0, 0] == pytest.approx(1000, abs=1)
    assert grade.hlg_to_nits(np.zeros((1, 3)))[0, 0] == 0


def test_hlg_inverse_matches_the_oetf():
    linear = np.linspace(0.0, 1.0, 50)
    assert np.allclose(grade.hlg_inverse_oetf(hlg_encode(linear)), linear, atol=1e-5)


def test_pq_known_points():
    assert grade.pq_to_nits(np.array([0.0]))[0] == pytest.approx(0, abs=1e-6)
    assert grade.pq_to_nits(np.array([1.0]))[0] == pytest.approx(10000, rel=1e-4)
    assert grade.pq_to_nits(np.array([0.5081]))[0] == pytest.approx(100, abs=1)
    assert grade.pq_to_nits(np.array([0.5807]))[0] == pytest.approx(203, abs=2)


def test_gamut_matrix_keeps_white():
    assert np.allclose(grade.BT2020_TO_BT709.sum(axis=1), 1.0, atol=1e-3)


def test_tone_curve_leaves_skin_alone_and_rolls_off():
    x = np.linspace(0, 8, 400)
    y = grade.tone_curve(x)
    below = x <= grade.KNEE
    assert np.allclose(y[below], x[below])
    assert np.all(np.diff(y) >= 0) and y.max() <= 1.0
    assert 0.7 < grade.tone_curve(np.array([1.0]))[0] < 0.9


def test_hdr_conversion_maps_black_and_white():
    black = grade.hdr_to_sdr(np.zeros((1, 3)), "arib-std-b67")
    peak = grade.hdr_to_sdr(np.ones((1, 3)), "arib-std-b67")
    assert np.all(black == 0) and np.all(peak > 0.985) and np.all(peak <= 1)


def test_hdr_conversion_is_not_washed_out():
    # Shown as if it were SDR, HLG looks flat: skin loses colour and the darks do not go down.
    linear = np.array([[0.14, 0.09, 0.07], [0.002, 0.002, 0.002]])
    coded = hlg_encode(linear)
    mapped = grade.hdr_to_sdr(coded, "arib-std-b67")
    assert mapped[1].mean() < 0.1
    plain, ours = grade.rgb_to_lab(coded[0]), grade.rgb_to_lab(mapped[0])
    assert np.hypot(ours[1], ours[2]) > 1.3 * np.hypot(plain[1], plain[2])
    assert np.all(mapped >= 0) and np.all(mapped <= 1)


def test_saturated_hdr_colours_stay_in_gamut_with_their_hue():
    corners = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.6, 0.6]])
    mapped = grade.hdr_to_sdr(corners, "arib-std-b67")
    assert np.all(mapped >= 0) and np.all(mapped <= 1)
    assert mapped[0].argmax() == 1 and mapped[1].argmax() == 0 and mapped[2].argmax() == 2


def test_hdr_lut_is_65_point_and_monotone_on_grey():
    table = grade.hdr_lut("arib-std-b67")
    assert table.shape == (65**3, 3)
    ramp = grade.apply_lut(table, np.repeat(np.linspace(0, 1, 200)[:, None], 3, axis=1))
    assert np.all(np.diff(ramp[:, 1]) >= -0.002)
    assert np.allclose(ramp[:, 0], ramp[:, 1], atol=1e-3)


def test_cube_round_trip(tmp_path):
    table = grade.identity_grid(5) ** 1.2
    path = grade.write_cube(tmp_path / "a.cube", table, "test")
    assert "LUT_3D_SIZE 5" in path.read_text()
    assert np.allclose(grade.read_cube(path), table, atol=2e-6)


def test_identity_cube_order_is_red_fastest():
    grid = grade.identity_grid(3)
    assert grid[1].tolist() == [0.5, 0.0, 0.0] and grid[3].tolist() == [0.0, 0.5, 0.0] and grid[9].tolist() == [0.0, 0.0, 0.5]


def test_apply_lut_agrees_with_the_function():
    grid = grade.identity_grid(33)
    table = np.clip(grid**0.9, 0, 1)
    pixels = np.random.default_rng(0).random((5000, 3)).astype(np.float32)
    assert np.abs(grade.apply_lut(table, pixels) - pixels**0.9).max() < 0.02


def bars_hdr() -> np.ndarray:
    """Grey steps over a warm skin patch, all in HLG."""
    steps = np.linspace(0.0, 1.0, 16)
    image = np.zeros((180, 320, 3), np.float64)
    for i, value in enumerate(steps):
        image[:90, i * 20 : (i + 1) * 20] = value
    image[90:] = hlg_encode(np.array([0.13, 0.085, 0.07]))
    return image


def test_filter_chain_on_hdr_matches_the_maths_and_tags_bt709(tmp_path):
    source = write_hdr(tmp_path / "hdr.mkv", bars_hdr())
    clip = probe_clip(source, clip_id="hdr")
    assert clip.is_hdr
    lut = grade.write_cube(tmp_path / "hdr.cube", grade.hdr_lut("arib-std-b67"), "hdr")
    done = render(source, grade.video_filter(clip, lut), tmp_path / "out.mp4")

    tags = clip_tags(done)
    assert tags["pix_fmt"] == "yuv420p"
    assert (tags["color_space"], tags["color_transfer"], tags["color_primaries"], tags["color_range"]) == ("bt709", "bt709", "bt709", "tv")

    got = first_frame_rgb(done, 320, 180)
    expected = grade.hdr_to_sdr(bars_hdr(), "arib-std-b67")
    for x in (30, 110, 190, 270):
        assert np.abs(got[40, x] - expected[40, x]).max() < 0.02
    assert np.abs(got[135, 160] - expected[135, 160]).max() < 0.02


def test_black_stays_black_through_the_chain(tmp_path):
    source = write_hdr(tmp_path / "hdr.mkv", bars_hdr())
    clip = probe_clip(source, clip_id="hdr")
    lut = grade.write_cube(tmp_path / "hdr.cube", grade.hdr_lut("arib-std-b67"), "hdr")
    done = render(source, grade.video_filter(clip, lut), tmp_path / "out.mp4")
    assert first_frame_rgb(done, 320, 180)[40, 10].max() < 0.02


def gradient_clip(tmp_path: Path) -> tuple[Path, Clip]:
    # A shallow ramp: about 10 8-bit steps across 640 pixels.
    ramp = np.linspace(0.40, 0.44, 640)[None, :, None].repeat(360, axis=0).repeat(3, axis=2)
    source = write_hdr(tmp_path / "ramp.mkv", ramp, frames=4)
    return source, probe_clip(source, clip_id="ramp")


def luma_means(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
        check=True,
    ).stdout
    frame = np.frombuffer(raw, np.uint8).reshape(360, 640).astype(np.float64)
    return frame.mean(axis=0)


def test_ten_bit_ramp_does_not_band(tmp_path):
    source, clip = gradient_clip(tmp_path)
    lut = grade.write_cube(tmp_path / "ramp.cube", grade.hdr_lut("arib-std-b67"), "ramp")
    chain = grade.video_filter(clip, lut)
    done = tmp_path / "out.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", chain, "-c:v", "ffv1", str(done)], check=True)
    means = luma_means(done)
    assert len(np.unique(np.round(means, 1))) > 40
    assert np.abs(np.diff(np.convolve(means, np.ones(16) / 16, mode="valid"))).max() < 0.35


def test_geometry_runs_while_the_picture_is_still_deep(tmp_path):
    source, clip = gradient_clip(tmp_path)
    lut = grade.write_cube(tmp_path / "ramp.cube", grade.hdr_lut("arib-std-b67"), "ramp")
    chain = grade.video_filter(clip, lut, geometry="crop=320:180:0:0,scale=640:360")
    assert chain.index("lut3d") < chain.index("crop=") < chain.index("format=yuv420p")
    done = tmp_path / "out.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", chain, "-c:v", "ffv1", str(done)], check=True)
    assert len(np.unique(np.round(luma_means(done), 1))) > 20


def test_lut_path_with_awkward_characters(tmp_path):
    folder = tmp_path / "it's a: test, [v1]; final"
    folder.mkdir()
    source = write_clip(tmp_path / "plain.mp4", [np.full((120, 160, 3), 128, np.uint8)] * 4)
    clip = probe_clip(source, clip_id="plain")
    lut = grade.write_cube(folder / "plain.cube", grade.identity_grid(9), "identity")
    done = render(source, grade.video_filter(clip, lut), tmp_path / "out.mp4")
    assert abs(first_frame_rgb(done, 160, 120).mean() - 128 / 255) < 0.01


def test_sdr_chain_without_a_lut_only_normalises(tmp_path):
    source = write_clip(tmp_path / "plain.mp4", [np.full((120, 160, 3), 128, np.uint8)] * 4)
    chain = grade.video_filter(probe_clip(source, clip_id="plain"), None)
    assert "lut3d" not in chain and chain.endswith("color_trc=bt709")


def test_hdr_clip_without_a_lut_is_refused(tmp_path):
    source = write_hdr(tmp_path / "hdr.mkv", bars_hdr())
    with pytest.raises(AltocueError) as caught:
        grade.video_filter(probe_clip(source, clip_id="hdr"), None)
    assert "altocue grade" in caught.value.fix


def test_full_range_input_is_read_as_full_range(tmp_path):
    clip = Clip(id="x", path="x", duration=1, fps=30, width=10, height=10, pix_fmt="yuvj420p")
    assert "in_range=pc" in grade.video_filter(clip, None)


def test_lab_round_trip():
    rgb = np.random.default_rng(1).random((2000, 3)).astype(np.float32)
    assert np.abs(grade.lab_to_rgb(grade.rgb_to_lab(rgb)) - rgb).max() < 2e-3


def test_correction_at_zero_strength_is_identity():
    pixels = np.random.default_rng(1).random((500, 3)).astype(np.float32)
    correction = grade.Correction(gains=(1.05, 1.0, 0.95), lift=0.1)
    assert np.allclose(correction.apply(pixels, 0.0), pixels, atol=1e-5)


def test_white_balance_keeps_clipped_highlights_white():
    correction = grade.Correction(gains=(1.08, 1.0, 0.92))
    assert np.allclose(correction.apply(np.ones((1, 3), np.float32)), 1.0, atol=1e-5)


def analyse(frames: list[np.ndarray]) -> grade.ClipStats:
    return grade.measure_frames("x", [f.astype(np.float32) / 255 for f in frames])


def test_stats_find_skin_and_neutrals():
    stats = analyse([scene()] * 3)
    assert stats.face_rate == 1.0 and len(stats.skin) > 300 and len(stats.rest) > 3000
    assert 25 < stats.skin_hue < 80


def test_white_balance_reduces_a_cast_and_stays_capped():
    frames = [scene(gains=(1.0, 1.0, 0.72))] * 3
    stats = analyse(frames)
    solved = grade.solve_clip(stats)
    assert solved.gains[2] > 1.0 and solved.gains[0] < 1.02
    assert max(abs(g - 1) for g in solved.gains) <= grade.CAP + 1e-6
    before = grade.rgb_to_lab(stats.rest).mean(axis=0)
    after = grade.rgb_to_lab(solved.apply(stats.rest)).mean(axis=0)
    assert abs(after[2]) < abs(before[2])


def test_a_neutral_frame_gets_no_white_balance():
    solved = grade.solve_clip(analyse([scene()] * 3))
    assert all(abs(g - 1) < 0.01 for g in solved.gains)


def test_exposure_reads_the_face_not_the_window():
    image = scene(level=0.35)
    image[:, :200] = 250  # a bright window beside the speaker
    stats = analyse([image] * 3)
    solved = grade.solve_clip(stats)
    assert solved.lift > 0
    before, after = grade.median_luma(stats.skin), grade.median_luma(solved.apply(stats.skin))
    assert after > before
    assert after / before <= 1 + grade.CAP + 1e-3


def test_a_well_exposed_face_is_left_alone():
    assert grade.solve_clip(analyse([scene()] * 3)).lift == 0


def test_no_evidence_means_no_change():
    rng = np.random.default_rng(3)
    # Saturated flat colours, no skin and no neutrals to lean on.
    image = np.zeros((240, 320, 3), np.uint8)
    image[:120] = (220, 30, 40)
    image[120:] = (30, 200, 60)
    frames = [np.clip(image + rng.integers(-3, 3, image.shape), 0, 255).astype(np.uint8)] * 3
    stats = analyse(frames)
    assert stats.confidence < grade.MIN_CONFIDENCE
    assert grade.solve_clip(stats).is_identity


def test_skin_check_backs_off():
    pixels = np.tile(np.array([[0.78, 0.60, 0.50]], np.float32), (400, 1))
    correction = grade.Correction(gains=(0.94, 1.08, 0.94))
    strength, moved = grade.back_off(correction, pixels, limit=0.5)
    assert 0 <= strength < 1 and moved <= 0.5
    assert grade.back_off(correction, pixels, limit=30)[0] == 1.0


def test_shot_match_pulls_a_clip_toward_the_reference():
    reference = analyse([scene(wall=(150, 150, 150))] * 3)
    other = analyse([scene(gains=(1.0, 0.97, 0.9), wall=(140, 140, 140))] * 3)
    solved = grade.solve_clip(other, reference=reference)
    before = np.abs(grade.lab_stats(other.skin)[0] - grade.lab_stats(reference.skin)[0]).sum()
    after = np.abs(grade.lab_stats(solved.apply(other.skin))[0] - grade.lab_stats(reference.skin)[0]).sum()
    assert after < before


def test_white_balance_trusts_the_trim_not_the_cream_wall():
    image = scene(wall=(225, 205, 165))
    image[:, :160] = 235  # white trim
    solved = grade.solve_clip(analyse([image] * 3))
    assert all(abs(g - 1) < 0.02 for g in solved.gains)


def test_all_changes_together_stay_in_the_face_budget():
    reference = analyse([scene(level=1.0)] * 3)
    other = analyse([scene(level=0.3)] * 3)
    solved = grade.solve_clip(other, reference=reference)
    before, after = grade.median_luma(other.skin), grade.median_luma(solved.apply(other.skin))
    assert after > before
    assert after / before <= 1 + grade.FACE_BUDGET + 1e-3


def test_two_people_never_pull_on_each_other():
    pale = analyse([scene(skin=(225, 180, 165))] * 3)
    deep = analyse([scene(skin=(120, 80, 55))] * 3)
    assert not grade.same_skin(pale, deep)
    assert grade.solve_clip(deep, reference=pale).skin_match is None


def test_each_clip_matches_the_longest_take_with_the_same_skin():
    long_one = analyse([scene(skin=(225, 180, 165))] * 3)
    short_one = analyse([scene(skin=(225, 180, 165), level=0.8)] * 3)
    long_two = analyse([scene(skin=(120, 80, 55))] * 3)
    wall = analyse([np.full((240, 320, 3), 128, np.uint8)] * 3)
    for name, sample, seconds in (("a", long_one, 90), ("b", short_one, 20), ("c", long_two, 30), ("d", wall, 5)):
        sample.clip, sample.duration = name, seconds
    targets = grade.match_targets({s.clip: s for s in (long_one, short_one, long_two, wall)})
    assert targets == {"a": "a", "b": "a", "c": "c", "d": "a"}


def test_shot_match_skips_a_different_room():
    reference = analyse([scene(wall=(90, 110, 170))] * 3)
    other = analyse([scene(wall=(210, 190, 120))] * 3)
    solved = grade.solve_clip(other, reference=reference)
    assert solved.rest_match is None


@pytest.mark.parametrize(("look", "axis", "sign"), [("warm", 2, 1), ("cool", 2, -1)])
def test_warm_and_cool_lean_the_right_way(look, axis, sign):
    grey = np.full((1, 3), 0.5, np.float32)
    shifted = grade.rgb_to_lab(grade.apply_look(grey, look))[0]
    assert sign * shifted[axis] > 0.5


def test_natural_is_identity():
    pixels = np.random.default_rng(1).random((100, 3)).astype(np.float32)
    assert np.array_equal(grade.apply_look(pixels, "natural"), pixels)


def test_punchy_adds_contrast_and_colour():
    pixels = np.array([[0.25, 0.25, 0.25], [0.75, 0.75, 0.75], [0.6, 0.45, 0.4]], np.float32)
    out = grade.apply_look(pixels, "punchy")
    assert out[0, 0] < pixels[0, 0] and out[1, 0] > pixels[1, 0]
    before, after = grade.rgb_to_lab(pixels[2:]), grade.rgb_to_lab(out[2:])
    assert np.hypot(*after[0, 1:]) > np.hypot(*before[0, 1:])


def test_filmic_lifts_black_and_holds_white():
    out = grade.apply_look(np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], np.float32), "filmic")
    assert out[0].max() > 0.015 and out[1].min() < 0.99


@pytest.mark.parametrize("look", grade.LOOKS)
def test_looks_stay_gentle_and_monotone(look):
    ramp = np.repeat(np.linspace(0, 1, 256, dtype=np.float32)[:, None], 3, axis=1)
    out = grade.apply_look(ramp, look)
    assert np.all(np.diff(out[:, 1]) >= -2e-3)
    assert np.abs(out - ramp).max() < 0.07


def test_unknown_look_is_refused(tmp_path):
    project = make_project(tmp_path, [write_clip(tmp_path / "a.mp4", [scene()] * 15)])
    with pytest.raises(AltocueError) as caught:
        grade.grade_project(project, look="sepia")
    assert "natural" in caught.value.fix


def test_grade_project_bakes_one_cube_per_clip_and_reports(tmp_path):
    good = write_clip(tmp_path / "a.mp4", [scene(wall=(150, 150, 150))] * 60)
    shifted = write_clip(tmp_path / "b.mp4", [scene(gains=(1.0, 1.0, 0.8), wall=(150, 150, 150))] * 30)
    project = make_project(tmp_path, [good, shifted])
    report = grade.grade_project(project)

    assert report["reference"] == "a" and report["look"] == "natural"
    assert (project.lut_dir / "a.cube").exists() and (project.lut_dir / "b.cube").exists()
    assert "LUT_3D_SIZE 33" in (project.lut_dir / "b.cube").read_text()
    saved = json.loads((project.lut_dir / "report.json").read_text())
    assert saved == report
    entry = report["clips"]["b"]
    assert {"before", "after", "changes", "confidence", "lut"} <= set(entry)
    assert abs(entry["after"]["rest_b"]) < abs(entry["before"]["rest_b"])
    assert entry["changes"]["gains"][2] > 1


def test_the_baked_cube_reaches_the_render(tmp_path):
    shifted = write_clip(tmp_path / "b.mp4", [scene(gains=(1.0, 1.0, 0.8))] * 30)
    project = make_project(tmp_path, [shifted])
    grade.grade_project(project)
    clip = project.clip("b")
    done = render(shifted, grade.video_filter(clip, project.lut_dir / "b.cube"), tmp_path / "out.mp4")
    before, after = first_frame_rgb(shifted, 640, 480), first_frame_rgb(done, 640, 480)
    assert after[20, 20, 2] > before[20, 20, 2] + 0.005
    tags = clip_tags(done)
    assert tags["color_transfer"] == "bt709" and tags["pix_fmt"] == "yuv420p"


def test_grading_is_deterministic_and_one_lut_per_clip(tmp_path):
    source = write_clip(tmp_path / "a.mp4", [scene(gains=(1.0, 0.98, 0.9))] * 30)
    one = make_project(tmp_path, [source], "one")
    two = make_project(tmp_path, [source], "two")
    grade.grade_project(one)
    grade.grade_project(two)
    assert (one.lut_dir / "a.cube").read_bytes() == (two.lut_dir / "a.cube").read_bytes()


def test_grade_is_cached_until_forced(tmp_path):
    project = make_project(tmp_path, [write_clip(tmp_path / "a.mp4", [scene()] * 30)])
    grade.grade_project(project)
    cube = project.lut_dir / "a.cube"
    stamp = cube.stat().st_mtime_ns
    grade.grade_project(project)
    assert cube.stat().st_mtime_ns == stamp
    grade.grade_project(project, force=True)
    assert cube.stat().st_mtime_ns != stamp


def test_look_comes_from_config_and_the_flag_wins(tmp_path, capsys):
    project = make_project(tmp_path, [write_clip(tmp_path / "a.mp4", [scene()] * 30)])
    text = (project.root / "altocue.toml").read_text().replace('look = "natural"', 'look = "warm"')
    (project.root / "altocue.toml").write_text(text)
    assert grade.grade_project(Project.open(project.root))["look"] == "warm"
    assert main(["grade", "--project", str(project.root), "--look", "cool"]) == 0
    assert json.loads((project.lut_dir / "report.json").read_text())["look"] == "cool"
    assert "a" in capsys.readouterr().out


def test_disabled_grade_still_converts_hdr(tmp_path):
    source = write_hdr(tmp_path / "hdr.mkv", bars_hdr())
    project = make_project(tmp_path, [source])
    project.config["grade"] = {"enabled": False}
    report = grade.grade_project(project)
    assert report["clips"]["hdr"]["changes"]["hdr"] == "arib-std-b67"
    assert (project.lut_dir / "hdr.cube").exists()


def test_hdr_clip_grades_end_to_end(tmp_path):
    frame = face_image(640, 480, ((320, 230, 95),), (150, 150, 150)).astype(np.float64) / 255
    # Lift the SDR picture into HLG so the face must survive the same maths on the way back.
    coded = hlg_encode(frame**2.4 * 0.6)
    source = write_hdr(tmp_path / "phone.mkv", coded, frames=20)
    project = make_project(tmp_path, [source])
    report = grade.grade_project(project)
    entry = report["clips"]["phone"]
    assert entry["changes"]["hdr"] == "arib-std-b67"
    assert entry["faces"]["rate"] > 0.5
    done = render(source, grade.video_filter(project.clip("phone"), project.lut_dir / "phone.cube"), tmp_path / "out.mp4")
    assert clip_tags(done)["color_primaries"] == "bt709"
    assert first_frame_rgb(done, 640, 480)[20, 20].mean() > 0.45


def test_pq_clip_converts(tmp_path):
    m1, m2, c1, c2, c3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
    nits = 60.0 / 10000
    grey = ((c1 + c2 * nits**m1) / (1 + c3 * nits**m1)) ** m2
    source = write_hdr(tmp_path / "pq.mkv", np.full((120, 160, 3), grey), transfer="smpte2084")
    clip = probe_clip(source, clip_id="pq")
    assert clip.is_hdr
    lut = grade.write_cube(tmp_path / "pq.cube", grade.hdr_lut("smpte2084"), "pq")
    done = render(source, grade.video_filter(clip, lut), tmp_path / "out.mp4")
    assert first_frame_rgb(done, 160, 120).mean() == pytest.approx((60 / grade.PQ_WHITE_NITS) ** (1 / 2.4), abs=0.03)


def test_samples_come_from_what_survives_the_cut():
    clip = Clip(id="x", path="x", duration=60, fps=30, width=10, height=10)
    times = grade.sample_times(clip, [(10.0, 12.0), (40.0, 41.0)])
    assert len(times) == grade.SAMPLES
    assert all(10 <= t <= 12 or 40 <= t <= 41 for t in times)
    assert all(0 <= t <= 60 for t in grade.sample_times(clip, None))


def test_missing_source_is_explained(tmp_path):
    source = write_clip(tmp_path / "a.mp4", [scene()] * 15)
    project = make_project(tmp_path, [source])
    source.unlink()
    with pytest.raises(AltocueError) as caught:
        grade.grade_project(project)
    assert "altocue ingest" in caught.value.fix


def test_framing_reads_hdr_through_the_same_maths(tmp_path):
    picture = face_image(1280, 720, ((360, 330, 150),), (150, 150, 150)).astype(np.float64) / 255
    source = write_hdr(tmp_path / "wide.mkv", hlg_encode(picture**2.4 * 0.6), frames=30)
    project = make_project(tmp_path, [source])
    project.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="wide", start=0, end=0.9)]))
    x, _, w, _ = frame_project(project).segments[0].crop
    assert x < 360 < x + w and abs(x + w / 2 - 360) < 40
