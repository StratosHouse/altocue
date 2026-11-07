"""QA reads one graphics frame by seeking to it, and gets the same frame a full decode would."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from altocue import ffmpeg
from altocue.qa.gates import layer_frame


def test_a_seeked_layer_frame_is_the_frame_at_that_time(tmp_path: Path, monkeypatch):
    layers = tmp_path / "layers"
    layers.mkdir()
    movie = layers / "overlay.mov"
    # four seconds whose colour changes every frame, with transparency, in the codec HyperFrames writes
    source = "nullsrc=s=96x96:r=30:d=4,geq=r='mod(N*7,256)':g='mod(N*13,256)':b='mod(N*29,256)':a='128'"
    prores = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, *prores, str(movie)], check=True)
    seen: list[list[str]] = []
    real = ffmpeg.run
    monkeypatch.setattr(ffmpeg, "run", lambda args, **kw: (seen.append(args), real(args, **kw))[1])
    context = SimpleNamespace(project=SimpleNamespace(layers_dir=layers), stage="final")
    got = tmp_path / "got.png"
    assert layer_frame(context, 2.5, got)
    assert seen[0].index("-ss") < seen[0].index("-i")  # it seeks before it opens the file
    want = tmp_path / "want.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(movie), "-ss", "2.500", "-frames:v", "1", "-pix_fmt", "rgba", str(want)], check=True)
    assert np.array_equal(np.asarray(Image.open(got)), np.asarray(Image.open(want)))
