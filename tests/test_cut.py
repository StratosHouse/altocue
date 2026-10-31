from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from altocue import cut, ffmpeg
from altocue.cli import main
from altocue.cut import expected_words, map_words, propose, removal_id, validate
from altocue.cut.detect import candidates, same, stream, stutters
from altocue.cut.snap import OVERLAP, POST, PRE, frame_count, plays, snap, to_grid
from altocue.errors import AltocueError
from altocue.models import Clip, Edl, Removed, Segment, Word
from altocue.project import Project
from altocue.transcribe import HOP, Envelope, classify

RATE = 16000


def timed(text: str, *, start: float = 0.0, step: float = 0.3, gap: float = 0.0) -> list[Word]:
    words = []
    for token in text.split():
        words.append(Word(text=token, start=round(start, 3), end=round(start + step, 3)))
        start += step + gap
    return words


def speech(seconds: float, spans: list[tuple[float, float]], *, level: float = 0.25, noise: float = 0.0, seed: int = 1) -> np.ndarray:
    """Tone bursts stand in for speech: what matters here is where the energy is."""
    samples = (
        np.random.default_rng(seed).normal(0, noise, int(RATE * seconds)).astype(np.float32)
        if noise
        else np.zeros(int(RATE * seconds), dtype=np.float32)
    )
    for start, end in spans:
        a, b = int(start * RATE), int(end * RATE)
        samples[a:b] += level * np.sin(2 * np.pi * 180 * np.arange(b - a) / RATE).astype(np.float32)
    return samples


def envelope(seconds: float, spans: list[tuple[float, float]], **options) -> Envelope:
    return Envelope.measure(speech(seconds, spans, **options), RATE)


def add_clip(tmp_path: Path, name: str, seconds: float, spans: list[tuple[float, float]] | None, **options) -> Clip:
    if spans is None:
        return Clip(id=name, path=str(tmp_path / f"{name}-missing.mp4"), duration=seconds, fps=30, width=1080, height=1920, has_audio=False)
    wav = ffmpeg.write_wav(tmp_path / f"{name}.wav", speech(seconds, spans, **options), RATE)
    return Clip(id=name, path=str(wav), duration=seconds, fps=30, width=1080, height=1920)


def make_project(tmp_path: Path, clips: dict[str, tuple[Clip, list[Word]]], *, fps: int = 0, **cut_settings) -> Project:
    project = Project.create(tmp_path / "video")
    project.save_clips([clip for clip, _ in clips.values()])
    for name, (_, words) in clips.items():
        project.save_words(name, classify(words), "test")
    if fps:
        project.config["output"]["fps"] = fps
    project.config["cut"].update(cut_settings)
    return project


def silent_project(tmp_path: Path, words: list[Word], seconds: float | None = None, **options) -> Project:
    """Words only, no audio: the transcript is all there is to go on."""
    duration = seconds if seconds is not None else words[-1].end + 0.5
    return make_project(tmp_path, {"a": (add_clip(tmp_path, "a", duration, None), words)}, **options)


def spans_of(words: list[Word]) -> list[tuple[float, float]]:
    return [(w.start, w.end) for w in words]


def voiced_project(tmp_path: Path, words: list[Word], seconds: float | None = None, **options) -> Project:
    duration = seconds if seconds is not None else words[-1].end + 0.5
    return make_project(tmp_path, {"a": (add_clip(tmp_path, "a", duration, spans_of(words)), words)}, **options)


def kinds(edl: Edl) -> list[str]:
    return [r.kind for r in edl.removed]


def spoken(project: Project, edl: Edl) -> str:
    return " ".join(expected_words(project, edl))


# The ported constants, exactly.


def test_snap_constants_are_the_proven_ones():
    assert (PRE, POST, HOP, OVERLAP) == (0.10, 0.14, 0.02, 0.08)


def test_threshold_formula():
    env = envelope(4.0, [(1.0, 3.0)], noise=0.01)
    assert env.threshold == pytest.approx(max(env.floor + 6.0, env.loud - 26.0, -52.0))


def test_threshold_has_a_floor_in_digital_silence():
    assert envelope(4.0, [(1.0, 3.0)], level=0.0005).threshold == -52.0


def test_in_point_moves_to_the_real_onset():
    env = envelope(5.0, [(1.3, 3.0)])
    start, _ = snap(env, 1.0, 3.0)
    assert start == pytest.approx(1.3 - PRE, abs=0.03)


def test_in_point_is_never_earlier_than_asked_minus_50_ms():
    env = envelope(5.0, [(1.0, 3.0)])
    start, _ = snap(env, 1.02, 3.0)
    assert start >= 1.02 - 0.05 - 1e-9


def test_far_late_onset_falls_back_to_the_first_word():
    env = envelope(5.0, [(2.1, 3.5)])
    start, _ = snap(env, 1.3, 3.5, first_word_start=1.0)
    assert start == pytest.approx(1.0 - 0.04)


