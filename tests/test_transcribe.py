from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from altocue import ffmpeg, locks, transcribe
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.generate import http
from altocue.models import Clip, Word
from altocue.project import Project
from altocue.transcribe import (
    Envelope,
    classify,
    clean,
    drop_loops,
    multipart,
    prompt_for,
    scribe_words,
    select,
    transcribe_project,
    words_from_tokens,
)


@pytest.fixture(autouse=True)
def real_stage(monkeypatch):
    """The CLI imports stages by name, so a stub left in `sys.modules` would be run instead."""
    monkeypatch.setitem(sys.modules, "altocue.transcribe", transcribe)


def timed(text: str, *, start: float = 0.0, step: float = 0.3, gap: float = 0.0) -> list[Word]:
    words = []
    for token in text.split():
        words.append(Word(text=token, start=round(start, 3), end=round(start + step, 3)))
        start += step + gap
    return words


def tone_wav(path: Path, seconds: float = 2.0, *, spans: tuple[tuple[float, float], ...] = ((0.2, 1.8),)) -> Path:
    rate = 16000
    samples = np.zeros(int(rate * seconds), dtype=np.float32)
    for start, end in spans:
        a, b = int(start * rate), int(end * rate)
        samples[a:b] = 0.3 * np.sin(2 * np.pi * 220 * np.arange(b - a) / rate)
    return ffmpeg.write_wav(path, samples, rate)


def project_with(tmp_path: Path, *clips: Clip) -> Project:
    project = Project.create(tmp_path / "video")
    project.save_clips(list(clips))
    return project


def audio_clip(tmp_path: Path, name: str = "a", seconds: float = 2.0) -> Clip:
    wav = tone_wav(tmp_path / f"{name}.wav", seconds, spans=((0.2, seconds - 0.2),))
    return Clip(id=name, path=str(wav), duration=seconds, fps=30, width=1080, height=1920)


class FakeEngine:
    name = "fake"
    hosted = False
    calls = 0

    def available(self) -> bool:
        return True

    def install_fix(self) -> str:
        return ""

    def notice(self, seconds: float) -> str:
        return "fake: local."

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]:
        self.calls += 1
        return timed("um hello there", start=0.3)


@pytest.mark.parametrize("text", ["um", "Um,", "UH.", "uhm", "Hmm...", "er", "mhm"])
def test_filler_tokens_are_classified(text):
    assert classify([Word(text, 0, 0.3)])[0].kind == "filler"


@pytest.mark.parametrize("text", ["uh-huh", "like", "so", "umbrella", "right"])
def test_ordinary_words_are_not_fillers(text):
    assert classify([Word(text, 0, 0.3)])[0].kind == "word"


def test_spoken_cue_on_its_own_is_a_cue():
    words = timed("That was wrong.", step=0.2) + timed("Redo.", start=1.4) + timed("That was right.", start=2.4, step=0.2)
    assert [w.kind for w in classify(words)] == ["word"] * 3 + ["cue"] + ["word"] * 3


def test_cue_with_softener_marks_the_whole_phrase():
    words = timed("Done.", step=0.2) + timed("Sorry, let me start again.", start=0.8, step=0.2) + timed("Now", start=2.2)
    assert [w.kind for w in classify(words)] == ["word"] + ["cue"] * 5 + ["word"]


@pytest.mark.parametrize("line", ["We had to redo the whole kitchen", "We cut that out of the budget", "The engine would not start again today"])
def test_cue_words_inside_a_sentence_stay_words(line):
    assert all(w.kind == "word" for w in classify(timed(line, step=0.25)))


def test_weak_cue_needs_real_pauses():
    fluent = timed("Click save.", step=0.2) + timed("Try again.", start=0.45, step=0.2) + timed("It works.", start=0.9, step=0.2)
    assert all(w.kind == "word" for w in classify(fluent))
    alone = timed("Click save.", step=0.2) + timed("Try again.", start=1.4, step=0.2) + timed("It works.", start=2.6, step=0.2)
    assert [w.kind for w in classify(alone)] == ["word", "word", "cue", "cue", "word", "word"]


def test_slate_is_a_cue():
    words = timed("Okay.", step=0.3) + timed("Take two.", start=0.9, step=0.3) + timed("So", start=2.0)
    assert [w.kind for w in classify(words)] == ["cue", "cue", "cue", "word"]


