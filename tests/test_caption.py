import json

import pytest

from altocue import caption
from altocue.caption import PHRASING, build_pages, phrasing, strip_punctuation
from altocue.errors import AltocueError
from altocue.models import Clip, Edl, Segment, Word
from altocue.project import Project


def word(text, start, end, segment="a"):
    return {"text": text, "start": start, "end": end, "segment": segment}


def speech(text, start=0.0, step=0.3, segment="a"):
    """Words said one after another, each taking most of its step."""
    return [word(token, round(start + n * step, 3), round(start + n * step + step * 0.8, 3), segment) for n, token in enumerate(text.split())]


def shown(pages):
    return [" ".join(w["text"] for w in page["words"]) for page in pages]


def test_pages_cap_words_without_orphans():
    pages = build_pages(speech("one two three four five"), max_words=4)
    assert [len(page["words"]) for page in pages] == [3, 2]


def test_no_page_passes_the_most_words_on_its_own():
    pages = build_pages(speech("it goes on and on with no pause or stop of any kind for a very long time"), max_words=3)
    assert max(len(page["words"]) for page in pages) <= 3


def test_pages_never_cross_a_cut():
    pages = build_pages([word("one", 0, 0.2, "a"), word("two", 0.2, 0.4, "b")])
    assert len(pages) == 2


def test_pages_break_at_sentence_end():
    pages = build_pages([word("Done.", 0, 0.2), word("Next", 0.25, 0.4)])
    assert [p["words"][0]["text"] for p in pages] == ["Done", "Next"]


def test_words_said_together_share_a_page():
    pages = build_pages([word("a", 0, 0.1), word("b", 0.2, 0.3), word("c", 0.4, 0.5)])
    assert len(pages) == 1 and len(pages[0]["words"]) == 3


def test_a_slow_speaker_gets_the_same_pages_as_a_fast_one():
    """The old window ran from a page's first word, so three words a second never reached four words a page."""
    line = "nobody ever told me about that"
    assert shown(build_pages(speech(line, step=0.2), max_words=4)) == shown(build_pages(speech(line, step=0.55), max_words=4))
    assert max(len(page["words"]) for page in build_pages(speech(line, step=0.55), max_words=4)) >= 3


def test_a_real_pause_ends_the_page():
    words = [word("before", 0.0, 0.3), word("the", 0.32, 0.5), word("pause", 1.2, 1.5), word("ends", 1.52, 1.8)]
    assert shown(build_pages(words)) == ["before the", "pause ends"]


def test_the_old_combine_ms_is_still_taken_and_now_means_the_gap_between_two_words():
    words = [word("one", 0.0, 0.2), word("two", 0.5, 0.7), word("three", 1.0, 1.2), word("four", 1.5, 1.7)]
    assert len(build_pages(words, combine_ms=700)) == 1  # 1.5 seconds from first word to last, and no gap over 0.3
    assert len(build_pages(words, combine_ms=200)) == 4


def test_page_holds_but_never_overlaps_the_next():
    pages = build_pages([word("one", 0, 0.2), word("two", 1.0, 1.2)], combine_ms=100)
    assert pages[0]["end"] <= pages[1]["start"] and pages[0]["end"] > 0.2


def test_punctuation_only_tokens_vanish():
    assert strip_punctuation('"Hello,"') == "Hello"
    assert build_pages([word("--!", 0, 0.1)]) == []


def test_a_question_keeps_its_mark():
    assert strip_punctuation('"Really?"') == "Really?"
    assert strip_punctuation("Stop!") == "Stop!"
    assert strip_punctuation("Done.") == "Done"
    pages = shown(build_pages(speech("Is this the best way? Yes! It is."), max_words=3))
    assert pages == ["Is this", "the best way?", "Yes!", "It is"]


def test_a_page_never_runs_into_the_next_one_across_a_cut():
    pages = build_pages([word("one", 0, 0.5, "a"), word("two", 0.5, 0.8, "b")])
    assert pages[0]["end"] <= pages[1]["start"]


def test_a_comma_is_a_good_place_to_break():
    assert shown(build_pages(speech("one two, three four"), max_words=4)) == ["one two", "three four"]


def test_a_comma_does_not_strand_one_word():
    """A comma used to end the page whatever came of it, and "sound," sat alone for a fifth of a second."""
    pages = build_pages([word("sound,", 0.1, 0.3), word("it", 0.32, 0.4), word("fixes", 0.42, 0.6)])
    assert shown(pages) == ["sound it fixes"]


