from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from altocue import ffmpeg, master
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import Project

RATE = 48000


def speech(seconds: float = 8.0, level: float = 0.08, seed: int = 1) -> np.ndarray:
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * np.cumsum(130 + 20 * np.sin(2 * np.pi * 0.7 * t)) / RATE
    voiced = sum(np.sin(k * phase) / k for k in range(1, 25))
    gate = np.convolve((np.sin(2 * np.pi * 1.7 * t) > -0.2).astype(float), np.ones(480) / 480, mode="same")
    breath = np.random.default_rng(seed).normal(0, 0.02, n) * gate
    shaped = (voiced + breath) * gate
    return shaped / np.abs(shaped).max() * level


def video(wav: Path, path: Path) -> Path:
    seconds = ffmpeg.probe_json(wav)["format"]["duration"]
    args = ["-f", "lavfi", "-i", f"testsrc2=size=128x72:rate=30:duration={seconds}", "-i", str(wav)]
    ffmpeg.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-shortest", str(path)])
    return path


def video_md5(path: Path) -> str:
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True
    )
    return done.stdout.strip()


def audio_stream(path: Path) -> dict:
    return next(s for s in ffmpeg.probe_json(path)["streams"] if s["codec_type"] == "audio")


@pytest.fixture
def voice(tmp_path: Path) -> Path:
    return ffmpeg.write_wav(tmp_path / "voice.wav", speech(), RATE)


@pytest.fixture
def film(tmp_path: Path, voice: Path) -> Path:
    return video(voice, tmp_path / "film.mp4")


def test_master_hits_the_targets_and_says_so(tmp_path, film):
    dest = tmp_path / "final.mp4"
    result = master.master_file(film, dest)
    measured = ffmpeg.loudness(dest)
    assert result["integrated"] == pytest.approx(measured["integrated"], abs=0.11)
    assert result["true_peak"] == pytest.approx(measured["true_peak"], abs=0.11)
    assert measured["integrated"] == pytest.approx(-14.0, abs=0.5)
    assert measured["true_peak"] < -1.5
    assert result["passed"] is True


@pytest.mark.parametrize("target", [(-16.0, -2.0), (-12.0, -1.0)])
def test_master_respects_other_targets(tmp_path, film, target):
    result = master.master_file(film, tmp_path / "final.mp4", loudness=target[0], true_peak=target[1])
    assert result["integrated"] == pytest.approx(target[0], abs=0.5)
    assert result["true_peak"] < target[1]


def test_video_is_copied_untouched(tmp_path, film):
    dest = tmp_path / "final.mp4"
    master.master_file(film, dest)
    assert video_md5(dest) == video_md5(film)


def test_audio_is_48k(tmp_path, voice):
    source = tmp_path / "low.mp4"
    ffmpeg.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=64x64:r=30:d=8",
            "-i",
            str(voice),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-ar",
            "44100",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ]
    )
    dest = tmp_path / "final.mp4"
    master.master_file(source, dest)
    assert audio_stream(dest)["sample_rate"] == "48000"


def test_audio_only_files_work(tmp_path, voice):
    dest = tmp_path / "final.wav"
    result = master.master_file(voice, dest)
    assert audio_stream(dest)["codec_name"] == "pcm_s16le" and audio_stream(dest)["sample_rate"] == "48000"
    assert not [s for s in ffmpeg.probe_json(dest)["streams"] if s["codec_type"] == "video"]
    assert result["integrated"] == pytest.approx(-14.0, abs=0.5)


def test_peaks_the_gain_would_push_over_are_limited_not_handed_to_dynamic_mode(tmp_path):
    samples = speech(level=0.5)
    samples[RATE * 3 : RATE * 3 + 40] = 0.85
    wav = ffmpeg.write_wav(tmp_path / "peaky.wav", samples, RATE)
    before = master.measure(wav, -14.0, -1.5)
    assert before["input_tp"] + (-14.0 - before["input_i"]) > -1.5
    result = master.master_file(wav, tmp_path / "final.wav")
    assert result["mode"] == "linear" and result["limiter"] is True
    assert result["integrated"] == pytest.approx(-14.0, abs=0.5) and result["true_peak"] < -1.5
    assert not result["warnings"]


def test_true_peak_stays_under_after_aac(tmp_path):
    rng = np.random.default_rng(2)
    samples = speech(level=0.6)
    for at in rng.integers(RATE, 7 * RATE, 40):
        samples[at : at + 12] += rng.choice([-0.35, 0.35])
    wav = ffmpeg.write_wav(tmp_path / "hot.wav", samples, RATE)
    film = video(wav, tmp_path / "hot.mp4")
    dest = tmp_path / "final.mp4"
    result = master.master_file(film, dest)
    assert ffmpeg.loudness(dest)["true_peak"] < -1.5
    assert result["passed"] is True