def test_custom_cue_from_settings():
    words = timed("One.", step=0.3) + timed("Banana.", start=1.0) + timed("Two.", start=2.0)
    assert [w.kind for w in classify(words, cues=["banana"])] == ["word", "cue", "word"]


def test_zero_length_words_get_a_real_length():
    fixed = clean([Word("a", 0.5, 0.5), Word("b", 0.9, 0.9)], duration=2.0)
    assert all(w.end - w.start >= 0.039 for w in fixed)
    assert fixed[0].end <= fixed[1].start


def test_overlaps_are_removed_and_order_restored():
    fixed = clean([Word("b", 0.5, 0.9), Word("a", 0.1, 0.6)], duration=2.0)
    assert [w.text for w in fixed] == ["a", "b"]
    assert fixed[0].end <= fixed[1].start


def test_words_never_pass_the_end_of_the_clip():
    fixed = clean([Word("a", 0.1, 0.4), Word("b", 1.9, 2.6), Word("ghost", 3.0, 3.4)], duration=2.0)
    assert [w.text for w in fixed] == ["a", "b"]
    assert fixed[-1].end <= 2.0


def test_short_collapsed_run_is_spread_and_marked_unsure():
    before = timed("one two three four five six seven eight nine ten eleven twelve", step=0.3)
    stacked = [Word(t, 3.6, 3.6) for t in ("thir teen four".split())]
    after = timed("sixteen", start=5.0)
    fixed = clean(before + stacked + after, duration=6.0)
    spread = fixed[12:15]
    assert all(b.start >= a.end for a, b in zip(spread, spread[1:], strict=False))
    assert all(w.end - w.start >= 0.039 for w in spread)
    assert all(w.confidence <= 0.1 for w in spread)
    assert fixed[0].confidence == 1.0


def test_mostly_collapsed_transcript_is_refused():
    words = [Word(f"w{i}", 0.5, 0.5) for i in range(8)]
    with pytest.raises(AltocueError, match="collapsed") as caught:
        clean(words, duration=4.0)
    assert caught.value.fix


def test_non_speech_tags_are_dropped():
    words = [Word("[Music]", 0, 1), Word("(laughs)", 1, 2), Word("♪", 2, 3), Word("real", 3, 3.4), Word("...", 3.4, 3.5)]
    assert [w.text for w in clean(words, duration=5.0)] == ["real"]


def test_phrase_loop_keeps_one_copy():
    loop = timed("see you soon " * 5 + "bye")
    assert [w.text for w in drop_loops(loop)] == ["see", "you", "soon", "bye"]


def test_prompted_filler_loop_keeps_one_copy():
    assert [w.text for w in drop_loops(timed("uh uh uh uh so"))] == ["uh", "so"]


def test_real_stutter_survives_the_loop_guard():
    words = timed("I I I think")
    assert len(drop_loops(words)) == 4


def test_unsure_sign_off_goes_but_a_sure_one_stays():
    unsure = [Word(t, i, i + 0.5, confidence=0.2) for i, t in enumerate("thanks for watching".split())]
    sure = [Word(t, i, i + 0.5, confidence=0.9) for i, t in enumerate("thanks for watching".split())]
    assert clean(unsure, duration=5.0) == []
    assert len(clean(sure, duration=5.0)) == 3


def test_words_over_silence_are_dropped(tmp_path):
    envelope = Envelope.from_wav(tone_wav(tmp_path / "a.wav", 6.0, spans=((0.5, 2.0),)))
    words = [Word("real", 0.6, 1.0), Word("ghost", 4.0, 4.5), Word("edge", 2.2, 2.5)]
    assert [w.text for w in clean(words, duration=6.0, envelope=envelope)] == ["real", "edge"]


def test_silence_guard_stands_down_on_noisy_audio(tmp_path):
    rate = 16000
    noise = np.random.default_rng(1).normal(0, 0.05, rate * 4).astype(np.float32)
    envelope = Envelope.from_wav(ffmpeg.write_wav(tmp_path / "n.wav", noise, rate))
    assert not envelope.reliable
    assert len(clean([Word("kept", 1.0, 1.4)], duration=4.0, envelope=envelope)) == 1


def test_parakeet_pieces_become_words():
    pieces = [
        (" N", 0.4, 0.56),
        ("ow", 0.56, 0.72),
        (" that", 0.72, 0.88),
        (" I", 0.88, 0.96),
        ("'", 0.96, 1.04),
        ("ve", 1.04, 1.12),
        (".", 1.12, 1.16),
    ]
    tokens = [SimpleNamespace(text=t, start=a, end=b, confidence=0.9 - i * 0.01) for i, (t, a, b) in enumerate(pieces)]
    words = words_from_tokens(tokens)
    assert [w.text for w in words] == ["Now", "that", "I've."]
    assert words[0].start == 0.4 and words[0].end == 0.72
    assert words[2].end == 1.12 and words[2].confidence == pytest.approx(0.85)


