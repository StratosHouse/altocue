"""Subtitle cues: same word timing as the captions, readable, and never across a cut."""

from __future__ import annotations

from altocue import subtitles
from altocue.subtitles import Cue, build_cues, reading_report, to_srt, to_vtt, wrap


def word(text, start, end, segment="s001"):
    return {"text": text, "start": start, "end": end, "segment": segment}


def speech(text, start=0.0, step=0.3, segment="s001"):
    return [word(token, round(start + i * step, 3), round(start + (i + 1) * step, 3), segment) for i, token in enumerate(text.split())]


def test_a_cue_never_crosses_a_cut():
    words = speech("this runs right up to the cut", segment="s001") + speech("and carries on after it", start=2.1, segment="s002")
    cues = build_cues(words, {"s001": 2.1, "s002": 3.6})
    assert {cue.segment for cue in cues} == {"s001", "s002"}
    first = [cue for cue in cues if cue.segment == "s001"]
    assert all(cue.end <= 2.1 for cue in first)


def test_the_hold_stops_at_the_cut_even_with_a_gap_after_it():
    words = [word("Done.", 0.3, 0.6, "s001"), word("Next", 1.5, 1.8, "s002")]
    cues = build_cues(words, {"s001": 0.62, "s002": 2.0})
    assert cues[0].end == 0.62


def test_lines_stay_short_and_cues_stay_two_lines():
    text = "a fairly long sentence that keeps going on and on without any kind of pause at all until it finally stops somewhere near the far end of the shot"
    cues = build_cues(speech(text), {"s001": 60.0})
    assert len(cues) > 1
    for cue in cues:
        assert len(cue.lines) <= subtitles.LINES
        assert all(len(line) <= subtitles.LINE for line in cue.lines)


def test_a_cue_never_runs_past_seven_seconds():
    words = [word(f"w{i}", i * 1.5, i * 1.5 + 0.2) for i in range(12)]
    cues = build_cues(words, {"s001": 30.0})
    assert all(cue.end - cue.start <= subtitles.LONGEST for cue in cues)


def test_a_sentence_end_starts_a_new_cue():
    cues = build_cues(speech("It works every time. Now try it yourself."), {"s001": 10.0})
    assert [" ".join(cue.lines) for cue in cues] == ["It works every time.", "Now try it yourself."]


def test_a_long_pause_starts_a_new_cue():
    words = [word("before", 0.0, 0.3), word("the", 0.3, 0.5), word("pause", 2.0, 2.4), word("ends", 2.4, 2.7)]
    assert len(build_cues(words, {"s001": 5.0})) == 2


def test_a_short_cue_stays_up_long_enough_to_read_when_there_is_room():
    cues = build_cues([word("Yes", 0.0, 0.2)], {"s001": 5.0})
    assert round(cues[0].end, 3) >= round(subtitles.SHORTEST, 3)


def test_cues_never_overlap():
    cues = build_cues(speech("One. Two. Three. Four. Five. Six.", step=0.4), {"s001": 10.0})
    for cue, following in zip(cues, cues[1:], strict=False):
        assert cue.end <= following.start


def test_a_fast_one_word_sentence_joins_the_next_one():
    words = [word("Right.", 0.0, 0.2), word("So", 0.2, 0.4), word("here", 0.4, 0.6), word("we", 0.6, 0.8), word("go", 0.8, 1.0)]
    cues = build_cues(words, {"s001": 5.0})
    assert [" ".join(cue.lines) for cue in cues] == ["Right. So here we go"]


def test_tokens_with_no_letters_do_not_become_cues():
    assert build_cues([word("--", 0.0, 0.2)], {"s001": 1.0}) == []


def test_wrap_breaks_after_punctuation_when_it_can():
    assert wrap("When the sound drops out, nobody stays to the end of it") == ["When the sound drops out,", "nobody stays to the end of it"]


def test_wrap_leaves_short_text_alone():
    assert wrap("Short enough") == ["Short enough"]


def test_wrap_never_strands_an_article_at_the_end_of_a_line():
    lines = wrap("We spent most of last year quietly building the machine that makes this")
    assert lines[0].split()[-1].lower() not in {"a", "an", "the"}
    assert all(len(line) <= subtitles.LINE for line in lines)


def test_wrap_keeps_a_pronoun_with_its_verb():
    assert wrap("It reads every word and it cuts the pauses, the ums, and")[0].split()[-1] != "it"


def test_srt_is_numbered_with_comma_milliseconds():
    assert to_srt([Cue(1.5, 3.25, ["Hello there"], "s001"), Cue(3661.0, 3662.0, ["One", "Two"], "s002")]) == (
        "1\n00:00:01,500 --> 00:00:03,250\nHello there\n\n2\n01:01:01,000 --> 01:01:02,000\nOne\nTwo\n"
    )


def test_vtt_has_its_header_and_point_milliseconds():
    assert to_vtt([Cue(1.5, 3.25, ["Hello there"], "s001")]) == "WEBVTT\n\n00:00:01.500 --> 00:00:03.250\nHello there\n"


def test_vtt_escapes_what_would_break_a_cue():
    assert "Fish &amp; chips &lt;3" in to_vtt([Cue(0, 1, ["Fish & chips <3"], "s001")])


def test_a_cue_at_the_limit_after_rounding_is_not_over_it():
    assert reading_report([Cue(0.0, 2.797, ["x" * 56], "s001")])["over_limit"] == 0


def test_the_report_says_how_fast_the_fastest_cue_reads():
    report = reading_report([Cue(0.0, 1.0, ["x" * 30], "s001"), Cue(2.0, 4.0, ["y" * 10], "s001")])
    assert report["cues"] == 2 and report["fastest_cps"] == 30.0 and report["over_limit"] == 1 and report["longest_line"] == 30
