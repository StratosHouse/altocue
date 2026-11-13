from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import clean, ffmpeg, mouth
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import Clip, Word
from altocue.project import Project, write_json

RATE = 48000


def speech(seconds: float, level: float = 0.1) -> np.ndarray:
    """Voiced bursts at a syllable rate: harmonics of a wandering pitch, with pauses."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * np.cumsum(130 + 20 * np.sin(2 * np.pi * 0.7 * t)) / RATE
    voiced = sum(np.sin(k * phase) / k for k in range(1, 25))
    gate = np.convolve((np.sin(2 * np.pi * 1.7 * t) > 0.1).astype(float), np.ones(480) / 480, mode="same")
    shaped = voiced * gate
    return shaped / np.abs(shaped).max() * level


def hiss(seconds: float, db: float, seed: int = 7) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, 10 ** (db / 20), round(seconds * RATE))


def tone(seconds: float, hz: float, db: float) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    return np.sin(2 * np.pi * hz * t) * 10 ** (db / 20) * np.sqrt(2)


def band_db(samples: np.ndarray, lo: float, hi: float) -> float:
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples)))) ** 2
    freqs = np.fft.rfftfreq(len(samples), 1 / RATE)
    return float(10 * np.log10(spectrum[(freqs >= lo) & (freqs <= hi)].sum() + 1e-12))


def lag(a: np.ndarray, b: np.ndarray, limit: int = 6000) -> int:
    """How many samples `b` runs behind `a`."""
    size = len(a) + len(b)
    corr = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    window = np.r_[corr[-limit:], corr[: limit + 1]]
    return int(np.argmax(np.abs(window))) - limit


def source(folder: Path, samples: np.ndarray, name: str = "take.mov", rate: int = RATE, stereo: bool = False) -> Clip:
    """A short video with uncompressed audio, so the fixture holds exactly these samples."""
    wav = ffmpeg.write_wav(folder / f"{name}.wav", samples, RATE)
    path = folder / name
    args = [
        "-f",
        "lavfi",
        "-i",
        f"color=c=gray:s=64x64:r=30:d={len(samples) / RATE:.3f}",
        "-i",
        str(wav),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    args += ["-af", f"aresample={rate}", "-c:a", "pcm_s16le", "-ac", "2" if stereo else "1", "-t", f"{len(samples) / RATE:.3f}"]
    ffmpeg.run([*args, str(path)])
    wav.unlink()
    return ffmpeg.probe_clip(path, clip_id=Path(name).stem)


def stub(folder: Path, body: str = "") -> str:
    """A stand-in `deep-filter`. Like the real one, `-D` loses the last 1440 samples."""
    script = folder / "deep-filter"
    script.write_text(f"""#!{sys.executable}
import sys, wave
from pathlib import Path
args = sys.argv[1:]
if "--version" in args:
    print("deep_filter 0.5.6")
    raise SystemExit(0)
out = Path(args[args.index("-o") + 1])
out.mkdir(parents=True, exist_ok=True)
source = Path(args[-1])
with wave.open(str(source), "rb") as handle:
    params = handle.getparams()
    frames = bytearray(handle.readframes(handle.getnframes()))
{body}
if "-D" in args:
    frames = frames[:-2880]
with wave.open(str(out / source.name), "wb") as handle:
    handle.setparams(params)
    handle.writeframes(bytes(frames))
""")
    script.chmod(0o755)
    return str(script)


def isolator(folder: Path, body: str = "", code: int = 0) -> str:
    """A stand-in for the Swift helper. Like the real one it keeps length and time."""
    script = folder / "isolate"
    script.write_text(f"""#!{sys.executable}
import json, shutil, sys
from pathlib import Path
args = sys.argv[1:]
if args == ["--check"]:
    print(json.dumps({{"available": True, "model": "high quality"}}))
    raise SystemExit(0)
source, dest, wet = args
Path(dest).with_name("wet.txt").write_text(wet)
{body}
if {code}:
    raise SystemExit({code})
if not Path(dest).exists():
    shutil.copy(source, dest)
""")
    script.chmod(0o755)
    return str(script)


def wav_info(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as handle:
        return handle.getframerate(), handle.getnchannels(), handle.getsampwidth(), handle.getnframes()


def test_noise_floor_reads_the_gaps_between_words():
    assert clean.noise_floor(speech(6) + hiss(6, -60), RATE) == pytest.approx(-60, abs=3)


def test_noise_floor_of_digital_silence_is_not_a_crash():
    assert clean.noise_floor(np.zeros(RATE), RATE) <= -120


@pytest.mark.parametrize("hz", [50, 60])
def test_hum_is_found_at_either_mains_frequency(hz):
    found = clean.detect_hum(speech(6) + hiss(6, -70) + tone(6, hz, -50), RATE)
    assert found is not None and found["hz"] == hz


def test_buzz_with_a_weak_fundamental_is_still_hum():
    buzz = tone(6, 120, -52) + tone(6, 180, -56) + tone(6, 60, -90)
    found = clean.detect_hum(speech(6) + hiss(6, -70) + buzz, RATE)
    assert found is not None and found["hz"] == 60


def test_no_hum_is_reported_on_clean_speech():
    assert clean.detect_hum(speech(6) + hiss(6, -70), RATE) is None


def test_a_hum_far_below_the_noise_is_left_alone():
    assert clean.detect_hum(speech(6) + hiss(6, -50) + tone(6, 60, -95), RATE) is None


def test_the_quietest_stretch_is_room_and_never_half_digital_silence():
    samples = speech(4, 0.15) + hiss(4, -60)
    samples[: RATE // 4] = 0.0
    stretch = clean.quietest(samples.astype(np.float32), RATE)
    assert 20 * np.log10(np.sqrt(np.mean(np.square(stretch.astype(np.float64))))) == pytest.approx(-60, abs=2)


def test_harder_work_for_worse_audio():
    order = [clean.plan_denoise(snr)["strength"] for snr in (60, 38, 26, 8)]
    assert order == ["none", "light", "normal", "strong"]
    assert clean.plan_denoise(38)["atten_db"] < clean.plan_denoise(8)["atten_db"]


@pytest.mark.parametrize(("snr", "wet"), [(60, 0.0), (38, 68.4), (26, 87.4), (8, 96.8)])
def test_the_wet_mix_leaves_the_same_noise_as_the_attenuation_limit(snr, wet):
    assert clean.plan_denoise(snr)["wet"] == pytest.approx(wet, abs=0.05)


def test_tone_chain_notches_only_when_hum_was_found():
    assert "bandreject" not in clean.tone_filters(None)
    chain = clean.tone_filters({"hz": 50})
    assert [f"bandreject=f={hz}" in chain for hz in (50, 100, 150)] == [True, True, True]


def test_tone_chain_uses_stock_filters_only():
    names = {part.split("=")[0] for part in clean.tone_filters({"hz": 60}).split(",")}
    assert names <= ffmpeg.filters()
    assert not names & {"drawtext", "subtitles", "zscale"}


def test_the_tone_chain_no_longer_carries_ffmpegs_de_esser():
    for name in clean.DEESS:
        assert "deesser" not in clean.tone_filters(None, name)


def test_an_unknown_de_ess_strength_is_refused():
    with pytest.raises(AltocueError) as caught:
        clean.tone_filters(None, "extreme")
    assert "strong" in caught.value.fix


def vowel(seconds: float, body_db: float, f0: float = 130.0) -> np.ndarray:
    """A held vowel: harmonics under two formants, its 200 to 3000 Hz body at `body_db`, with short fades."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * np.cumsum(f0 + 8 * np.sin(2 * np.pi * 3 * t)) / RATE
    ks = np.arange(1, int(5000 / f0))
    amps = 1 / (1 + ((ks * f0 - 600) / 500) ** 2) + 0.4 / (1 + ((ks * f0 - 1700) / 500) ** 2) + 0.02
    body = sum(a**2 / 2 for k, a in zip(ks, amps, strict=True) if 200 <= k * f0 <= 3000)
    wave = sum(a * np.sin(k * phase) for k, a in zip(ks, amps, strict=True)) * 10 ** (body_db / 20) / np.sqrt(body)
    fade = np.minimum(1, np.minimum(np.arange(n), np.arange(n)[::-1]) / (0.02 * RATE))
    return wave * fade