def test_late_punctuation_does_not_stretch_a_word():
    pieces = [(" that", 32.08, 32.4), (".", 46.88, 47.2), (" Okay", 47.2, 47.52)]
    tokens = [SimpleNamespace(text=t, start=a, end=b, confidence=0.9) for t, a, b in pieces]
    words = words_from_tokens(tokens)
    assert [w.text for w in words] == ["that.", "Okay"]
    assert words[0].end == 32.4


def test_a_bare_space_piece_starts_the_next_word():
    pieces = [(" one", 0.0, 0.2), (" ", 0.3, 0.4), ("two", 0.4, 0.6)]
    tokens = [SimpleNamespace(text=t, start=a, end=b, confidence=0.9) for t, a, b in pieces]
    words = words_from_tokens(tokens)
    assert [(w.text, w.start) for w in words] == [("one", 0.0), ("two", 0.4)]


def test_long_word_ends_at_the_first_real_silence(tmp_path):
    envelope = Envelope.from_wav(tone_wav(tmp_path / "a.wav", 8.0, spans=((1.0, 1.5), (5.0, 7.0))))
    fixed = clean([Word("drawn", 1.0, 6.5)], duration=8.0, envelope=envelope)
    assert fixed[0].end == pytest.approx(1.5, abs=0.05)


def test_long_word_is_capped_when_there_is_no_audio_to_ask():
    assert clean([Word("drawn", 1.0, 6.5)], duration=8.0)[0].end == pytest.approx(1.0 + transcribe.LONG_WORD)


def test_a_drawn_out_word_with_no_silence_keeps_its_length(tmp_path):
    envelope = Envelope.from_wav(tone_wav(tmp_path / "a.wav", 8.0, spans=((1.0, 3.5),)))
    assert clean([Word("sooo", 1.0, 3.0)], duration=8.0, envelope=envelope)[0].end == 3.0


def test_sound_that_ends_long_before_the_picture_still_splits():
    """The marks are placed by the clip's length, and the envelope stops where the sound does."""
    envelope = Envelope.of_levels(np.full(int(100 / transcribe.HOP), -40.0))
    marks = transcribe.quiet_marks(envelope, 400.0)
    assert marks and all(mark < 100 for mark in marks)


def test_scribe_response_keeps_only_words():
    data = {
        "words": [
            {"text": "Hello", "start": 0.1, "end": 0.5, "type": "word", "logprob": -0.1},
            {"text": " ", "start": 0.5, "end": 0.6, "type": "spacing"},
            {"text": "(laughter)", "start": 0.6, "end": 0.9, "type": "audio_event"},
            {"text": "um", "start": 1.0, "end": 1.2, "type": "word", "logprob": 0.0},
        ]
    }
    words = scribe_words(data)
    assert [w.text for w in words] == ["Hello", "um"]
    assert words[0].confidence == pytest.approx(math.exp(-0.1))


