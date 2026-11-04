from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import ffmpeg, mix
from altocue.errors import AltocueError
from altocue.models import Clip, Edl, Segment
from altocue.project import Project

RATE = 48000


def tone(seconds: float, hz: float, amp: float = 0.3, phase: float = 0.0) -> np.ndarray:
    return amp * np.sin(2 * np.pi * hz * np.arange(int(seconds * RATE)) / RATE + phase)


def noise(seconds: float, amp: float = 0.1, seed: int = 1) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, amp, int(seconds * RATE))


def speech_bursts(seconds: float, amp: float = 0.2, every: float = 2.0, length: float = 1.0) -> np.ndarray:
    """Low-passed noise, so a bed tone above 6 kHz can be read back on its own."""
    n = int(seconds * RATE)
    raw = np.random.default_rng(3).normal(0, 1, n + 480)
    low = np.convolve(raw, np.ones(16) / 16, mode="valid")[:n]
    gate = (np.arange(n) / RATE) % every < length
    return low / low.std() * amp * gate


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9))


def stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 2 and handle.getframerate() == RATE and handle.getsampwidth() == 2
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, 2) / 32768.0


def band_rms_db(x: np.ndarray, hz: float) -> float:
    """Level of one tone in a slice of signal."""
    window = np.hanning(len(x))
    spectrum = np.fft.rfft(x * window)
    bin_at = int(round(hz * len(x) / RATE))
    return float(20 * np.log10(2 * np.abs(spectrum[bin_at - 2 : bin_at + 3]).max() / window.sum() / np.sqrt(2) + 1e-9))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    made = Project.create(tmp_path / "video")
    made.save_clips([Clip(id=c, path=f"{c}.mov", duration=4, fps=30, width=64, height=64) for c in ("a", "b")])
    return made


def cleaned(project: Project, clip: str, samples: np.ndarray) -> np.ndarray:
    path = ffmpeg.write_wav(project.audio_dir / f"{clip}.wav", samples, RATE)
    return ffmpeg.read_wav(path)[0]


def edl(*parts: tuple[str, float, float] | tuple[str, float, float, float]) -> Edl:
    segments = [Segment(id=f"s{i}", clip=p[0], start=p[1], end=p[2], gain=p[3] if len(p) > 3 else 1.0) for i, p in enumerate(parts)]
    return Edl(fps=30, segments=segments)


def test_voice_is_sample_accurate_through_the_edl(project, tmp_path):
    a = cleaned(project, "a", noise(4, seed=1))
    b = cleaned(project, "b", noise(4, seed=2))
    out = mix.mix_voice(project, edl(("a", 0.5, 1.5), ("b", 2.0, 3.0), ("a", 3.0, 3.5)), tmp_path / "voice.wav")
    voice, rate = ffmpeg.read_wav(out)
    assert rate == RATE and len(voice) == round(2.5 * RATE)
    assert np.allclose(voice[20000:28000], a[24000 + 20000 : 24000 + 28000], atol=3e-4)
    assert np.allclose(voice[RATE + 8000 : RATE + 40000], b[2 * RATE + 8000 : 2 * RATE + 40000], atol=3e-4)
    assert np.allclose(voice[2 * RATE + 4000 : 2 * RATE + 12000], a[3 * RATE + 4000 : 3 * RATE + 12000], atol=3e-4)


def test_each_segment_lasts_as_many_frames_as_the_picture(project, tmp_path):
    cleaned(project, "a", noise(4))
    parts = [("a", i * 0.1, i * 0.1 + 0.052) for i in range(30)]
    frames = sum(max(1, round((p[2] - p[1]) * 30)) for p in parts)
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(*parts), tmp_path / "voice.wav"))
    assert frames != round(30 * 0.052 * 30)
    assert len(voice) == round(frames / 30 * RATE)


def test_sound_stays_with_the_picture_after_rounded_segments(project, tmp_path):
    a = cleaned(project, "a", noise(4))
    parts = [("a", 0.0, 0.52), ("a", 1.0, 1.52), ("a", 2.0, 3.0)]
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(*parts), tmp_path / "voice.wav"))
    start = round((16 + 16) / 30 * RATE)
    assert np.allclose(voice[start + 4000 : start + 40000], a[2 * RATE + 4000 : 2 * RATE + 40000], atol=3e-4)


def test_joins_do_not_click(project, tmp_path):
    cleaned(project, "a", tone(4, 220))
    cleaned(project, "b", tone(4, 220, phase=np.pi))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0.5, 1.5), ("b", 1.5, 2.5)), tmp_path / "voice.wav"))
    steady = np.abs(np.diff(tone(1, 220))).max()
    assert np.abs(np.diff(voice)).max() < steady * 1.5