def test_out_point_moves_to_the_last_speech_frame():
    env = envelope(5.0, [(1.0, 2.5)])
    _, end = snap(env, 1.0, 2.9)
    assert end == pytest.approx(2.5 + POST, abs=0.03)


def test_out_point_never_lands_inside_the_last_word():
    env = envelope(5.0, [(1.0, 2.0)])
    _, end = snap(env, 1.0, 2.5, last_word_end=2.5)
    assert end >= 2.5 + 0.06 - 1e-9


def test_snap_keeps_the_original_when_the_result_is_tiny():
    env = envelope(5.0, [(1.0, 1.05)])
    assert snap(env, 0.98, 1.1) == (0.98, 1.1)


def test_snap_can_be_switched_off_per_edge():
    env = envelope(5.0, [(1.3, 2.5)])
    assert snap(env, 1.0, 2.9, snap_in=False)[0] == 1.0
    assert snap(env, 1.0, 2.9, snap_out=False)[1] == 2.9


def test_threshold_adapts_to_outdoor_noise():
    quiet, loud = envelope(6.0, [(2.0, 4.0)], noise=0.001), envelope(6.0, [(2.0, 4.0)], noise=0.03)
    assert loud.threshold > quiet.threshold + 10
    assert loud.reliable
    assert snap(loud, 1.5, 4.5)[0] == pytest.approx(2.0 - PRE, abs=0.05)
    assert snap(loud, 1.5, 4.5)[1] == pytest.approx(4.0 + POST, abs=0.05)


def test_a_fixed_silence_floor_would_fail_where_this_works():
    env = envelope(6.0, [(2.0, 4.0)], noise=0.03)
    assert (env.db > -45).mean() > 0.95
    assert env.speech.mean() < 0.45


def test_steady_noise_is_not_trusted_and_edges_stay_on_the_transcript(tmp_path):
    words = timed("hello there friend", start=1.0)
    clip = add_clip(tmp_path, "a", 4.0, [(0.0, 4.0)], noise=0.05)
    project = make_project(tmp_path, {"a": (clip, words)})
    edl = propose(project)
    assert not cut.envelope_of(project.clips()[0]).reliable
    assert edl.segments[0].start == pytest.approx(1.0 - PRE, abs=1 / 30)


# Layer 1.


def test_long_pause_is_removed_and_short_one_stays(tmp_path):
    words = timed("one two", step=0.3) + timed("three", start=0.9) + timed("four", start=3.0)
    edl = propose(silent_project(tmp_path, words))
    pauses = [r for r in edl.removed if r.kind == "silence" and "long pause" in r.evidence]
    assert [(r.start, r.end) for r in pauses] == [(1.2, 3.0)]
    assert len(edl.segments) == 2


def test_pause_setting_is_read_from_the_project(tmp_path):
    words = timed("one", step=0.3) + timed("two", start=0.9)
    assert len(propose(silent_project(tmp_path, words, max_pause=0.7)).segments) == 1
    assert len(propose(silent_project(tmp_path / "b", words, max_pause=0.4)).segments) == 2


def test_lead_and_tail_silence_are_explained(tmp_path):
    words = timed("hello there", start=1.0)
    edl = propose(silent_project(tmp_path, words, seconds=3.0))
    reasons = [r.evidence for r in edl.removed if r.kind == "silence"]
    assert any("before the first word" in r for r in reasons) and any("after the last word" in r for r in reasons)


def test_filler_is_cut(tmp_path):
    words = timed("well um that works")
    project = silent_project(tmp_path, words)
    edl = propose(project)
    assert "filler" in kinds(edl)
    assert spoken(project, edl) == "well that works"
    assert len(edl.segments) == 2


def test_fillers_can_be_kept(tmp_path):
    project = silent_project(tmp_path, timed("well um that works"), remove_fillers=False)
    assert spoken(project, propose(project)) == "well um that works"


def test_spoken_cue_is_cut(tmp_path):
    words = timed("It was a great day.", step=0.2) + timed("Redo.", start=1.6) + timed("Something else entirely now.", start=2.6, step=0.2)
    project = silent_project(tmp_path, words)
    edl = propose(project)
    assert "cue" in kinds(edl)
    assert "Redo." not in expected_words(project, edl)


def test_pause_with_speech_energy_waits_for_a_ruling(tmp_path):
    words = timed("one two") + timed("three four", start=2.0)
    clip = add_clip(tmp_path, "a", 3.2, [*spans_of(words), (0.9, 1.5)])
    edl = propose(make_project(tmp_path, {"a": (clip, words)}))
    gap = next(r for r in edl.removed if r.start == 0.6)
    assert gap.decision == "review" and "speech energy" in gap.evidence
    assert len(edl.segments) == 1