def hush_burst(seconds: float, level_db: float, seed: int, low: float = 4500, high: float = 9000) -> np.ndarray:
    """Band-limited noise at `level_db` RMS with 15 ms fades: an S, or a plosive's burst when it is short."""
    n = int(seconds * RATE)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    noise = np.fft.irfft(np.fft.rfft(np.random.default_rng(seed).normal(0, 1, n)) * ((freqs > low) & (freqs < high)), n)
    edge = min(int(0.015 * RATE), n // 2)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, edge))
    envelope = np.ones(n)
    envelope[:edge], envelope[n - edge :] = ramp, ramp[::-1]
    return noise / noise.std() * 10 ** (level_db / 20) * envelope


def sibilant_line(s_to_voice: float | list[float], count: int = 6, body_db: float = -26.0) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Vowel, S, vowel, S... with each S at `s_to_voice` dB against the vowels' body, or one level per S. Returns where each S sits."""
    levels = s_to_voice if isinstance(s_to_voice, list) else [s_to_voice] * count
    parts, where, at = [], [], 0
    for index, level in enumerate(levels):
        held, hiss_ = vowel(0.3, body_db), hush_burst(0.12, body_db + level, seed=index + 10)
        parts += [held, hiss_]
        where.append((at + len(held), at + len(held) + len(hiss_)))
        at += len(held) + len(hiss_)
    parts.append(vowel(0.3, body_db))
    voice = np.concatenate(parts) + np.random.default_rng(5).normal(0, 10 ** (-75 / 20), at + int(0.3 * RATE))
    return voice.astype(np.float32), where


def s_band(samples: np.ndarray, where: list[tuple[int, int]]) -> float:
    return float(np.mean([band_db(samples[a:b], 3500, 12000) for a, b in where]))


@pytest.mark.parametrize("strength", ["light", "normal", "strong"])
@pytest.mark.parametrize("level", [-10.0, -18.0])
def test_natural_and_soft_s_sounds_are_left_exactly_alone_on_every_strength(strength, level):
    """A good wireless lavalier with natural S sounds needs nothing, and a soft S must never be made softer."""
    voice, _ = sibilant_line(level)
    out, said = mouth.deess(voice, RATE, strength)
    assert out is voice
    assert said["sibilants"] == 6 and said["softened"] == 0 and said["most_db"] == 0
    assert said["s_to_voice_db"][0] == pytest.approx(level, abs=1.0)  # the measure reads what was built
    assert said["s_to_voice_db"][0] == said["s_to_voice_db"][1]


@pytest.mark.parametrize("strength", ["light", "normal", "strong"])
def test_natural_s_sounds_are_not_ranked_against_each_other(strength):
    """The first de-esser turned the louder half of any voice's S sounds down by up to 5 dB, harsh or not. That was the lisp."""
    voice, _ = sibilant_line([-14.0, -12.0, -10.0, -8.0, -6.0, -10.0, -12.0, -9.0])
    out, said = mouth.deess(voice, RATE, strength)
    assert out is voice and said["sibilants"] == 8 and said["softened"] == 0


@pytest.mark.parametrize("strength", ["light", "normal", "strong"])
def test_only_the_harsh_s_sounds_in_a_line_are_softened(strength):
    levels = [-10.0, 2.0, -12.0, 0.0, -8.0, 3.0]
    voice, where = sibilant_line(levels)
    out, said = mouth.deess(voice, RATE, strength)
    for (a, b), level in zip(where, levels, strict=True):
        drop = band_db(voice[a:b], 3500, 12000) - band_db(out[a:b], 3500, 12000)
        if level <= mouth.DEESS_CEILING[strength]:
            assert drop < 0.1, (level, drop)
        else:
            assert 0.5 < drop <= mouth.DEESS_DB[strength] + 0.1, (level, drop)
    assert said["softened"] == sum(level > mouth.DEESS_CEILING[strength] + mouth.LEAST_DB for level in levels)


@pytest.mark.parametrize("strength", ["light", "normal", "strong"])
def test_a_harsh_s_comes_down_toward_the_ceiling_and_no_further_than_the_cap(strength):
    voice, where = sibilant_line(0.0)
    out, said = mouth.deess(voice, RATE, strength)
    before, after = said["s_to_voice_db"]
    ceiling, cap = mouth.DEESS_CEILING[strength], mouth.DEESS_DB[strength]
    assert said["softened"] == 6 and 0 < said["most_db"] <= cap
    assert after < before - 1.0  # it did come down
    assert after >= max(ceiling, before - cap) - 0.6  # toward the ceiling, never past it, never more than the cap
    assert after >= mouth.LISP_FLOOR_DB and said["lowest_softened_db"] >= mouth.LISP_FLOOR_DB
    assert s_band(voice, where) - s_band(out, where) <= cap + 0.1
    assert said["held"] == (6 if before - ceiling > cap else 0)
    assert 3500 <= said["centre_hz"] <= 9500


def test_light_is_the_smallest_softening_and_strong_the_most():
    voice, where = sibilant_line(0.0)
    drops = [s_band(voice, where) - s_band(mouth.deess(voice, RATE, name)[0], where) for name in ("light", "normal", "strong")]
    assert 0.5 < drops[0] < drops[1] < drops[2] <= mouth.GUARD_MOST_DB + 0.1
    assert drops[0] <= mouth.DEESS_DB["light"] + 0.1


def test_strong_never_takes_an_s_into_a_lisp():
    """However harsh the S, strong takes at most its cap. An old project that says strong cannot lisp."""
    voice, _ = sibilant_line(8.0)
    _, said = mouth.deess(voice, RATE, "strong")
    assert said["most_db"] <= mouth.GUARD_MOST_DB
    assert said["lowest_softened_db"] >= mouth.LISP_FLOOR_DB and said["held"] == 6
    assert said["loudest_db"][1] > mouth.DEESS_CEILING["strong"]  # still harsh, and the report says so


def test_the_lisp_floor_holds_even_when_a_strength_asks_for_more(monkeypatch):
    monkeypatch.setitem(mouth.DEESS_CEILING, "strong", -30.0)
    monkeypatch.setitem(mouth.DEESS_DB, "strong", 30.0)
    monkeypatch.setattr(mouth, "GUARD_MOST_DB", 30.0)
    voice, _ = sibilant_line(-6.0)
    _, said = mouth.deess(voice, RATE, "strong")
    assert said["softened"] == 6 and said["held"] == 6
    assert said["lowest_softened_db"] >= mouth.LISP_FLOOR_DB - 0.1
    assert said["s_to_voice_db"][1] >= mouth.LISP_FLOOR_DB - 0.1
    assert mouth.plan_cut(-6.0, "strong") == (pytest.approx(4.0), True)
    assert mouth.plan_cut(-11.0, "strong") == (0.0, True)  # already under the floor: never touched


def test_vowels_are_unchanged_and_only_the_s_band_moves():
    voice, where = sibilant_line(0.0)
    out, _ = mouth.deess(voice, RATE, "strong")
    reach = mouth.SIZE + mouth.STEP  # an S's frames hear this far either side of it
    untouched = np.ones(len(voice), bool)
    for a, b in where:
        untouched[max(0, a - reach) : b + reach] = False
    assert np.array_equal(out[untouched], voice[untouched])  # every vowel sample away from an S, bit for bit
    for (_, b), (a, _) in zip(where, [*where[1:], (len(voice), 0)], strict=True):
        held = slice(b, a)  # the whole vowel after each S, edges included
        assert abs(band_db(out[held], 100, 3000) - band_db(voice[held], 100, 3000)) < 0.05


def test_softening_eases_in_and_out_with_no_click_at_the_edges():
    voice, where = sibilant_line(6.0)
    out, _ = mouth.deess(voice, RATE, "strong")
    change = (out - voice).astype(np.float64)
    assert band_db(change, 20, 2500) < band_db(change, 3000, 16000) - 40  # a click would spread down into the voice's body
    for a, b in where:
        edge = change[a - 480 : a + 480]
        assert np.abs(edge).max() < np.abs(change[a:b]).max()  # nothing jumps at the onset


def released(burst_seconds: float) -> np.ndarray:
    """Vowel, a closure of silence, a release burst louder than the voice, vowel: four times over, then a breath in a pause."""
    parts = []
    for index in range(4):
        parts += [vowel(0.3, -26.0), np.zeros(int(0.05 * RATE)), hush_burst(burst_seconds, -22.0, seed=index, low=2500, high=10000)]
    breath = hush_burst(0.4, -40.0, seed=9, low=300, high=8000)
    parts += [vowel(0.3, -26.0), np.zeros(RATE // 5), breath, np.zeros(RATE // 5), vowel(0.3, -26.0)]
    voice = np.concatenate(parts)
    return (voice + np.random.default_rng(5).normal(0, 10 ** (-75 / 20), len(voice))).astype(np.float32)


def test_a_plosive_burst_and_a_breath_are_not_s_sounds():
    voice = released(0.012)  # a harsh /t/ release
    found = mouth.find_sibilants(voice, RATE)
    assert found is not None and found.runs == []
    out, said = mouth.deess(voice, RATE, "strong")
    assert out is voice and said["softened"] == 0
    assert len(mouth.find_sibilants(released(0.1), RATE).runs) == 4  # the same sound held as long as an S is one


def test_too_little_voice_to_judge_by_is_left_alone():
    noise = hush_burst(1.0, -30.0, seed=1).astype(np.float32)
    out, said = mouth.deess(noise, RATE, "strong")
    assert out is noise and said["s_to_voice_db"] == [None, None] and mouth.s_to_voice(noise, RATE) is None


def test_de_essing_off_returns_the_samples_untouched_and_still_measures():
    voice, _ = sibilant_line(0.0)
    out, said = mouth.deess(voice, RATE, "off")
    assert out is voice and said["softened"] == 0 and said["s_to_voice_db"][0] == pytest.approx(0.0, abs=1.0)


def test_an_unknown_strength_is_refused_by_the_de_esser():
    with pytest.raises(AltocueError) as caught:
        mouth.deess(sibilant_line(0.0)[0], RATE, "extreme")
    assert "light" in caught.value.fix


def test_the_mouth_report_says_what_the_de_esser_measured_and_held():
    voice, _ = sibilant_line(8.0)
    _, report = mouth.clean_mouth(voice, RATE, None, False, "strong")
    assert report["sibilants"] == 6 and report["sibilants_softened"] == 6 and report["deess_held"] == 6
    assert report["s_to_voice_db"][1] < report["s_to_voice_db"][0] and report["deess_lowest_db"] >= mouth.LISP_FLOOR_DB
    assert report["deess_limits"] == {"ceiling_db": -6.0, "most_db": 5.0, "floor_db": mouth.LISP_FLOOR_DB}
    assert report["sibilance_db"][1] < report["sibilance_db"][0]
    line = clean.sibilance_note(report)
    assert "6 of 6 S sounds softened" in line and "lisp guard held back 6" in line


def test_the_summary_says_plainly_when_nothing_needed_softening():
    _, report = mouth.clean_mouth(sibilant_line(-10.0)[0], RATE, None, False, "light")
    assert clean.sibilance_note(report).startswith(", no S sound needed softening (S to voice -10")
    assert clean.sibilance_note({"deess_most_db": 7.0}) == ", S sounds down up to 7.0 dB"  # an old report still reads


def test_every_strength_keeps_its_name_and_the_caps_are_small():
    assert list(clean.DEESS) == ["off", "light", "normal", "strong"]
    assert clean.DEESS["light"] < clean.DEESS["normal"] < clean.DEESS["strong"] == mouth.GUARD_MOST_DB <= 6
    assert all(ceiling is None or ceiling > mouth.LISP_FLOOR_DB for ceiling in mouth.DEESS_CEILING.values())


def clicked(seconds: float = 6.0) -> tuple[np.ndarray, list[float]]:
    """Room tone with a phrase, and three short clicks standing alone in the quiet."""
    voice = hiss(seconds, -62).astype(np.float32)
    phrase = speech(2.35, 0.15)  # four whole syllables, so none is cut short into a burst
    voice[int(2.6 * RATE) : int(2.6 * RATE) + len(phrase)] += phrase
    at = [0.8, 1.5, 5.6]
    for t in at:
        i = int(t * RATE)
        voice[i : i + 96] += np.hanning(96) * np.sign(np.sin(np.arange(96))) * 0.3
    return voice, at


def test_mouth_clicks_alone_in_quiet_are_found_and_turned_down():
    voice, at = clicked()
    found = mouth.find_clicks(voice, RATE)
    assert [round(start, 1) for start, _ in found] == at
    out = mouth.quiet_clicks(voice, RATE, found)
    for t in at:
        i = int(t * RATE)
        assert np.abs(out[i : i + 96]).max() < np.abs(voice[i : i + 96]).max() / 20
    assert len(out) == len(voice)


def test_a_burst_just_before_a_word_is_kept_as_its_plosive():
    voice, _ = clicked()
    found = mouth.find_clicks(voice, RATE, words=[(1.55, 1.9), (2.6, 5.0)])
    assert [round(start, 1) for start, _ in found] == [0.8, 5.6]


def test_speech_has_no_clicks():
    assert mouth.find_clicks(speech(4, 0.1) + hiss(4, -62), RATE) == []


def test_output_is_mono_48k_16_bit_and_the_exact_source_length(tmp_path):
    clip = source(tmp_path, speech(3) + hiss(3, -60), rate=44100, stereo=True)
    dest = tmp_path / "out.wav"
    clean.clean_clip(clip, dest, deep_filter=None)
    assert wav_info(dest) == (48000, 1, 2, round(clip.duration * RATE))


def test_afftdn_drops_the_noise_and_keeps_the_speech(tmp_path):
    clip = source(tmp_path, speech(6, 0.15) + hiss(6, -45))
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None)
    assert report["engine"] == "afftdn" and report["strength"] in {"normal", "light"}
    assert report["noise_drop_db"] >= 6
    assert report["speech_retained_db"] > -3
    assert report["clipped_after"] == 0


def test_clipped_speech_is_redrawn_before_the_denoiser_and_reported(tmp_path):
    clip = source(tmp_path, np.clip(speech(3, 1.4), -1, 1) + hiss(3, -60))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None)
    fixed = report["declip"]
    assert fixed["runs"] > 0 and fixed["repaired"] > 0 and fixed["samples"] >= 3 * fixed["runs"] and fixed["longest_ms"] > 0
    assert fixed["headroom_db"] < 0 and report["clipped_before"] > 0 and report["clipped_after"] == 0
    assert "clipped peaks redrawn" in clean.describe(report)


def test_a_clip_with_no_flat_tops_goes_through_declipping_exactly_as_it_was(tmp_path):
    samples = (speech(3, 0.5) + hiss(3, -60)).astype(np.float32)
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", samples, RATE)
    path, back, report = clean._declip(raw, samples, tmp_path)
    assert path == raw and back is samples and report["runs"] == 0 and not (tmp_path / "declipped.wav").exists()


def test_afftdn_delay_is_compensated(tmp_path):
    original = speech(4, 0.15) + hiss(4, -48)
    clip = source(tmp_path, original)
    dest = tmp_path / "out.wav"
    clean.clean_clip(clip, dest, deep_filter=None)
    cleaned, _ = ffmpeg.read_wav(dest)
    assert abs(lag(original, cleaned)) <= 12


def test_audio_that_starts_late_stays_late(tmp_path):
    made = source(tmp_path, speech(2, 0.2))
    late = tmp_path / "late.mov"
    ffmpeg.run(["-i", made.path, "-itsoffset", "0.5", "-i", made.path, "-map", "0:v", "-map", "1:a", "-c", "copy", str(late)])
    clip = Clip(id="late", path=str(late), duration=2.5, fps=30, width=64, height=64)
    dest = tmp_path / "out.wav"
    clean.clean_clip(clip, dest, deep_filter=None, denoise=False)
    cleaned, _ = ffmpeg.read_wav(dest)
    onset = np.argmax(np.abs(cleaned) > 0.02) / RATE
    original = np.argmax(np.abs(speech(2, 0.2)) > 0.02) / RATE
    assert len(cleaned) == 2.5 * RATE
    assert onset == pytest.approx(original + 0.5, abs=0.02)


def test_hum_is_notched_in_the_output(tmp_path):
    original = speech(6, 0.15) + hiss(6, -68) + tone(6, 60, -42) + tone(6, 120, -48) + tone(6, 180, -54)
    clip = source(tmp_path, original)
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None, denoise=False)
    cleaned, _ = ffmpeg.read_wav(dest)
    assert report["hum_hz"] == 60
    level_shift = report["level_lufs"] - report["level_before_lufs"]
    assert band_db(cleaned, 58, 62) - level_shift < band_db(original, 58, 62) - 20


