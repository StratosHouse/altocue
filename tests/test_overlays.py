"""Overlays are made in code and drawn under the captions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from altocue import overlays, render, timeline
from altocue.errors import AltocueError
from altocue.motion import Layer
from altocue.plate import Canvas, encode_args
from altocue.project import Project

C = Canvas(180, 320, 30, 18, "veryfast")


def test_one_overlay_or_a_list_is_read():
    assert overlays.parse({"type": "grain", "amount": 0.2}) == [{"type": "grain", "amount": 0.2}]
    assert [o["type"] for o in overlays.parse([{"type": "grain"}, {"type": "vignette"}])] == ["grain", "vignette"]
    assert overlays.parse(None) == []


def test_a_type_alone_takes_a_sensible_amount():
    assert overlays.parse({"type": "dust"})[0]["amount"] == overlays.DEFAULT_AMOUNT


@pytest.mark.parametrize("raw", [{"type": "lens_flare"}, {"type": "grain", "amount": 2}, {"amount": 0.2}, "grain"])
def test_what_cannot_be_drawn_is_refused_with_a_fix(raw):
    with pytest.raises(AltocueError) as caught:
        overlays.parse(raw)
    assert caught.value.fix


def test_an_overlay_can_follow_the_words():
    words = [{"text": "now", "start": 1.5}, {"text": "done", "start": 3.0}]
    found = timeline.resolve_overlays({"type": "light_leak", "start": {"word": "now"}, "end": {"word": "done"}}, words)
    assert (found[0]["start"], found[0]["end"]) == (1.5, 3.0)


def plain_clip(path: Path, frames: int = 30, colour: str = "0x808080") -> Path:
    args = ["-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=180x320:r=30:d={frames / 30}"]
    subprocess.run(["ffmpeg", *args, *encode_args(C), str(path)], check=True)
    return path


def drawn(tmp_path: Path, raw: dict, *, colour: str = "0x808080") -> np.ndarray:
    """Run one overlay through the same graph the render uses, and read the frames back."""
    tmp_path.mkdir(exist_ok=True)
    project = Project.create(tmp_path / "video")
    base = plain_clip(tmp_path / "base.mp4", colour=colour)
    items = overlays.prepare(project, overlays.parse(raw), C)
    inputs, graph, label = overlays.graph(items, "b", C, 1)
    filters = ";".join(["[0:v]null[b]", *graph])
    dest = tmp_path / "drawn.mp4"
    args = ["-i", str(base), *inputs, "-filter_complex", filters, "-map", f"[{label}]", "-frames:v", "30"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, *encode_args(C), str(dest)], check=True)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, 320, 180).astype(float)


def test_grain_moves_every_frame_and_leaves_the_level_alone(tmp_path):
    frames = drawn(tmp_path, {"type": "grain", "amount": 0.4})
    plain = drawn(tmp_path / "plain", {"type": "grain", "amount": 0.0})
    assert len(frames) == 30
    assert abs(frames.mean() - plain.mean()) < 1.5
    assert frames[0].std() > 1 and np.abs(frames[0] - frames[1]).mean() > 0.5


def test_amount_zero_changes_nothing(tmp_path):
    frames = drawn(tmp_path, {"type": "grain", "amount": 0.0})
    assert frames.std() < 0.6


def test_a_vignette_darkens_the_corners_not_the_middle(tmp_path):
    frame = drawn(tmp_path, {"type": "vignette", "amount": 0.6})[10]
    assert frame[4:20, 4:20].mean() < frame[150:170, 80:100].mean() - 12


def test_a_light_leak_adds_warm_light_at_an_edge(tmp_path):
    frame = drawn(tmp_path, {"type": "light_leak", "amount": 0.7}, colour="0x303030")[10]
    assert frame.max() > frame.min() + 12


def test_dust_is_specks_on_a_clean_frame(tmp_path):
    frames = drawn(tmp_path, {"type": "dust", "amount": 0.6})
    off = np.abs(frames - np.median(frames))
    assert 0 < (off > 12).mean() < 0.02


def test_paper_is_the_same_on_every_frame(tmp_path):
    frames = drawn(tmp_path, {"type": "paper", "amount": 0.6})
    assert frames[0].std() > 1 and np.abs(frames[0] - frames[20]).mean() < 0.6


def test_plates_are_made_once(tmp_path):
    project = Project.create(tmp_path / "video")
    first = overlays.prepare(project, overlays.parse({"type": "paper"}), C)[0]["plate"]
    stamp = Path(first).stat().st_mtime_ns
    assert overlays.prepare(project, overlays.parse({"type": "paper"}), C)[0]["plate"] == first
    assert Path(first).stat().st_mtime_ns == stamp


def test_overlays_sit_under_the_graphics_in_the_composite(tmp_path):
    project = Project.create(tmp_path / "video")
    layer = Layer(path=tmp_path / "layer.mov", renderer="hyperframes")
    items = overlays.prepare(project, overlays.parse({"type": "grain", "amount": 0.2}), C)
    plan = {"tail": 0, "inserts": [], "total": 2.0, "overlay": items}
    args = render.compose_args(project, tmp_path / "p.mp4", tmp_path / "a.wav", layer, plan, C, tmp_path / "o.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert graph.index("hardlight") < graph.index("[gfx]overlay")


def test_an_insert_with_edges_is_one_clip_with_the_plate_blended_in(tmp_path):
    project = Project.create(tmp_path / "video")
    picture = plain_clip(tmp_path / "picture.mp4", 60, "0x202020")
    plain_clip(project.root / "shot.mp4", 60, "0xe0e0e0")
    item = {"file": "shot.mp4", "start": 0.5, "end": 1.5, "in": {"type": "dissolve", "duration": 0.2}, "out": {"type": "dissolve", "duration": 0.2}}
    baked = render.bake_insert(project, item, picture, C, project.work)
    read = ["-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", baked, *read], capture_output=True, check=True).stdout
    levels = np.frombuffer(out, np.uint8).astype(float)
    assert len(levels) == 30
    assert levels[0] < 60 and levels[15] > 200 and levels[-1] < 80  # plate at both edges, shot in the middle
    assert (np.diff(levels[:6]) >= -1).all()  # and it rises through the dissolve


def test_an_insert_too_short_for_its_edges_is_refused(tmp_path):
    project = Project.create(tmp_path / "video")
    picture = plain_clip(tmp_path / "picture.mp4")
    plain_clip(project.root / "shot.mp4")
    item = {"file": "shot.mp4", "start": 0.0, "end": 0.2, "in": {"type": "dissolve", "duration": 0.2}, "out": {"type": "dissolve", "duration": 0.2}}
    with pytest.raises(AltocueError, match="too short"):
        render.bake_insert(project, item, picture, C, project.work)
