"""Declipping, in NumPy alone: flat tops found, peaks drawn back nearer the truth, and a clean file left exactly alone."""

from __future__ import annotations

import numpy as np
import pytest

from altocue import declip

RATE = 48000


def pcm(samples: np.ndarray) -> np.ndarray:
    """What a 16-bit recorder keeps: anything past full scale is held there, and every sample is a step of 16 bits."""
    return (np.rint(np.clip(samples, -1.0, 1.0) * 32767) / 32768).astype(np.float32)


def sine(hz: float, peak: float, seconds: float = 1.0) -> np.ndarray:
    return peak * np.sin(2 * np.pi * hz * np.arange(int(seconds * RATE)) / RATE)


def voiced(seconds: float, peak: float) -> np.ndarray:
    """Harmonics of a wandering pitch in syllables, as `test_clean.speech` makes them, scaled so its peak is `peak`."""
    t = np.arange(int(seconds * RATE)) / RATE
    phase = 2 * np.pi * np.cumsum(130 + 20 * np.sin(2 * np.pi * 0.7 * t)) / RATE
    wave = sum(np.sin(k * phase) / k for k in range(1, 25)) * np.convolve((np.sin(2 * np.pi * 1.7 * t) > 0.1).astype(float), np.ones(480) / 480, mode="same")
    return wave / np.abs(wave).max() * peak


def error(made: np.ndarray, truth: np.ndarray, where: np.ndarray) -> float:
    return float(np.sqrt(np.mean((made[where] - truth[where]) ** 2)))


@pytest.mark.parametrize("hz", [110.0, 220.0, 440.0])
def test_a_clipped_sine_gets_its_peaks_back(hz):
    truth = sine(hz, 1.4)
    clipped = pcm(truth)
    out, report = declip.declip(clipped, RATE)
    back = out / 10 ** (report["headroom_db"] / 20)  # undo the headroom to compare with the truth
    over = np.abs(truth) > 1.0
    assert report["runs"] == report["repaired"] == 2 * hz and report["samples"] == pytest.approx(over.sum(), rel=0.02)
    assert error(back, truth, over) < error(clipped, truth, over) / 3
    assert np.abs(out).max() <= declip.HEADROOM + 1e-6 and report["headroom_db"] < 0
    assert len(out) == len(clipped)


def test_clipped_speech_comes_back_nearer_the_original():
    truth = voiced(2.0, 1.3)
    clipped = pcm(truth)
    out, report = declip.declip(clipped, RATE)
    back = out / 10 ** (report["headroom_db"] / 20)
    over = np.abs(truth) > 1.0
    assert report["repaired"] > 0 and report["longest_ms"] <= declip.LONGEST_SECONDS * 1000
    assert error(back, truth, over) < error(clipped, truth, over)
    peaks = [np.abs(back[a:b]).max() - np.abs(clipped[a:b]).max() for a, b, _ in declip.find_clipped(clipped)]
    assert min(peaks) >= -2e-4 and max(peaks) > 0.05  # never under the flat top, and the peaks did rise


@pytest.mark.parametrize(
    "make",
    [
        lambda: pcm(voiced(2.0, 0.95)),
        lambda: pcm(sine(220.0, 0.99)),
        lambda: pcm(sine(60.0, 0.9)),  # a hum's rounded top can hold three samples level
        lambda: pcm(sine(30.0, 0.95)),  # and a rumble's five
        lambda: pcm(voiced(2.0, 0.1)),
    ],
)
def test_a_clean_recording_passes_through_exactly(make):
    clean = make()
    out, report = declip.declip(clean, RATE)
    assert out is clean and report["runs"] == 0 and report["repaired"] == 0 and report["headroom_db"] == 0.0


def test_one_side_clipped_is_found_on_that_side_only():
    truth = voiced(1.0, 0.6) + sine(130.0, 0.2)
    clipped = pcm(np.minimum(truth, 0.5))
    runs = declip.find_clipped(clipped)
    assert runs and {sign for *_, sign in runs} == {1}


def test_a_clip_well_under_full_scale_still_counts_when_the_tops_are_flat():
    clipped = pcm(np.clip(sine(220.0, 0.75), -0.4, 0.4))
    out, report = declip.declip(clipped, RATE)
    assert report["repaired"] == 440 and report["headroom_db"] == 0.0  # the redrawn peaks fit, so the level is kept
    assert np.abs(out).max() > 0.6


def test_a_run_too_long_to_guess_is_left_as_it_is():
    truth = sine(20.0, 3.0)  # flat for many milliseconds at a time
    clipped = pcm(truth)
    out, report = declip.declip(clipped, RATE)
    assert report["runs"] > 0 and report["repaired"] == 0 and out is clipped
    assert report["longest_ms"] > declip.LONGEST_SECONDS * 1000


def test_silence_and_an_empty_clip_are_not_a_crash():
    for nothing in (np.zeros(RATE, np.float32), np.zeros(0, np.float32)):
        out, report = declip.declip(nothing, RATE)
        assert out is nothing and report["runs"] == 0