def test_hard_join_would_click(project):
    a, b = tone(4, 220), tone(4, 220, phase=np.pi)
    hard = np.r_[a[:48000], b[48000 + 240 :]]
    assert np.abs(np.diff(hard)).max() > np.abs(np.diff(a)).max() * 3


def test_equal_power_join_does_not_dip_on_different_noise(project, tmp_path):
    cleaned(project, "a", noise(4, seed=4))
    cleaned(project, "b", noise(4, seed=5))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 2), ("b", 2, 4)), tmp_path / "voice.wav", fade_ms=30))
    around = voice[2 * RATE - 720 : 2 * RATE + 720]
    assert rms_db(around) == pytest.approx(rms_db(voice[RATE // 2 : RATE]), abs=1.0)


def test_fade_length_sets_the_blend(project, tmp_path):
    a = cleaned(project, "a", noise(4, seed=4))
    cleaned(project, "b", noise(4, seed=5))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 2), ("b", 2, 4)), tmp_path / "voice.wav", fade_ms=30))
    assert np.allclose(voice[2 * RATE - 800 : 2 * RATE - 730], a[2 * RATE - 800 : 2 * RATE - 730], atol=3e-4)
    assert not np.allclose(voice[2 * RATE - 700 : 2 * RATE - 640], a[2 * RATE - 700 : 2 * RATE - 640], atol=1e-3)


def test_contiguous_segments_play_through_untouched(project, tmp_path):
    a = cleaned(project, "a", noise(4))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 1.0, 1.7), ("a", 1.7, 2.4)), tmp_path / "voice.wav"))
    assert np.allclose(voice[2000:-2000], a[RATE + 2000 : RATE + len(voice) - 2000], atol=3e-4)


def test_a_small_gap_in_one_clip_does_not_bump_the_level(project, tmp_path):
    cleaned(project, "a", tone(4, 220))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0.5, 1.5), ("a", 1.5 + 1 / RATE * 5, 2.5)), tmp_path / "voice.wav"))
    assert np.abs(voice).max() < 0.33


def test_segment_gain_is_applied(project, tmp_path):
    cleaned(project, "a", tone(4, 440))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 1, 1.0), ("a", 2, 3, 0.5)), tmp_path / "voice.wav"))
    assert rms_db(voice[10000:40000]) - rms_db(voice[58000:88000]) == pytest.approx(6.02, abs=0.1)


def test_ends_are_faded(project, tmp_path):
    cleaned(project, "a", tone(4, 440, phase=np.pi / 2))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 1, 2)), tmp_path / "voice.wav"))
    assert abs(voice[0]) < 0.01 and abs(voice[-1]) < 0.01


def test_join_at_the_start_of_a_clip_still_fades(project, tmp_path):
    cleaned(project, "a", tone(4, 220))
    cleaned(project, "b", tone(4, 220, phase=np.pi))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 3.0, 4.0), ("b", 0.0, 1.0)), tmp_path / "voice.wav"))
    assert len(voice) == 2 * RATE
    assert np.abs(np.diff(voice)).max() < np.abs(np.diff(tone(1, 220))).max() * 1.5


def test_a_segment_shorter_than_the_fade(project, tmp_path):
    cleaned(project, "a", noise(4))
    parts = [("a", 0.1, 1.0), ("a", 1.5, 1.51), ("a", 2.0, 3.0)]
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(*parts), tmp_path / "voice.wav"))
    assert len(voice) == round((27 + 1 + 30) / 30 * RATE)


def test_a_clip_without_audio_and_no_room_to_borrow_is_silence(project, tmp_path):
    project.save_clips(
        [
            Clip(id="a", path="a.mov", duration=4, fps=30, width=64, height=64),
            Clip(id="b", path="b.mov", duration=4, fps=30, width=64, height=64, has_audio=False),
        ]
    )
    cleaned(project, "a", tone(4, 440))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 1), ("b", 0, 1)), tmp_path / "voice.wav"))
    assert len(voice) == 2 * RATE and np.abs(voice[RATE + 1000 :]).max() == 0


def test_missing_cleaned_audio_says_what_to_run(project, tmp_path):
    with pytest.raises(AltocueError) as caught:
        mix.mix_voice(project, edl(("a", 0, 1)), tmp_path / "voice.wav")
    assert "altocue clean" in caught.value.fix


def test_empty_cut_is_refused(project, tmp_path):
    with pytest.raises(AltocueError):
        mix.mix_voice(project, Edl(fps=30), tmp_path / "voice.wav")


def test_backwards_segment_is_refused(project, tmp_path):
    cleaned(project, "a", noise(4))
    with pytest.raises(AltocueError):
        mix.mix_voice(project, edl(("a", 2, 1)), tmp_path / "voice.wav")


def talk(seconds: float = 4.0, room_db: float = -60.0, seed: int = 2) -> np.ndarray:
    """Bursts of voice over a steady room, the shape of a cleaned clip."""
    n = int(seconds * RATE)
    return speech_bursts(seconds, 0.15, every=1.0, length=0.6)[:n] + np.random.default_rng(seed).normal(0, 10 ** (room_db / 20), n)