LINES = (
    "the best way to learn a language is to speak it every day with people who know it",
    "we went to the shop on the corner for a loaf of bread and a pint of milk",
    "she said that the answer was in the back of the book by the door",
    "you can see it from the top of the hill at the end of the road",
)


@pytest.mark.parametrize("limits", [{"max_words": 3, "max_chars": 22}, {"max_words": 4, "max_chars": 32}])
@pytest.mark.parametrize("line", LINES)
def test_no_page_ends_on_an_article_or_a_preposition(line, limits):
    pages = build_pages(speech(line), **limits)
    stranded = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "from", "by"}
    assert [page["words"][-1]["text"] for page in pages[:-1] if page["words"][-1]["text"].lower() in stranded] == []
    assert len(pages) > 2


@pytest.mark.parametrize("limits", [{"max_words": 3, "max_chars": 22}, {"max_words": 4, "max_chars": 32}])
def test_a_number_stays_with_its_unit(limits):
    for line in ("sales grew by 20 percent in the first 6 months of trading", "it took us about 3 hours and cost nearly 40 dollars a head"):
        pages = shown(build_pages(speech(line), **limits))
        for pair in ("20 percent", "6 months", "3 hours", "40 dollars"):
            if pair.split()[0] in line.split():
                assert any(pair in page for page in pages), (pair, pages)


@pytest.mark.parametrize("limits", [{"max_words": 3, "max_chars": 22}, {"max_words": 4, "max_chars": 32}])
def test_a_name_stays_whole(limits):
    pages = shown(build_pages(speech("so then we flew to New York and I met Dr. Smith at the airport"), **limits))
    assert any("New York" in page for page in pages), pages
    assert any("Dr Smith" in page for page in pages), pages


def test_the_full_stop_of_a_title_ends_no_sentence():
    assert caption.ends_sentence("Done.") and caption.ends_sentence('Done."') and caption.ends_sentence("Why?")
    assert not caption.ends_sentence("Dr.", phrasing("en")) and not caption.ends_sentence("Done,")


def test_one_long_word_does_not_drag_short_ones_onto_its_page():
    pages = shown(build_pages(speech("internationalisation infrastructure is a to I"), max_words=3, max_chars=22))
    assert pages[0] == "internationalisation"
    assert all(len(page) <= 22 for page in pages)


def test_an_unknown_language_still_pages_sensibly():
    """No word list, so punctuation, pauses, length and balance do the work."""
    assert phrasing("xx") == caption.NO_PHRASING and phrasing("") == caption.NO_PHRASING and phrasing("en-GB") == PHRASING["en"]
    line = "uno dos tres cuatro cinco seis siete, ocho nueve diez once. Doce trece catorce quince dieciséis"
    pages = build_pages(speech(line), max_words=3, max_chars=22, language="xx")
    texts = shown(pages)
    assert " ".join(texts).replace(" ", "") == line.replace(",", "").replace(".", "").replace(" ", "")  # every word, in order
    assert all(1 < len(page["words"]) <= 3 for page in pages) and all(len(text) <= 22 for text in texts)
    assert any(text.endswith("siete") for text in texts) and any(text.endswith("once") for text in texts)
    assert "short_by" not in {key for page in pages for key in page}


def test_a_language_is_one_entry_in_a_table():
    assert set(PHRASING) >= {"en"}
    assert {"the", "of", "my", "to"} <= PHRASING["en"].binds and {"and", "is", "we"} <= PHRASING["en"].leans


# Time on screen


def test_a_short_page_joins_its_neighbour_in_the_same_run():
    """Four quick words on a three word page: two pages of two would each flash by in a fifth of a second."""
    quick = speech("that is so true", step=0.1)
    assert [len(page["words"]) for page in build_pages(quick, max_words=3, min_show=0)] == [2, 2]
    joined = build_pages(quick, max_words=3)
    assert shown(joined) == ["that is so true"]
    assert joined[0]["end"] - joined[0]["start"] >= 0.4


def test_a_one_word_page_before_a_cut_joins_the_page_before_it():
    words = [word("go", 0.0, 0.15), word("to", 0.17, 0.3), word("the", 0.32, 0.45), word("store", 0.47, 0.7), word("today.", 0.72, 0.9)]
    ends = {"a": 0.92, "b": 3.0}
    apart = build_pages(words, max_words=4, min_show=0, ends=ends)
    assert shown(apart) == ["go to the store", "today"] and apart[1]["end"] - apart[1]["start"] < 0.4
    assert shown(build_pages(words, max_words=4, ends=ends)) == ["go to the store today"]


