"""A film with no voice: the music leads and effects are levelled against it."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import ffmpeg, mix
from altocue.errors import AltocueError
from altocue.timeline import resolve_sfx

RATE = 48000


def tone(seconds: float, hz: float, amp: float = 0.2) -> np.ndarray:
    return amp * np.sin(2 * np.pi * hz * np.arange(int(seconds * RATE)) / RATE)


def score(tmp_path: Path, seconds: float = 12.0, name: str = "score.wav") -> Path:
    """A steady bed at 300 Hz, so effects at other frequencies can be read apart from it."""
    return ffmpeg.write_wav(tmp_path / name, tone(seconds, 300), RATE)


def stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64).reshape(-1, 2)[:, 0] / 32768.0


def report(path: Path) -> dict:
    return json.loads(path.with_suffix(".json").read_text())


def rms_db(samples: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(samples))) + 1e-12))


def peak_db(samples: np.ndarray) -> float:
    return float(20 * np.log10(np.abs(samples).max() + 1e-12))


def high(samples: np.ndarray, hz: float = 1000.0) -> np.ndarray:
    """What is left once the 300 Hz bed is taken out."""
    freqs = np.fft.rfftfreq(len(samples), 1 / RATE)
    return np.fft.irfft(np.fft.rfft(samples) * (freqs > hz), len(samples))


def click(tmp_path: Path, name: str = "click.wav", hz: float = 4000.0, seconds: float = 0.05) -> Path:
    n = int(seconds * RATE)
    return ffmpeg.write_wav(tmp_path / name, tone(seconds, hz, 0.5) * np.hanning(n), RATE)


def test_no_voice_needs_to_be_told_how_long_the_film_runs(tmp_path):
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path))
    assert "seconds" in caught.value.fix


def test_the_film_runs_as_long_as_it_was_told_plus_its_tail(tmp_path):
    out = mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), seconds=8.0, tail=2.0)
    assert len(stereo(out)) == 10 * RATE
    assert report(out)["mode"] == "no voice" and report(out)["voice_lufs"] is None


def test_a_silent_voice_track_counts_as_no_voice(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", np.zeros(8 * RATE), RATE)
    out = mix.mix_bed(voice, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"file": str(click(tmp_path)), "at": 2.0}])
    assert report(out)["mode"] == "no voice" and len(stereo(out)) == 8 * RATE


def test_a_real_voice_is_still_a_voice(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", tone(4, 200, 0.2), RATE)
    assert report(mix.mix_bed(voice, tmp_path / "mix.wav", music=score(tmp_path)))["mode"] == "voice"


def test_the_music_is_brought_to_the_bed_level(tmp_path):
    out = mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), seconds=10.0)
    assert ffmpeg.loudness(out)["integrated"] == pytest.approx(mix.BED_LUFS, abs=1)


@pytest.mark.parametrize("name", ["click", "pop", "tick"])
def test_small_sounds_land_over_the_music_and_not_under_it(tmp_path, name):
    bare = mix.mix_bed(None, tmp_path / "bare.wav", music=score(tmp_path), seconds=10.0)
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"name": name, "at": 5.0}], seconds=10.0))
    effect = out - stereo(bare)
    assert peak_db(effect[int(4.5 * RATE) : int(5.5 * RATE)]) - ffmpeg.loudness(bare)["integrated"] == pytest.approx(mix._sound_table()[name]["bed_db"], abs=1)


def test_a_cue_lands_where_the_table_or_the_cue_says(tmp_path):
    sound = click(tmp_path)
    cues = [{"file": str(sound), "at": 3.0}, {"file": str(sound), "at": 6.0, "over_db": 9}]
    bare = mix.mix_bed(None, tmp_path / "bare.wav", music=score(tmp_path), seconds=10.0)
    path = mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=cues, seconds=10.0)
    effect, placed = stereo(path) - stereo(bare), report(path)["sfx"]
    bed = ffmpeg.loudness(bare)["integrated"]
    assert peak_db(effect[int(2.5 * RATE) : int(3.5 * RATE)]) - bed == pytest.approx(mix.BED_OVER_DB, abs=1)
    assert peak_db(effect[int(5.5 * RATE) : int(6.5 * RATE)]) - bed == pytest.approx(9, abs=1)
    assert placed[1]["over_bed_db"] == pytest.approx(9, abs=0.5)


def test_a_hit_follows_a_quiet_section_of_the_music(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "score.wav", np.r_[tone(5, 300, 0.05), tone(5, 300, 0.2)], RATE)
    sound = click(tmp_path)
    cues = [{"file": str(sound), "at": 3.0, "min_gap_ms": 0}, {"file": str(sound), "at": 8.0}]
    path = mix.mix_bed(None, tmp_path / "mix.wav", music=music, sfx=cues, seconds=10.0)
    first, second = report(path)["sfx"]
    assert first["gain_db"] < second["gain_db"] - 4


def test_a_hit_in_the_first_moments_is_not_lost_to_the_fade_in(tmp_path):
    sound = str(click(tmp_path))
    path = mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"file": sound, "at": 0.0}, {"file": sound, "at": 5.0}], seconds=10.0)
    first, second = report(path)["sfx"]
    assert first["gain_db"] > second["gain_db"] - 7


def test_an_effect_can_have_the_film_to_itself(tmp_path):
    path = mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"name": "ding", "at": 1.0}], seconds=4.0)
    out = stereo(path)
    assert peak_db(out) == pytest.approx(mix.BED_LUFS + report(path)["sfx"][0]["over_bed_db"], abs=1)
    assert report(path)["music"] is None


def test_a_level_for_a_voice_is_refused_with_no_voice(tmp_path):
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"name": "pop", "at": 1.0, "under_db": 10}], seconds=4.0)
    assert "over_db" in caught.value.fix


def test_a_level_too_far_over_the_music_is_refused(tmp_path):
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"name": "pop", "at": 1.0, "over_db": 30}], seconds=4.0)
    assert "over" in caught.value.fix


def test_the_tail_fade_is_tight_and_ends_on_the_last_sample(tmp_path):
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), seconds=10.0))
    body = rms_db(out[5 * RATE : 6 * RATE])
    assert rms_db(out[-int(0.6 * RATE) : -int(0.35 * RATE)]) == pytest.approx(body, abs=0.5)
    assert abs(out[-1]) < 0.002


def test_the_head_is_tight(tmp_path):
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), seconds=10.0))
    assert abs(out[0]) < 0.002
    assert rms_db(out[int(0.06 * RATE) : int(0.16 * RATE)]) == pytest.approx(rms_db(out[RATE : 2 * RATE]), abs=0.5)


def test_a_score_that_ends_itself_is_left_alone(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "score.wav", tone(10, 300), RATE)
    path = mix.mix_bed(None, tmp_path / "mix.wav", music=music, seconds=10.0, music_end="own")
    out = stereo(path)
    assert report(path)["music"]["starts_at"] == 0 and report(path)["music"]["loops"] == 1
    assert rms_db(out[-int(0.1 * RATE) : -int(0.02 * RATE)]) == pytest.approx(rms_db(out[RATE : 2 * RATE]), abs=0.3)


def test_a_score_that_ends_early_is_not_looped_over_the_end(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "score.wav", tone(6, 300), RATE)
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=music, seconds=8.0, music_end="own"))
    assert len(out) == 8 * RATE and np.abs(out[7 * RATE :]).max() == 0


def bed_only(out: np.ndarray) -> np.ndarray:
    """The 300 Hz bed in 10 ms frames, in dB, whatever else is in the mix."""
    freqs = np.fft.rfftfreq(len(out), 1 / RATE)
    low = np.fft.irfft(np.fft.rfft(out) * (freqs < 600), len(out))
    return 20 * np.log10(np.sqrt(np.mean(low[: len(low) // 480 * 480].reshape(-1, 480) ** 2, axis=1)) + 1e-9)


def test_the_music_breathes_in_before_a_big_hit(tmp_path):
    cue = {"file": str(click(tmp_path)), "at": 5.0, "dip_db": 9}
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[cue], seconds=10.0))
    level = bed_only(out)
    assert level[300] - level[497] == pytest.approx(9, abs=1.5)  # all the way down just before the hit
    assert level[300] - level[450] < 1.0  # and not down long before it
    assert level[300] - level[520] < 1.0  # and straight back


@pytest.mark.parametrize("name", ["boom", "punch"])
def test_the_biggest_hits_take_a_dip_from_the_table(tmp_path, name):
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"name": name, "at": 5.0}], seconds=10.0))
    level = bed_only(out)
    assert level[300] - level[470:500].min() >= 5


def test_a_click_takes_no_dip(tmp_path):
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"name": "click", "at": 5.0}], seconds=10.0))
    level = bed_only(out)
    assert level[300] - level[470:499].min() < 0.5


def test_a_dip_near_the_start_is_a_shorter_one_and_not_a_crash(tmp_path):
    out = mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"file": str(click(tmp_path)), "at": 0.1, "dip_db": 9}], seconds=4.0)
    assert len(stereo(out)) == 4 * RATE


def test_a_dip_too_deep_is_refused(tmp_path):
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=[{"name": "pop", "at": 1.0, "dip_db": 20}], seconds=4.0)
    assert "12" in caught.value.fix


def rising(tmp_path: Path, seconds: float = 0.4) -> Path:
    """A swell whose loudest moment is at its end."""
    n = int(seconds * RATE)
    return ffmpeg.write_wav(tmp_path / "swell.wav", tone(seconds, 4000, 0.5) * np.arange(n) / n, RATE)


def test_a_sound_can_be_placed_by_its_peak(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", tone(6, 200, 0.1), RATE)
    swell = rising(tmp_path)
    plain = stereo(mix.mix_bed(voice, tmp_path / "a.wav", sfx=[{"file": str(swell), "at": 3.0}]))
    peaked = stereo(mix.mix_bed(voice, tmp_path / "b.wav", sfx=[{"file": str(swell), "at": 3.0, "align": "peak"}]))
    assert np.argmax(np.abs(high(plain))) == pytest.approx(3.4 * RATE, abs=48)
    assert np.argmax(np.abs(high(peaked))) == pytest.approx(3.0 * RATE, abs=48)


def test_a_sound_placed_by_its_peak_at_the_very_start_loses_its_lead_in(tmp_path):
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"file": str(rising(tmp_path)), "at": 0.1, "align": "peak"}], seconds=2.0))
    assert np.argmax(np.abs(out)) == pytest.approx(0.1 * RATE, abs=48)


def test_a_stated_offset_moves_a_sound(tmp_path):
    out = stereo(mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"file": str(click(tmp_path)), "at": 1.0, "offset_ms": -40}], seconds=2.0))
    assert np.argmax(np.abs(out) > 0.001) == pytest.approx(0.96 * RATE, abs=240)


def test_an_unknown_alignment_is_refused(tmp_path):
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"name": "pop", "at": 1.0, "align": "middle"}], seconds=2.0)
    assert "peak" in caught.value.fix


def test_a_sound_repeated_too_soon_is_dropped_and_reported(tmp_path):
    cues = [{"name": "boom", "at": 1.0}, {"name": "boom", "at": 1.5}, {"name": "boom", "at": 4.5}, {"name": "click", "at": 1.5}]
    done = report(mix.mix_bed(None, tmp_path / "mix.wav", sfx=cues, seconds=6.0))
    assert [(row["name"], row["at"]) for row in done["sfx"]] == [("boom", 1.0), ("click", 1.5), ("boom", 4.5)]
    assert done["skipped"] == [{"name": "boom", "at": 1.5, "why": "too soon after the last one"}]


def test_a_cue_can_ask_for_its_own_gap(tmp_path):
    cues = [{"name": "boom", "at": 1.0}, {"name": "boom", "at": 1.5, "min_gap_ms": 0}]
    assert len(report(mix.mix_bed(None, tmp_path / "mix.wav", sfx=cues, seconds=6.0))["sfx"]) == 2


def test_the_mix_never_clips_however_hot_the_hits(tmp_path):
    cues = [{"name": name, "at": 2.0, "over_db": 12} for name in ("boom", "punch", "sub", "clang")]
    assert peak_db(stereo(mix.mix_bed(None, tmp_path / "mix.wav", music=score(tmp_path), sfx=cues, seconds=6.0))) <= -0.9


def test_every_bundled_sound_says_how_to_place_it():
    rows = json.loads((mix.SFX_DIR / "sounds.json").read_text())
    assert {row["name"] for row in rows} == set(mix.sfx_names())
    for row in rows:
        assert row["align"] in mix.ALIGNS, row["name"]
        assert 3 <= row["bed_db"] <= 10, row["name"]
        assert row["min_gap_ms"] >= 0 and isinstance(row["offset_ms"], int), row["name"]
        assert row["dip_db"] == 0 or 6 <= row["dip_db"] <= 12, row["name"]


def test_a_film_can_set_these_from_motion_json():
    _, cues = resolve_sfx([{"name": "boom", "at": 1.0, "over_db": 9, "dip_db": 8, "align": "peak", "offset_ms": 10}], [], 4.0, Path("."))
    assert cues[0]["over_db"] == 9 and cues[0]["dip_db"] == 8 and cues[0]["align"] == "peak"