def test_multipart_carries_fields_and_file(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFdata")
    body, content_type = multipart({"model_id": "m"}, wav)
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="model_id"\r\n\r\nm\r\n' in body and b"RIFFdata" in body


def installed(monkeypatch, *names):
    specs = {transcribe.ENGINES[n].module for n in names}
    monkeypatch.setattr(transcribe.importlib.util, "find_spec", lambda module: object() if module in specs else None)


def test_auto_prefers_parakeet_on_apple_silicon(monkeypatch):
    monkeypatch.setattr(transcribe, "apple_silicon", lambda: True)
    installed(monkeypatch, "parakeet", "mlx-whisper", "faster-whisper")
    assert select().name == "parakeet"
    installed(monkeypatch, "mlx-whisper", "faster-whisper")
    assert select(None).name == "mlx-whisper"


def test_auto_uses_faster_whisper_elsewhere(monkeypatch):
    monkeypatch.setattr(transcribe, "apple_silicon", lambda: False)
    installed(monkeypatch, "parakeet", "mlx-whisper", "faster-whisper")
    assert select("auto").name == "faster-whisper"


def test_auto_never_picks_a_hosted_engine(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "key")
    monkeypatch.setattr(transcribe, "apple_silicon", lambda: True)
    installed(monkeypatch)
    with pytest.raises(AltocueError) as caught:
        select()
    assert "altocue[" in caught.value.fix


@pytest.mark.parametrize("silicon", [True, False])
def test_no_hosted_engine_is_ever_in_the_auto_order(monkeypatch, silicon):
    monkeypatch.setattr(transcribe, "apple_silicon", lambda: silicon)
    assert not any(transcribe.ENGINES[name].hosted for name in transcribe.auto_order())


def test_local_engine_says_it_stays_on_this_machine():
    assert "this machine" in transcribe.ENGINES["parakeet"].notice(60) and "2.5 GB" in transcribe.ENGINES["parakeet"].notice(60)


def test_hosted_engine_needs_its_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(AltocueError) as caught:
        select("elevenlabs")
    assert "ELEVENLABS_API_KEY" in caught.value.fix


def test_unknown_engine_lists_the_choices():
    with pytest.raises(AltocueError) as caught:
        select("nope")
    assert "parakeet" in caught.value.fix


def test_named_engine_not_installed_says_how(monkeypatch):
    installed(monkeypatch)
    with pytest.raises(AltocueError) as caught:
        select("faster-whisper")
    assert "altocue[faster-whisper]" in caught.value.fix


def test_heavy_imports_stay_inside_the_adapters():
    heavy = "{'mlx_whisper', 'faster_whisper', 'parakeet_mlx', 'mlx'}"
    code = f"import sys, altocue.transcribe, altocue.cut; print(sorted(m for m in sys.modules if m.split('.')[0] in {heavy}))"
    done = subprocess.run(["python", "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_project_transcribes_saves_and_skips(tmp_path, capsys):
    clip = audio_clip(tmp_path)
    project = project_with(tmp_path, clip)
    engine = FakeEngine()
    assert transcribe_project(project, engine=engine) == {"a": 3}
    words = project.words("a")
    assert [w.kind for w in words] == ["filler", "word", "word"]
    assert json.loads((project.words_dir / "a.json").read_text())["engine"] == "fake"
    assert transcribe_project(project, engine=engine) == {}
    assert engine.calls == 1
    assert "already transcribed" in capsys.readouterr().out
    assert transcribe_project(project, engine=engine, force=True) == {"a": 3}
    assert engine.calls == 2


def test_clip_without_audio_gets_an_empty_word_file(tmp_path):
    clip = Clip(id="b", path=str(tmp_path / "nothing.mp4"), duration=2.0, fps=30, width=10, height=10, has_audio=False)
    project = project_with(tmp_path, clip)
    engine = FakeEngine()
    assert transcribe_project(project, engine=engine) == {"b": 0}
    assert project.words("b") == [] and engine.calls == 0


def test_project_setting_chooses_the_engine(tmp_path, monkeypatch):
    project = project_with(tmp_path, audio_clip(tmp_path))
    project.config["transcribe"] = {"engine": "faster-whisper"}
    picked = []
    monkeypatch.setattr(transcribe, "select", lambda name=None: picked.append(name) or FakeEngine())
    transcribe_project(project)
    assert picked == ["faster-whisper"]


def test_only_whisper_takes_the_filler_prompt(tmp_path):
    project = project_with(tmp_path, audio_clip(tmp_path))
    project.config["transcribe"] = {"words": ["Altocue"]}
    assert "Um, uh" in prompt_for(project, engine="mlx-whisper") and "Altocue." in prompt_for(project, engine="mlx-whisper")
    assert prompt_for(project, engine="parakeet") == ""


def test_hosted_run_says_where_audio_goes_and_what_it_costs(tmp_path, monkeypatch, capsys):
    clip = audio_clip(tmp_path)
    project = project_with(tmp_path, clip)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    sent = {}

    def fake_open(request, timeout):
        sent["url"], sent["key"] = request.full_url, request.get_header("Xi-api-key")
        body = {"words": [{"text": "hello", "start": 0.3, "end": 0.7, "type": "word", "logprob": 0.0}]}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(http, "open_keyed", fake_open)
    assert transcribe_project(project, engine="elevenlabs") == {"a": 1}
    output = capsys.readouterr().out
    assert "api.elevenlabs.io" in output and "$" in output
    assert "secret-key" not in output
    assert sent == {"url": "https://api.elevenlabs.io/v1/speech-to-text", "key": "secret-key"}


def test_hosted_failure_has_a_fix(tmp_path, monkeypatch):
    project = project_with(tmp_path, audio_clip(tmp_path))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "key")

    def refuse(request, timeout):
        raise transcribe.urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO())

    monkeypatch.setattr(http, "open_keyed", refuse)
    with pytest.raises(AltocueError) as caught:
        transcribe_project(project, engine="elevenlabs")
    assert caught.value.fix


def test_a_render_can_be_read_back_without_a_project(clip, monkeypatch):
    monkeypatch.setattr(transcribe, "select", lambda name=None: FakeEngine())
    words = transcribe.transcribe_file(clip)
    assert [w.kind for w in words] == ["filler", "word", "word"]


def test_cli_passes_engine_and_force(tmp_path, monkeypatch, capsys):
    project = project_with(tmp_path, audio_clip(tmp_path))
    monkeypatch.setitem(transcribe.ENGINES, "fake", FakeEngine())
    assert main(["transcribe", "--project", str(project.root), "--engine", "fake"]) == 0
    assert main(["transcribe", "--project", str(project.root), "--engine", "fake", "--force"]) == 0
    assert "Transcribed 1 clips" in capsys.readouterr().out


def test_cli_reports_a_bad_engine(tmp_path, capsys):
    project = project_with(tmp_path, audio_clip(tmp_path))
    assert main(["transcribe", "--project", str(project.root), "--engine", "nope"]) == 1
    assert "Unknown engine" in capsys.readouterr().err


@pytest.fixture(scope="module")
def spoken_wav(tmp_path_factory) -> Path:
    if not shutil.which("say"):
        pytest.skip("needs the macOS say command")
    folder = tmp_path_factory.mktemp("speech")
    aiff = folder / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "This is a short test of the word timing. It has two sentences."], check=True)
    return ffmpeg.extract_wav(aiff, folder / "s.wav", rate=16000)