def test_a_joined_page_never_passes_the_most_words_by_more_than_one_or_the_most_characters():
    quick = speech("one two three four five six", step=0.1)
    assert max(len(page["words"]) for page in build_pages(quick, max_words=3)) <= 4
    assert max(len(text) for text in shown(build_pages(quick, max_words=3, max_chars=14))) <= 14


def test_a_short_page_holds_into_the_silence_after_it():
    """"Absolutely." needs half a second at twenty characters a second, and the usual hold gives it less."""
    words = [word("Absolutely.", 1.0, 1.1), word("Next", 2.0, 2.3), word("point", 2.32, 2.6)]
    held = build_pages(words, ends={"a": 5.0})
    assert held[0]["start"] == 1.0 and held[0]["end"] == pytest.approx(1.5)
    assert "short_by" not in held[0]


def test_a_hold_never_runs_into_the_next_page_or_across_a_cut():
    words = [word("Absolutely.", 1.0, 1.1), word("Next", 1.3, 1.6, "b"), word("point", 1.62, 1.9, "b")]
    pages = build_pages(words, ends={"a": 1.2, "b": 5.0})
    assert pages[0]["end"] <= 1.2 and pages[0]["end"] <= pages[1]["start"]


def test_a_page_against_a_cut_starts_a_little_early_when_that_gives_it_its_time():
    words = [word("Okay.", 0.1, 0.4), word("Right.", 1.0, 1.2)]
    pages = build_pages(words, ends={"a": 1.3})
    assert pages[1]["start"] == pytest.approx(0.9) and pages[1]["end"] == 1.3
    assert pages[0]["end"] <= pages[1]["start"]
    assert pages[1]["words"][0]["start"] == 1.0  # the word keeps its own time. Only the page comes up sooner
    assert "short_by" not in pages[1]


def test_an_early_start_never_crosses_a_cut_or_covers_a_spoken_word():
    across = build_pages([word("Okay.", 0.1, 0.95, "a"), word("Right.", 1.0, 1.2, "b")], ends={"a": 0.98, "b": 1.3})
    assert across[1]["start"] >= 0.98
    spoken = build_pages([word("Okay.", 0.1, 0.97), word("Right.", 1.0, 1.2)], ends={"a": 1.3})
    assert spoken[1]["start"] >= 0.97
    unknown = build_pages([word("Okay.", 0.1, 0.4, "a"), word("Right.", 1.0, 1.2, "b")])  # no cut times, so nothing says where the cut is
    assert unknown[1]["start"] == 1.0


def test_a_page_with_no_room_says_how_much_time_it_is_missing():
    pages = build_pages([word("Right.", 0.05, 0.17)], ends={"a": 0.2})
    assert pages[0]["start"] == 0.0 and pages[0]["end"] == 0.2
    assert pages[0]["short_by"] == pytest.approx(0.2)


def test_no_minimum_can_be_asked_for():
    pages = build_pages([word("Right.", 0.05, 0.17)], ends={"a": 0.2}, min_show=0)
    assert pages[0]["start"] == 0.05 and "short_by" not in pages[0]


# A project


def jump_cut(tmp_path, words, segments, fps=50, aspect="9:16"):
    project = Project.create(tmp_path / "video", aspect=aspect)
    project.save_clips([Clip(id="a", path=str(tmp_path / "gone.mp4"), duration=4.0, fps=fps, width=1080, height=1920, has_audio=False)])
    project.save_words("a", words, "test")
    project.save_edl(Edl(fps=fps, segments=[Segment(id=f"s{n:03d}", clip="a", start=a, end=b) for n, (a, b) in enumerate(segments, 1)]))
    return project


def said(text, start=0.3, step=0.3):
    return [Word(token, round(start + n * step, 3), round(start + n * step + step * 0.8, 3)) for n, token in enumerate(text.split())]


def test_the_hold_stops_at_the_cut(tmp_path):
    words = [Word("one", 0.3, 0.6), Word("two", 1.5, 1.8)]
    pages = caption.build(jump_cut(tmp_path, words, [(0.0, 0.62), (1.2, 2.0)]))
    assert pages[0]["end"] == 0.62


def test_captions_carry_the_cut_they_were_made_for(tmp_path):
    project = jump_cut(tmp_path, [Word("one", 0.3, 0.6)], [(0.0, 0.62)])
    caption.build(project)
    assert json.loads((project.work / "captions.json").read_text())["cut"] == project.cut_mark()


