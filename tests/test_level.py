from __future__ import annotations

import numpy as np
import pytest

from altocue import ffmpeg, level
from altocue.level import Found

RATE = 48000


def line(seconds: float, db: float, pitch: float = 120.0, seed: int = 1) -> np.ndarray:
    """A voiced stretch with syllables: harmonics of a wandering pitch, at a level in dB."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    wander = np.random.default_rng(seed).normal(0, 1, n // 4800 + 2).cumsum() * 3
    phase = 2 * np.pi * np.cumsum(pitch + np.interp(t, np.arange(len(wander)) * 0.1, wander)) / RATE
    voiced = sum(np.sin(k * phase) / k for k in range(1, 30))
    shaped = voiced * (0.65 + 0.35 * np.sin(2 * np.pi * 3.5 * t))
    edge = np.minimum(1, np.minimum(np.arange(n), n - 1 - np.arange(n)) / 480)
    return shaped / np.sqrt(np.mean(shaped**2)) * 10 ** (db / 20) * edge


def hush(seconds: float, db: float = -70.0, seed: int = 3) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, 10 ** (db / 20), int(seconds * RATE))


def breath(seconds: float = 0.35, db: float = -42.0, seed: int = 5) -> np.ndarray:
    """Noise shaped like an inhale: most of its energy from 400 Hz to 4 kHz, and a soft swell."""
    n = int(seconds * RATE)
    white = np.random.default_rng(seed).normal(0, 1, n)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    shaped = np.fft.irfft(np.fft.rfft(white) * ((freqs > 400) & (freqs < 4000)), n)
    swell = np.sin(np.pi * np.arange(n) / n) ** 0.7
    return shaped / np.sqrt(np.mean(shaped**2)) * 10 ** (db / 20) * swell


def script(*parts: np.ndarray, room: float = -70.0) -> np.ndarray:
    voice = np.concatenate(parts)
    return voice + np.random.default_rng(3).normal(0, 10 ** (room / 20), len(voice))


def rms_db(samples: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(samples))) + 1e-12))


def seconds(found: list[tuple[int, int]]) -> list[tuple[float, float]]:
    return [(round(a / RATE, 2), round(b / RATE, 2)) for a, b in found]


def test_phrases_are_found_between_the_pauses():
    voice = script(hush(0.5), line(2, -24), hush(0.6), line(1.5, -24, seed=2), hush(0.5))
    found = level.analyse(voice, RATE)
    assert len(found.phrases) == 2 and not found.breaths
    assert seconds(found.phrases)[0] == pytest.approx((0.5, 2.5), abs=0.1)
    assert seconds(found.phrases)[1] == pytest.approx((3.1, 4.6), abs=0.1)


def test_a_short_gap_does_not_split_a_phrase():
    voice = script(hush(0.5), line(1, -24), hush(0.12), line(1, -24, seed=2), hush(0.5))
    assert len(level.analyse(voice, RATE).phrases) == 1


def test_a_long_run_is_split_so_a_line_that_trails_off_can_be_caught():
    voice = script(hush(0.3), line(9, -24), hush(0.3))
    found = level.analyse(voice, RATE)
    assert len(found.phrases) >= 3
    assert all(b - a <= (level.LONGEST_PHRASE + 0.1) * RATE for a, b in found.phrases)


def test_noise_with_no_voice_in_it_is_not_a_phrase():
    assert level.analyse(hush(3, -30), RATE).phrases == []


def test_too_short_to_analyse_is_nothing_found():
    assert level.analyse(hush(0.01), RATE) == Found([], [])


def test_a_breath_between_lines_is_a_breath():
    voice = script(hush(0.5), line(1.5, -24), hush(0.4), breath(), hush(0.4), line(1.5, -24, seed=2), hush(0.5))
    found = level.analyse(voice, RATE)
    assert len(found.breaths) == 1 and len(found.phrases) == 2
    assert seconds(found.breaths)[0][0] == pytest.approx(2.4, abs=0.15)


def test_a_breath_right_before_a_word_is_still_a_breath():
    voice = script(hush(0.5), line(1.5, -24), hush(0.5), breath(0.4), hush(0.05), line(1.5, -24, seed=2), hush(0.5))
    assert len(level.analyse(voice, RATE).breaths) == 1


def test_a_loud_burst_is_not_called_a_breath():
    voice = script(hush(0.5), line(1.5, -24), hush(0.4), breath(db=-24), hush(0.4), line(1.5, -24, seed=2), hush(0.5))
    assert level.analyse(voice, RATE).breaths == []


def test_a_long_noise_is_not_called_a_breath():
    voice = script(hush(0.5), line(1.5, -24), hush(0.4), breath(2.0), hush(0.4), line(1.5, -24, seed=2), hush(0.5))
    assert level.analyse(voice, RATE).breaths == []


def test_a_hiss_at_the_end_of_a_word_is_not_a_breath():
    n = int(0.3 * RATE)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    white = np.random.default_rng(9).normal(0, 1, n)
    hiss = np.fft.irfft(np.fft.rfft(white) * ((freqs > 4500) & (freqs < 9000)), n)
    hiss = hiss / np.sqrt(np.mean(hiss**2)) * 10 ** (-30 / 20)
    voice = script(hush(0.5), line(1.5, -24), hush(0.2), hiss, hush(0.6), line(1.5, -24, seed=2), hush(0.5))
    assert level.analyse(voice, RATE).breaths == []


def test_noise_under_a_transcribed_word_is_not_a_breath():
    parts = (hush(0.5), line(1.5, -24), hush(0.4), breath(), hush(0.4), line(1.5, -24, seed=2), hush(0.5))
    voice = script(*parts)
    assert len(level.analyse(voice, RATE).breaths) == 1
    assert level.analyse(voice, RATE, words=[(2.35, 2.8)]).breaths == []


def test_a_quiet_aside_comes_up_and_a_loud_line_comes_down():
    voice = script(hush(0.4), line(2, -22), hush(0.5), line(2, -34, seed=2), hush(0.5), line(2, -22, seed=3), hush(0.4))
    out, report = level.level_voice(voice, RATE)
    a, b = out[int(0.8 * RATE) : int(2.0 * RATE)], out[int(3.3 * RATE) : int(4.5 * RATE)]
    assert rms_db(a) - rms_db(b) < 2.5
    assert report["phrases"] == 3


def test_gain_stays_inside_its_limits():
    voice = script(hush(0.4), line(2, -12), hush(0.5), line(2, -60, seed=2), hush(0.5), line(2, -24, seed=3), hush(0.4), room=-90)
    _, report = level.level_voice(voice, RATE)
    assert report["most_boost_db"] <= level.MOST_BOOST_DB
    assert report["most_cut_db"] >= -level.MOST_CUT_DB


def test_a_noisy_room_gets_less_boost_because_the_room_comes_up_too():
    parts = (hush(0.4), line(2, -22), hush(0.5), line(2, -34, seed=2), hush(0.5), line(2, -22, seed=3), hush(0.4))
    quiet = level.level_voice(script(*parts, room=-80), RATE)[1]
    noisy = level.level_voice(script(*parts, room=-44), RATE)[1]
    assert noisy["most_boost_db"] < quiet["most_boost_db"] - 3


def test_the_pauses_are_left_exactly_alone():
    voice = script(hush(0.4), line(2, -22), hush(0.8), line(2, -34, seed=2), hush(0.8), line(2, -22, seed=3), hush(0.4))
    out, _ = level.level_voice(voice, RATE)
    assert np.allclose(out[int(2.6 * RATE) : int(3.0 * RATE)], voice[int(2.6 * RATE) : int(3.0 * RATE)], atol=1e-6)
    assert np.allclose(out[int(5.4 * RATE) : int(5.8 * RATE)], voice[int(5.4 * RATE) : int(5.8 * RATE)], atol=1e-6)


def test_a_breath_is_not_pumped_by_the_phrases_around_it():
    parts = (hush(0.5), line(1.5, -34), hush(0.4), breath(), hush(0.4), line(1.5, -34, seed=2), hush(0.5), line(1.5, -20, seed=3), hush(0.5))
    voice = script(*parts)
    out, report = level.level_voice(voice, RATE)
    assert report["most_boost_db"] > 3
    spot = slice(int(2.42 * RATE), int(2.73 * RATE))
    assert rms_db(out[spot]) == pytest.approx(rms_db(voice[spot]), abs=0.1)


def test_a_breath_is_turned_down_and_never_cut():
    voice = script(hush(0.5), line(1.5, -24), hush(0.4), breath(), hush(0.4), line(1.5, -24, seed=2), hush(0.5))
    spot = slice(int(2.45 * RATE), int(2.7 * RATE))
    out, report = level.level_voice(voice, RATE, breath_down_db=9.0)
    assert report["breaths"] == 1
    assert rms_db(out[spot]) == pytest.approx(rms_db(voice[spot]) - 9, abs=1)
    assert rms_db(out[spot]) > rms_db(hush(0.25, -70)) + 6


def test_breaths_are_left_alone_unless_asked():
    voice = script(hush(0.5), line(1.5, -24), hush(0.4), breath(), hush(0.4), line(1.5, -24, seed=2), hush(0.5))
    spot = slice(int(2.45 * RATE), int(2.7 * RATE))
    assert rms_db(level.level_voice(voice, RATE)[0][spot]) == pytest.approx(rms_db(voice[spot]), abs=0.1)


def test_only_breaths_can_be_asked_for():
    voice = script(hush(0.4), line(2, -22), hush(0.5), breath(), hush(0.5), line(2, -34, seed=2), hush(0.4))
    out, report = level.level_voice(voice, RATE, phrases=False, breath_down_db=9.0)
    assert report["most_boost_db"] == 0
    assert np.allclose(out[: int(2.3 * RATE)], voice[: int(2.3 * RATE)], atol=1e-6)


def test_the_gain_never_steps():
    voice = script(hush(0.4), line(2, -22), hush(0.5), line(2, -34, seed=2), hush(0.5), line(2, -22, seed=3), hush(0.4))
    out, _ = level.level_voice(voice, RATE)
    ratio = np.abs(out) / (np.abs(voice) + 1e-3)
    assert np.abs(np.diff(20 * np.log10(ratio[np.abs(voice) > 0.01] + 1e-6))).max() < 40 and np.abs(out).max() < 1


def test_a_boost_cannot_clip():
    peaky = line(2, -30, seed=2)
    peaky[RATE] = 0.9
    voice = script(hush(0.4), line(2, -18), hush(0.5), peaky, hush(0.4))
    assert np.abs(level.level_voice(voice, RATE)[0]).max() <= 0.999


def test_nothing_found_is_nothing_changed():
    noise = hush(3, -30)
    out, report = level.level_voice(noise, RATE)
    assert np.array_equal(out, noise) and report["phrases"] == 0


def test_length_is_kept():
    voice = script(hush(0.4), line(2, -22), hush(0.5))
    assert len(level.level_voice(voice, RATE)[0]) == len(voice)


def test_measured_range_comes_down(tmp_path):
    takes = [hush(0.5)]
    for index in range(10):
        takes += [line(3, -20 if index % 2 else -34, seed=index), hush(0.6)]
    voice = script(*takes)
    before = ffmpeg.write_wav(tmp_path / "before.wav", voice, RATE)
    after = ffmpeg.write_wav(tmp_path / "after.wav", level.level_voice(voice, RATE)[0], RATE)
    wide, narrow = ffmpeg.loudness(before)["range"], ffmpeg.loudness(after)["range"]
    assert wide >= 10 and narrow <= wide - 7