@pytest.mark.slow
@pytest.mark.parametrize("name", ["parakeet", "mlx-whisper", "faster-whisper"])
def test_real_engine_gives_clean_words(name, spoken_wav):
    engine = transcribe.ENGINES[name]
    if not engine.available():
        pytest.skip(f"{name} is not installed")
    samples, rate = ffmpeg.read_wav(spoken_wav)
    duration = len(samples) / rate
    raw = engine.transcribe(spoken_wav, language="en", prompt="")
    words = clean(raw, duration=duration, envelope=Envelope.measure(samples, rate))
    assert len(words) >= 10
    assert all(0 <= w.start < w.end <= duration for w in words)
    assert all(b.start >= a.end for a, b in zip(words, words[1:], strict=False))
    assert any(transcribe.core(w.text) == "sentences" for w in words)


def test_a_cue_after_an_apology_counts_mid_sentence():
    times = [("has", 0.0, 0.2), ("to", 0.2, 0.3), ("sorry,", 0.35, 0.6), ("redo.", 0.65, 0.9), ("A", 2.0, 2.1)]
    words = [transcribe.Word(*item) for item in times]
    kinds = [w.kind for w in transcribe.classify(words)]
    assert kinds == ["word", "word", "cue", "cue", "word"]


# Long recordings


def steady_wav(path: Path, seconds: float, quiet: list[tuple[float, float]]) -> Path:
    rate = transcribe.RATE
    samples = 0.2 * np.sin(2 * np.pi * 200 * np.arange(int(seconds * rate)) / rate).astype(np.float32)
    for start, end in quiet:
        samples[int(start * rate) : int(end * rate)] = 0.0
    return ffmpeg.write_wav(path, samples, rate)


def test_the_envelope_reads_a_file_in_blocks_and_measures_the_same(tmp_path, monkeypatch):
    wav = steady_wav(tmp_path / "a.wav", 61.03, [(3.0, 5.0), (40.0, 41.0)])
    expected = Envelope.measure(*ffmpeg.read_wav(wav))
    monkeypatch.setattr(ffmpeg, "read_wav", lambda *a: pytest.fail("the whole file went into memory"))
    found = Envelope.from_wav(wav)
    assert np.allclose(found.db, expected.db)
    assert (found.floor, found.loud, found.threshold) == pytest.approx((expected.floor, expected.loud, expected.threshold))


def test_an_empty_wav_still_gives_an_envelope(tmp_path):
    wav = ffmpeg.write_wav(tmp_path / "a.wav", np.zeros(0, dtype=np.float32), transcribe.RATE)
    assert len(Envelope.from_wav(wav).db) == 0