def test_clips_at_different_levels_end_up_together(tmp_path):
    quiet = clean.clean_clip(source(tmp_path, speech(5, 0.01) + hiss(5, -75), "quiet.mov"), tmp_path / "q.wav", deep_filter=None)
    loud = clean.clean_clip(source(tmp_path, speech(5, 0.5) + hiss(5, -60), "loud.mov"), tmp_path / "l.wav", deep_filter=None)
    assert abs(quiet["level_lufs"] - loud["level_lufs"]) < 1
    assert quiet["level_lufs"] == pytest.approx(clean.VOICE_LUFS, abs=1)


def aside(tmp_path: Path) -> Clip:
    from test_level import hush, line

    takes = [hush(0.5)]
    for index in range(10):
        takes += [line(3, -18 if index % 2 else -32, seed=index), hush(0.6)]
    voice = np.concatenate(takes)
    return source(tmp_path, voice + np.random.default_rng(1).normal(0, 10 ** (-72 / 20), len(voice)), "aside.mov")


def test_phrases_are_levelled_and_the_report_shows_the_range_fall(tmp_path):
    report = clean.clean_clip(aside(tmp_path), tmp_path / "out.wav", deep_filter=None, denoise=False)
    assert report["phrases"] >= 10 and report["most_boost_db"] > 3
    assert report["range_after"] <= report["range_before"] - 5
    assert report["level_lufs"] == pytest.approx(clean.VOICE_LUFS, abs=1)


