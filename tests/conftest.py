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


@pytest.fixture(autouse=True)
def no_own_sound_packs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pack registered on this machine never leaks into a test."""
    from altocue.stock import packs

    monkeypatch.setattr(packs, "REGISTRY", tmp_path_factory.mktemp("home") / "packs.json")


@pytest.fixture(autouse=True)
def no_own_profile(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answers this machine's owner gave `altocue start` never leak into a test."""
    monkeypatch.setenv("ALTOCUE_HOME", str(tmp_path_factory.mktemp("altocue-home")))


@pytest.fixture(autouse=True)
def house_brand(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real project reads its brand kit before any asset. Tests start on the house look, chosen, and test_brand_kit tests the gate."""
    from altocue import project

    monkeypatch.setattr(project, "DEFAULT_CONFIG", project.DEFAULT_CONFIG.replace('name = ""               #', 'name = "default"        #', 1))


@pytest.fixture(autouse=True)
def show_tracebacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure nobody planned for raises through `main`, so a failing test shows where. test_core tests the three plain lines a user sees."""
    monkeypatch.setenv("ALTOCUE_TRACEBACK", "1")