def test_wide_range_falls_back_to_dynamic_and_says_so(tmp_path):
    t = np.arange(RATE * 40) / RATE
    level = np.where((t // 5) % 2 == 0, 0.5, 0.08)
    wav = ffmpeg.write_wav(tmp_path / "wide.wav", np.sin(2 * np.pi * 440 * t) * level, RATE)
    result = master.master_file(wav, tmp_path / "final.wav")
    assert result["mode"] == "dynamic"
    assert any("dynamic" in note for note in result["warnings"])
    assert result["true_peak"] < -1.5


def test_range_a_little_over_the_target_stays_linear(tmp_path):
    t = np.arange(RATE * 40) / RATE
    level = np.where((t // 5) % 2 == 0, 0.25, 0.055)
    wav = ffmpeg.write_wav(tmp_path / "wide.wav", np.sin(2 * np.pi * 440 * t) * level, RATE)
    before = master.measure(wav, -14.0, -1.5)
    assert master.RANGE < before["input_lra"] <= master.MAX_LINEAR_RANGE
    result = master.master_file(wav, tmp_path / "final.wav")
    assert result["mode"] == "linear" and not result["warnings"]
    assert result["range"] == pytest.approx(before["input_lra"], abs=0.5)
    assert result["integrated"] == pytest.approx(-14.0, abs=0.5)


def test_filters_keep_audio_in_time_with_the_picture(tmp_path):
    samples = speech(level=0.2)
    samples[RATE * 4 : RATE * 4 + 96] = 0.95
    wav = ffmpeg.write_wav(tmp_path / "click.wav", samples, RATE)
    film = video(wav, tmp_path / "click.mp4")
    dest = tmp_path / "final.mp4"
    master.master_file(film, dest)
    a, _ = ffmpeg.read_wav(ffmpeg.extract_wav(film, tmp_path / "a.wav"))
    b, _ = ffmpeg.read_wav(ffmpeg.extract_wav(dest, tmp_path / "b.wav"))
    window = np.arange(RATE * 3, RATE * 5)
    assert abs(int(np.argmax(np.abs(a[window]))) - int(np.argmax(np.abs(b[window])))) <= 48


def test_length_is_kept(tmp_path, voice):
    dest = tmp_path / "final.wav"
    master.master_file(voice, dest)
    assert abs(len(ffmpeg.read_wav(dest)[0]) - len(ffmpeg.read_wav(voice)[0])) <= 2


def test_no_half_file_is_left_when_it_fails(tmp_path):
    silent = ffmpeg.write_wav(tmp_path / "silent.wav", np.zeros(RATE * 2), RATE)
    dest = tmp_path / "final.wav"
    with pytest.raises(AltocueError):
        master.master_file(silent, dest)
    assert not dest.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["silent.wav"]


def test_silent_source_is_refused_with_a_fix(tmp_path):
    silent = ffmpeg.write_wav(tmp_path / "silent.wav", np.zeros(RATE * 2), RATE)
    with pytest.raises(AltocueError) as caught:
        master.master_file(silent, tmp_path / "final.wav")
    assert caught.value.fix


def test_video_without_audio_is_refused(tmp_path):
    mute = tmp_path / "mute.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=gray:s=64x64:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mute)])
    with pytest.raises(AltocueError) as caught:
        master.master_file(mute, tmp_path / "final.mp4")
    assert "no audio" in str(caught.value) and caught.value.fix


def test_missing_source_is_refused(tmp_path):
    with pytest.raises(AltocueError):
        master.master_file(tmp_path / "nope.mp4", tmp_path / "final.mp4")


def test_overwriting_the_source_is_refused(tmp_path, voice):
    with pytest.raises(AltocueError):
        master.master_file(voice, voice)


def test_limiter_never_needs_a_filter_stock_ffmpeg_lacks():
    assert {"loudnorm", "alimiter", "aresample", "ebur128"} <= ffmpeg.filters()


def test_cli_with_explicit_files(tmp_path, film, capsys):
    dest = tmp_path / "out.mp4"
    assert main(["master", "--input", str(film), "--output", str(dest)]) == 0
    assert dest.exists() and "LUFS" in capsys.readouterr().out


def test_cli_accepts_negative_targets(tmp_path, film):
    dest = tmp_path / "out.mp4"
    assert main(["master", "--input", str(film), "--output", str(dest), "--loudness", "-16", "--true-peak", "-2"]) == 0
    assert ffmpeg.loudness(dest)["integrated"] == pytest.approx(-16, abs=0.5)


def test_cli_hot_preset(tmp_path, film):
    dest = tmp_path / "out.mp4"
    assert main(["master", "--input", str(film), "--output", str(dest), "--loudness", "hot"]) == 0
    assert ffmpeg.loudness(dest)["integrated"] == pytest.approx(-12, abs=0.5)


def test_cli_input_needs_an_output(tmp_path, film, capsys):
    assert main(["master", "--input", str(film)]) == 1
    assert "--output" in capsys.readouterr().err


def test_cli_defaults_to_the_project_render(tmp_path, film):
    project = Project.create(tmp_path / "video")
    (project.out / "render.mp4").write_bytes(film.read_bytes())
    assert main(["master", "--project", str(project.root)]) == 0
    assert (project.out / "final.mp4").exists()
    report = json.loads((project.audio_dir / "master.json").read_text())
    assert report["integrated"] == pytest.approx(-14, abs=0.5)


def test_cli_reads_the_project_targets(tmp_path, film):
    project = Project.create(tmp_path / "video")
    settings = project.root / "altocue.toml"
    settings.write_text(settings.read_text().replace("loudness = -14.0", "loudness = -16.0"))
    (project.out / "render.mp4").write_bytes(film.read_bytes())
    assert main(["master", "--project", str(project.root)]) == 0
    assert ffmpeg.loudness(project.out / "final.mp4")["integrated"] == pytest.approx(-16, abs=0.5)


def test_cli_without_a_render_says_what_to_run(tmp_path, capsys):
    project = Project.create(tmp_path / "video")
    assert main(["master", "--project", str(project.root)]) == 1
    assert "altocue render" in capsys.readouterr().err


def test_a_film_with_no_sound_at_all_passes_through(tmp_path):
    source = tmp_path / "quiet.mp4"
    args = ["-f", "lavfi", "-i", "testsrc2=size=128x72:rate=30:duration=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2"]
    ffmpeg.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
    with pytest.raises(AltocueError):
        master.master_file(source, tmp_path / "out.mp4")
    assert master.master_file(source, tmp_path / "out.mp4", silent_film=True)["mode"] == "silent"
    assert (tmp_path / "out.mp4").exists()