def test_phrase_levelling_can_be_turned_off(tmp_path):
    clip = aside(tmp_path)
    on = clean.clean_clip(clip, tmp_path / "on.wav", deep_filter=None, denoise=False)
    off = clean.clean_clip(clip, tmp_path / "off.wav", deep_filter=None, denoise=False, phrases=False)
    assert off["most_boost_db"] == 0 and off["range_after"] > on["range_after"] + 3


def test_breaths_come_down_only_when_asked(tmp_path):
    from test_level import breath, hush, line

    voice = np.concatenate([hush(0.5), line(2, -24), hush(0.4), breath(), hush(0.4), line(2, -24, seed=2), hush(0.5)])
    clip = source(tmp_path, voice)
    spot = slice(int(2.95 * RATE), int(3.2 * RATE))
    kept = clean.clean_clip(clip, tmp_path / "kept.wav", deep_filter=None, denoise=False)
    down = clean.clean_clip(clip, tmp_path / "down.wav", deep_filter=None, denoise=False, breath_down_db=9.0)
    assert kept["breaths"] == down["breaths"] == 1
    a, b = ffmpeg.read_wav(tmp_path / "kept.wav")[0][spot], ffmpeg.read_wav(tmp_path / "down.wav")[0][spot]
    assert 20 * np.log10(np.sqrt(np.mean(a**2)) / np.sqrt(np.mean(b**2))) >= 6