def floor_db(samples: np.ndarray, hop: int = 240) -> float:
    """The lowest level in 5 ms frames."""
    frames = samples[: len(samples) // hop * hop].reshape(-1, hop)
    return float(20 * np.log10(np.sqrt(np.mean(frames**2, axis=1)).min() + 1e-9))


def test_a_room_loop_keeps_the_level_and_colour_of_the_quietest_stretch():
    freqs = np.fft.rfftfreq(4 * RATE, 1 / RATE)
    white = np.random.default_rng(6).normal(0, 1, 4 * RATE)
    room = np.fft.irfft(np.fft.rfft(white) * (freqs < 2000), 4 * RATE)
    room = room / room.std() * 10 ** (-58 / 20)
    gate = (np.arange(4 * RATE) / RATE) % 1.0 < 0.6
    loop = mix.room_loop((noise(4, 0.1) * gate + room).astype(np.float32))
    assert loop is not None and len(loop) == mix.ROOM_SECONDS * RATE
    assert rms_db(loop) == pytest.approx(-58, abs=1.5)
    spectrum = np.abs(np.fft.rfft(loop)) ** 2
    bins = np.fft.rfftfreq(len(loop), 1 / RATE)
    assert spectrum[bins < 2000].sum() > spectrum[bins > 4000].sum() * 30


def test_a_room_loop_joins_itself_without_a_click():
    loop = mix.room_loop(talk().astype(np.float32))
    assert abs(loop[0] - loop[-1]) < 4 * np.abs(np.diff(loop)).mean()


def test_a_room_loop_is_the_same_every_time():
    samples = talk().astype(np.float32)
    assert np.array_equal(mix.room_loop(samples), mix.room_loop(samples))


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros(4 * RATE),  # digital silence is not a room
        tone(4, 220),  # no quiet stretch at all
        speech_bursts(4, 0.15, every=1.0, length=0.6),  # pauses of digital silence
    ],
    ids=["silence", "steady", "gated"],
)
def test_a_clip_with_no_room_to_sample_gives_none(samples):
    assert mix.room_loop(samples.astype(np.float32)) is None


def test_a_loud_stretch_never_passes_for_a_room():
    assert mix.room_loop((talk(room_db=-32.0)).astype(np.float32)) is None


def test_a_clip_with_no_sound_gets_the_room_of_its_neighbour(project, tmp_path):
    project.save_clips(
        [
            Clip(id="a", path="a.mov", duration=4, fps=30, width=64, height=64),
            Clip(id="b", path="b.mov", duration=4, fps=30, width=64, height=64, has_audio=False),
        ]
    )
    cleaned(project, "a", talk())
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 1), ("b", 0, 1), ("a", 1, 2)), tmp_path / "voice.wav"))
    gap = voice[RATE + 2400 : 2 * RATE - 2400]
    assert np.abs(gap).max() > 0
    assert rms_db(gap) == pytest.approx(-60, abs=2)


def test_the_floor_holds_across_a_cut_with_nothing_either_side_of_it(project, tmp_path):
    a = talk()
    a[: RATE // 4] = 0.0
    a[-RATE // 4 :] = 0.0
    cleaned(project, "a", a)
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0.2, 3.0), ("a", 3.8, 4.0), ("a", 1.0, 3.0)), tmp_path / "voice.wav"))
    join = round(84 / 30 * RATE)
    assert floor_db(voice[join - 2400 : join + 2400]) > -66


def test_room_tone_leaves_speech_alone(project, tmp_path):
    a = cleaned(project, "a", talk())
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 2), ("a", 3.0, 4.0)), tmp_path / "voice.wav"))
    assert np.allclose(voice[4800 : RATE - 4800], a[4800 : RATE - 4800], atol=3e-4)


def test_each_clip_keeps_its_own_room(project, tmp_path):
    project.save_clips(
        [
            Clip(id="a", path="a.mov", duration=4, fps=30, width=64, height=64),
            Clip(id="b", path="b.mov", duration=4, fps=30, width=64, height=64),
            Clip(id="c", path="c.mov", duration=4, fps=30, width=64, height=64, has_audio=False),
        ]
    )
    cleaned(project, "a", talk(room_db=-64))
    cleaned(project, "b", talk(room_db=-52, seed=8))
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 1), ("c", 0, 1), ("b", 0, 1), ("c", 0, 1)), tmp_path / "voice.wav"))
    assert rms_db(voice[RATE + 4800 : 2 * RATE - 4800]) == pytest.approx(-64, abs=2.5)
    assert rms_db(voice[3 * RATE + 4800 : 4 * RATE - 4800]) == pytest.approx(-52, abs=2.5)


