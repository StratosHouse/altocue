"""Fixtures are generated at test time, so the repo holds no media."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def make_clip(path: Path, *, seconds: float = 2.0, size: str = "320x568", rate: int = 30, audio: bool = True) -> Path:
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac"]
    subprocess.run([*args, str(path)], check=True)
    return path


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    return make_clip(tmp_path / "take one.mp4")
