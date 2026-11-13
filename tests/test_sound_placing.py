"""Effects land on the frame of their event and stay off the first sound of a word and the tail of a line. NumPy first; a few go through ffmpeg."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from altocue import ffmpeg, mix

RATE = 48000
FPS = 30


def burst(seconds: float = 0.2, peak_at: float = 0.0, amp: float = 0.5) -> np.ndarray:
    """A noise burst whose loudest moment is at `peak_at`, falling away either side."""
    n = int(seconds * RATE)
    shape = np.exp(-np.abs(np.arange(n) / RATE - peak_at) / 0.02)
    return amp * np.random.default_rng(4).uniform(-1, 1, n) * shape


def ramp(seconds: float = 0.4) -> np.ndarray:
    """A riser: quiet to loud, then it stops."""
    n = int(seconds * RATE)
    return 0.5 * np.sin(2 * np.pi * 3000 * np.arange(n) / RATE) * (np.arange(n) / n) ** 2


def words(*spans: tuple[float, float], text: str = "word") -> list[dict]:
    return [{"start": start, "end": end, "text": text} for start, end in spans]


def placed(samples: np.ndarray, at: float, *, align: str = "start", sync: str = "free", said=None, fps=None, total: float = 10.0, **cue):
    cue = {"file": "x.wav", "at": at, "align": align, "sync": sync, **cue}
    return mix._place(samples, cue, mix._placing(cue), round(total * RATE), fps=fps, said=said)


# A line from 1.0 to 2.4 s, then a pause, then a line from 3.5 s.
LINES = words((1.0, 1.3), (1.35, 1.8), (1.9, 2.4), (3.5, 3.9), (4.0, 4.6))


def test_lines_end_at_a_pause_and_at_a_full_stop():
    said = mix.speech([*words((0.0, 0.4)), {"start": 0.45, "end": 0.9, "text": "done."}, *words((0.95, 1.2), (2.0, 2.3))])
    assert list(said.line_ends) == [0.9, 1.2, 2.3]
    assert said.tails[0] == pytest.approx([0.9 - mix.LINE_TAIL, 0.95 - mix.ONSET_BEFORE])  # the breath stops short of the next word
    assert said.tails[1] == pytest.approx([1.2 - mix.LINE_TAIL, 1.2 + mix.LINE_BREATH])


def test_no_words_means_no_speech():
    assert mix.speech(None) is None and mix.speech([]) is None


@pytest.mark.parametrize(
    ("at", "zone"),
    [(0.5, "clear"), (0.97, "word onset"), (1.05, "word onset"), (2.2, "line tail"), (2.6, "line tail"), (3.0, "clear"), (3.46, "word onset")],
)
def test_each_moment_reads_as_clear_a_word_onset_or_a_line_tail(at, zone):
    assert mix.ZONES[int(mix.speech(LINES).zone(at)[0])] == zone


def test_a_sound_in_clear_air_is_left_alone():
    sound = burst()
    plain = placed(sound, 3.0)
    checked = placed(sound, 3.0, said=mix.speech(LINES))
    assert checked.start == plain.start and checked.trim_db == 0
    assert checked.facts["action"] == "kept" and checked.facts["zone"] == "clear" and plain.facts["action"] == "unchecked"


def test_a_free_sound_on_a_word_moves_into_clear_air_nearby_and_earlier_first():
    out = placed(burst(), 3.5, said=mix.speech(LINES))  # its hit is the first sound of a word
    facts = out.facts
    assert facts["zone"] == "word onset" and facts["action"] == "nudged"
    assert -mix.NUDGE_MOST * 1000 <= facts["moved_ms"] < 0  # into the pause before the word, not onto the word's second syllable
    assert mix.speech(LINES).zone(out.hit / RATE)[0] == 0
    assert out.trim_db == 0 and facts["to_word_onset"] <= -mix.ONSET_BEFORE


def test_a_free_sound_with_no_clear_air_in_reach_comes_down_instead():
    busy = words(*[(t, t + 0.12) for t in np.arange(1.0, 3.0, 0.15)])
    out = placed(burst(), 2.05, said=mix.speech(busy))
    assert out.facts["action"].startswith("ducked") and out.trim_db == -mix.UNDER_WORD_DB and out.facts["moved_ms"] == 0


def test_a_sound_late_in_the_breath_after_a_line_is_moved_past_it():
    out = placed(burst(), 2.65, said=mix.speech(LINES))
    assert out.facts["zone"] == "line tail" and out.facts["action"] == "nudged"
    assert out.hit / RATE > 2.4 + mix.LINE_BREATH and mix.speech(LINES).zone(out.hit / RATE)[0] == 0


def test_a_free_sound_deep_in_the_tail_of_a_line_comes_down_where_it_is():
    out = placed(burst(), 2.3, said=mix.speech(LINES))  # half a second of line and a breath either side: no clear air within reach
    assert out.facts["zone"] == "line tail" and out.facts["action"] == "ducked" and out.facts["moved_ms"] == 0


def test_a_picture_sound_on_a_word_keeps_its_frame_comes_down_and_stops_before_the_next_word():
    long_sound = burst(1.0, 0.0) * np.exp(np.arange(RATE) / RATE / 0.02 - np.arange(RATE) / RATE / 0.3)  # rings on for most of a second
    out = placed(long_sound, 1.35, sync="picture", said=mix.speech(LINES), fps=FPS)
    facts = out.facts
    assert facts["zone"] == "word onset" and facts["action"] == "ducked and shortened"
    assert out.start + mix.marks(long_sound)["onset"] == round(41 * RATE / FPS)  # 1.35 s is between frames 40 and 41, and the picture first shows it on 41
    assert out.trim_db == -mix.UNDER_WORD_DB and facts["under_word_db"] == mix.UNDER_WORD_DB
    assert out.start + len(out.samples) <= round((1.9 - mix.ONSET_BEFORE) * RATE)  # silent before the next word's first sound
    assert facts["shortened_to"] == pytest.approx(1.9 - mix.ONSET_BEFORE - 41 / FPS, abs=0.002)


def test_a_short_picture_sound_on_a_word_is_only_brought_down():
    out = placed(burst(0.05), 1.0, sync="picture", said=mix.speech(LINES), fps=FPS)
    assert out.facts["action"] == "ducked" and out.facts["shortened_to"] is None


@pytest.mark.parametrize("at", np.random.default_rng(7).uniform(0.2, 9.0, 40).round(4))
def test_a_picture_sound_never_moves_more_than_a_frame(at):
    for align, sound in (("start", burst(0.2, 0.01)), ("peak", burst(0.3, 0.12)), ("end", ramp())):
        out = placed(sound, float(at), align=align, sync="picture", said=mix.speech(LINES), fps=FPS)
        assert 0 <= out.facts["moved_ms"] < 1000 / FPS + 0.5
        assert out.facts["landed_frame"] == out.facts["event_frame"]


@pytest.mark.parametrize("fps", [24, 25, 30, 60])
def test_each_alignment_meets_the_first_frame_that_shows_the_event(fps):
    at = 2.0 + 0.4 / fps  # between two frames
    frame = int(np.ceil(at * fps))
    sound = burst(0.3, 0.1)
    found = mix.marks(sound)
    for align, mark in (("start", "onset"), ("peak", "peak"), ("end", "end")):
        out = placed(sound, at, align=align, sync="picture", fps=fps)
        assert out.start - 0 + found[mark] == round(frame / fps * RATE), align
        assert out.facts["event_frame"] == out.facts["landed_frame"] == frame


def test_end_alignment_lets_a_riser_finish_on_the_event():
    out = placed(ramp(), 3.0, align="end")
    assert out.start + mix.marks(ramp())["end"] == 3 * RATE and out.start + len(ramp()) - 3 * RATE < RATE // 100


def test_a_free_sound_is_not_moved_to_a_frame():
    assert placed(burst(), 2.0 + 0.4 / FPS, fps=FPS).facts["moved_ms"] == 0


def test_a_repeat_is_quieter_and_an_untuned_bundled_one_moves_in_pitch():
    first = mix._variation({"name": "pop", "at": 1}, 0)
    again = mix._variation({"name": "pop", "at": 1}, 1)
    tuned = mix._variation({"name": "ding", "at": 1}, 1)
    file = mix._variation({"file": "x.wav", "at": 1}, 1)
    assert first is None and again == {"gain_db": mix.VARY_GAIN_DB[1], "cents": mix.VARY_CENTS[1]}
    assert tuned["cents"] == 0 and file["cents"] == 0 and tuned["gain_db"] < 0
    assert mix._variation({"name": "pop", "at": 1, "vary": False}, 3) is None


def test_a_repitched_repeat_still_puts_its_peak_on_the_frame():
    sound = burst(0.3, 0.12)
    cue = {"name": "pop", "at": 2.0, "align": "peak", "sync": "picture"}
    out = mix._place(sound, cue, mix._placing(cue), 10 * RATE, fps=FPS, occurrence=1)
    assert len(out.samples) != len(sound)  # it was played faster
    assert out.hit == 60 * RATE // FPS and out.facts["variation"]["cents"] == mix.VARY_CENTS[1]


def test_repitching_moves_the_pitch_and_keeps_the_level():
    tone = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(RATE // 2) / RATE)
    up = mix._repitched(tone, 100)
    freqs = np.fft.rfftfreq(len(up), 1 / RATE)
    assert freqs[np.argmax(np.abs(np.fft.rfft(up)))] == pytest.approx(1000 * 2 ** (1 / 12), rel=0.005)
    assert np.sqrt(np.mean(up[1000:-1000] ** 2)) == pytest.approx(np.sqrt(np.mean(tone**2)), rel=0.03)


def test_every_bundled_sound_aligns_as_its_family_does_or_says_why():
    from altocue import library

    for row in json.loads((mix.SFX_DIR / "sounds.json").read_text()):
        natural = library.SOUND_FAMILIES[row["family"]][0]
        assert row["align"] == natural or row.get("align_why"), row["name"]


# Through ffmpeg: the bundled files decode, and what is measured in the mix is what the report says.


def test_the_measured_marks_in_the_table_are_the_files_own(tmp_path):
    for row in json.loads((mix.SFX_DIR / "sounds.json").read_text()):
        samples, _ = ffmpeg.read_wav(ffmpeg.extract_wav(mix.sfx_path(row["name"]), tmp_path / f"{row['name']}.wav"))
        found = mix.measure(samples)
        for key in ("onset_ms", "peak_ms", "end_ms"):
            assert row[key] == pytest.approx(found[key], abs=3), (row["name"], key)
        assert row["seconds"] == pytest.approx(found["seconds"], abs=0.02), row["name"]


def test_every_bundled_sound_meets_its_frame_by_its_own_alignment(tmp_path):
    for name in mix.sfx_names():
        samples, _ = ffmpeg.read_wav(ffmpeg.extract_wav(mix.sfx_path(name), tmp_path / f"{name}.wav"))
        cue = {"name": name, "at": 2.013, "sync": "picture"}
        place = mix._placing(cue)
        out = mix._place(samples.astype(np.float64), cue, place, 10 * RATE, fps=FPS)
        mark = {"start": "onset", "peak": "peak", "end": "end"}[place["align"]]
        assert out.start + mix.marks(samples)[mark] == round(61 * RATE / FPS), name
        assert out.facts["landed_frame"] == 61, name


def stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0


def test_in_the_mix_the_peak_lands_in_its_frame_and_the_report_says_where(tmp_path):
    hit = ffmpeg.write_wav(tmp_path / "hit.wav", burst(0.4, 0.15), RATE)
    at = 3.0 + 0.5 / 25
    out = mix.mix_bed(None, tmp_path / "mix.wav", sfx=[{"file": str(hit), "at": at, "align": "peak", "sync": "picture"}], seconds=6.0, fps=25)
    loudest = int(np.argmax(np.abs(stereo(out)[:, 0])))
    assert loudest == pytest.approx(76 * RATE / 25, abs=2)  # frame 76 starts at 3.04 s
    row = json.loads(out.with_suffix(".json").read_text())["sfx"][0]
    assert row["event_frame"] == row["landed_frame"] == 76 and row["peak_at"] == pytest.approx(3.04, abs=1e-3)


def test_with_words_the_report_gives_the_facts_and_the_rule(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", 0.1 * np.sin(np.arange(6 * RATE) * 0.05), RATE)
    hit = ffmpeg.write_wav(tmp_path / "hit.wav", burst(0.2, 0.01), RATE)
    cues = [{"file": str(hit), "at": 3.5}, {"file": str(hit), "at": 1.35, "sync": "picture"}, {"file": str(hit), "at": 5.5}]
    out = mix.mix_bed(voice, tmp_path / "mix.wav", sfx=cues, fps=FPS, words=LINES)
    report = json.loads(out.with_suffix(".json").read_text())
    rows = {row["at"]: row for row in report["sfx"]}
    assert rows[3.5]["action"] == "nudged" and rows[1.35]["action"].startswith("ducked") and rows[5.5]["action"] == "kept"
    for row in report["sfx"]:
        assert {"asked_at", "landed_at", "moved_ms", "peak_at", "event_frame", "landed_frame", "zone", "to_word_onset", "to_line_end"} <= set(row)
    assert report["placing"]["words"] == {"onsets": 5, "lines": 2} and report["placing"]["fps"] == FPS
    assert rows[5.5]["variation"] == {"gain_db": mix.VARY_GAIN_DB[2], "cents": 0}  # the third hearing of the same file


def test_a_mix_with_no_effects_has_no_placing_and_the_same_samples(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", 0.1 * np.sin(np.arange(3 * RATE) * 0.05), RATE)
    plain = mix.mix_bed(voice, tmp_path / "a.wav")
    told = mix.mix_bed(voice, tmp_path / "b.wav", fps=FPS, words=LINES)
    assert "placing" not in json.loads(told.with_suffix(".json").read_text())
    assert plain.read_bytes() == told.read_bytes()


def test_the_same_film_mixes_to_the_same_samples(tmp_path):
    voice = ffmpeg.write_wav(tmp_path / "voice.wav", 0.1 * np.sin(np.arange(6 * RATE) * 0.05), RATE)
    cues = [{"name": "pop", "at": t, "sync": "picture"} for t in (1.0, 1.5, 2.0, 3.6, 4.1)]
    first = mix.mix_bed(voice, tmp_path / "a.wav", sfx=cues, fps=FPS, words=LINES)
    second = mix.mix_bed(voice, tmp_path / "b.wav", sfx=cues, fps=FPS, words=LINES)
    assert first.read_bytes() == second.read_bytes()
