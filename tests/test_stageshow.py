"""Stageshow inserts: found where the skills CLI puts it, refused clearly when it is not ready, filmed frame-exact when it is."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from altocue import stageshow
from altocue.cli import main
from altocue.errors import AltocueError


def fake_stageshow(root: Path, *, installed: bool = True) -> Path:
    folder = root / "stageshow"
    (folder / "kit").mkdir(parents=True)
    (folder / "kit" / "clock.ts").write_text("export function installVirtualClock() {}\n")
    (folder / "package.json").write_text("{}")
    if installed:
        for name in stageshow.NEEDS:
            (folder / "node_modules" / name).mkdir(parents=True)
    return folder


def test_it_is_found_where_the_skills_cli_installs_it(tmp_path, monkeypatch):
    monkeypatch.delenv("ALTOCUE_STAGESHOW", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = fake_stageshow(tmp_path / ".claude" / "skills")
    assert stageshow.locate(None) == folder.resolve()


def test_the_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake_stageshow(tmp_path / ".claude" / "skills")
    mine = fake_stageshow(tmp_path / "elsewhere")
    monkeypatch.setenv("ALTOCUE_STAGESHOW", str(mine))
    assert stageshow.locate(None) == mine.resolve()


def test_a_missing_install_says_how_to_install(tmp_path, monkeypatch):
    monkeypatch.delenv("ALTOCUE_STAGESHOW", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(AltocueError) as caught:
        stageshow.locate(None)
    assert "benjitaylor/skills" in caught.value.fix


def test_a_folder_without_npm_install_is_named(tmp_path):
    folder = fake_stageshow(tmp_path, installed=False)
    found = stageshow.problems(folder, chrome="/bin/true")
    assert any("npm install" in line for line in found)


def test_names_become_file_names():
    assert stageshow.slug("http://127.0.0.1:5199/?capture=Hero Demo") == "http-127-0-0-1-5199-capture-hero-demo"
    assert stageshow.slug("***") == "stage"


def test_record_and_doctor_are_registered(capsys):
    with pytest.raises(SystemExit):
        main(["stageshow", "--help"])
    assert "record" in capsys.readouterr().out


def test_the_recorder_ships_with_the_package():
    assert stageshow.RECORDER.exists()
    text = stageshow.RECORDER.read_text()
    assert "installVirtualClock" in text and "step(" in text


REAL = os.environ.get("ALTOCUE_STAGESHOW")


@pytest.mark.skipif(not (REAL and shutil.which("node") and shutil.which("ffmpeg")), reason="needs a real Stageshow install in ALTOCUE_STAGESHOW")
def test_a_real_stage_films_the_same_frames_twice(tmp_path):
    """Frame-exact means two runs give the same pictures, and the stage really moves between frames."""
    outs = []
    for run in ("a", "b"):
        out = tmp_path / f"{run}.mp4"
        assert main(["stageshow", "record", "--example", "payout-flow", "--seconds", "2", "--fps", "15", "--scale", "1", "--out", str(out)]) == 0
        outs.append(out)
        meta = json.loads(out.with_suffix(".json").read_text())
        assert meta["illustrative"] and meta["frames"] == 30
    # Chrome may dither a blurred gradient by a level or two between runs. Motion is what must not drift.
    first, second = (frames_of(out) for out in outs)
    assert first.shape == second.shape and first.shape[0] == 30
    assert int(np.abs(first.astype(int) - second.astype(int)).max()) <= 6
    moved = [float(np.abs(first[i + 1].astype(int) - first[i].astype(int)).mean()) for i in range(len(first) - 1)]
    assert sum(step > 0.05 for step in moved) > 5


def frames_of(path: Path) -> np.ndarray:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    width, height = (int(v) for v in probe.stdout.strip().split(","))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width)