def test_a_word_tail_in_the_pause_is_not_missed_speech(tmp_path):
    words = timed("one two") + timed("three four", start=2.0)
    clip = add_clip(tmp_path, "a", 3.2, [(0.0, 0.84), (1.84, 2.6)])
    edl = propose(make_project(tmp_path, {"a": (clip, words)}))
    assert next(r for r in edl.removed if r.start == 0.6).decision == "auto"


def test_quiet_pause_is_cut(tmp_path):
    words = timed("one two") + timed("three four", start=2.0)
    edl = propose(voiced_project(tmp_path, words))
    assert next(r for r in edl.removed if r.start == 0.6).decision == "auto"
    assert len(edl.segments) == 2


def test_clip_with_no_words_stays_until_ruled_on(tmp_path):
    words = timed("hello there")
    clips = {"a": (add_clip(tmp_path, "a", 2.0, None), words), "b": (add_clip(tmp_path, "b", 3.0, None), [])}
    project = make_project(tmp_path, clips)
    edl = propose(project)
    assert [s.clip for s in edl.segments] == ["a", "b"]
    item = next(r for r in edl.removed if r.clip == "b")
    assert item.decision == "review" and validate(project, edl) == []


# Layer 2.


def detect(words: list[Word], name: str = "a") -> list:
    clip = Clip(id=name, path="", duration=(words[-1].end if words else 0) + 1, fps=30, width=10, height=10)
    return candidates([clip], {name: classify(words)})


def test_short_tokens_must_match_exactly():
    assert not same("the", "they")
    assert not same("them", "they")
    assert same("provider", "providers")
    assert not same("cat", "cats")


def test_stutter_keeps_the_last_copy(tmp_path):
    words = timed("I I I think so", step=0.2)
    project = silent_project(tmp_path, words)
    edl = propose(project)
    item = next(r for r in edl.removed if r.kind == "stutter")
    assert item.decision == "auto" and item.score >= 0.9
    assert spoken(project, edl) == "I think so"


def test_broken_word_is_cut(tmp_path):
    words = timed("we wa- want this", step=0.2)
    project = silent_project(tmp_path, words)
    assert spoken(project, propose(project)) == "we want this"


def test_a_word_shared_across_a_sentence_break_is_no_stutter():
    assert detect(timed("What did you do? Do you know?", step=0.2)) == []


def test_emphasis_is_kept_and_reported():
    found = detect(timed("it was very very good", step=0.2))
    assert [c.decision for c in found] == ["keep"]


def test_grammatical_double_waits_for_a_ruling():
    assert [c.decision for c in detect(timed("what he had had was fine", step=0.2))] == ["review"]


def test_phrase_stutter_is_found():
    found = detect(timed("look in the in the box", step=0.2))
    assert [(c.kind, c.decision) for c in found] == [("stutter", "auto")]


def test_repeat_run_with_a_pause_is_auto():
    words = timed("We built this tool for writers.", step=0.2) + timed("We built this tool for writers who edit.", start=2.0, step=0.2)
    found = [c for c in detect(words) if c.kind == "retake"]
    assert len(found) == 1
    assert found[0].decision == "auto" and found[0].score >= 0.8
    assert (found[0].start, found[0].end) == (0.0, 2.0)
    assert "said again" in found[0].evidence


def test_the_last_of_three_takes_survives(tmp_path):
    line = "Our product saves hours every week."
    words = timed(line, step=0.2) + timed(line, start=2.0, step=0.2) + timed(line, start=4.0, step=0.2)
    project = silent_project(tmp_path, words)
    edl = propose(project)
    assert sum(r.kind == "retake" and r.decision == "auto" for r in edl.removed) == 2
    assert spoken(project, edl) == line


def test_false_start_is_cut(tmp_path):
    words = timed("The main reason-", step=0.2) + timed("The main reason we shipped early was money.", start=1.0, step=0.2)
    project = silent_project(tmp_path, words)
    edl = propose(project)
    assert any(r.kind == "retake" and r.decision == "auto" for r in edl.removed)
    assert expected_words(project, edl)[:4] == ["The", "main", "reason", "we"]


def test_long_earlier_take_with_unique_content_goes_to_review():
    take = "So this product helps writers finish drafts faster. Our customers include schools and hospitals across three countries."
    again = "So this product helps writers finish drafts faster with better notes."
    words = timed(take, step=0.2) + timed(again, start=8.0, step=0.2)
    found = [c for c in detect(words) if c.kind == "retake"]
    assert [c.decision for c in found] == ["review"]
    assert "content" in found[0].reason


def test_review_item_stays_in_the_video(tmp_path):
    take = "So this product helps writers finish drafts faster. Our customers include schools and hospitals across three countries."
    again = "So this product helps writers finish drafts faster with better notes."
    words = timed(take, step=0.2) + timed(again, start=8.0, step=0.2)
    project = silent_project(tmp_path, words)
    edl = propose(project)
    assert "hospitals" in spoken(project, edl)


