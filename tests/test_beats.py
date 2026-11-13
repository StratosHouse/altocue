"""A track's beat grid from its own sound, and music that ends on a bar line instead of fading mid-phrase."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import beats, ffmpeg, mix, score

RATE = 48000


def drums(bpm: float, seconds: float, first: float = 0.37, bar_on: int = 0, seed: int = 0) -> np.ndarray:
    """A kick on the first beat of every bar and a hat on every beat, with a quiet pad under it."""
    n = int(seconds * RATE)
    rng = np.random.default_rng(seed)
    out = 0.03 * np.sin(2 * np.pi * 220 * np.arange(n) / RATE)
    kick = 0.8 * np.sin(2 * np.pi * 60 * np.arange(4800) / RATE) * np.exp(-np.arange(4800) / RATE / 0.05)
    for index, at in enumerate(np.arange(first, seconds, 60 / bpm)):
        start = int(round(at * RATE))
        if index % 4 == bar_on:
            part = out[start : start + len(kick)]
            part += kick[: len(part)]
        hat = 0.3 * rng.normal(0, 1, 960) * np.exp(-np.arange(960) / RATE / 0.01)
        part = out[start : start + len(hat)]
        part += hat[: len(part)]
    return out


def nearest(found: list[float], wanted: np.ndarray) -> np.ndarray:
    return np.abs(np.array(found)[:, None] - wanted[None, :]).min(axis=1)


@pytest.mark.parametrize("bpm", [84, 100, 120, 128])
def test_a_steady_track_gives_its_tempo_beats_and_bar_line(bpm):
    found = beats.grid(drums(bpm, 30), RATE)
    truth = np.arange(0.37, 30, 60 / bpm)
    assert found["confident"] and found["bpm"] == pytest.approx(bpm, rel=0.005)
    assert nearest(found["beats"], truth).max() < 0.015
    assert nearest(found["downbeats"], truth[::4]).max() < 0.015 and found["bar_sure"]


def test_the_bar_line_follows_the_kick_not_the_first_beat():
    found = beats.grid(drums(120, 30, bar_on=2), RATE)
    assert found["downbeats"][0] == pytest.approx(0.37 + 2 * 0.5, abs=0.015)


def test_four_even_kicks_give_beats_but_no_sure_bar_line():
    even = sum(drums(120, 30, bar_on=b, seed=b) for b in range(4))
    found = beats.grid(even, RATE)
    assert found["confident"] and not found["bar_sure"]


def test_noise_and_a_held_tone_have_no_grid_and_say_why():
    noise = beats.grid(np.random.default_rng(1).normal(0, 0.1, 30 * RATE), RATE)
    tone = beats.grid(0.2 * np.sin(2 * np.pi * 220 * np.arange(30 * RATE) / RATE), RATE)
    assert not noise["confident"] and "confidence" in noise["why"]
    assert not tone["confident"] and tone["why"] == "no clear attacks to count" and tone["beats"] == []


def test_a_clip_too_short_for_four_bars_is_not_believed():
    found = beats.grid(drums(120, 5), RATE)
    assert not found["confident"] and "beats" in found["why"]


@pytest.mark.parametrize("style", ["glass", "paper"])
def test_altocues_own_score_is_read_back_on_its_own_grid(style):
    """The score knows where it put every beat, so it checks the reader."""
    samples, info = score.compose([{"id": "a", "start": 0.0, "end": 24.0, "beat": "product"}], 24.0, style=style, seed=3)
    found = beats.grid(samples, RATE)
    assert found["confident"] and found["bpm"] == pytest.approx(info["bpm"], rel=0.005)
    assert np.median(nearest(found["beats"], np.array(info["beats"]))) < 0.01


def test_the_grid_is_the_same_every_time():
    track = drums(110, 20)
    assert beats.grid(track, RATE) == beats.grid(track.copy(), RATE)


# The ending


def grid_of(bpm: float, first: float, seconds: float, sure: bool = True) -> dict:
    lines = np.arange(first, seconds, 60 / bpm)
    return {"bpm": bpm, "confident": True, "beats": list(lines), "downbeats": list(lines[::4]), "bar_sure": sure}


def test_a_fade_ending_moves_the_track_so_a_bar_line_meets_the_last_sample():
    found = grid_of(120, 0.25, 60)  # bars at 0.25, 2.25, 4.25 ...
    shift, tail, facts = mix._bar_ending(found, 60 * RATE, round(10.6 * RATE), "fade", None, tight=False)
    assert facts["rule"] == "bar" and facts["track_at_end"] == pytest.approx(10.25)
    assert shift == round(0.35 * RATE) and tail == round(0.5 * RATE)  # a beat at 120 bpm, inside 0.25 to 0.6 s


def test_a_bar_line_just_after_the_end_opens_the_track_a_little_later_instead():
    shift, _, facts = mix._bar_ending(grid_of(120, 0.25, 60), 60 * RATE, round(10.1 * RATE), "fade", None, tight=True)
    assert facts["shift"] == pytest.approx(-0.15) and shift == round(-0.15 * RATE) and facts["fade"] == mix.TIGHT_TAIL


def test_with_no_sure_bar_line_the_ending_meets_a_beat():
    _, _, facts = mix._bar_ending(grid_of(120, 0.25, 60, sure=False), 60 * RATE, round(10.6 * RATE), "fade", None, tight=False)
    assert facts["rule"] == "beat" and facts["track_at_end"] == pytest.approx(10.75)


@pytest.mark.parametrize(
    ("end", "start", "found", "length", "why"),
    [
        ("land", None, grid_of(120, 0.25, 60), 60, "own ending"),
        ("own", None, grid_of(120, 0.25, 60), 60, "ends itself"),
        ("fade", 48000, grid_of(120, 0.25, 60), 60, "music_from"),
        ("fade", None, {"bpm": None, "confident": False, "beats": [], "downbeats": [], "why": "no clear attacks to count"}, 60, "no clear attacks"),
        ("fade", None, grid_of(120, 0.25, 60), 5, "loops"),
    ],
)
def test_every_other_case_keeps_its_ending_and_says_why(end, start, found, length, why):
    shift, tail, facts = mix._bar_ending(found, length * RATE, 10 * RATE, end, start, tight=False)
    assert shift is None and tail is None and not facts["moved"] and why in facts["why"]


def test_the_shaped_bed_puts_the_bar_line_on_the_last_sample():
    track = np.tile(np.arange(30 * RATE, dtype=float)[:, None], (1, 2))  # each sample holds its own place in the track
    bed, begin, _ = mix._shaped(track, 10 * RATE, "fade", shift=round(0.35 * RATE), tail=RATE // 2)
    assert begin == 0 and not bed[: round(0.35 * RATE) - 1].any()
    assert bed[-RATE // 2 - 1, 0] == pytest.approx(10 * RATE - RATE // 2 - 1 - round(0.35 * RATE))  # unfaded up to the fade
    opened, begin, _ = mix._shaped(track, 10 * RATE, "fade", shift=-round(0.15 * RATE), tail=RATE // 2)
    assert begin == round(0.15 * RATE) and opened[RATE, 0] == RATE + begin


def stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0


def test_under_a_voice_a_fade_ending_lands_on_a_bar_and_the_grid_is_reported_in_film_seconds(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "drums.wav", drums(120, 40, first=0.25), RATE)
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", 0.1 * np.sin(2 * np.pi * 150 * np.arange(int(10.6 * RATE)) / RATE), RATE)
    out = mix.mix_bed(voice, tmp_path / "mix.wav", music=music, music_end="fade")
    report = json.loads(out.with_suffix(".json").read_text())["music"]
    assert report["grid"]["confident"] and report["grid"]["bpm"] == pytest.approx(120, rel=0.005)
    assert report["ending"]["rule"] == "bar" and report["ending"]["end_to_bar"] == pytest.approx(0, abs=0.02)
    assert report["enters_at"] == pytest.approx(0.35, abs=0.02)  # the top of the track comes in a little after the film starts
    assert report["downbeats"][-1] == pytest.approx(10.6 - 2.0, abs=0.02)  # the last bar line inside the film starts its last bar


def test_with_no_voice_the_music_opens_the_film_and_a_beat_meets_the_end(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "drums.wav", drums(120, 40, first=0.25), RATE)
    out = mix.mix_bed(None, tmp_path / "mix.wav", music=music, seconds=10.6, music_end="fade")
    report = json.loads(out.with_suffix(".json").read_text())["music"]
    assert report["ending"]["rule"] == "beat" and report["ending"]["shift"] == pytest.approx(-0.15, abs=0.02)
    assert report["starts_at"] == pytest.approx(0.15, abs=0.02) and "enters_at" not in report
    assert np.abs(stereo(out)[: int(0.05 * RATE)]).max() > 0  # no silence at the head of a film the music leads


def test_a_track_with_no_beat_fades_as_before(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "pad.wav", 0.2 * np.sin(2 * np.pi * 300 * np.arange(20 * RATE) / RATE), RATE)
    out = mix.mix_bed(None, tmp_path / "mix.wav", music=music, seconds=8.0, music_end="fade")
    report = json.loads(out.with_suffix(".json").read_text())["music"]
    assert not report["ending"]["moved"] and "no clear attacks" in report["ending"]["why"] and report["starts_at"] == 0
    assert report["beats"] == [] and "enters_at" not in report


def test_a_landed_track_keeps_its_own_ending_and_reports_its_beats(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "drums.wav", drums(120, 30, first=0.25), RATE)
    out = mix.mix_bed(None, tmp_path / "mix.wav", music=music, seconds=10.0)
    report = json.loads(out.with_suffix(".json").read_text())["music"]
    assert not report["ending"]["moved"] and report["ending"]["rule"] == "land"
    assert all(0 <= beat < 10.0 for beat in report["beats"]) and len(report["beats"]) >= 18
    in_track = np.array(report["beats"]) + report["starts_at"]  # backtimed: film seconds plus where the film starts in the track
    assert nearest(list(in_track), np.arange(0.25, 30, 0.5)).max() < 0.015