def test_room_tone_follows_the_segment_gain(project, tmp_path):
    project.save_clips(
        [
            Clip(id="a", path="a.mov", duration=4, fps=30, width=64, height=64),
            Clip(id="b", path="b.mov", duration=4, fps=30, width=64, height=64, has_audio=False),
        ]
    )
    cleaned(project, "a", talk())
    voice, _ = ffmpeg.read_wav(mix.mix_voice(project, edl(("a", 0, 1), ("b", 0, 1, 0.5)), tmp_path / "voice.wav"))
    assert rms_db(voice[RATE + 4800 : 2 * RATE - 4800]) == pytest.approx(-66, abs=2)


def test_room_tone_can_be_turned_off(project, tmp_path):
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace("room_tone = true", "room_tone = false"))
    plain = Project.open(project.root)
    plain.save_clips(
        [
            Clip(id="a", path="a.mov", duration=4, fps=30, width=64, height=64),
            Clip(id="b", path="b.mov", duration=4, fps=30, width=64, height=64, has_audio=False),
        ]
    )
    cleaned(plain, "a", talk())
    voice, _ = ffmpeg.read_wav(mix.mix_voice(plain, edl(("a", 0, 1), ("b", 0, 1)), tmp_path / "voice.wav"))
    assert np.abs(voice[RATE + 1000 :]).max() == 0
    assert not (tmp_path / "voice-room.wav").exists()


def test_the_voice_report_names_the_rooms(project, tmp_path):
    cleaned(project, "a", talk())
    out = mix.mix_voice(project, edl(("a", 0, 2)), tmp_path / "voice.wav")
    report = json.loads(out.with_suffix(".json").read_text())
    assert report["room"]["a"] == pytest.approx(-60, abs=2) and "filled_seconds" in report


def test_the_room_runs_on_under_the_tail_and_fades(project, tmp_path):
    cleaned(project, "a", talk())
    voice = mix.mix_voice(project, edl(("a", 0, 2)), tmp_path / "voice.wav")
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", room=voice.with_name("voice-room.wav"), tail=3.0))[:, 0]
    assert len(out) == 5 * RATE
    assert rms_db(out[3 * RATE : 4 * RATE]) == pytest.approx(-60, abs=2)
    assert abs(out[-1]) < 1e-3 and floor_db(out[2 * RATE - 2400 : 2 * RATE + 2400]) > -70


def test_no_room_file_means_a_silent_tail(tmp_path):
    voice, _ = voice_file(tmp_path, 2)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", room=tmp_path / "no-such-room.wav", tail=1.0))[:, 0]
    assert np.abs(out[2 * RATE + 100 :]).max() == 0


def voice_file(tmp_path: Path, seconds: float = 10.0, amp: float = 0.15) -> tuple[Path, np.ndarray]:
    samples = speech_bursts(seconds, amp)
    return ffmpeg.write_wav(tmp_path / "voice.wav", samples, RATE), samples


def test_voice_alone_passes_through_as_stereo(tmp_path):
    voice, samples = voice_file(tmp_path)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav"))
    assert len(out) == len(samples)
    assert np.allclose(out[:, 0], out[:, 1]) and np.allclose(out[:, 0], samples, atol=3e-4)


def test_music_ducks_under_speech_and_comes_back(tmp_path):
    voice, _ = voice_file(tmp_path, 12)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(12, 9000, 0.2), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", music=music))[:, 0]
    during = band_rms_db(out[int(4.4 * RATE) : int(4.9 * RATE)], 9000)
    after = band_rms_db(out[int(5.62 * RATE) : int(5.74 * RATE)], 9000)  # the next line's ramp starts at 5.75 s
    assert after - during >= 6


def test_music_sits_under_the_voice_even_for_short_lines(tmp_path):
    n = 10 * RATE
    samples = np.zeros(n)
    samples[4 * RATE : int(4.3 * RATE)] = speech_bursts(0.3, 0.1, every=1, length=1)
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", samples, RATE)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(10, 9000, 0.3), RATE)
    out = mix.mix_bed(voice, tmp_path / "mix.wav", music=music)
    report = json.loads(out.with_suffix(".json").read_text())
    assert report["music"]["under_voice_db"] >= 9.5


def test_word_tails_do_not_pump_the_music(tmp_path):
    n = 12 * RATE
    t = np.arange(n) / RATE
    envelope = np.exp(-(t % 1.0) * 4) * ((t % 2.0) < 1.0)
    samples = speech_bursts(12, 0.25, every=1, length=1) * envelope
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", samples, RATE)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(12, 9000, 0.2), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", music=music))[:, 0]
    decaying = band_rms_db(out[int(4.5 * RATE) : int(4.95 * RATE)], 9000)
    open_gap = band_rms_db(out[int(7.4 * RATE) : int(7.95 * RATE)], 9000)
    assert open_gap - decaying <= 11


