"""Word-timed caption pages. Pages never cross a cut and only show words really spoken.

A page breaks where a phrase does. A cut, a change of speaker, the end of a sentence and a real pause always end one.
Between those the breaks are the cheapest set under a small cost: not after a word that leans on the next, not inside
a name, not between a number and its unit, and not into a page too long to read or a single stray word.
Then every page is given time to be seen, and the ones a cut leaves no time for are written down.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any

from . import brand as brands
from . import cut, platforms
from .errors import AltocueError
from .project import Project, write_json

LEADING = "\"'“‘("
TRAILING = ".,!?;:\"'”’)"
KEPT = "?!"  # a question reads differently without its mark, so these two stay on screen
SENTENCE_END = ".!?"
CLAUSE_END = ",;:"
HOLD_AFTER = 0.28
MAX_GAP_MS = 600  # a silence longer than this between two neighbouring words ends the page
MIN_SHOW = 0.4  # seconds. A house floor for word-by-word captions. Subtitles keep their own, longer one in subtitles.py
READING = 20.0  # characters a second, the same reading speed subtitles.py holds a cue to
PULL_EARLY = 0.12  # the most a page may start before its first word, into silence
WORDS_TALL, WORDS_WIDE = 3, 4
CHARS_NARROW, CHARS_WIDE = 22, 32
SPEAKER_COLOURS = ("#f3f7fa", "#ffd08a", "#a8d8ff", "#ffb3c7", "#d2c2ff")
SPEAKER_DISPLAYS = {"": (), "colour": ("colour",), "name": ("name",), "both": ("colour", "name")}

# What a break costs. A higher total is a worse set of pages.
BIND = 8.0  # after a word that cannot stand without the next, between a number and its unit, or inside a name
LEAN = 4.0  # after a word that reads better with the next, when there is nowhere kinder to break
WELCOME = 3.0  # taken off after a comma or before a conjunction
ONE_WORD = 6.0  # a page of one word that does not end a sentence
PER_CHAR_OVER = 10.0  # each character past the limit
UNEVEN = 1.0  # each word of difference between neighbouring pages
PAGE = 1.0  # each page, so the same words never take more pages than they need
NUMBER = re.compile(r"[$£€¥]?\d")


@dataclass(frozen=True)
class Phrasing:
    """What one language needs to keep a phrase whole. Every list is lower case, with a straight apostrophe."""

    binds: frozenset[str] = frozenset()  # words that cannot end a page: articles, prepositions, possessives
    endings: tuple[str, ...] = ()  # word endings that bind the same way, such as a possessive or a contraction
    leans: frozenset[str] = frozenset()  # words that would sooner not end one: conjunctions, auxiliaries, subject pronouns
    joins: frozenset[str] = frozenset()  # conjunctions. A break before one is welcome
    titles: frozenset[str] = frozenset()  # they stay with the name that follows
    short_forms: frozenset[str] = frozenset()  # written with a full stop that ends no sentence, such as "Dr."
    numbers: frozenset[str] = frozenset()  # numbers written as words
    units: frozenset[str] = frozenset()  # they stay with the number before them


def _set(*groups: str) -> frozenset[str]:
    return frozenset(word for group in groups for word in group.split())


_JOINS_EN = "and or but nor so because if when while although though unless until since whether that which who whose where"

# Keyed by language code, as `[transcribe] language` writes it. Add a language by adding an entry.
# A language with no entry gets an empty Phrasing, and its pages still break on punctuation, pauses, length and balance.
PHRASING: dict[str, Phrasing] = {
    "en": Phrasing(
        binds=_set(
            "a an the",
            "of in on at to for with from by into onto about over under after before between through during",
            "without within against among across toward towards upon per via than as",
            "my your his her its our their",
        ),
        endings=("'s", "'re", "'ve", "'ll", "'d", "'m", "n't"),
        leans=_set(
            _JOINS_EN,
            "this these those every each",
            "i we they he she",
            "is are was were am be been being have has had do does did will would shall should can could may might must",
            "not very",
        ),
        joins=_set(_JOINS_EN),
        titles=_set("mr mrs ms miss mx dr prof professor sir dame st"),
        short_forms=_set("mr mrs ms mx dr prof st"),
        numbers=_set(
            "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen",
            "twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion",
        ),
        units=_set(
            "percent per % hundred thousand million billion trillion k m bn",
            "dollars dollar pounds pound euros euro cents pence bucks grand",
            "seconds second minutes minute hours hour days day weeks week months month years year times x am pm a.m p.m",
            "mm cm km metres metre meters meter kilometres kilometers miles mile feet foot inches inch yards",
            "mg g kg grams kilos kilograms lb lbs oz ounces litres liters ml gallons",
            "degrees fps mph kph kb mb gb tb px pixels frames points people",
        ),
    ),
}
NO_PHRASING = Phrasing()


def phrasing(language: str | None) -> Phrasing:
    """The lists for a language code such as `en` or `en-GB`. An unknown one gets the empty set."""
    return PHRASING.get(re.split(r"[-_]", str(language or "").strip().lower())[0], NO_PHRASING)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")


def strip_punctuation(text: str) -> str:
    """A word as a caption shows it: no quotes, commas or full stops. `?` and `!` stay. A token with no letter or digit is nothing."""
    bare = text.strip().lstrip(LEADING)
    core = bare.rstrip(TRAILING)
    if not any(ch.isalnum() for ch in core):
        return ""
    return core + "".join(ch for ch in bare[len(core) :] if ch in KEPT)


def _plain(text: str) -> str:
    return text.strip().lstrip(LEADING).rstrip(TRAILING).lower().replace("’", "'")


def _capital(text: str) -> bool:
    first = next((ch for ch in text if ch.isalnum()), "")
    return first.isupper()


def _last_mark(text: str) -> str:
    return text.rstrip().rstrip("\"'”’)")[-1:]


def _numeric(word: str, rules: Phrasing) -> bool:
    return bool(NUMBER.match(word)) or word in rules.numbers


def ends_sentence(text: str, rules: Phrasing = NO_PHRASING) -> bool:
    """A full stop, `?` or `!` ends a sentence, and the full stop of a short form such as "Dr." does not."""
    mark = _last_mark(text)
    return bool(mark) and mark in SENTENCE_END and not (mark == "." and _plain(text) in rules.short_forms)


def join_cost(left: str, right: str, rules: Phrasing = NO_PHRASING) -> float:
    """What a break between two neighbouring words costs. More is worse, and under nothing is a good place for one."""
    if _last_mark(left) and _last_mark(left) in CLAUSE_END:
        return -WELCOME
    before, after = _plain(left), _plain(right)
    cost = 0.0
    counted = _numeric(before, rules) and (after in rules.units or _numeric(after, rules))
    if before in rules.binds or any(before.endswith(ending) for ending in rules.endings) or counted or before in rules.titles:
        cost += BIND
    elif before in rules.leans:
        cost += LEAN
    if _capital(left) and _capital(right):
        cost += BIND  # a run of capitals is a name
    if not cost and after in rules.joins:
        cost -= WELCOME
    return cost


def joins(texts: list[str], language: str | None = "en") -> list[float]:
    """The cost of a break after each word but the last, for whoever sets these words on lines."""
    rules = phrasing(language)
    return [join_cost(left, right, rules) for left, right in zip(texts, texts[1:], strict=False)]


def _chars(words: list[dict[str, Any]]) -> int:
    return len(" ".join(w["shown"] for w in words))


def paged(run: list[dict[str, Any]], max_words: int, max_chars: int, rules: Phrasing) -> list[list[dict[str, Any]]]:
    """Break one run of speech into pages at the cheapest places, by dynamic programming over where the last page starts."""
    count = len(run)
    most = max(int(max_words), 1)
    breaks = [join_cost(left["text"], right["text"], rules) for left, right in zip(run, run[1:], strict=False)]
    # best[(end, size)] is the cheapest way to page the first `end` words with a last page of `size`, and the size before it
    best: dict[tuple[int, int], tuple[float, int]] = {}
    for end in range(1, count + 1):
        for size in range(1, min(most, end) + 1):
            begin = end - size
            own = PAGE
            if size == 1 and not run[begin].get("stop") and not ends_sentence(run[begin]["text"], rules):
                own += ONE_WORD
            if max_chars > 0:
                own += PER_CHAR_OVER * max(_chars(run[begin:end]) - max_chars, 0)
            if begin == 0:
                best[(end, size)] = (own, 0)
                continue
            before = [(best[(begin, earlier)][0] + breaks[begin - 1] + UNEVEN * abs(earlier - size), earlier) for earlier in range(1, min(most, begin) + 1)]
            cost, earlier = min(before)
            best[(end, size)] = (cost + own, earlier)
    size = min(range(1, min(most, count) + 1), key=lambda last: (best[(count, last)][0], last))
    pages, end = [], count
    while end > 0:
        pages.append(run[end - size : end])
        end, size = end - size, best[(end, size)][1]
    return pages[::-1]


def _runs(words: list[dict[str, Any]], max_gap: float, rules: Phrasing) -> list[list[dict[str, Any]]]:
    """Speech between two hard breaks: a cut, a change of speaker, the end of a sentence, or a real pause."""
    runs: list[list[dict[str, Any]]] = []
    for word in words:
        shown = strip_punctuation(word["text"])
        last = runs[-1][-1] if runs else None
        if not shown:
            if last is not None and last["segment"] == word["segment"] and any(mark in word["text"] for mark in SENTENCE_END):
                last["stop"] = True  # a full stop the transcriber wrote as a word of its own
            continue
        said = {**word, "shown": shown}
        carries_on = (
            last is not None
            and last["segment"] == word["segment"]
            and last.get("speaker", "") == word.get("speaker", "")
            and word["start"] - last["end"] <= max_gap
            and not last.get("stop")
            and not ends_sentence(last["text"], rules)
        )
        if carries_on:
            runs[-1].append(said)
        else:
            runs.append([said])
    return runs


def _time(pages: list[dict[str, Any]], ends: dict[str, float]) -> None:
    """A page runs from its first word to a short hold past its last, never into the next page and never across a cut."""
    for page in pages:
        page["start"], page["end"] = round(page["said"][0]["start"], 3), round(page["said"][-1]["end"], 3)
    for page, following in zip(pages, pages[1:], strict=False):
        page["end"] = round(min(page["end"] + HOLD_AFTER, following["start"]), 3)
    if pages:
        pages[-1]["end"] = round(pages[-1]["end"] + HOLD_AFTER, 3)
    for page in pages:
        page["end"] = min(page["end"], ends.get(page["segment"], page["end"]))


def _floor(page: dict[str, Any], min_show: float) -> float:
    return max(min_show, _chars(page["said"]) / READING)


def _short(page: dict[str, Any], min_show: float) -> float:
    """Seconds this page falls short of its time on screen. Under a millisecond is rounding, not a short page."""
    missing = _floor(page, min_show) - (page["end"] - page["start"])
    return missing if missing > 0.001 else 0.0


def _merge_short(pages: list[dict[str, Any]], min_show: float, max_words: int, max_chars: int, rules: Phrasing) -> bool:
    """Join the first short page to a neighbour in its own run, where the two still fit. True when one was joined."""
    for index, page in enumerate(pages):
        if not _short(page, min_show):
            continue
        choices = []
        for other in (index - 1, index + 1):
            if not 0 <= other < len(pages) or pages[other]["run"] != page["run"]:
                continue
            first, second = sorted((index, other))
            said = [*pages[first]["said"], *pages[second]["said"]]
            if len(said) <= max_words + 1 and (max_chars <= 0 or _chars(said) <= max_chars):
                # the costlier break is the one most worth closing, and a tie goes to the page before
                choices.append((-join_cost(pages[first]["said"][-1]["text"], pages[second]["said"][0]["text"], rules), other, first, second, said))
        if choices:
            _, _, first, second, said = min(choices, key=lambda choice: choice[:2])
            pages[first : second + 1] = [{**pages[first], "said": said}]
            return True
    return False


def _hold(pages: list[dict[str, Any]], min_show: float, ends: dict[str, float]) -> None:
    """Give a short page its time: later into the silence after it, then up to `PULL_EARLY` sooner into the silence before."""
    starts = dict(zip(ends, [0.0, *ends.values()], strict=False))
    for index, page in enumerate(pages):
        if not _short(page, min_show):
            continue
        floor = _floor(page, min_show)
        after = pages[index + 1]["start"] if index + 1 < len(pages) else float("inf")
        page["end"] = round(max(page["end"], min(page["start"] + floor, after, ends.get(page["segment"], float("inf")))), 3)
        missing = _short(page, min_show)
        if not missing:
            continue
        before = pages[index - 1] if index else None
        if page["segment"] in starts:
            earliest = starts[page["segment"]]
        elif before is None or before["segment"] == page["segment"]:
            earliest = 0.0
        else:
            continue  # the cut before this page is somewhere in the gap, and nothing says where
        if before is not None:
            kept = min(before["end"], before["start"] + _floor(before, min_show))  # never take the page before under its own time
            earliest = max(earliest, before["said"][-1]["end"], kept)
        start = round(max(page["start"] - missing, page["start"] - PULL_EARLY, earliest), 3)
        if start < page["start"]:
            page["start"] = start
            if before is not None and before["end"] > start:
                before["end"] = start


def build_pages(
    words: list[dict[str, Any]],
    *,
    max_words: int = WORDS_WIDE,
    max_chars: int = 0,
    combine_ms: int = MAX_GAP_MS,
    min_show: float = MIN_SHOW,
    language: str | None = "en",
    ends: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Group mapped words into pages. Each word needs `text`, `start`, `end` and `segment`.

    `max_words` is never passed, except by one word when a page too short to see joins its neighbour. `max_chars` is
    the length a page should stay under, and 0 sets no limit. `combine_ms` is the longest silence between two
    neighbouring words on one page. It once meant the time from a page's first word to its last, which broke pages
    by how fast someone spoke. `min_show` is the least time a page stays up, and 0 asks for none.
    `ends` gives each segment's last moment in output seconds, in the order they play. No page holds past it.

    A page that could not be given its time carries `short_by`, the seconds it is missing.
    """
    rules = phrasing(language)
    ends = ends or {}
    pages = [
        {"said": chunk, "run": number, "segment": chunk[0]["segment"]}
        for number, run in enumerate(_runs(words, combine_ms / 1000, rules))
        for chunk in paged(run, max_words, max_chars, rules)
    ]
    _time(pages, ends)
    if min_show > 0:
        while _merge_short(pages, min_show, max_words, max_chars, rules):
            _time(pages, ends)
        _hold(pages, min_show, ends)
    made = []
    for page in pages:
        said = page["said"]
        shown = [{"text": w["shown"], "start": round(w["start"], 3), "end": round(w["end"], 3), **({"tag": w["tag"]} if w.get("tag") else {})} for w in said]
        speaker = {"speaker": said[0]["speaker"]} if said[0].get("speaker") else {}
        missing = round(_short(page, min_show), 3) if min_show > 0 else 0.0
        made.append({"start": page["start"], "end": page["end"], "segment": page["segment"], "words": shown, **speaker, **({"short_by": missing} if missing else {})})
    return made