def test_transcript_times_reach_the_breath_finder(tmp_path):
    from test_level import breath, hush, line

    voice = np.concatenate([hush(0.5), line(2, -24), hush(0.4), breath(), hush(0.4), line(2, -24, seed=2), hush(0.5)])
    clip = source(tmp_path, voice)
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None, denoise=False, words=[(0.5, 2.5), (2.9, 3.25), (3.65, 5.65)])
    assert report["breaths"] == 0


def test_clean_audio_skips_the_denoiser(tmp_path):
    called = tmp_path / "called"
    binary = stub(tmp_path, f"Path({str(called)!r}).write_text('x')")
    clip = source(tmp_path, speech(4, 0.3))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=binary)
    assert report["strength"] == "none" and report["engine"] == "none"
    assert not called.exists()


def test_deep_filter_output_keeps_length_and_time(tmp_path):
    original = speech(4, 0.15) + hiss(4, -48)
    clip = source(tmp_path, original)
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=stub(tmp_path))
    cleaned, _ = ffmpeg.read_wav(dest)
    assert report["engine"] == "deep-filter"
    assert len(cleaned) == len(original)
    assert abs(lag(original, cleaned)) <= 12


def test_isolation_output_keeps_length_and_time(tmp_path):
    original = speech(4, 0.15) + hiss(4, -48)
    clip = source(tmp_path, original)
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None, isolate=isolator(tmp_path))
    cleaned, _ = ffmpeg.read_wav(dest)
    assert report["engine"] == "apple"
    assert len(cleaned) == len(original)
    assert abs(lag(original, cleaned)) <= 12


def test_isolation_gets_the_wet_mix_the_plan_asked_for(tmp_path):
    seen = tmp_path / "seen.txt"
    binary = isolator(tmp_path, f"Path({str(seen)!r}).write_text(wet)")
    clip = source(tmp_path, speech(4, 0.15) + hiss(4, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None, isolate=binary)
    assert float(seen.read_text()) == pytest.approx(clean.plan_denoise(report["snr_before"])["wet"], abs=0.1)


def test_clean_audio_skips_isolation(tmp_path):
    called = tmp_path / "called"
    binary = isolator(tmp_path, f"Path({str(called)!r}).write_text('x')")
    report = clean.clean_clip(source(tmp_path, speech(4, 0.3)), tmp_path / "out.wav", deep_filter=None, isolate=binary)
    assert report["engine"] == "none" and not called.exists()


def test_isolation_that_eats_speech_hands_over_to_the_next_engine(tmp_path):
    silence = (
        "import wave\n"
        "with wave.open(source, 'rb') as h: p, f = h.getparams(), h.readframes(h.getnframes())\n"
        "with wave.open(dest, 'wb') as o: o.setparams(p); o.writeframes(bytes(len(f)))"
    )
    clip = source(tmp_path, speech(5, 0.15) + hiss(5, -45))
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None, isolate=isolator(tmp_path, silence))
    assert report["engine"] == "afftdn" and report["kept_original"] is False
    assert any("took too much speech" in note for note in report["warnings"])
    assert np.abs(ffmpeg.read_wav(dest)[0]).max() > 0.05


def test_isolation_that_fails_falls_back_with_a_warning(tmp_path):
    clip = source(tmp_path, speech(4, 0.15) + hiss(4, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None, isolate=isolator(tmp_path, code=3))
    assert report["engine"] == "afftdn"
    assert any("voice isolation" in note for note in report["warnings"])


def test_isolation_of_the_wrong_length_falls_back(tmp_path):
    short = "import wave\nwith wave.open(source, 'rb') as h: p, f = h.getparams(), h.readframes(1000)\nwith wave.open(dest, 'wb') as o: o.setparams(p); o.writeframes(f)"
    clip = source(tmp_path, speech(4, 0.15) + hiss(4, -45))
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None, isolate=isolator(tmp_path, short))
    assert report["engine"] == "afftdn" and wav_info(dest)[3] == 4 * RATE