def test_line_said_again_minutes_later_survives():
    line = "Great teams ship small changes every single day."
    middle = timed(" ".join(f"filler{i}" for i in range(20)), start=2.0, step=0.5)
    words = timed(line, step=0.2) + middle + timed(line, start=200.0, step=0.2)
    assert [c for c in detect(words) if c.kind == "retake"] == []


def test_fluent_repetition_is_kept_with_a_reason():
    words = timed("people want better tools people want better tools every day", step=0.2)
    found = [c for c in detect(words) if c.decision != "auto"]
    assert any(c.decision == "keep" for c in found)


def test_ordinary_shared_words_are_not_a_retake():
    words = timed("The train leaves at seven.", step=0.2) + timed("The train station opens much earlier today.", start=2.0, step=0.2)
    assert [c for c in detect(words) if c.kind == "retake"] == []


def test_cue_turns_a_repeat_into_a_sure_cut(tmp_path):
    take = timed("So here is what we found in the data last year.", step=0.2)
    cue = timed("Let's try again.", start=3.0, step=0.2)
    again = timed("So here is what we found.", start=5.0, step=0.2)
    project = silent_project(tmp_path, take + cue + again)
    edl = propose(project)
    item = next(r for r in edl.removed if r.kind == "retake")
    assert item.decision == "auto" and item.score >= 0.9
    assert spoken(project, edl) == "So here is what we found."


def test_cue_with_no_later_match_flags_the_line_before_it():
    words = (
        timed("This sentence came out badly.", step=0.2)
        + timed("Redo.", start=1.6)
        + timed("Totally different words follow now.", start=2.6, step=0.2)
    )
    found = [c for c in detect(words) if c.kind == "retake"]
    assert [c.decision for c in found] == ["review"]
    assert "cue" in found[0].reason


def test_retake_in_the_next_clip_is_a_review_item(tmp_path):
    first = timed("Once the bread has cooled we can slice the whole loaf for the table.", step=0.2) + timed(
        "Well I do not", start=4.0, step=0.2
    )
    second = timed("Once the bread has cooled we can cut the whole loaf for the big table tonight.", step=0.2)
    clips = {"a": (add_clip(tmp_path, "a", 6.0, None), first), "b": (add_clip(tmp_path, "b", 5.0, None), second)}
    project = make_project(tmp_path, clips)
    edl = propose(project)
    item = next(r for r in edl.removed if r.kind == "retake")
    assert item.clip == "a" and item.decision == "review"
    assert item.start == 0.0 and item.end == first[-1].end


def test_retake_in_the_next_clip_takes_its_whole_line(tmp_path):
    first = timed("Right so we will begin by sanding the frame.", step=0.2)
    second = timed("First we begin by sanding the frame outside.", step=0.2)
    clips = {"a": (add_clip(tmp_path, "a", 3.0, None), first), "b": (add_clip(tmp_path, "b", 3.0, None), second)}
    item = next(r for r in propose(make_project(tmp_path, clips)).removed if r.kind == "retake")
    assert item.start == 0.0


def test_a_mid_sentence_restart_keeps_the_words_before_it(tmp_path):
    words = timed("And the thing is we built", step=0.2) + timed("we built a tool for writers.", start=1.6, step=0.2)
    found = [c for c in detect(words) if c.kind == "retake"]
    assert [c.start for c in found] == [0.8]


def test_distant_clips_do_not_pair(tmp_path):
    line = "Great teams ship small changes every day."
    clips = {
        "a": (add_clip(tmp_path, "a", 3.0, None), timed(line, step=0.2)),
        "b": (add_clip(tmp_path, "b", 3.0, None), timed("Totally unrelated words spoken right here.", step=0.2)),
        "c": (add_clip(tmp_path, "c", 3.0, None), timed(line, step=0.2)),
    }
    assert not any(r.kind == "retake" for r in propose(make_project(tmp_path, clips)).removed)


def test_fillers_and_cues_do_not_count_as_matching_words():
    tokens = stream([Clip(id="a", path="", duration=5, fps=30, width=1, height=1)], {"a": classify(timed("um hello uh there"))})
    assert [t.norm for t in tokens] == ["hello", "there"]
    assert stutters(tokens) == []


# The edit itself.


def test_multi_clip_project_keeps_clip_order_and_ids(tmp_path):
    clips = {
        "a": (add_clip(tmp_path, "a", 3.0, [(0.5, 2.0)]), timed("hello there world", start=0.5, step=0.5)),
        "b": (add_clip(tmp_path, "b", 3.0, [(1.0, 2.5)]), timed("second clip speaks", start=1.0, step=0.5)),
    }
    project = make_project(tmp_path, clips)
    edl = propose(project)
    assert [s.clip for s in edl.segments] == ["a", "b"]
    assert [s.id for s in edl.segments] == ["s001", "s002"]
    assert validate(project, edl) == []
    assert expected_words(project, edl) == ["hello", "there", "world", "second", "clip", "speaks"]


