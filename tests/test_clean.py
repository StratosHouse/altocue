from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import clean, ffmpeg
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import Clip
from altocue.project import Project

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
    return np.random.default_rng(seed).normal(0, 10 ** (db / 20), int(seconds * RATE))


def tone(seconds: float, hz: float, db: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
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


def test_harder_work_for_worse_audio():
    order = [clean.plan_denoise(snr)["strength"] for snr in (60, 38, 26, 8)]
    assert order == ["none", "light", "normal", "strong"]
    assert clean.plan_denoise(38)["atten_db"] < clean.plan_denoise(8)["atten_db"]


def test_tone_chain_notches_only_when_hum_was_found():
    assert "bandreject" not in clean.tone_filters(None)
    chain = clean.tone_filters({"hz": 50})
    assert [f"bandreject=f={hz}" in chain for hz in (50, 100, 150)] == [True, True, True]


def test_tone_chain_uses_stock_filters_only():
    names = {part.split("=")[0] for part in clean.tone_filters({"hz": 60}).split(",")}
    assert names <= ffmpeg.filters()
    assert not names & {"drawtext", "subtitles", "zscale"}


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
    monkeypatch.setattr(clean, "find_deep_filter", lambda: None)
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


def test_a_hung_binary_is_caught_by_the_version_check(tmp_path, monkeypatch):
    script = tmp_path / "deep-filter"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setattr(clean, "VERSION_TIMEOUT", 0.3)
    assert "quarantine" in clean.check_binary(str(script))


def test_a_working_binary_passes_the_version_check(tmp_path):
    assert clean.check_binary(stub(tmp_path)) is None


def test_a_changed_denoise_setting_redoes_the_sound(project):
    clean.clean_project(project)
    config = project.root / "altocue.toml"
    config.write_text(config.read_text().replace("denoise = true", "denoise = false"))
    again = clean.clean_project(Project.open(project.root))
    assert again["clips"]["noisy"]["status"] != "reused"