def test_failed_isolation_tries_deep_filter_next(tmp_path):
    clip = source(tmp_path, speech(4, 0.15) + hiss(4, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=stub(tmp_path), isolate=isolator(tmp_path, code=3))
    assert report["engine"] == "deep-filter"


def test_a_denoiser_that_eats_speech_is_rejected(tmp_path):
    binary = stub(tmp_path, "frames = bytearray(len(frames))")
    clip = source(tmp_path, speech(5, 0.15) + hiss(5, -45))
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=binary)
    cleaned, _ = ffmpeg.read_wav(dest)
    assert report["engine"] == "none" and report["kept_original"] is True
    assert any("kept the original" in note for note in report["warnings"])
    assert np.abs(cleaned).max() > 0.05


def test_a_clip_with_no_words_keeps_its_own_sound_and_level(tmp_path):
    original = hiss(3, -40)
    clip = source(tmp_path, original)
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None, spoken=False)
    cleaned, _ = ffmpeg.read_wav(dest)
    assert report["status"] == "no_speech" and report["engine"] == "none"
    assert np.allclose(cleaned, original, atol=3e-4)


def test_a_denoiser_that_finds_nothing_to_keep_means_no_speech(tmp_path):
    binary = stub(tmp_path, "frames = bytearray(len(frames))")
    original = speech(5, 0.02) + hiss(5, -45)
    clip = source(tmp_path, original)
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=binary)
    cleaned, _ = ffmpeg.read_wav(dest)
    assert report["status"] == "no_speech" and report["kept_original"] is True
    assert np.allclose(cleaned, original, atol=3e-4)


