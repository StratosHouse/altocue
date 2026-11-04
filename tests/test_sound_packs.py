"""Your own licensed sound packs: registered once, copied into one project privately, never shipped, and typed one key at a time."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import cli, ffmpeg, mix
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
    assert row["licensed"] and row["private"] and row["source"] == "pack:vector" and row["sha256"]
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


def test_typing_expands_to_one_short_key_per_character():
    hits = mix.keystrokes({"file": "k.wav", "at": 2.0, "keys": {"count": 4, "every": 0.07}, "under_db": 9})
    assert [round(hit["at"], 2) for hit in hits] == [2.0, 2.07, 2.14, 2.21]
    assert all(hit["max_ms"] == mix.KEY_MS and hit["min_gap_ms"] == 0 and "keys" not in hit for hit in hits)
    exact = mix.keystrokes({"file": "k.wav", "at": 1.0, "keys": {"at": [0.3, 0, 0.1]}, "max_ms": 60})
    assert [hit["at"] for hit in exact] == [1.0, 1.1, 1.3] and exact[0]["max_ms"] == 60


@pytest.mark.parametrize("keys", [{"count": 3, "every": 0.005}, {"count": 0, "every": 0.1}, {"at": [-0.1]}, [1, 2]])
def test_typing_that_cannot_be_heard_as_keys_is_refused(keys):
    with pytest.raises(AltocueError):
        mix.keystrokes({"file": "k.wav", "at": 1.0, "keys": keys})


def test_each_key_is_heard_where_it_lands_and_stops_short(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", np.r_[0.1 * np.sin(np.arange(RATE) * 0.05), np.zeros(3 * RATE)], RATE)
    typing = sound(tmp_path / "typing.wav", seconds=1.5, level=0.5)
    keys = [0.0, 0.25, 0.5]
    out = mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"file": str(typing), "at": 1.5, "keys": {"at": keys}}])
    with wave.open(str(out), "rb") as handle:
        left = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0]
    for offset in keys:
        at = round((1.5 + offset) * RATE)
        assert np.abs(left[at : at + 2400]).max() > 300
        assert np.abs(left[at + round(0.12 * RATE) : at + round(0.2 * RATE)]).max() == 0  # the key ended, so the long sound did not ring on
    report = json.loads(out.with_suffix(".json").read_text())
    assert len(report["sfx"]) == 3 and not report["skipped"]