class Pieces:
    """Hears one word per second, and says how much it was handed each time."""

    name, hosted = "pieces", False

    def __init__(self) -> None:
        self.lengths: list[float] = []

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]:
        seconds = len(ffmpeg.read_wav(wav)[0]) / transcribe.RATE
        self.lengths.append(seconds)
        return [Word(text=f"w{at}", start=at + 0.2, end=at + 0.6) for at in range(int(seconds))]


def test_a_long_recording_goes_in_pieces_split_where_it_is_quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "PIECE", 10.0)
    monkeypatch.setattr(transcribe, "REACH", 3.0)
    wav = steady_wav(tmp_path / "a.wav", 32.0, [(8.5, 9.0), (20.5, 21.0)])
    engine = Pieces()
    words = transcribe.in_pieces(engine, wav, Envelope.from_wav(wav), 32.0, language="en", prompt="")
    assert engine.lengths == pytest.approx([8.75, 12.0, 11.25], abs=0.1)
    assert [word.start for word in words] == sorted(word.start for word in words)
    assert words[8].start == pytest.approx(8.95, abs=0.1)


def test_a_short_recording_stays_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "PIECE", 100.0)
    wav = steady_wav(tmp_path / "a.wav", 12.0, [(5.0, 6.0)])
    engine = Pieces()
    transcribe.in_pieces(engine, wav, Envelope.from_wav(wav), 12.0, language="en", prompt="")
    assert engine.lengths == pytest.approx([12.0], abs=0.05)


def test_a_hosted_engine_gets_the_whole_file(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "PIECE", 10.0)
    monkeypatch.setattr(transcribe, "REACH", 3.0)
    wav = steady_wav(tmp_path / "a.wav", 32.0, [(8.5, 9.0), (20.5, 21.0)])
    engine = Pieces()
    engine.hosted = True
    transcribe.in_pieces(engine, wav, Envelope.from_wav(wav), 32.0, language="en", prompt="")
    assert len(engine.lengths) == 1


def test_memory_goes_back_after_every_piece(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "PIECE", 10.0)
    monkeypatch.setattr(transcribe, "REACH", 3.0)
    wav = steady_wav(tmp_path / "a.wav", 32.0, [(8.5, 9.0), (20.5, 21.0)])
    freed: list[int] = []
    engine = Pieces()
    monkeypatch.setattr(transcribe, "release_memory", lambda: freed.append(len(engine.lengths)))
    transcribe.in_pieces(engine, wav, Envelope.from_wav(wav), 32.0, language="en", prompt="")
    assert freed == [1, 2, 3]


def test_a_render_is_read_back_in_shorter_pieces(tmp_path, monkeypatch):
    """Under a score, parakeet drops whole lines from a long stretch that it hears in a short one."""
    wav = steady_wav(tmp_path / "a.wav", 70.0, [(29.5, 30.5)])
    assert transcribe.quiet_marks(Envelope.from_wav(wav), 70.0) == []
    assert transcribe.quiet_marks(Envelope.from_wav(wav), 70.0, piece=transcribe.READBACK_PIECE) == pytest.approx([30.0], abs=0.6)
    seen: dict = {}
    monkeypatch.setattr(transcribe, "transcribe_clip", lambda clip, engine, **kwargs: seen.update(kwargs) or [])
    monkeypatch.setattr(transcribe, "select", lambda engine=None: object())
    monkeypatch.setattr(transcribe.ffmpeg, "probe_clip", lambda path: object())
    transcribe.transcribe_file(tmp_path / "film.mp4")
    assert seen["piece"] == transcribe.READBACK_PIECE


def test_a_film_is_read_back_only_where_it_has_a_voice(tmp_path, monkeypatch):
    """A line either side of a stretch of music is read on its own, in film seconds, so the two never run together."""
    wav = steady_wav(tmp_path / "film.wav", 10.0, [])
    seen: list[float] = []

    class Spans:
        hosted = False
        needs_gb = 0.0
        name = "fake"

        def transcribe(self, piece, *, language, prompt):
            samples, rate = ffmpeg.read_wav(piece)
            seen.append(len(samples) / rate)
            return [Word(text="said", start=0.2, end=0.6)]

    monkeypatch.setattr(transcribe, "select", lambda engine=None: Spans())
    monkeypatch.setattr(transcribe.ffmpeg, "probe_clip", lambda path: SimpleNamespace(path=wav, duration=10.0))
    words = transcribe.transcribe_file(tmp_path / "film.mp4", spans=[(0.3, 1.8), (5.8, 7.2)])
    assert seen == pytest.approx([1.5, 1.4], abs=0.02)
    assert [w.start for w in words] == pytest.approx([0.5, 6.0])
    assert [w.end for w in words] == pytest.approx([0.9, 6.4])


