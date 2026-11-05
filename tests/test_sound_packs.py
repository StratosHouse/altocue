"""Your own licensed sound packs: registered once, copied into one project privately, and never shipped."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from altocue import cli, ffmpeg
from altocue.errors import AltocueError
from altocue.project import Project
from altocue.stock import credits, packs, sfx

RATE = 48000


def sound(path: Path, seconds: float = 0.5, level: float = 0.3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(round(seconds * RATE)) / RATE
    return ffmpeg.write_wav(path, level * np.sin(2 * np.pi * 900 * t) * np.exp(-t * 4), RATE)


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    root = tmp_path / "Vector"
    sound(root / "Keyboard 01.wav", seconds=1.2)
    sound(root / "UI" / "Soft Hit 02.wav")
    sound(root / "Whoosh" / "Soft Hit 02.wav")
    (root / "readme.txt").write_text("not a sound")
    return root


def run(capsys, *argv: str) -> str:
    assert cli.main(["sfx", *argv]) == 0
    return capsys.readouterr().out


def test_a_pack_is_registered_by_name_and_counts_its_sounds(pack, capsys):
    out = run(capsys, "pack", "Vector", str(pack), "--licence", "Bought from the maker, single user")
    assert "Pack vector: 3 sounds" in out
    assert packs.packs()["vector"] == {"folder": str(pack.resolve()), "licence": "Bought from the maker, single user"}
    listed = run(capsys, "packs")
    assert "vector" in listed and "3 sounds" in listed


def test_two_sounds_with_one_name_keep_their_folder(pack):
    assert {"keyboard-01", "soft-hit-02", "whoosh-soft-hit-02"} == set(packs.sounds(pack))


def test_a_pack_needs_a_folder_with_sounds_and_a_licence(tmp_path, pack):
    with pytest.raises(AltocueError, match="No folder"):
        packs.add("vector", tmp_path / "missing", "mine")
    (tmp_path / "empty").mkdir()
    with pytest.raises(AltocueError, match="No sound files"):
        packs.add("vector", tmp_path / "empty", "mine")
    with pytest.raises(AltocueError, match="licence"):
        packs.add("vector", pack, " ")


def test_search_finds_pack_sounds_by_word(pack, capsys):
    packs.add("vector", pack, "mine")
    out = run(capsys, "search", "keyboard")
    assert "vector:keyboard-01" in out and "private" in out


def test_get_copies_into_a_private_ignored_folder_and_records_the_licence(pack, tmp_path, capsys):
    project = Project.create(tmp_path / "video")
    packs.add("vector", pack, "Bought from the maker, single user")
    run(capsys, "get", "vector:keyboard-01", "-p", str(project.root))
    copied = project.root / "sfx" / "private" / "vector" / "keyboard-01.wav"
    assert copied.read_bytes() == (pack / "Keyboard 01.wav").read_bytes()
    assert "*" in (project.root / "sfx" / "private" / ".gitignore").read_text().splitlines()
    row = credits.records(project)[0]
    assert row["licensed"] and row["source"] == "pack:vector" and row["sha256"]
    assert not credits.owes(row)


def test_a_missing_pack_sound_suggests_close_names(pack, tmp_path):
    packs.add("vector", pack, "mine")
    with pytest.raises(AltocueError, match="no sound named") as caught:
        sfx.fetch(Project.create(tmp_path / "video"), "vector:keyboard")
    assert "keyboard-01" in caught.value.fix


def test_removing_a_pack_deletes_no_file(pack):
    packs.add("vector", pack, "mine")
    packs.remove("vector")
    assert packs.packs() == {} and (pack / "Keyboard 01.wav").exists()


def test_a_freesound_id_is_not_mistaken_for_a_pack(pack):
    packs.add("vector", pack, "mine")
    assert packs.find("freesound:2") is None and packs.find("whoosh3") is None


def test_credits_name_a_licensed_sound_as_private(pack, tmp_path, capsys):
    project = Project.create(tmp_path / "video")
    packs.add("vector", pack, "Bought from the maker, single user")
    relative = sfx.fetch(project, "vector:keyboard-01")
    (project.root / "motion.json").write_text(f'{{"sfx": [{{"file": "{relative}", "at": 1.0}}]}}')
    assert cli.main(["credits", "-p", str(project.root)]) == 0
    err = capsys.readouterr().err
    assert "Licensed to you, keep private: keyboard-01" in err and "Bought from the maker" in err