def test_a_denoiser_that_eats_a_quarter_of_the_speech_is_rejected(tmp_path):
    binary = stub(tmp_path, "frames[len(frames) // 2: len(frames) * 3 // 4] = bytes(len(frames) * 3 // 4 - len(frames) // 2)")
    clip = source(tmp_path, speech(8, 0.15) + hiss(8, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=binary)
    assert report["kept_original"] is True and report["status"] == "cleaned"


def test_a_broken_binary_falls_back_with_a_warning(tmp_path):
    script = tmp_path / "deep-filter"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(0o755)
    clip = source(tmp_path, speech(4, 0.15) + hiss(4, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=str(script))
    assert report["engine"] == "afftdn"
    assert any("deep-filter" in note for note in report["warnings"])


def test_denoise_can_be_turned_off(tmp_path):
    clip = source(tmp_path, speech(4, 0.15) + hiss(4, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None, denoise=False)
    assert report["engine"] == "none" and report["strength"] == "none"


def test_silent_audio_is_reported_and_keeps_its_length(tmp_path):
    clip = source(tmp_path, np.zeros(RATE * 2))
    dest = tmp_path / "out.wav"
    report = clean.clean_clip(clip, dest, deep_filter=None)
    assert report["status"] == "silent"
    assert wav_info(dest)[3] == round(clip.duration * RATE)


@pytest.mark.parametrize("seconds", [0.01, 0.1, 0.3])
def test_very_short_clips_are_not_a_crash(tmp_path, seconds):
    clip = source(tmp_path, speech(seconds, 0.2) + hiss(seconds, -50))
    dest = tmp_path / "out.wav"
    clean.clean_clip(clip, dest, deep_filter=None)
    assert wav_info(dest)[3] == round(clip.duration * RATE)


def test_hum_scan_stays_small_on_a_long_clip():
    long_take = np.tile(speech(4, 0.1) + hiss(4, -60) + tone(4, 60, -50), 150)
    assert clean.detect_hum(long_take, RATE)["hz"] == 60


def machine(monkeypatch: pytest.MonkeyPatch, *, apple: str | None = None, deep_filter: str | None = None) -> None:
    monkeypatch.setattr(clean, "find_isolator", lambda: apple)
    monkeypatch.setattr(clean, "find_deep_filter", lambda: deep_filter)


def test_auto_uses_apple_voice_isolation_where_the_mac_has_it(monkeypatch, tmp_path):
    machine(monkeypatch, apple="/iso")
    assert clean.choose_denoiser() == ("apple", "/iso", [])


def test_auto_uses_deep_filter_where_there_is_no_apple_unit(monkeypatch, tmp_path):
    binary = stub(tmp_path)
    machine(monkeypatch, deep_filter=binary)
    assert clean.choose_denoiser() == ("deep-filter", binary, [])


def test_auto_falls_back_to_stock_ffmpeg(monkeypatch):
    machine(monkeypatch)
    assert clean.choose_denoiser() == ("afftdn", None, [])


def test_a_named_engine_wins_over_auto(monkeypatch, tmp_path):
    binary = stub(tmp_path)
    machine(monkeypatch, apple="/iso", deep_filter=binary)
    assert clean.choose_denoiser("deep-filter") == ("deep-filter", binary, [])
    assert clean.choose_denoiser("afftdn") == ("afftdn", None, [])


def test_a_named_engine_this_machine_lacks_says_so_and_falls_back(monkeypatch):
    machine(monkeypatch)
    name, _, notes = clean.choose_denoiser("apple")
    assert name == "afftdn" and "Apple voice isolation" in notes[0]


def test_a_broken_deep_filter_is_passed_over_with_a_note(monkeypatch, tmp_path):
    script = tmp_path / "deep-filter"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(0o755)
    machine(monkeypatch, deep_filter=str(script))
    name, _, notes = clean.choose_denoiser()
    assert name == "afftdn" and "deep-filter" in notes[0]


def test_an_unknown_engine_is_refused():
    with pytest.raises(AltocueError) as caught:
        clean.choose_denoiser("magic")
    assert "deep-filter" in caught.value.fix


def test_the_isolator_needs_a_working_audio_unit(monkeypatch, tmp_path):
    monkeypatch.setattr(clean.swift, "compiled", lambda source, name: Path(isolator(tmp_path)))
    clean.find_isolator.cache_clear()
    assert clean.find_isolator() == str(tmp_path / "isolate")
    monkeypatch.setattr(clean.swift, "compiled", lambda source, name: Path("/usr/bin/false"))
    clean.find_isolator.cache_clear()
    assert clean.find_isolator() is None
    clean.find_isolator.cache_clear()


def test_the_isolator_needs_a_mac_and_a_compiler(monkeypatch):
    monkeypatch.setattr(clean.swift, "compiled", lambda source, name: None)
    clean.find_isolator.cache_clear()
    assert clean.find_isolator() is None
    clean.find_isolator.cache_clear()


def test_setup_downloads_nothing_where_the_mac_already_has_isolation(monkeypatch, capsys):
    from altocue import setup

    machine(monkeypatch, apple="/iso")
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded"))
    setup.install_denoiser(yes=True)
    assert "Apple voice isolation" in capsys.readouterr().out


def test_setup_can_still_fetch_deep_filter_on_a_mac(monkeypatch):
    from altocue import setup

    machine(monkeypatch, apple="/iso", deep_filter="/already/there")
    setup.install_denoiser(yes=True, even_on_a_mac=True)


def test_doctor_names_the_backend_that_applies(monkeypatch, tmp_path):
    from altocue import doctor

    machine(monkeypatch, apple="/iso")
    monkeypatch.setattr(clean, "isolator_model", lambda: "high quality")
    found = doctor.check_denoiser()
    assert found.id == "denoiser" and found.ok and "Apple voice isolation" in found.detail and "high quality" in found.detail

    binary = stub(tmp_path)
    machine(monkeypatch, deep_filter=binary)
    found = doctor.check_denoiser()
    assert found.ok and "deep-filter" in found.detail and binary in found.detail

    machine(monkeypatch)
    found = doctor.check_denoiser()
    assert not found.ok and not found.required and "afftdn" in found.detail and "altocue setup" in found.fix


def test_find_deep_filter_prefers_the_override(tmp_path, monkeypatch):
    binary = stub(tmp_path)
    monkeypatch.setenv("ALTOCUE_DEEP_FILTER", binary)
    assert clean.find_deep_filter() == binary


def test_find_deep_filter_uses_path(tmp_path, monkeypatch):
    binary = stub(tmp_path)
    monkeypatch.delenv("ALTOCUE_DEEP_FILTER", raising=False)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert clean.find_deep_filter() == binary


def test_find_deep_filter_reports_absence(tmp_path, monkeypatch):
    monkeypatch.delenv("ALTOCUE_DEEP_FILTER", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(clean, "DEEP_FILTER_HOME", tmp_path / "nope")
    assert clean.find_deep_filter() is None


def test_release_pins_are_official_and_hashed():
    assert clean.DEEP_FILTER_VERSION == "0.5.6"
    for release in clean.DEEP_FILTER_RELEASES.values():
        assert release["url"].startswith("https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-")
        assert len(release["sha256"]) == 64 and int(release["sha256"], 16) >= 0
    assert {"darwin-arm64", "darwin-x86_64", "linux-x86_64", "linux-aarch64"} <= set(clean.DEEP_FILTER_RELEASES)


def test_setup_installs_where_find_looks():
    from altocue import setup

    assert clean.DEEP_FILTER_HOME == setup.HOME / "bin"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.delenv("ALTOCUE_DEEP_FILTER", raising=False)
    machine(monkeypatch)
    media = tmp_path / "media"
    media.mkdir()
    noisy = source(media, speech(3, 0.15) + hiss(3, -48), "noisy.mov")
    mute = media / "mute.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=gray:s=64x64:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mute)])
    folder = tmp_path / "video"
    assert main(["new", str(folder), "--input", noisy.path, str(mute)]) == 0
    return Project.open(folder)


def test_clean_project_reports_every_clip_and_saves_it(project):
    report = clean.clean_project(project)
    assert report["clips"]["mute"]["status"] == "no_audio"
    entry = report["clips"]["noisy"]
    assert entry["status"] == "cleaned" and entry["engine"] == "afftdn"
    assert {"noise_floor_before", "noise_floor_after", "hum_hz", "engine"} <= set(entry)
    assert (project.audio_dir / "noisy.wav").exists()
    assert not (project.audio_dir / "mute.wav").exists()
    assert json.loads((project.audio_dir / "report.json").read_text()) == report


def test_clean_project_reads_the_transcript(project):
    project.save_words("noisy", [], "test")
    assert clean.clean_project(project)["clips"]["noisy"]["status"] == "no_speech"


def test_clean_project_reuses_finished_clips_unless_forced(project):
    clean.clean_project(project)
    wav = project.audio_dir / "noisy.wav"
    stamp = wav.stat().st_mtime_ns
    again = clean.clean_project(project)
    assert again["clips"]["noisy"]["status"] == "reused" and wav.stat().st_mtime_ns == stamp
    forced = clean.clean_project(project, force=True)
    assert forced["clips"]["noisy"]["status"] == "cleaned" and wav.stat().st_mtime_ns != stamp


def say(project: Project, times: list[tuple[float, float]]) -> None:
    record = {"engine": "test", "source_clock": ffmpeg.SOURCE_CLOCK, "words": [Word(text="word", start=a, end=b).to_dict() for a, b in times]}
    write_json(project.words_dir / "noisy.json", record)


def test_new_word_times_redo_the_sound(project):
    """Phrase levelling, the click guard and the noise reading all follow the words. Sound cleaned around the old ones was kept."""
    say(project, [(0.2, 0.9), (1.4, 2.2)])
    first = clean.clean_project(project)["clips"]["noisy"]
    wav = project.audio_dir / "noisy.wav"
    stamp = wav.stat().st_mtime_ns
    assert first["status"] == "cleaned" and first["words"] == clean.words_mark([(0.2, 0.9), (1.4, 2.2)])
    assert clean.clean_project(project)["clips"]["noisy"]["status"] == "reused"
    say(project, [(0.2, 0.9), (1.4004, 2.2)])  # the same hundredth of a second is the same word
    kept = clean.clean_project(project)["clips"]["noisy"]
    assert kept["status"] == "reused" and kept["words"] == first["words"] and wav.stat().st_mtime_ns == stamp
    say(project, [(0.2, 0.9), (1.7, 2.5)])
    again = clean.clean_project(project)["clips"]["noisy"]
    assert again["status"] == "cleaned" and again["words"] != first["words"] and wav.stat().st_mtime_ns != stamp
    assert json.loads((project.audio_dir / "report.json").read_text())["clips"]["noisy"]["words"] == again["words"]


def test_sound_cleaned_before_word_times_were_recorded_is_redone(project):
    clean.clean_project(project)
    path = project.audio_dir / "report.json"
    saved = json.loads(path.read_text())
    del saved["clips"]["noisy"]["words"]
    path.write_text(json.dumps(saved))
    assert clean.clean_project(project)["clips"]["noisy"]["status"] == "cleaned"


def test_a_missing_transcript_and_an_empty_one_are_not_the_same_words():
    assert clean.words_mark(None) != clean.words_mark([])


def test_cli_clean(project, capsys):
    assert main(["clean", "--project", str(project.root)]) == 0
    out = capsys.readouterr().out
    assert "noisy" in out and "no audio" in out


def test_missing_source_names_the_clip(tmp_path):
    clip = Clip(id="gone", path=str(tmp_path / "gone.mov"), duration=1, fps=30, width=64, height=64)
    with pytest.raises(AltocueError) as caught:
        clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None)
    assert "gone" in str(caught.value) and caught.value.fix


@pytest.mark.slow
@pytest.mark.skipif(clean.find_deep_filter() is None, reason="deep-filter is not installed")
def test_real_deep_filter_keeps_time_and_length(tmp_path):
    original = speech(5, 0.15) + hiss(5, -45)
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", original, RATE)
    out = clean._run_deep_filter(clean.find_deep_filter(), raw, tmp_path, 6.0, len(original))
    cleaned, _ = ffmpeg.read_wav(out)
    assert len(cleaned) == len(original)
    assert abs(lag(original, cleaned)) <= 1


real_isolator = pytest.mark.skipif(clean.find_isolator() is None, reason="this machine has no Apple voice isolation")


@pytest.fixture
def said(tmp_path: Path) -> np.ndarray:
    """Speech from macOS `say`. The audio unit removes what it does not hear as a voice, and that includes `speech()`."""
    aiff = tmp_path / "said.aiff"
    subprocess.run(["say", "-o", str(aiff), "Every clip gets its own room, and nothing here should move in time."], check=True)
    return ffmpeg.read_wav(ffmpeg.extract_wav(aiff, tmp_path / "said.wav"))[0]


@real_isolator
def test_real_isolation_keeps_time_and_length(tmp_path, said):
    original = said + hiss(len(said) / RATE, -50)
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", original, RATE)
    cleaned, _ = ffmpeg.read_wav(clean._run_isolate(clean.find_isolator(), raw, tmp_path, 100.0, len(original)))
    assert len(cleaned) == len(original)
    assert abs(lag(original, cleaned)) <= 1


@real_isolator
def test_the_wet_mix_decides_how_far_the_room_falls(tmp_path, said):
    paused = np.r_[said, np.zeros(2 * RATE), said]
    original = paused + hiss(len(paused) / RATE, -50)
    raw = ffmpeg.write_wav(tmp_path / "raw.wav", original, RATE)
    plan = clean.plan_denoise(26)
    cleaned, _ = ffmpeg.read_wav(clean._run_isolate(clean.find_isolator(), raw, tmp_path, plan["wet"], len(original)))
    assert clean.noise_floor(original, RATE) - clean.noise_floor(cleaned, RATE) == pytest.approx(plan["atten_db"], abs=2)
    assert clean.speech_change(original, cleaned, RATE)["retained_db"] > -1


@real_isolator
def test_real_isolation_of_a_voice_it_cannot_hear_is_caught(tmp_path):
    clip = source(tmp_path, speech(5, 0.15) + hiss(5, -45))
    report = clean.clean_clip(clip, tmp_path / "out.wav", deep_filter=None, isolate=clean.find_isolator(), spoken=True)
    assert report["engine"] == "afftdn" and report["status"] == "cleaned"
    assert any("took too much speech" in note for note in report["warnings"])


def test_a_hung_binary_is_caught_by_the_version_check(tmp_path, monkeypatch):
    script = tmp_path / "deep-filter"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setattr(clean, "VERSION_TIMEOUT", 0.3)
    assert "quarantine" in clean.check_binary(str(script))


def test_a_working_binary_passes_the_version_check(tmp_path):
    assert clean.check_binary(stub(tmp_path)) is None


def test_a_changed_engine_redoes_the_sound(project, monkeypatch, tmp_path):
    clean.clean_project(project)
    machine(monkeypatch, apple=isolator(tmp_path))
    again = clean.clean_project(project)
    assert again["denoiser"] == "apple" and again["clips"]["noisy"]["status"] == "cleaned"


def test_the_project_can_name_its_engine(project, monkeypatch, tmp_path):
    machine(monkeypatch, apple=isolator(tmp_path))
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace('denoiser = "auto"', 'denoiser = "afftdn"'))
    report = clean.clean_project(Project.open(project.root))
    assert report["denoiser"] == "afftdn" and report["clips"]["noisy"]["engine"] == "afftdn"


def test_a_failed_engine_reaches_the_report(project, monkeypatch, tmp_path):
    machine(monkeypatch, apple=isolator(tmp_path, code=3))
    report = clean.clean_project(project)
    assert report["clips"]["noisy"]["engine"] == "afftdn"
    assert any("voice isolation" in note for note in report["clips"]["noisy"]["warnings"])


@pytest.mark.parametrize(("old", "new"), [('deess = "light"', 'deess = "strong"'), ("breaths = 0", "breaths = 9"), ("level_phrases = true", "level_phrases = false")])
def test_a_changed_sound_setting_redoes_the_sound(project, old, new):
    clean.clean_project(project)
    config = project.root / "altocue.toml"
    assert old in config.read_text()
    config.write_text(config.read_text().replace(old, new))
    assert clean.clean_project(Project.open(project.root))["clips"]["noisy"]["status"] != "reused"


def test_the_cli_can_override_the_sound_settings(project):
    assert main(["clean", "--project", str(project.root), "--deess", "strong", "--breaths", "9"]) == 0
    saved = json.loads((project.audio_dir / "report.json").read_text())
    assert saved["deess"] == "strong" and saved["breaths"] == 9


def test_a_changed_denoise_setting_redoes_the_sound(project):
    clean.clean_project(project)
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace("denoise = true", "denoise = false"))
    again = clean.clean_project(Project.open(project.root))
    assert again["clips"]["noisy"]["status"] != "reused"