def test_a_piece_fits_inside_the_engines_own_chunk():
    assert transcribe.PIECE + transcribe.REACH <= transcribe.Parakeet.chunk
    assert transcribe.PIECE * 1.5 <= transcribe.Parakeet.chunk


def fake_mlx(monkeypatch) -> SimpleNamespace:
    calls = SimpleNamespace(cache=None, memory=None, cleared=0)

    def clear() -> None:
        calls.cleared += 1

    core = SimpleNamespace(set_cache_limit=lambda v: setattr(calls, "cache", v), set_memory_limit=lambda v: setattr(calls, "memory", v), clear_cache=clear)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    return calls


def test_mlx_gets_a_ceiling_and_a_small_cache(monkeypatch):
    calls = fake_mlx(monkeypatch)
    transcribe.limit_mlx()
    assert calls.cache == transcribe.MLX_CACHE_BYTES and calls.memory == transcribe.MLX_LIMIT_BYTES
    assert calls.cache < 2**30 < calls.memory < 8 * 2**30


def test_releasing_memory_clears_the_mlx_cache(monkeypatch):
    calls = fake_mlx(monkeypatch)
    transcribe.release_memory()
    assert calls.cleared == 1


def test_releasing_memory_needs_no_mlx(monkeypatch):
    monkeypatch.delitem(sys.modules, "mlx.core", raising=False)
    transcribe.release_memory()


def test_running_out_of_mlx_memory_reads_plainly(monkeypatch, tmp_path):
    def fail(name):
        raise AssertionError("no model in this test")

    class Model:
        def transcribe(self, *args, **kwargs):
            raise RuntimeError("[metal::malloc] Attempting to allocate 9000000000 bytes")

    monkeypatch.setattr(transcribe, "load_parakeet", lambda name: Model())
    with pytest.raises(AltocueError, match="ran out of memory") as error:
        transcribe.ENGINES["parakeet"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    assert error.value.fix


def room(monkeypatch, *, memory: float | None = 40.0, disk: float = 400.0) -> None:
    monkeypatch.setattr(transcribe, "free_memory_gb", lambda: memory)
    monkeypatch.setattr(transcribe.shutil, "disk_usage", lambda path: SimpleNamespace(free=int(disk * 2**30)))


def test_a_transcribe_will_not_start_short_of_memory(monkeypatch):
    room(monkeypatch, memory=3.2)
    with pytest.raises(AltocueError, match=r"3\.2 GB of memory") as error:
        transcribe.check_room(transcribe.ENGINES["parakeet"], 60.0)
    assert "Close" in str(error.value.fix)


def test_a_transcribe_will_not_start_short_of_disk(monkeypatch):
    room(monkeypatch, disk=2.0)
    with pytest.raises(AltocueError, match=r"2\.0 GB of disk") as error:
        transcribe.check_room(transcribe.ENGINES["parakeet"], 60.0)
    assert "swap" in str(error.value)


def test_the_disk_check_counts_the_sound_it_will_write(monkeypatch):
    room(monkeypatch, disk=transcribe.MIN_FREE_DISK_GB + 0.1)
    transcribe.check_room(transcribe.ENGINES["parakeet"], 60.0)
    with pytest.raises(AltocueError):
        transcribe.check_room(transcribe.ENGINES["parakeet"], 3600.0 * 3)


def test_enough_room_passes(monkeypatch):
    room(monkeypatch)
    transcribe.check_room(transcribe.ENGINES["parakeet"], 3600.0)


def test_a_hosted_engine_needs_no_memory_of_its_own(monkeypatch):
    room(monkeypatch, memory=0.5)
    transcribe.check_room(transcribe.ENGINES["elevenlabs"], 60.0)


def test_memory_that_cannot_be_read_does_not_stop_the_job(monkeypatch, capsys):
    room(monkeypatch, memory=None)
    transcribe.check_room(transcribe.ENGINES["parakeet"], 60.0)
    assert "free memory" in capsys.readouterr().out


def test_nothing_loads_when_the_guard_refuses(tmp_path, monkeypatch):
    clip = tmp_path / "take.mp4"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=1", "-f", "lavfi", "-i", "sine=duration=1"]
    subprocess.run([*args, "-shortest", str(clip)], check=True)
    assert main(["new", str(tmp_path / "video"), "--input", str(clip)]) == 0
    project = Project.open(tmp_path / "video")
    room(monkeypatch, memory=1.0)

    class Refused(Pieces):
        needs_gb = 6.0

        def notice(self, seconds: float) -> str:
            return ""

        def transcribe(self, *args, **kwargs):
            pytest.fail("the engine ran after the guard refused")

    with pytest.raises(AltocueError, match="memory"):
        transcribe_project(project, engine=Refused())


def test_free_memory_reads_something_here():
    found = locks.free_memory_gb()
    assert found is None or 0 < found < locks.memory_gb() * 1.05


seen: list[dict] = []  # the options each fake transcription was given


def fake_whisper(monkeypatch, gpus: int, cuda_fails: bool = False) -> list[tuple[str, str, str]]:
    """faster-whisper and ctranslate2 stand-ins: record each model made, and fail on CUDA when its libraries are missing."""
    made: list[tuple[str, str, str]] = []
    seen.clear()

    class Model:
        def __init__(self, name, device, compute_type):
            made.append((name, device, compute_type))
            self.device = device

        def transcribe(self, samples, **options):
            seen.append(options)

            def segments():
                if self.device == "cuda" and cuda_fails:
                    raise RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
                yield SimpleNamespace(words=[SimpleNamespace(word=" Hello", start=0.0, end=0.4, probability=0.9)])

            return segments(), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: gpus))
    monkeypatch.setattr(ffmpeg, "read_wav", lambda *_: (np.zeros(16000, dtype=np.float32), 16000))
    monkeypatch.delenv("ALTOCUE_WHISPER_DEVICE", raising=False)
    return made


