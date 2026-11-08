"""Music and echo off a voice: measured first, undone when it costs the words."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from altocue import ffmpeg, stems

RATE = 48000


def speechy(seconds: float = 12.0, seed: int = 1) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Bursts of shaped noise as words, with quiet between them."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE), np.float32)
    words, at = [], 0.3
    while at < seconds - 0.8:
        length = rng.uniform(0.18, 0.4)
        n = int(length * RATE)
        burst = rng.normal(0, 0.25, n) * np.hanning(n)
        start = int(at * RATE)
        out[start : start + n] += np.convolve(burst, np.ones(8) / 8, "same").astype(np.float32)
        words.append((at, at + length))
        at += length + rng.uniform(0.35, 0.7)
    return out + rng.normal(0, 0.0005, len(out)).astype(np.float32), words


def chords(count: int) -> np.ndarray:
    t = np.arange(count) / RATE
    return (0.05 * (np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 277 * t) + np.sin(2 * np.pi * 330 * t))).astype(np.float32)


def room(dry: np.ndarray, rt60: float, seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    length = int(rt60 * RATE)
    tail = rng.normal(0, 1, length) * np.exp(-6.9 * np.arange(length) / length)
    tail[0] = 1 / 0.05
    wet = np.convolve(dry, tail * 0.05, "full")[: len(dry)]
    return (wet / np.abs(wet).max() * 0.5).astype(np.float32)


def test_music_between_the_words_is_measured():
    voice, words = speechy()
    assert stems.music_under(voice + chords(len(voice)), RATE, words)["music"]
    assert not stems.music_under(voice, RATE, words)["music"]
    assert not stems.music_under(voice, RATE, None)["music"]


def test_a_ringing_room_is_measured_and_a_dry_one_is_not():
    voice, words = speechy(20.0)
    wet = stems.ring_time(room(voice, 1.0), RATE, words)
    dry = stems.ring_time(voice, RATE, words)
    assert wet is not None and wet > stems.RT60_ECHO
    assert dry is None or dry < stems.RT60_ECHO


def tail_db(samples: np.ndarray, words: list[tuple[float, float]]) -> float:
    """The 200 ms after each word against the words, in dB."""
    after = np.mean([np.mean(samples[int(e * RATE) : int((e + 0.2) * RATE)] ** 2) for _, e in words])
    during = np.mean([np.mean(samples[int(s * RATE) : int(e * RATE)] ** 2) for s, e in words])
    return float(10 * np.log10(after / during))


def test_dereverb_keeps_the_length_and_takes_a_little_off_the_tail():
    voice, words = speechy(8.0)
    rng = np.random.default_rng(2)
    length = int(0.6 * RATE)
    response = rng.normal(0, 1, length) * np.exp(-6.9 * np.arange(length) / length) * 0.02
    response[0] = 1
    wet = np.convolve(voice, response)[: len(voice)].astype(np.float32)
    dry = stems.dereverb(wet, RATE, "strong")
    assert len(dry) == len(wet)
    assert tail_db(dry, words) < tail_db(wet, words) - 0.3  # modest, as the module says
    assert abs(stems.word_level(dry, RATE, words) - stems.word_level(wet, RATE, words)) < 1.5


def test_a_ringing_room_is_named_when_echo_is_off(tmp_path):
    voice, words = speechy(20.0)
    wet = room(voice, 1.0)
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", wet, RATE)
    warnings: list[str] = []
    path, _, report = stems.prepare_voice(raw, wet, RATE, words, "off", "off", warnings)
    assert path == raw and report["echo"] == "off" and report["rt60"] > stems.RT60_ECHO
    assert any("rings" in w for w in warnings)


def test_long_recordings_are_dereverbed_in_chunks(monkeypatch):
    monkeypatch.setattr(stems, "CHUNK", 1.0)
    voice, _ = speechy(4.0)
    out = stems.dereverb(room(voice, 0.6), RATE, "light")
    assert len(out) == len(voice) and np.isfinite(out).all()


def test_music_without_demucs_is_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.setattr(stems, "have_demucs", lambda: False)
    voice, words = speechy()
    mixed = voice + chords(len(voice))
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", mixed, RATE)
    warnings: list[str] = []
    path, samples, report = stems.prepare_voice(raw, mixed, RATE, words, "auto", "off", warnings)
    assert path == raw and report["music"] == "not installed"
    assert any("Demucs" in w for w in warnings)


def test_a_split_that_eats_the_words_is_undone(tmp_path, monkeypatch):
    voice, words = speechy()
    mixed = voice + chords(len(voice))
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", mixed, RATE)
    monkeypatch.setattr(stems, "have_demucs", lambda: True)

    def eaten(source: Path, folder: Path, count: int, rate: int) -> Path:
        return ffmpeg.write_wav(folder / "vocals.wav", mixed * 0.1, rate)

    monkeypatch.setattr(stems, "separate", eaten)
    warnings: list[str] = []
    path, _, report = stems.prepare_voice(raw, mixed, RATE, words, "on", "off", warnings)
    assert path == raw and report["music"] == "undone"


def test_a_good_split_is_used(tmp_path, monkeypatch):
    voice, words = speechy()
    mixed = voice + chords(len(voice))
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", mixed, RATE)
    monkeypatch.setattr(stems, "have_demucs", lambda: True)
    monkeypatch.setattr(stems, "separate", lambda source, folder, count, rate: ffmpeg.write_wav(folder / "vocals.wav", voice, rate))
    path, samples, report = stems.prepare_voice(raw, mixed, RATE, words, "auto", "off", [])
    assert report["music"] == "removed" and path != raw and len(samples) == len(mixed)
    assert not stems.music_under(samples, RATE, words)["music"]
