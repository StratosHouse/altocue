"""The picture encoder: x264 by default, NVENC on a machine with an NVIDIA GPU when asked."""

from __future__ import annotations

from pathlib import Path

import pytest

from altocue import ffmpeg, plate
from altocue.errors import AltocueError

DELIVERY = plate.Canvas(1080, 1920, 60, 16, "medium")
DRAFT = plate.Canvas(540, 960, 60, 26, "veryfast")


def value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


@pytest.fixture
def gpu(monkeypatch):
    def has(works: bool) -> None:
        monkeypatch.setattr(ffmpeg, "nvenc_works", lambda: works)

    return has


def test_x264_is_the_default(monkeypatch, gpu):
    monkeypatch.delenv("ALTOCUE_ENCODER", raising=False)
    gpu(True)  # a GPU alone changes nothing
    args = plate.encode_args(DELIVERY)
    assert value(args, "-c:v") == "libx264" and value(args, "-crf") == "16" and value(args, "-preset") == "medium"


def test_nvenc_keeps_the_canvas_quality_and_colour(monkeypatch, gpu):
    monkeypatch.setenv("ALTOCUE_ENCODER", "nvenc")
    gpu(True)
    args = plate.encode_args(DELIVERY)
    assert value(args, "-c:v") == "h264_nvenc" and value(args, "-cq") == "16" and value(args, "-preset") == "p7"
    assert value(args, "-rc") == "vbr" and value(args, "-b:v") == "0" and value(args, "-pix_fmt") == "yuv420p"
    assert value(args, "-spatial-aq") == "1" and value(args, "-temporal-aq") == "1"
    assert value(args, "-colorspace") == "bt709" and value(args, "-color_range") == "tv"
    assert value(plate.encode_args(DRAFT), "-preset") == "p4"  # a draft stays quick


def test_auto_falls_back_to_x264_without_a_gpu(monkeypatch, gpu):
    monkeypatch.setenv("ALTOCUE_ENCODER", "auto")
    gpu(False)
    assert value(plate.encode_args(DELIVERY), "-c:v") == "libx264"
    gpu(True)
    assert value(plate.encode_args(DELIVERY), "-c:v") == "h264_nvenc"


def test_asking_for_nvenc_without_a_gpu_is_refused(monkeypatch, gpu):
    monkeypatch.setenv("ALTOCUE_ENCODER", "nvenc")
    gpu(False)
    with pytest.raises(AltocueError, match="cannot encode on an NVIDIA GPU"):
        plate.encode_args(DELIVERY)


def test_an_unknown_encoder_is_refused(monkeypatch):
    monkeypatch.setenv("ALTOCUE_ENCODER", "quicksync")
    with pytest.raises(AltocueError, match="x264, nvenc or auto"):
        ffmpeg.video_encoder()


def test_switching_encoder_rebuilds_the_plate(monkeypatch, gpu, tmp_path):
    """Plate pieces are joined by stream copy, so a cached x264 piece is never reused in an NVENC plate."""
    from altocue import grade
    from altocue.models import Clip, Segment

    source = tmp_path / "a.mov"
    source.write_bytes(b"take")
    take = Clip(id="a", path=str(source), duration=10, fps=30, width=1920, height=1080, rotation=0)
    cut = Segment(id="s001", clip="a", start=1.0, end=2.0)
    encoded: list[str] = []

    def fake_run(args, **_):
        encoded.append(args[args.index("-c:v") + 1])
        Path(args[-1]).write_bytes(b"frames")

    monkeypatch.setattr(grade, "clip_lut", lambda *_: None)
    monkeypatch.setattr(ffmpeg, "run", fake_run)
    gpu(True)
    monkeypatch.setenv("ALTOCUE_ENCODER", "x264")
    first = plate.build_segment(None, take, cut, DELIVERY, tmp_path)
    assert plate.build_segment(None, take, cut, DELIVERY, tmp_path) == first and encoded == ["libx264"]  # cached
    monkeypatch.setenv("ALTOCUE_ENCODER", "nvenc")
    second = plate.build_segment(None, take, cut, DELIVERY, tmp_path)
    assert second != first and encoded == ["libx264", "h264_nvenc"]


def test_frames_too_small_for_nvenc_stay_on_x264(monkeypatch, gpu):
    monkeypatch.setenv("ALTOCUE_ENCODER", "nvenc")
    gpu(True)
    assert value(plate.encode_args(plate.Canvas(96, 96, 30, 16, "veryfast")), "-c:v") == "libx264"
