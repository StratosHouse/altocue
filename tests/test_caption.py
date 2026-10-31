from altocue.caption import balanced, build_pages, strip_punctuation


def word(text, start, end, segment="a"):
    return {"text": text, "start": start, "end": end, "segment": segment}


def test_pages_cap_words_without_orphans():
    run = [{"text": str(i)} for i in range(5)]
    assert [len(c) for c in balanced(run, 4)] == [3, 2]


def test_pages_never_cross_a_cut():
    pages = build_pages([word("one", 0, 0.2, "a"), word("two", 0.2, 0.4, "b")])
    assert len(pages) == 2


def test_pages_break_at_sentence_end():
    pages = build_pages([word("Done.", 0, 0.2), word("Next", 0.25, 0.4)])
    assert [p["words"][0]["text"] for p in pages] == ["Done", "Next"]


def test_words_within_the_window_share_a_page():
    pages = build_pages([word("a", 0, 0.1), word("b", 0.2, 0.3), word("c", 0.4, 0.5)], combine_ms=700)
    assert len(pages) == 1 and len(pages[0]["words"]) == 3


def test_page_holds_but_never_overlaps_the_next():
    pages = build_pages([word("one", 0, 0.2), word("two", 1.0, 1.2)], combine_ms=100)
    assert pages[0]["end"] <= pages[1]["start"] and pages[0]["end"] > 0.2


def test_punctuation_only_tokens_vanish():
    assert strip_punctuation('"Hello,"') == "Hello"
    assert build_pages([word("--!", 0, 0.1)]) == []


def test_a_page_never_runs_into_the_next_one_across_a_cut():
    pages = build_pages([word("one", 0, 0.5, "a"), word("two", 0.5, 0.8, "b")])
    assert pages[0]["end"] <= pages[1]["start"]


def test_a_comma_ends_the_page():
    pages = build_pages([word("sound,", 0.1, 0.3), word("it", 0.32, 0.4), word("fixes", 0.42, 0.6)], combine_ms=700)
    assert [[w["text"] for w in p["words"]] for p in pages] == [["sound"], ["it", "fixes"]]
