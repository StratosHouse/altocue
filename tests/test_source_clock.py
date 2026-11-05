"""Generated packet-clock regressions, with no real footage or speech model.

The fixture keeps AAC payloads intact but shortens packet PTS/durations. A plain
decoder still emits the full AAC frame samples, reproducing sample-clock drift.
Pulse locations, not just total duration, are the acceptance criterion.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from altocue import clean, ffmpeg, transcribe
from altocue.errors import AltocueError
from altocue.models import Clip, Word
from altocue.project import Project, write_json

RATE = 48000


def pulses(seconds: float = 6.0) -> np.ndarray:
    samples = np.zeros(round(seconds * RATE), dtype=np.float32)
    length = round(0.12 * RATE)
    burst = 0.5 * np.hanning(length) * np.sin(2 * np.pi * 1200 * np.arange(length) / RATE)
    for at in (0.75, 2.75, 4.75):
        first = round(at * RATE)
        samples[first : first + length] = burst
    return samples


def centers(samples: np.ndarray, rate: int) -> np.ndarray:
    """Energy centres of separated bursts; insensitive to AAC ringing."""
    active = np.flatnonzero(np.abs(samples) > max(float(np.abs(samples).max()) * 0.08, 1e-5))
    assert len(active), "The fixture has no audible pulses"
    groups = np.split(active, np.flatnonzero(np.diff(active) > round(rate * 0.15)) + 1)
    return np.array([np.average(group / rate, weights=samples[group].astype(float) ** 2) for group in groups])


def packet_times(path: Path) -> np.ndarray:
    import subprocess

    result = subprocess.run(
        [ffmpeg.require("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_packets", "-show_entries", "packet=pts_time", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return np.array([float(packet["pts_time"]) for packet in json.loads(result.stdout)["packets"]])


@pytest.fixture
def clock_clip(tmp_path):
    wav = ffmpeg.write_wav(tmp_path / "pulses.wav", pulses(), RATE)
    normal = tmp_path / "normal.mov"
    ffmpeg.run([
        "-f", "lavfi", "-i", "color=c=gray:s=64x64:r=30:d=6", "-i", str(wav),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(normal),
    ])
    overlap = tmp_path / "overlap.mov"
    ffmpeg.run([
        "-i", str(normal), "-map", "0", "-c", "copy", "-bsf:a",
        "setts=pts=PTS-N*64:dts=DTS-N*64:duration=DURATION-64", str(overlap),
    ])
    decoded = tmp_path / "plain.wav"
    ffmpeg.run(["-i", str(overlap), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(decoded)])
    plain = centers(ffmpeg.read_wav(decoded)[0], RATE)
    old, new = packet_times(normal), packet_times(overlap)
    assert len(old) == len(new) and np.all(np.diff(new) > 0)
    packet = np.searchsorted(old, plain, side="right") - 1
    expected = plain + new[packet] - old[packet]
    # The fixture must really reproduce substantial internal drift, or the test
    # could pass while checking an ordinary file that never exercised the bug.
    assert plain[-1] - expected[-1] > 0.25
    return normal, overlap, expected


@pytest.mark.parametrize("rate", [16000, 48000])
def test_decode_tracks_packet_clock_through_the_entire_take(clock_clip, tmp_path, rate):
    _, source, expected = clock_clip
    output = ffmpeg.extract_wav(source, tmp_path / f"conformed-{rate}.wav", rate=rate)
    samples, actual_rate = ffmpeg.read_wav(output)
    assert actual_rate == rate
    assert centers(samples, rate) == pytest.approx(expected, abs=0.004)


def test_clean_extraction_matches_transcription_clock(clock_clip, tmp_path):
    _, source, expected = clock_clip
    clip = ffmpeg.probe_clip(source)
    output = tmp_path / "clean-input.wav"
    count = clean._extract(clip, output)
    samples, rate = ffmpeg.read_wav(output)
    assert count == len(samples) == round(clip.duration * RATE)
    assert centers(samples, rate) == pytest.approx(expected, abs=0.004)


@pytest.mark.parametrize("malformed", [False, True])
def test_range_seek_matches_the_same_source_clock(clock_clip, tmp_path, malformed):
    source = clock_clip[int(malformed)]
    whole = ffmpeg.extract_wav(source, tmp_path / "whole.wav")
    full, rate = ffmpeg.read_wav(whole)
    start, end = 1.25, 5.25
    direct = ffmpeg.extract_wav(source, tmp_path / "direct.wav", start=start, end=end)
    canonical = ffmpeg.extract_wav(whole, tmp_path / "canonical.wav", start=start, end=end)
    expected = centers(full[round(start * rate) : round(end * rate)], rate)
    for output in (direct, canonical):
        samples, got_rate = ffmpeg.read_wav(output)
        assert got_rate == rate
        assert centers(samples, rate) == pytest.approx(expected, abs=0.004)


def test_normal_pcm_stays_sample_aligned(tmp_path):
    original = pulses()
    source = ffmpeg.write_wav(tmp_path / "input.wav", original, RATE)
    output = ffmpeg.extract_wav(source, tmp_path / "output.wav")
    actual, rate = ffmpeg.read_wav(output)
    assert rate == RATE and len(actual) == len(original)
    assert centers(actual, rate) == pytest.approx(centers(original, RATE), abs=1 / RATE)


def test_delayed_audio_keeps_its_real_offset(clock_clip, tmp_path):
    source, _, _ = clock_clip
    late = tmp_path / "late.mov"
    ffmpeg.run(["-i", str(source), "-itsoffset", "0.5", "-i", str(source), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(late)])
    normal = ffmpeg.extract_wav(source, tmp_path / "normal.wav")
    delayed = ffmpeg.extract_wav(late, tmp_path / "delayed.wav")
    assert centers(ffmpeg.read_wav(delayed)[0], RATE) == pytest.approx(centers(ffmpeg.read_wav(normal)[0], RATE) + 0.5, abs=0.004)


def word_project(tmp_path):
    project = Project.create(tmp_path / "project")
    words = [Word(text="Hello", start=0.75, end=1.0)]
    project.save_words("clip", words, "test")
    return project, words


def test_old_words_are_not_silently_reused_or_overwritten(tmp_path):
    project, _ = word_project(tmp_path)
    path = project.words_dir / "clip.json"
    previous = path.read_bytes()
    project.edl_path.write_text('{"approved": true}')
    with pytest.raises(AltocueError, match="unverified source clock") as error:
        transcribe.done(project, "clip")
    assert "transcribe --force" in error.value.fix
    assert path.read_bytes() == previous
    assert project.edl_path.read_text() == '{"approved": true}'


def test_clean_refuses_unverified_word_timings(tmp_path):
    project, _ = word_project(tmp_path)
    clip = Clip(id="clip", path="unused.mov", duration=6, fps=30, width=64, height=64)
    with pytest.raises(AltocueError, match="unverified source clock"):
        clean._words(project, clip)


def test_verified_words_keep_clock_when_text_or_speaker_changes(tmp_path):
    project, words = word_project(tmp_path)
    transcribe._save_words(project, "clip", words, "test")
    assert transcribe.done(project, "clip")
    project.save_words("clip", [Word(text="Hi", start=0.75, end=1.0)], "edited")
    record = json.loads((project.words_dir / "clip.json").read_text())
    assert record["source_clock"] == ffmpeg.SOURCE_CLOCK
    assert record["words"][0]["text"] == "Hi"
    assert transcribe.done(project, "clip")


def test_manual_import_is_not_given_a_verified_clock(tmp_path):
    project, _ = word_project(tmp_path)
    assert "source_clock" not in json.loads((project.words_dir / "clip.json").read_text())


def test_clean_cache_from_old_clock_is_rebuilt(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "project")
    source = tmp_path / "source.mov"
    source.write_bytes(b"not decoded: extraction is stubbed")
    clip = Clip(id="clip", path=str(source), duration=1, fps=30, width=64, height=64)
    project.save_clips([clip])
    dest = project.audio_dir / "clip.wav"
    dest.write_bytes(b"old audio")
    settings = {"denoiser": "afftdn", "denoise": True, "level_phrases": True, "deess": "light", "breaths": 0.0}
    write_json(project.audio_dir / "report.json", {**settings, "clips": {"clip": {"status": "cleaned"}}})
    monkeypatch.setattr(clean, "choose_denoiser", lambda name: ("afftdn", None, []))
    calls = []

    def rebuild(clip, dest, **kwargs):
        calls.append(clip.id)
        dest.write_bytes(b"rebuilt audio")
        return {"status": "cleaned"}

    monkeypatch.setattr(clean, "clean_clip", rebuild)
    report = clean.clean_project(project)
    assert calls == ["clip"] and report["source_clock"] == ffmpeg.SOURCE_CLOCK
    assert clean.clean_project(project)["clips"]["clip"]["status"] == "reused"
    assert calls == ["clip"]