def test_short_key_does_not_cut_the_bed(tmp_path):
    samples = speech_bursts(10, 0.15)
    samples[3 * RATE :] = 0
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", samples, RATE)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(10, 9000, 0.2), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", music=music))
    assert len(out) == 10 * RATE
    assert rms_db(out[6 * RATE : 7 * RATE, 0]) > -50


def test_short_music_loops_to_cover_the_voice(tmp_path):
    voice, samples = voice_file(tmp_path, 8)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(1.5, 9000, 0.2), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", music=music))
    assert len(out) == len(samples)
    assert band_rms_db(out[int(3.6 * RATE) : int(3.9 * RATE), 0], 9000) > -60


def test_music_level_follows_the_voice_not_the_file(tmp_path):
    voice, _ = voice_file(tmp_path, 10)
    results = []
    for amp in (0.02, 0.4):
        music = ffmpeg.write_wav(tmp_path / f"music{amp}.wav", tone(10, 9000, amp), RATE)
        out = stereo(mix.mix_bed(voice, tmp_path / f"mix{amp}.wav", music=music))[:, 0]
        results.append(band_rms_db(out[int(5.2 * RATE) : int(5.9 * RATE)], 9000))
    assert results[0] == pytest.approx(results[1], abs=1)


def test_the_bed_runs_on_through_the_tail(tmp_path):
    voice, samples = voice_file(tmp_path, 6)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(12, 9000, 0.2), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", music=music, sfx=[{"name": "whoosh", "at": 6.0}], tail=8.0))
    assert len(out) == len(samples) + 8 * RATE
    assert rms_db(out[7 * RATE : 8 * RATE, 0]) > -50


def bed_gain_db(voice: np.ndarray, seconds: float = 12.0) -> np.ndarray:
    """What the ducking did to a steady bed, in dB every 10 ms."""
    bed = np.full((int(seconds * RATE), 2), 0.1)
    ridden, _ = mix._ride(bed, voice, -45.0)
    return 20 * np.log10(ridden[::480, 0] / 0.1)


def lines(seconds: float, *spans: tuple[float, float]) -> np.ndarray:
    voice = np.zeros(int(seconds * RATE))
    for start, end in spans:
        voice[int(start * RATE) : int(end * RATE)] = speech_bursts(end - start, 0.2, every=1, length=1)[: int(end * RATE) - int(start * RATE)]
    return voice


def test_the_music_is_down_by_the_time_the_first_word_lands():
    gain = bed_gain_db(lines(12, (5, 7)))
    assert gain[300] == pytest.approx(0, abs=0.01)
    assert gain[int(5.0 * 100)] <= gain[600] + 0.5
    assert gain[int(4.6 * 100)] > -0.5  # and it did not leave long before


def test_the_ramp_down_and_back_up_are_smooth():
    gain = bed_gain_db(lines(12, (5, 7)))
    steps = np.diff(gain) * 100  # dB per second
    assert gain.min() < -6
    assert np.abs(steps).max() < 80
    assert steps[720:].max() < steps[:600].min() * -0.6  # coming back is slower than going down


def test_the_music_stays_down_across_a_breath_between_words():
    gain = bed_gain_db(lines(12, (3, 5), (5.25, 7)))
    assert gain[int(5.1 * 100)] == pytest.approx(gain[int(4.5 * 100)], abs=1)


def test_the_music_comes_back_in_a_real_pause():
    gain = bed_gain_db(lines(12, (2, 4), (7, 9)))
    assert gain[int(5.5 * 100)] == pytest.approx(0, abs=0.1)


def test_no_speech_leaves_the_bed_alone():
    bed = np.full((4 * RATE, 2), 0.1)
    ridden, under = mix._ride(bed, np.zeros(4 * RATE), -45.0)
    assert np.array_equal(ridden, bed) and under == 0.0


def tune(seconds: float, last_hit: float = 0.2, end_silence: float = 0.0) -> np.ndarray:
    """A bed at 2 kHz that ends on a hit at 9 kHz, then whatever silence the file carries."""
    body = tone(seconds - last_hit, 2000, 0.05)
    return np.r_[body, tone(last_hit, 9000, 0.3), np.zeros(int(end_silence * RATE))]