def test_edges_snap_to_speech_inside_a_real_cut(tmp_path):
    words = timed("one two", start=1.0, step=0.4) + timed("three four", start=4.0, step=0.4)
    clip = add_clip(tmp_path, "a", 6.0, [(1.2, 1.8), (4.0, 4.8)])
    project = make_project(tmp_path, {"a": (clip, words)})
    edl = propose(project)
    assert edl.segments[0].start == pytest.approx(1.2 - PRE, abs=1 / 30 + 0.02)
    assert validate(project, edl) == []


def test_a_pause_with_no_real_silence_is_not_a_cut(tmp_path):
    words = [Word("one", 0.2, 0.7), Word("two", 1.15, 1.6)]
    clip = add_clip(tmp_path, "a", 2.2, [(0.2, 1.0), (1.1, 1.6)])
    project = make_project(tmp_path, {"a": (clip, words)})
    edl = propose(project)
    assert len(edl.segments) == 1
    assert validate(project, edl) == []


def test_in_point_never_earlier_than_first_word_lead(tmp_path):
    words = timed("one two", start=1.0, step=0.4)
    edl = propose(voiced_project(tmp_path, words, seconds=3.0))
    assert edl.segments[0].start >= 1.0 - PRE - 0.05 - 1 / 30


def test_edges_never_touch_a_removed_neighbour(tmp_path):
    words = timed("well um that works", step=0.3)
    project = voiced_project(tmp_path, words)
    edl = propose(project)
    first, second = edl.segments
    assert first.end <= 0.3 + 1e-6 and second.start >= 0.6 - 1e-6
    assert validate(project, edl) == []


def test_first_short_word_survives_a_late_onset(tmp_path):
    words = [Word("Hey", 1.0, 1.1), *timed("look over here", start=1.4, step=0.3)]
    clip = add_clip(tmp_path, "a", 3.5, [(1.4, 2.3)])
    project = make_project(tmp_path, {"a": (clip, words)})
    edl = propose(project)
    assert expected_words(project, edl)[0] == "Hey"


@pytest.mark.parametrize("fps", [24, 25, 30, 48, 50, 60, 120])
def test_every_segment_is_a_whole_number_of_frames(tmp_path, fps):
    words = timed("one two", start=0.33) + timed("three four", start=1.771) + timed("five six", start=3.117)
    project = voiced_project(tmp_path, words, fps=fps)
    edl = propose(project)
    assert edl.fps == fps
    for segment in edl.segments:
        for edge in (segment.start, segment.end):
            assert abs(edge * fps - round(edge * fps)) < 1e-3
    total = sum(frame_count(s.start, s.end, fps) for s in edl.segments)
    assert total == round(edl.duration * fps)


def test_no_segment_runs_past_the_last_full_frame(tmp_path):
    seconds = 2.05
    words = timed("right to the end", start=0.8, step=0.31)
    project = voiced_project(tmp_path, words, seconds=seconds, fps=30)
    edl = propose(project)
    assert edl.segments[-1].end * 30 <= math.floor(seconds * 30) + 1e-6


def test_grid_keeps_edges_inside_their_limits():
    assert to_grid(0.41, 1.19, 30, floor=0.41, ceiling=1.19) == (round(13 / 30, 6), round(35 / 30, 6))


def test_a_word_plays_with_80_ms_inside():
    word = Word("x", 1.0, 1.5)
    assert plays(word, 1.42, 2.0)
    assert not plays(word, 1.43, 2.0)


def test_a_word_shorter_than_80_ms_plays_when_all_of_it_does():
    assert plays(Word("a", 1.0, 1.05), 0.9, 2.0)


def test_a_tiny_word_outside_the_edge_does_not_play():
    tiny = Word("x", 107.0, 107.04)
    assert not plays(tiny, 107.0417, 108.0, tolerance=1 / 24)
    assert not plays(tiny, 106.0, 107.0, tolerance=1 / 24)
    assert plays(tiny, 107.015, 108.0, tolerance=1 / 24)


def test_a_long_word_gets_no_tolerance_on_the_80_ms_rule():
    assert not plays(Word("x", 1.0, 1.5), 1.45, 2.0, tolerance=1 / 24)


# Refusals.


def handmade(
    words: list[Word], segment: tuple[float, float], tmp_path: Path, *, audio: list[tuple[float, float]] | None = None, fps: int = 30
) -> tuple[Project, Edl]:
    duration = words[-1].end + 0.5
    project = make_project(tmp_path, {"a": (add_clip(tmp_path, "a", duration, audio), words)}, fps=fps)
    return project, Edl(fps=fps, segments=[Segment(id="s001", clip="a", start=segment[0], end=segment[1])])


def test_validate_refuses_a_cut_through_a_word(tmp_path):
    words = timed("one two three", step=0.6)
    project, edl = handmade(words, (0.0, 0.9), tmp_path)
    edl.removed = [Removed("a", 0.9, 2.3, "manual", "test")]
    assert any("cuts through" in p for p in validate(project, edl))