def test_the_last_page_stops_with_the_footage(tmp_path):
    pages = caption.build(jump_cut(tmp_path, [Word("one", 0.3, 0.6)], [(0.0, 0.62)]))
    assert pages[0]["end"] == 0.62


def test_a_page_still_holds_when_the_cut_is_far_away(tmp_path):
    words = [Word("one", 0.3, 0.6), Word("two", 1.5, 1.8)]
    pages = caption.build(jump_cut(tmp_path, words, [(0.0, 1.0), (1.2, 2.0)]))
    assert pages[0]["end"] == 0.88


def test_a_page_a_cut_leaves_no_time_for_is_written_down(tmp_path, capsys):
    """Nothing can be merged, held or pulled early here, so a later check must be able to find the page."""
    project = jump_cut(tmp_path, [Word("Right.", 0.5, 0.62), *said("And that is the whole of it.", start=1.2)], [(0.45, 0.65), (1.1, 3.6)])
    pages = caption.build(project)
    made = json.loads((project.work / "captions.json").read_text())
    assert made["short_pages"] == [{"page": 0, "start": 0.0, "shown": 0.2, "needed": 0.4, "short_by": 0.2, "text": "Right"}]
    assert pages[0]["short_by"] == 0.2 and made["pages"][0]["short_by"] == 0.2
    assert all("short_by" not in page for page in pages[1:])
    assert made["settings"]["min_show"] == 0.4
    assert caption.run(type("Args", (), {"project": str(project.root)})()) == 0
    said_so = capsys.readouterr().out
    assert "less time than they take to read: 1, of which 1 for under 0.4 seconds" in said_so and "short_pages in work/captions.json" in said_so


def test_nothing_is_written_down_when_every_page_has_its_time(tmp_path):
    project = jump_cut(tmp_path, said("And that is the whole of it."), [(0.0, 3.0)])
    caption.build(project)
    assert json.loads((project.work / "captions.json").read_text())["short_pages"] == []


def test_the_shape_of_the_canvas_sets_the_page(tmp_path):
    tall = caption.settings(jump_cut(tmp_path / "tall", [], []), {})
    wide = caption.settings(jump_cut(tmp_path / "wide", [], [], aspect="16:9"), {})
    square = caption.settings(jump_cut(tmp_path / "square", [], [], aspect="1:1"), {})
    assert (tall["max_words"], tall["max_chars"]) == (3, 22)
    assert (wide["max_words"], wide["max_chars"]) == (4, 32)
    assert (square["max_words"], square["max_chars"]) == (4, 22)
    assert tall["min_show"] == 0.4 and tall["max_gap_ms"] == 600 and tall["language"] == "en"


def test_the_project_outranks_the_brand_and_the_brand_outranks_the_shape(tmp_path):
    project = jump_cut(tmp_path, [], [])
    brand = {"max_words": 2, "combine_ms": 450}
    assert caption.settings(project, brand)["max_words"] == 2 and caption.settings(project, brand)["max_gap_ms"] == 450
    project.config["caption"] = {"max_words": 5, "max_chars": 40, "min_show": 0.6}
    how = caption.settings(project, brand)
    assert (how["max_words"], how["max_chars"], how["min_show"]) == (5, 40, 0.6)


@pytest.mark.parametrize("table", [{"max_words": -1}, {"max_chars": "many"}, {"max_words": 2.5}, {"min_show": -0.1}, {"min_show": True}])
def test_a_setting_that_makes_no_sense_is_refused(tmp_path, table):
    project = jump_cut(tmp_path, [], [])
    project.config["caption"] = table
    with pytest.raises(AltocueError) as refused:
        caption.settings(project, {})
    assert refused.value.fix


def test_a_new_project_carries_the_caption_settings(tmp_path):
    project = jump_cut(tmp_path, [], [])
    assert project.config["caption"] == {"max_words": 0, "max_chars": 0, "min_show": 0.4}


def test_a_built_page_says_how_many_words_go_on_each_line(tmp_path):
    project = jump_cut(tmp_path, said("the international committee of experts met again in Geneva yesterday"), [(0.0, 3.6)])
    pages = caption.build(project)
    assert all(sum(page["lines"]) == len(page["words"]) and len(page["lines"]) <= 2 for page in pages)
    assert json.loads((project.work / "captions.json").read_text())["pages"] == pages