def last_hit_at(out: np.ndarray) -> float:
    """When the 9 kHz hit starts, in seconds, read in 10 ms frames."""
    sos = np.fft.rfft(out)
    freqs = np.fft.rfftfreq(len(out), 1 / RATE)
    high = np.fft.irfft(sos * (freqs > 6000), len(out))
    level = np.sqrt(np.mean(high[: len(high) // 480 * 480].reshape(-1, 480) ** 2, axis=1))
    return float(np.argmax(level > level.max() / 2) * 480 / RATE)


def silent_voice(tmp_path: Path, seconds: float) -> Path:
    voice = np.zeros(int(seconds * RATE))
    voice[RATE // 2 : RATE] = speech_bursts(0.5, 0.15, every=1, length=1)[: RATE // 2]
    return ffmpeg.write_wav(tmp_path / "voice.wav", voice, RATE)


def test_the_end_of_the_track_lands_on_the_end_of_the_film(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tune(30), RATE)
    out = stereo(mix.mix_bed(silent_voice(tmp_path, 10), tmp_path / "mix.wav", music=music))[:, 0]
    assert len(out) == 10 * RATE
    assert last_hit_at(out) == pytest.approx(9.8, abs=0.03)


def test_the_landing_counts_the_tail_of_the_film(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tune(30), RATE)
    out = stereo(mix.mix_bed(silent_voice(tmp_path, 10), tmp_path / "mix.wav", music=music, tail=2.0))[:, 0]
    assert last_hit_at(out) == pytest.approx(11.8, abs=0.03)


def test_silence_at_the_end_of_the_file_does_not_count_as_music(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tune(30, end_silence=4.0), RATE)
    out = stereo(mix.mix_bed(silent_voice(tmp_path, 10), tmp_path / "mix.wav", music=music))[:, 0]
    assert last_hit_at(out) == pytest.approx(9.8, abs=0.03)


def test_a_short_track_loops_and_still_lands(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tune(4), RATE)
    path = mix.mix_bed(silent_voice(tmp_path, 10), tmp_path / "mix.wav", music=music)
    out = stereo(path)[:, 0]
    assert len(out) == 10 * RATE and abs(out[int(9.9 * RATE) : int(9.95 * RATE)]).max() > 0.01
    report = json.loads(path.with_suffix(".json").read_text())
    assert report["music"]["end"] == "land" and report["music"]["loops"] >= 2


def test_a_loop_join_does_not_click(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(1.5, 300, 0.2, phase=1.0), RATE)
    out = stereo(mix.mix_bed(silent_voice(tmp_path, 6), tmp_path / "mix.wav", music=music))[:, 0]
    steps = np.abs(np.diff(out[2 * RATE : 5 * RATE]))
    assert steps.max() < np.percentile(steps, 99) * 1.5


def test_fade_ending_starts_at_the_top_of_the_track(tmp_path):
    body = np.r_[tone(1, 5000, 0.1), tone(29, 2000, 0.05)]
    music = ffmpeg.write_wav(tmp_path / "music.wav", body, RATE)
    path = mix.mix_bed(silent_voice(tmp_path, 10), tmp_path / "mix.wav", music=music, music_end="fade")
    out = stereo(path)[:, 0]
    assert band_rms_db(out[int(0.6 * RATE) : int(0.9 * RATE)], 5000) > band_rms_db(out[int(0.6 * RATE) : int(0.9 * RATE)], 2000)
    assert json.loads(path.with_suffix(".json").read_text())["music"]["starts_at"] == 0
    assert rms_db(out[-RATE // 2 :]) < rms_db(out[6 * RATE : 7 * RATE]) - 6


def test_music_rises_at_the_head_and_is_gone_on_the_last_sample(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(30, 2000, 0.1), RATE)
    out = stereo(mix.mix_bed(silent_voice(tmp_path, 10), tmp_path / "mix.wav", music=music))[:, 0]
    assert abs(out[0]) < 0.001 and abs(out[-1]) < 0.001
    assert rms_db(out[: RATE // 10]) < rms_db(out[RATE : 2 * RATE]) - 6
    assert rms_db(out[-RATE // 20 :]) < rms_db(out[RATE : 2 * RATE]) - 6


def test_silent_music_is_refused(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", np.zeros(4 * RATE), RATE)
    with pytest.raises(AltocueError, match="silent"):
        mix.mix_bed(silent_voice(tmp_path, 4), tmp_path / "mix.wav", music=music)


def test_an_unknown_music_ending_is_refused(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(4, 2000, 0.1), RATE)
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(silent_voice(tmp_path, 4), tmp_path / "mix.wav", music=music, music_end="sudden")
    assert "land" in caught.value.fix


def test_mono_and_stereo_music_land_at_the_same_level(tmp_path):
    voice, _ = voice_file(tmp_path, 10)
    mono = ffmpeg.write_wav(tmp_path / "mono.wav", tone(10, 9000, 0.1), RATE)
    wide = tmp_path / "stereo.wav"
    ffmpeg.run(["-i", str(mono), "-ac", "2", "-c:a", "pcm_s16le", str(wide)])
    levels = []
    for music in (mono, wide):
        out = stereo(mix.mix_bed(voice, tmp_path / f"{music.stem}-mix.wav", music=music))[:, 0]
        levels.append(band_rms_db(out[int(5.2 * RATE) : int(5.9 * RATE)], 9000))
    assert levels[0] == pytest.approx(levels[1], abs=0.5)


def test_effect_lands_on_its_sample(tmp_path):
    voice, samples = voice_file(tmp_path)
    click = ffmpeg.write_wav(tmp_path / "click.wav", np.r_[np.ones(48) * 0.5, np.zeros(48)], RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"file": str(click), "at": 3.5}]))[:, 0]
    added = np.abs(out - samples[: len(out)])
    assert np.argmax(added > 0.001) == pytest.approx(3.5 * RATE, abs=2)


@pytest.mark.parametrize("under", [8, 10, 12])
def test_effect_sits_the_asked_distance_under_the_voice(tmp_path, under):
    voice, samples = voice_file(tmp_path, 10, 0.1)
    swell = ffmpeg.write_wav(tmp_path / "swell.wav", noise(0.5, 0.02), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"file": str(swell), "at": 5.0, "under_db": under}]))[:, 0]
    effect = ffmpeg.write_wav(tmp_path / "effect.wav", out - samples[: len(out)], RATE)
    level = ffmpeg.loudness(voice)["integrated"]
    assert mix.max_momentary(effect) == pytest.approx(level - under, abs=1.5)


def test_a_click_is_too_short_for_integrated_loudness_but_still_sounds(tmp_path):
    voice, samples = voice_file(tmp_path)
    tick = mix.sfx_path("tick")
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"name": "tick", "at": 5.2}]))[:, 0]
    added = out - samples[: len(out)]
    assert tick.exists() and np.abs(added).max() > 0.02


def test_effect_peak_ceiling_holds(tmp_path):
    voice, samples = voice_file(tmp_path)
    click = ffmpeg.write_wav(tmp_path / "click.wav", np.r_[np.ones(24), np.zeros(24)] * 0.9, RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"file": str(click), "at": 5.2}]))[:, 0]
    level = ffmpeg.loudness(voice)["integrated"]
    assert 20 * np.log10(np.abs(out - samples[: len(out)]).max()) <= level + 7 + 0.5