def test_cut_through_a_word_is_refused_when_audio_agrees(tmp_path):
    words = timed("one two three", step=0.6)
    project, edl = handmade(words, (0.0, 0.9), tmp_path, audio=[(0.0, 1.8)])
    edl.removed = [Removed("a", 0.9, 2.3, "manual", "test")]
    assert any("cuts through" in p for p in validate(project, edl))


def test_an_edge_past_an_early_word_start_is_allowed_when_that_part_is_silent(tmp_path):
    """Whisper dates first words up to 0.4 s early. That head holds no speech, so nothing is cut."""
    words = timed("one two", start=1.0, step=0.5)
    project, edl = handmade(words, (1.3, 2.1), tmp_path, audio=[(1.4, 2.0)])
    edl.removed = [Removed("a", 0.0, 1.0, "silence", "test"), Removed("a", 2.1, 2.5, "silence", "test")]
    assert validate(project, edl) == []


def test_validate_allows_edges_within_a_frame_of_a_word(tmp_path):
    words = timed("one two", step=0.5)
    project, edl = handmade(words, (0.0, 1 / 30 + 1.0), tmp_path)
    edl.segments[0].end = round(30 / 30, 6)
    edl.removed = []
    assert validate(project, edl) == []


def test_validate_flags_a_word_dropped_without_a_reason(tmp_path):
    words = timed("one two three", step=0.5)
    project, edl = handmade(words, (0.0, 1.0), tmp_path)
    assert any("no removal" in p for p in validate(project, edl))


def test_validate_flags_a_removed_word_that_still_plays(tmp_path):
    words = timed("one two", step=0.5)
    project, edl = handmade(words, (0.0, 1.0), tmp_path)
    edl.removed = [Removed("a", 0.0, 0.5, "filler", "test")]
    assert any("marked as removed" in p for p in validate(project, edl))


def test_validate_flags_overlapping_segments(tmp_path):
    words = timed("one two", step=0.5)
    project, edl = handmade(words, (0.0, 1.0), tmp_path)
    edl.segments.append(Segment(id="s002", clip="a", start=0.5, end=1.0))
    assert any("overlaps" in p for p in validate(project, edl))


def test_validate_flags_a_segment_off_the_frame_grid(tmp_path):
    words = timed("one two", step=0.5)
    project, edl = handmade(words, (0.0, 1.015), tmp_path)
    assert any("whole number of frames" in p for p in validate(project, edl))


def test_validate_flags_a_segment_past_the_clip(tmp_path):
    words = timed("one two", step=0.5)
    project, edl = handmade(words, (0.0, 5.0), tmp_path)
    assert any("past the end" in p for p in validate(project, edl))


def test_validate_flags_an_unknown_clip_and_a_bad_range(tmp_path):
    words = timed("one two", step=0.5)
    project, edl = handmade(words, (1.0, 0.5), tmp_path)
    edl.segments.append(Segment(id="s002", clip="nope", start=0.0, end=1.0))
    problems = validate(project, edl)
    assert any("bad range" in p for p in problems) and any("no clip named" in p for p in problems)


def test_map_words_refuses_a_bad_cut(tmp_path):
    words = timed("one two three", step=0.6)
    project, edl = handmade(words, (0.0, 0.9), tmp_path)
    with pytest.raises(AltocueError, match="Refusing"):
        map_words(project, edl)


def test_cli_writes_nothing_for_a_cut_it_refuses(tmp_path, monkeypatch, capsys):
    project = silent_project(tmp_path, timed("one two three"))
    monkeypatch.setattr(cut, "validate", lambda project, edl: ["s001: cuts through a word"])
    assert main(["cut", "--project", str(project.root)]) == 1
    assert "Refusing" in capsys.readouterr().err
    assert not project.edl_path.exists()


# Word mapping.


@pytest.mark.parametrize("fps", [24, 25, 30, 48, 50, 60, 120])
def test_map_words_works_at_any_whole_frame_rate(tmp_path, fps):
    words = timed("one two three four", start=0.4, step=0.25) + timed("five six seven eight", start=3.2, step=0.25)
    project = voiced_project(tmp_path, words, fps=fps)
    edl = propose(project)
    mapped = map_words(project, edl)
    assert [w["text"] for w in mapped] == [w.text for w in words]
    for word in mapped:
        assert word["end_frame"] > word["start_frame"]
    for left, right in zip(mapped, mapped[1:], strict=False):
        assert right["start_frame"] >= left["end_frame"]
        assert right["start"] >= left["start"]
    total = sum(frame_count(s.start, s.end, fps) for s in edl.segments)
    assert mapped[-1]["end_frame"] <= total