def test_faster_whisper_uses_an_nvidia_card_and_a_larger_model(monkeypatch, tmp_path):
    made = fake_whisper(monkeypatch, gpus=1)
    words = transcribe.ENGINES["faster-whisper"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    assert made == [("large-v3-turbo", "cuda", "float16")]
    assert [w.text for w in words] == ["Hello"]


def test_faster_whisper_falls_back_to_the_cpu_when_cuda_libraries_are_missing(monkeypatch, tmp_path, capsys):
    made = fake_whisper(monkeypatch, gpus=1, cuda_fails=True)
    words = transcribe.ENGINES["faster-whisper"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    assert made == [("large-v3-turbo", "cuda", "float16"), ("small.en", "cpu", "int8")]
    assert [w.text for w in words] == ["Hello"]
    assert "libcublas" in capsys.readouterr().err


def test_faster_whisper_stays_on_the_cpu_without_a_card_or_when_told(monkeypatch, tmp_path):
    made = fake_whisper(monkeypatch, gpus=0)
    transcribe.ENGINES["faster-whisper"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setenv("ALTOCUE_WHISPER_DEVICE", "cpu")
    transcribe.ENGINES["faster-whisper"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    assert made == [("small.en", "cpu", "int8"), ("small.en", "cpu", "int8")]


def test_a_finished_render_is_read_back_with_speech_detection(monkeypatch, tmp_path):
    """On the render PC, large-v3-turbo heard "We'll be right back." in a film whose only sound was music."""
    fake_whisper(monkeypatch, gpus=1)
    with transcribe.reading_back():
        transcribe.ENGINES["faster-whisper"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    assert seen[-1]["vad_filter"] is True


def test_a_raw_take_keeps_every_filler_without_speech_detection(monkeypatch, tmp_path):
    """Speech detection dropped the "Um" in the guy's raw take, and the cut needs every filler."""
    fake_whisper(monkeypatch, gpus=1)
    transcribe.ENGINES["faster-whisper"].transcribe(tmp_path / "a.wav", language="en", prompt="")
    assert seen[-1]["vad_filter"] is False


def test_transcribe_file_reads_back(monkeypatch, tmp_path):
    flags = []
    monkeypatch.setattr(transcribe, "transcribe_clip", lambda *a, **k: flags.append(transcribe.READING_BACK.get()) or [])
    monkeypatch.setattr(transcribe.ffmpeg, "probe_clip", lambda path: object())
    monkeypatch.setattr(transcribe, "select", lambda engine=None: object())
    transcribe.transcribe_file(tmp_path / "film.mp4")
    assert flags == [True] and transcribe.READING_BACK.get() is False
