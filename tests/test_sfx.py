from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import ffmpeg, mix, render
from altocue.errors import AltocueError
from altocue.motion import formats
from altocue.timeline import resolve_sfx

RATE = 48000
WORDS = [{"text": "Next,", "start": 1.0, "end": 1.4}, {"text": "next", "start": 2.0, "end": 2.4}, {"text": "done.", "start": 5.0, "end": 5.4}]


def resolved(raw, root: Path | None = None) -> tuple[bool, list[dict]]:
    return resolve_sfx(raw, WORDS, 8.0, root or Path("."))


@pytest.mark.parametrize(("raw", "house"), [(True, True), (None, True), (False, False)])
def test_a_plain_flag_still_turns_the_house_sounds_on_and_off(raw, house):
    assert resolved(raw) == (house, [])


def test_a_list_adds_cues_to_the_house_sounds():
    house, cues = resolved([{"name": "whoosh", "at": {"word": "next"}}])
    assert house is True and cues == [{"name": "whoosh", "at": 1.0}]


def test_cues_follow_an_occurrence_and_an_offset():
    _, cues = resolved([{"name": "ding", "at": {"word": "next", "occurrence": 2, "offset": 0.1}}])
    assert cues[0]["at"] == pytest.approx(2.1)


def test_cues_can_sit_at_a_time_in_seconds():
    assert resolved([{"name": "tick", "at": 3.5}])[1][0]["at"] == 3.5


def test_house_sounds_can_go_while_cues_stay():
    house, cues = resolved({"house": False, "cues": [{"name": "thud", "at": 1.0}]})
    assert house is False and [c["name"] for c in cues] == ["thud"]


def test_a_cue_can_carry_its_level():
    assert resolved([{"name": "rise", "at": 1.0, "under_db": 12}])[1][0]["under_db"] == 12


@pytest.mark.parametrize("name", mix.sfx_names())
def test_every_bundled_effect_can_be_called(name):
    assert resolved([{"name": name, "at": {"word": "done."}}])[1][0]["name"] == name



def test_a_project_file_can_be_a_cue(tmp_path):
    sound = tmp_path / "sounds" / "door.wav"
    sound.parent.mkdir()
    ffmpeg.write_wav(sound, np.zeros(480), RATE)
    assert resolved([{"file": "sounds/door.wav", "at": 1.0}], tmp_path)[1][0]["file"] == str(sound)


def test_a_missing_project_file_is_refused_with_the_path(tmp_path):
    with pytest.raises(AltocueError, match="door.wav"):
        resolved([{"file": "door.wav", "at": 1.0}], tmp_path)


def test_an_unknown_effect_lists_the_real_ones():
    with pytest.raises(AltocueError) as caught:
        resolved([{"name": "airhorn", "at": 1.0}])
    assert "whoosh" in caught.value.fix


def test_a_cue_needs_a_sound():
    with pytest.raises(AltocueError, match="name"):
        resolved([{"at": 1.0}])


def test_a_cue_needs_a_time():
    with pytest.raises(AltocueError) as caught:
        resolved([{"name": "pop"}])
    assert "at" in caught.value.fix


def test_a_word_no_longer_spoken_names_the_effect():
    with pytest.raises(AltocueError, match="Effect"):
        resolved([{"name": "pop", "at": {"word": "gone"}}])


def test_a_time_past_the_end_is_refused_before_the_render():
    with pytest.raises(AltocueError, match="outside"):
        resolved([{"name": "pop", "at": 9.0}])


def test_a_level_out_of_range_is_refused_before_the_render():
    with pytest.raises(AltocueError) as caught:
        resolved([{"name": "pop", "at": 1.0, "under_db": 3}])
    assert "8 to 12" in caught.value.fix


@pytest.mark.parametrize("raw", ["yes", 3, {"cues": "none"}])
def test_anything_else_is_refused(raw):
    with pytest.raises(AltocueError):
        resolved(raw)


def wipe() -> formats.Format:
    return next(fmt for fmt in formats.load().values() if fmt.group == "wipe" and fmt.sound)


def test_a_wipe_sounds_at_its_midpoint_where_the_cut_lands():
    fmt = wipe()
    cues = render.sfx_cues({"items": [{"type": fmt.id, "start": 2.0, "end": 3.0}]})
    assert cues == [{"name": fmt.sound, "at": 2.5}]


def test_every_wipe_cues_its_midpoint():
    for fmt in formats.load().values():
        if fmt.group == "wipe" and fmt.sound:
            assert render.sfx_cues({"items": [{"type": fmt.id, "start": 1.0, "end": 2.0}]})[0]["at"] == 1.5


def test_other_formats_still_sound_at_their_start():
    fmt = next(f for f in formats.load().values() if f.group != "wipe" and f.sound)
    assert render.sfx_cues({"items": [{"type": fmt.id, "start": 2.0, "end": 3.0}]})[0]["at"] == 2.0


def test_all_cues_merges_house_sounds_and_project_cues():
    timeline = {"items": [{"type": "chip", "start": 1.0, "end": 2.0}, {"type": "endcard", "start": 2.0, "end": 3.0}], "sfx": True, "cues": [{"name": "ding", "at": 2.5}]}
    assert [c["name"] for c in render.all_cues(timeline)] == ["whoosh", "ding"]


def test_house_off_keeps_project_cues():
    timeline = {"items": [{"type": "endcard", "start": 2.0, "end": 3.0}], "sfx": False, "cues": [{"name": "ding", "at": 2.5}]}
    assert [c["name"] for c in render.all_cues(timeline)] == ["ding"]


def test_every_effect_is_heard_where_it_was_called(tmp_path):
    names = mix.sfx_names()
    seconds = 2 * len(names) + 2
    silence = ffmpeg.write_wav(tmp_path / "voice.wav", np.r_[np.full(RATE, 0.1) * np.sin(np.arange(RATE) * 0.05), np.zeros((seconds - 1) * RATE)], RATE)
    cues = [{"name": name, "at": 1.5 + 2 * index, "align": "start"} for index, name in enumerate(names)]
    out = mix.mix_bed(silence, tmp_path / "mix.wav", sfx=cues)
    with wave.open(str(out), "rb") as handle:
        left = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").reshape(-1, 2)[:, 0]
    for cue in cues:
        at = int(cue["at"] * RATE)
        assert np.abs(left[at : at + RATE // 2]).max() > 300, cue["name"]
        assert np.abs(left[at - 2400 : at - 480]).max() == 0, cue["name"]