def test_output_times_close_the_gap_a_cut_removes(tmp_path):
    words = timed("one two", step=0.3) + timed("three four", start=5.0, step=0.3)
    project = silent_project(tmp_path, words, fps=30)
    edl = propose(project)
    mapped = map_words(project, edl)
    assert mapped[2]["segment"] == "s002"
    gap = mapped[2]["start"] - mapped[1]["end"]
    assert 0 <= gap < 0.4


def test_times_match_the_frame_total_after_every_segment(tmp_path):
    words = timed("one two", start=0.31, step=0.33) + timed("three four", start=2.37, step=0.33) + timed("five six", start=4.21, step=0.33)
    project = voiced_project(tmp_path, words, fps=24)
    edl = propose(project)
    mapped = map_words(project, edl)
    frame = 0
    for segment in edl.segments:
        first = next(w for w in mapped if w["segment"] == segment.id)
        assert first["start_frame"] >= frame
        frame += frame_count(segment.start, segment.end, 24)
        last = [w for w in mapped if w["segment"] == segment.id][-1]
        assert last["end_frame"] <= frame


def test_words_too_tight_for_the_frame_rate_are_pushed_not_collided(tmp_path):
    words = [Word("a", 1.0, 1.01), Word("b", 1.01, 1.02), Word("c", 1.02, 1.03), Word("done", 1.03, 1.6)]
    project = silent_project(tmp_path, words, fps=24)
    mapped = map_words(project, propose(project))
    assert all(b["start_frame"] >= a["end_frame"] for a, b in zip(mapped, mapped[1:], strict=False))


def test_a_word_cut_at_a_rounded_segment_end_still_fits(tmp_path):
    words = timed("one two", start=30.0, step=8.6)
    project = silent_project(tmp_path, words, seconds=48.0, fps=24)
    edl = Edl(
        fps=24, segments=[Segment(id="s001", clip="a", start=29.875, end=47.166667)], removed=[Removed("a", 0.0, 29.9, "silence", "t")]
    )
    project.save_words("a", [Word("one", 30.0, 47.2), Word("two", 47.4, 47.6)], "test")
    edl.removed.append(Removed("a", 47.3, 48.0, "silence", "t"))
    mapped = map_words(project, edl)
    assert mapped[-1]["end_frame"] == frame_count(29.875, 47.166667, 24)


def test_map_words_carries_kind_for_captions(tmp_path):
    project = silent_project(tmp_path, timed("hello um there"), remove_fillers=False)
    mapped = map_words(project, propose(project))
    assert [w["kind"] for w in mapped] == ["word", "filler", "word"]


# Layer 3 and the command.


def review_project(tmp_path: Path) -> Project:
    take = "So this product helps writers finish drafts faster. Our customers include schools and hospitals across three countries."
    again = "So this product helps writers finish drafts faster with better notes."
    words = timed(take, step=0.2) + timed(again, start=8.0, step=0.2)
    return voiced_project(tmp_path, words)


def test_cli_writes_edl_report_and_review(tmp_path, capsys):
    project = review_project(tmp_path)
    assert main(["cut", "--project", str(project.root)]) == 0
    assert project.edl_path.exists()
    report = (project.work / "cut-report.md").read_text()
    review = (project.work / "cut-review.md").read_text()
    edl = project.edl()
    for item in edl.removed:
        assert removal_id(item) in report
    waiting = [r for r in edl.removed if r.decision == "review"]
    assert waiting and all(removal_id(r) in review for r in waiting)
    assert "decisions.json" in review
    assert "wait for a ruling" in capsys.readouterr().out


def test_review_pack_is_a_small_fraction_of_the_word_json(tmp_path):
    project = review_project(tmp_path)
    assert main(["cut", "--project", str(project.root)]) == 0
    transcript = (project.work / "cut-review.md").read_text().split("## Transcript")[1]
    raw = (project.words_dir / "a.json").read_text()
    assert len(transcript) * 4 < len(raw)


def test_apply_review_cut_and_keep(tmp_path):
    project = review_project(tmp_path)
    assert main(["cut", "--project", str(project.root)]) == 0
    item = next(r for r in project.edl().removed if r.kind == "retake" and r.decision == "review")
    rulings = tmp_path / "decisions.json"
    rulings.write_text(json.dumps({removal_id(item): {"decision": "cut", "reason": "second read is better"}}))
    assert main(["cut", "--project", str(project.root), "--apply-review", str(rulings)]) == 0
    edl = project.edl()
    ruled = next(r for r in edl.removed if removal_id(r) == removal_id(item))
    assert ruled.decision == "auto" and "second read is better" in ruled.evidence
    assert "hospitals" not in " ".join(expected_words(project, edl))
    rulings.write_text(json.dumps({removal_id(item): "keep"}))
    assert main(["cut", "--project", str(project.root), "--apply-review", str(rulings)]) == 0
    edl = project.edl()
    assert next(r for r in edl.removed if removal_id(r) == removal_id(item)).decision == "keep"
    assert "hospitals" in " ".join(expected_words(project, edl))


