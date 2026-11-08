"""Motion tracking: a box and a face followed through the film, and the layers that ride on them."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from test_frame import face_image, write_clip

from altocue import brand as brands
from altocue import plate, track
from altocue.errors import AltocueError
from altocue.project import Project


def textured(width: int = 320, height: int = 240, left: int = 40, top: int = 90, side: int = 48) -> np.ndarray:
    image = np.full((height, width, 3), 120, np.uint8)
    rng = np.random.default_rng(7)
    image[top : top + side, left : left + side] = rng.integers(0, 255, (side, side, 1), dtype=np.uint8)
    return image


def test_items_are_checked_before_anything_runs():
    with pytest.raises(AltocueError):
        track.parse([{"type": "smear"}])
    with pytest.raises(AltocueError):
        track.parse([{"type": "blur", "target": {"box": [0.2, 0.2, 1.4, 0.1]}}])
    with pytest.raises(AltocueError):
        track.parse([{"type": "label", "target": "face"}])
    assert track.parse([{"type": "blur"}])[0]["target"] == "face"


def test_a_box_is_followed_both_ways_from_where_it_was_drawn():
    frames = [textured(left=40 + 4 * i) for i in range(30)]
    boxes = track.follow_box(frames, 10, (80.0, 90.0, 48.0, 48.0))
    assert boxes[10] == (80.0, 90.0, 48.0, 48.0)
    assert abs(boxes[-1][0] - (40 + 4 * 29)) < 4
    assert abs(boxes[0][0] - 40) < 4


def test_a_face_is_followed_and_kept_on_the_same_person():
    frames = [face_image(640, 360, ((int(150 + 10 * i), 180, 70), (560, 180, 40))) for i in range(12)]
    boxes = track.follow_face(frames)
    centres = [b[0] + b[2] / 2 for b in boxes if b]
    assert len(centres) == 12 and centres[-1] - centres[0] > 80


def test_short_gaps_fill_and_long_ones_stay_empty():
    box = (10.0, 10.0, 20.0, 20.0)
    filled = track.fill_and_smooth([box, None, None, box] + [None] * 40 + [box], 30)
    assert filled[1] is not None and filled[2] is not None
    assert filled[20] is None


def test_art_stays_inside_the_frame():
    assert track.placed((0, 0, 40, 40), (100, 30), "above", (320, 240)) == (0, 0)
    x, y = track.placed((100, 100, 40, 40), (60, 20), "below", (320, 240))
    assert y > 140 and 80 <= x <= 100


def film(tmp_path: Path) -> Path:
    frames = [face_image(270, 480, ((int(80 + 3 * i), 200, 50),)) for i in range(36)]
    return write_clip(tmp_path / "picture.mp4", frames)


def compose(picture: Path, items: list[dict], dest: Path) -> np.ndarray:
    c = plate.Canvas(270, 480, 30, 18, "veryfast")
    extra, lines, base = track.graph(items, "base", c, 1)
    graph = ";".join(["[0:v]null[base]", *lines, f"[{base}]format=yuv420p[video]"])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(picture), *extra, "-filter_complex", graph, "-map", "[video]", str(dest)], check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(dest), "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 480, 270)


def detail(image: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    patch = image[y : y + h, x : x + w].astype(float)
    return float(np.abs(np.diff(patch, axis=1)).mean())


def test_a_tracked_blur_hides_the_face_only_while_it_runs(tmp_path):
    picture = film(tmp_path)
    project = Project.create(tmp_path / "video")
    items = [{"type": "blur", "target": "face", "start": 0.4, "end": 1.0, "amount": 0.8}]
    ready = track.prepare(project, track.parse(items), picture, (270, 480), 30, brands.load(project))
    assert Path(ready[0]["layer"]).exists()
    before = compose(picture, [], tmp_path / "plain.mp4")
    after = compose(picture, ready, tmp_path / "blurred.mp4")
    face_at = (int(80 + 3 * 21) - 30, 160, 60, 70)
    assert detail(after[21], face_at) < 0.6 * detail(before[21], face_at)
    assert abs(detail(after[3], (int(80 + 9) - 30, 160, 60, 70)) - detail(before[3], (int(80 + 9) - 30, 160, 60, 70))) < 0.5
    assert np.abs(after[21][400:460, 200:260].astype(int) - before[21][400:460, 200:260].astype(int)).max() < 6


def test_a_label_rides_with_the_face(tmp_path):
    picture = film(tmp_path)
    project = Project.create(tmp_path / "video")
    items = [{"type": "label", "text": "Dr Example", "target": "face", "anchor": "below", "start": 0.0, "end": 1.2}]
    ready = track.prepare(project, track.parse(items), picture, (270, 480), 30, brands.load(project))
    before = compose(picture, [], tmp_path / "plain.mp4")
    after = compose(picture, ready, tmp_path / "label.mp4")
    changed = [np.argwhere(np.abs(after[i].astype(int) - before[i].astype(int)) > 40) for i in (2, 30)]
    assert all(len(rows) > 50 for rows in changed)
    assert all(rows[:, 0].min() > 230 for rows in changed)  # below the face, not over it
    assert changed[1][:, 1].mean() > changed[0][:, 1].mean() + 40  # it moved with them


def test_a_cached_layer_is_reused(tmp_path):
    picture = film(tmp_path)
    project = Project.create(tmp_path / "video")
    items = track.parse([{"type": "spotlight", "target": "face", "start": 0.0, "end": 1.0}])
    first = track.prepare(project, items, picture, (270, 480), 30, brands.load(project))
    stamp = Path(first[0]["layer"]).stat().st_mtime_ns
    again = track.prepare(project, items, picture, (270, 480), 30, brands.load(project))
    assert again[0]["layer"] == first[0]["layer"] and Path(again[0]["layer"]).stat().st_mtime_ns == stamp


def test_the_composite_reads_each_tracked_layer_from_its_own_input(tmp_path):
    from altocue import render
    from altocue.motion.layer import Layer

    project = Project.create(tmp_path / "video")
    c = plate.Canvas(270, 480, 30, 18, "veryfast")
    tracked = [
        {"type": "blur", "amount": 0.6, "start": 0.5, "end": 1.0, "layer": "blur.mkv"},
        {"type": "label", "amount": 0.6, "start": 0.2, "end": 1.5, "layer": "label.mkv"},
    ]
    overlay = [{"type": "light_leak", "amount": 0.2, "plate": "leak.mkv"}]
    plan = {"tail": 0, "inserts": [], "total": 2.0, "overlay": overlay, "tracked": tracked}
    args = render.compose_args(project, tmp_path / "p.mp4", tmp_path / "a.wav", Layer(path=tmp_path / "gfx.mov", renderer="hyperframes"), plan, c, tmp_path / "o.mp4")
    files = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    graph = args[args.index("-filter_complex") + 1]
    assert files[2:5] == ["blur.mkv", "label.mkv", "leak.mkv"]
    assert "[2:v]format=gray" in graph and "[3:v]format=yuva444p" in graph and "[4:v]scale" in graph
    assert graph.index("tracked1") < graph.index("[gfx]overlay")