def test_effect_past_the_end_is_refused(tmp_path):
    voice, _ = voice_file(tmp_path, 4)
    with pytest.raises(AltocueError):
        mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"name": "pop", "at": 9.0}])


def test_effect_level_outside_the_range_is_refused(tmp_path):
    voice, _ = voice_file(tmp_path)
    with pytest.raises(AltocueError) as caught:
        mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"name": "pop", "at": 1.0, "under_db": 3}])
    assert "8 to 12" in caught.value.fix


def test_unknown_effect_lists_the_names():
    with pytest.raises(AltocueError) as caught:
        mix.sfx_path("airhorn")
    assert "whoosh" in caught.value.fix


def test_missing_voice_is_refused(tmp_path):
    with pytest.raises(AltocueError):
        mix.mix_bed(tmp_path / "nope.wav", tmp_path / "mix.wav")


def test_mix_never_clips(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", speech_bursts(8, 0.32), RATE)
    music = ffmpeg.write_wav(tmp_path / "music.wav", tone(8, 9000, 0.9), RATE)
    out = stereo(mix.mix_bed(voice, tmp_path / "mix.wav", music=music, sfx=[{"name": "thud", "at": 1.0}, {"name": "pop", "at": 1.0}]))
    assert np.abs(out).max() < 0.95


def test_bed_report_is_saved_beside_the_mix(tmp_path):
    voice, _ = voice_file(tmp_path)
    out = mix.mix_bed(voice, tmp_path / "mix.wav", sfx=[{"name": "pop", "at": 2.0}])
    report = json.loads(out.with_suffix(".json").read_text())
    assert report["sfx"][0]["name"] == "pop" and report["sfx"][0]["under_db"] == 10
    assert report["music"] is None


def test_packaged_effects_are_small_named_and_licensed():
    folder = Path(mix.__file__).parent / "data" / "sfx"
    files = sorted(folder.glob("*.ogg"))
    assert 1 <= len(files) <= 24
    assert {p.stem for p in files} >= {"pop", "whoosh", "click", "tick", "rise", "thud"}
    assert all(p.stat().st_size < 40_000 for p in files)
    notes = (folder / "README.md").read_text()
    assert "kenney.nl" in notes and "CC0" in notes
    assert mix.sfx_names() == [p.stem for p in files]


def test_packaged_effects_decode_with_stock_ffmpeg(tmp_path):
    for name in mix.sfx_names():
        wav = ffmpeg.extract_wav(mix.sfx_path(name), tmp_path / f"{name}.wav")
        samples, rate = ffmpeg.read_wav(wav)
        assert rate == RATE and 0.01 < len(samples) / RATE < 1.5 and np.abs(samples).max() > 0.1


def test_music_comes_back_up_after_the_last_word():
    voice = np.zeros(4 * RATE)
    voice[: 2 * RATE] = 0.3 * np.sin(np.arange(2 * RATE) * 0.05)
    bed = np.full((8 * RATE, 2), 0.2)
    ridden, _ = mix._ride(bed, voice, -40.0)
    assert ridden[7 * RATE, 0] == pytest.approx(0.2, abs=0.01)


# Stems


def test_stems_add_back_up_to_the_mix(project, tmp_path):
    cleaned(project, "a", speech_bursts(4))
    voice = mix.mix_voice(project, edl(("a", 0.0, 4.0)), tmp_path / "voice.wav")
    music = ffmpeg.write_wav(tmp_path / "bed.wav", tone(8, 7000, 0.2), RATE)
    whole = mix.mix_bed(voice, tmp_path / "mix.wav", music=music, sfx=[{"name": "pop", "at": 1.0}], tail=0.5, stems=tmp_path / "stems")
    parts = [stereo(tmp_path / "stems" / f"{name}.wav") for name in mix.STEMS]
    mixed = stereo(whole)
    assert all(len(part) == len(mixed) == round(4.5 * RATE) for part in parts)
    assert all(np.abs(part).max() > 0.001 for part in parts)
    assert np.abs(sum(parts) - mixed).max() < 3 / 32768  # rounding, and nothing else


def test_stems_share_the_trim_that_kept_the_mix_under_its_ceiling(project, tmp_path):
    cleaned(project, "a", tone(4, 300, 0.95))
    voice = mix.mix_voice(project, edl(("a", 0.0, 4.0)), tmp_path / "voice.wav")
    music = ffmpeg.write_wav(tmp_path / "bed.wav", tone(8, 7000, 0.9), RATE)
    whole = mix.mix_bed(voice, tmp_path / "mix.wav", music=music, stems=tmp_path / "stems")
    assert json.loads(whole.with_suffix(".json").read_text())["trim_db"] < 0
    parts = [stereo(tmp_path / "stems" / f"{name}.wav") for name in mix.STEMS]
    assert np.abs(sum(parts) - stereo(whole)).max() < 3 / 32768


def test_a_room_tail_belongs_to_the_voice_stem(project, tmp_path):
    cleaned(project, "a", speech_bursts(4) + noise(4, 0.002))
    voice = mix.mix_voice(project, edl(("a", 0.0, 4.0)), tmp_path / "voice.wav")
    room = voice.with_name(f"{voice.stem}-room.wav")
    if not room.exists():
        pytest.skip("No room tone was found in this clip.")
    mix.mix_bed(voice, tmp_path / "mix.wav", tail=1.0, room=room, stems=tmp_path / "stems")
    assert np.abs(stereo(tmp_path / "stems" / "voice.wav")[-RATE // 2 :]).max() > 0
    assert np.abs(stereo(tmp_path / "stems" / "effects.wav")).max() < 3 / 32768


def test_a_film_with_no_voice_has_stems_too(tmp_path):
    music = ffmpeg.write_wav(tmp_path / "bed.wav", tone(8, 300, 0.2), RATE)
    whole = mix.mix_bed(None, tmp_path / "mix.wav", music=music, sfx=[{"name": "pop", "at": 1.0}], seconds=4.0, stems=tmp_path / "stems")
    parts = {name: stereo(tmp_path / "stems" / f"{name}.wav") for name in mix.STEMS}
    assert np.abs(parts["voice"]).max() == 0 and np.abs(parts["music"]).max() > 0.001 and np.abs(parts["effects"]).max() > 0.001
    assert np.abs(sum(parts.values()) - stereo(whole)).max() < 3 / 32768


def test_a_film_with_nothing_but_a_voice_has_silent_stems_for_the_rest(project, tmp_path):
    cleaned(project, "a", speech_bursts(4))
    voice = mix.mix_voice(project, edl(("a", 0.0, 4.0)), tmp_path / "voice.wav")
    mix.mix_bed(voice, tmp_path / "mix.wav", stems=tmp_path / "stems")
    assert np.abs(stereo(tmp_path / "stems" / "music.wav")).max() == 0
    assert np.abs(stereo(tmp_path / "stems" / "effects.wav")).max() < 3 / 32768


def test_asking_for_stems_changes_nothing_in_the_mix(project, tmp_path):
    cleaned(project, "a", speech_bursts(4))
    voice = mix.mix_voice(project, edl(("a", 0.0, 4.0)), tmp_path / "voice.wav")
    music = ffmpeg.write_wav(tmp_path / "bed.wav", tone(8, 7000, 0.2), RATE)
    plain = mix.mix_bed(voice, tmp_path / "plain.wav", music=music, sfx=[{"name": "pop", "at": 1.0}])
    split = mix.mix_bed(voice, tmp_path / "split.wav", music=music, sfx=[{"name": "pop", "at": 1.0}], stems=tmp_path / "stems")
    assert plain.read_bytes() == split.read_bytes()