def test_an_auto_cut_can_be_overruled(tmp_path):
    project = silent_project(tmp_path, timed("well um that works"))
    project.save_edl(propose(project))
    item = next(r for r in project.edl().removed if r.kind == "filler")
    rulings = tmp_path / "d.json"
    rulings.write_text(json.dumps({removal_id(item): "keep"}))
    assert main(["cut", "--project", str(project.root), "--apply-review", str(rulings)]) == 0
    assert "um" in expected_words(project, project.edl())


def test_rulings_for_an_older_cut_fail_loudly(tmp_path, capsys):
    project = silent_project(tmp_path, timed("well um that works"))
    project.save_edl(propose(project))
    rulings = tmp_path / "d.json"
    rulings.write_text(json.dumps({"zzzzzz": "cut"}))
    assert main(["cut", "--project", str(project.root), "--apply-review", str(rulings)]) == 1
    assert "No such review items" in capsys.readouterr().err


@pytest.mark.parametrize("content", ["not json", "[]", '{"a": "maybe"}'])
def test_bad_rulings_are_explained(tmp_path, capsys, content):
    project = silent_project(tmp_path, timed("well um that works"))
    project.save_edl(propose(project))
    rulings = tmp_path / "d.json"
    rulings.write_text(content)
    assert main(["cut", "--project", str(project.root), "--apply-review", str(rulings)]) == 1
    assert "Fix:" in capsys.readouterr().err


def test_missing_rulings_file_is_explained(tmp_path, capsys):
    project = silent_project(tmp_path, timed("well um that works"))
    project.save_edl(propose(project))
    assert main(["cut", "--project", str(project.root), "--apply-review", str(tmp_path / "none.json")]) == 1
    assert "No rulings file" in capsys.readouterr().err


def test_cut_without_a_transcript_says_what_to_run(tmp_path, capsys):
    project = Project.create(tmp_path / "video")
    project.save_clips([add_clip(tmp_path, "a", 2.0, None)])
    assert main(["cut", "--project", str(project.root)]) == 1
    assert "altocue transcribe" in capsys.readouterr().err


def test_same_inputs_give_the_same_edl(tmp_path):
    project = review_project(tmp_path)
    assert propose(project).to_dict() == propose(project).to_dict()


def test_edl_round_trips_and_still_validates(tmp_path):
    project = review_project(tmp_path)
    project.save_edl(propose(project))
    assert validate(project, project.edl()) == []


def quiet_envelope():
    from altocue.transcribe import Envelope

    rate = 48000
    loud = (np.sin(np.linspace(0, 3000, rate * 4)) * 0.3).astype(np.float32)
    silence = np.zeros(rate, dtype=np.float32) + 1e-5
    faint = (np.sin(np.linspace(0, 700, rate)) * 0.006).astype(np.float32)
    return Envelope.measure(np.concatenate([loud, silence, faint, silence]), rate)


def test_a_short_faint_line_goes_to_review():
    from altocue.cut.detect import quiet_lines
    from altocue.models import Word

    words = [Word("Main", 0.5, 1.0), Word("speaker", 1.0, 3.5), Word("Perfect.", 5.2, 5.8)]
    found = quiet_lines("a", words, quiet_envelope())
    assert [(c.kind, c.decision, round(c.start, 1)) for c in found] == [("quiet", "review", 5.2)]


def test_steady_speech_raises_no_quiet_line():
    from altocue.cut.detect import quiet_lines
    from altocue.models import Word

    assert quiet_lines("a", [Word("Main", 0.5, 1.0), Word("speaker", 1.0, 3.5)], quiet_envelope()) == []


def test_a_segment_can_be_removed_by_hand_with_a_reason(tmp_path, capsys):
    project = review_project(tmp_path)
    edl = propose(project)
    project.save_edl(edl)
    target = edl.segments[0]
    assert main(["cut", "--project", str(project.root), "--remove", target.id, "--reason", "Off topic"]) == 0
    again = project.edl()
    assert any(r.kind == "manual" and r.evidence == "Off topic" for r in again.removed)
    assert again.duration < edl.duration


def test_removing_an_unknown_segment_is_refused(tmp_path):
    project = review_project(tmp_path)
    project.save_edl(propose(project))
    assert main(["cut", "--project", str(project.root), "--remove", "nope"]) == 1


def test_a_ruled_out_um_stays_out_when_the_edge_snaps():
    from altocue.cut import bounds
    from altocue.models import Removed, Word

    clip = Clip(id="a", path="/a.wav", duration=6.0, fps=30, width=10, height=10)
    words = [Word("part.", 1.0, 1.5), Word("It", 3.0, 3.2)]
    wall = Removed("a", 1.5, 3.0, "silence", "pause with speech the transcript missed: 1.5 s", "auto")
    _, end = bounds([0], words, clip, None, 30, (wall,))
    start, _ = bounds([1], words, clip, None, 30, (wall,))
    assert end <= 1.5 + 0.2 and start >= 3.0 - 0.2