def mark_speakers(pages: list[dict[str, Any]], project: Project, colours: list[str]) -> None:
    """Colour a page by who says it, or name them when the voice changes. Off unless `[speakers] show` asks."""
    show = str(project.setting("speakers", "show", ""))
    if show not in SPEAKER_DISPLAYS:
        raise AltocueError(f"Unknown [speakers] show value {show}.", fix="Use colour, name, both, or leave it empty.")
    seen: dict[str, int] = {}
    latest = ""
    for page in pages:
        who = page.get("speaker", "")
        if not who:
            continue
        place = seen.setdefault(who, len(seen))
        if "colour" in SPEAKER_DISPLAYS[show]:
            page["colour"] = colours[place % len(colours)]
        if "name" in SPEAKER_DISPLAYS[show] and who != latest:
            page["name"] = str(project.setting("speakers", who, who))
        latest = who


def _count(value: Any, name: str, fix: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0 or value != int(value):
        raise AltocueError(f"{name} is {value!r}.", fix=fix)
    return int(value)


def settings(project: Project, style: dict[str, Any]) -> dict[str, Any]:
    """How this project's pages break. `[caption]` in altocue.toml wins, then `caption` in the brand file, then the shape of the canvas."""
    width, height = project.size

    def chosen(key: str, by_shape: int) -> int:
        fix = "Use a whole number, or 0 to follow the shape of the canvas."
        mine = _count(project.setting("caption", key, 0), f"[caption] {key} in altocue.toml", fix)
        brand = _count(style.get(key, 0), f"caption {key} in the brand file", fix)
        return mine or brand or by_shape

    min_show = project.setting("caption", "min_show", MIN_SHOW)
    if isinstance(min_show, bool) or not isinstance(min_show, int | float) or min_show < 0:
        raise AltocueError(f"[caption] min_show in altocue.toml is {min_show!r}.", fix=f"Use seconds, such as {MIN_SHOW}, or 0 for no minimum.")
    return {
        "max_words": chosen("max_words", WORDS_TALL if height > width else WORDS_WIDE),
        "max_chars": chosen("max_chars", CHARS_WIDE if width > height else CHARS_NARROW),
        "min_show": float(min_show),
        "max_gap_ms": _count(style.get("combine_ms", MAX_GAP_MS), "caption combine_ms in the brand file", "Use milliseconds, such as 600."),
        "language": str(project.setting("transcribe", "language", "en") or ""),
    }


def _line(page: dict[str, Any]) -> str:
    return " ".join(word["text"] for word in page["words"])


def build(project: Project) -> list[dict[str, Any]]:
    from .motion import html  # the layout knows how wide a line may be, and it draws these pages

    brand = brands.load(project)
    style = brand.caption
    edl = project.edl()
    how = settings(project, style)
    pages = build_pages(
        [w for w in cut.map_words(project, edl) if w.get("kind", "word") == "word"],
        max_words=how["max_words"],
        max_chars=how["max_chars"],
        combine_ms=how["max_gap_ms"],
        min_show=how["min_show"],
        language=how["language"],
        ends=cut.segment_ends(edl),
    )
    mark_speakers(pages, project, list(style.get("speaker_colours") or SPEAKER_COLOURS))
    width, height = project.size
    zone = platforms.zone_of(project)
    for page in pages:
        texts = [w["text"] for w in page["words"]]
        page["lines"] = html.caption_lines(texts, brand, width, height, zone, joins(texts, how["language"]))
    write_json(project.work / "captions.json", {"cut": project.cut_mark(), "settings": how, "short_pages": short_pages(pages), "pages": pages})
    return pages


def short_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The pages that could not be given their time, for a check to read. `page` is the place in `pages`, and the times are seconds.

    `shown` is how long the page is up, `needed` how long it should be, and `short_by` the difference.
    """
    found = []
    for index, page in enumerate(pages):
        if page.get("short_by"):
            shown = round(page["end"] - page["start"], 3)
            needed = round(shown + page["short_by"], 3)
            found.append({"page": index, "start": page["start"], "shown": shown, "needed": needed, "short_by": page["short_by"], "text": _line(page)})
    return found


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    pages = build(project)
    print(f"{len(pages)} caption pages, {sum(len(p['words']) for p in pages)} words")
    short = short_pages(pages)
    if short:
        floor = settings(project, brands.load(project).caption)["min_show"]
        brief = sum(1 for page in short if page["shown"] < floor)
        print(f"Pages up for less time than they take to read: {len(short)}, of which {brief} for under {floor:g} seconds. See short_pages in work/captions.json.")
    note = platforms.note(project)
    if note:
        print(note)
    return 0
