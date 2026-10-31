"""Find what could go. Certain things are cut, likely things carry their evidence and wait."""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

import numpy as np

from ..models import Clip, Decision, RemovalKind, Removed, Word
from ..transcribe import HOP, Envelope, core

VOICED = 0.3  # seconds of speech energy that make a pause worth a second look
WORD_DRIFT = 0.15  # word edges drift this far, so the ends of a pause can hold a word's own tail
VOICED_GAP = 3.0  # past this, a noisy pause is ambience, not a missed word
STUTTER_GAP = 0.6
RESTART_PAUSE = 0.2
SURE_PAUSE = 0.3
FLUENT_PAUSE = 0.15
MAX_SPAN = 60.0  # a line said again after this long is a callback, not a retake
MAX_TOKENS = 180
SHORT_TAKE = 6
FRAGMENT = 4  # a finished sentence that shares two words with the next one is just two sentences
SURE_COVERAGE = 0.8
SURE_SPAN = 20.0
LINE_PAUSE = 0.5
CLIP_OPENING = 6  # a retake in the next clip starts within this many words of its start
MAX_SKIP = 2
VOICED_PAUSE = "pause with speech the transcript missed"
QUIET_LINE_DB = 16.0  # this far under the clip's speech level reads as someone off-mic
QUIET_LINE_WORDS = 3
QUIET_LINE_GAP = 0.5
SENTENCE_END = ".?!"
CUT_OFF = ("-", "\u2014", "\u2013", "...", "\u2026")

STOPWORDS = frozenset(
    """a an the and or but so to of in on at for with from by is are was were be been am i i'm i've you you're he she it it's we we're they
    me my your our their this that these those there here do does did have has had will would can could should just like then than as if not
    no yes yeah okay ok right well go going gonna get got come on oh""".split()
)
EMPHASIS = frozenset("very really so no yes yeah ha haha bye never always much more now".split())
GRAMMAR_DOUBLES = frozenset({"had", "that"})


@dataclass
class Candidate:
    """One span that could go, with why and how sure."""

    clip: str
    start: float
    end: float
    kind: RemovalKind
    reason: str
    evidence: str
    score: float
    decision: Decision

    def removed(self) -> Removed:
        return Removed(
            self.clip,
            round(self.start, 3),
            round(self.end, 3),
            self.kind,
            f"{self.reason}: {self.evidence}",
            self.decision,
            round(self.score, 2),
        )


@dataclass
class Token:
    """A spoken word in the stream every clip feeds, fillers and cues left out."""

    clip: str
    index: int
    word: Word
    norm: str
    opening: bool  # within the first words of its clip


def quote(words: list[Word]) -> str:
    text = " ".join(w.text for w in words)
    return f'"{text}"' if len(text) <= 70 else f'"{text[:67]}..."'


def certain(clip: Clip, words: list[Word], *, max_pause: float, remove_fillers: bool, envelope: Envelope | None) -> list[Candidate]:
    """Layer 1: pauses over the limit, filler tokens and spoken cues."""
    found: list[Candidate] = []
    if not words:
        return [
            Candidate(
                clip.id,
                0.0,
                clip.duration,
                "silence",
                "no speech in this clip",
                "the transcript is empty, so it stays until ruled on",
                0.5,
                "review",
            )
        ]
    if words[0].start > 0.05:
        found.append(Candidate(clip.id, 0.0, words[0].start, "silence", "before the first word", f"{words[0].start:.2f} s", 1.0, "auto"))
    if clip.duration - words[-1].end > 0.05:
        found.append(
            Candidate(
                clip.id,
                words[-1].end,
                clip.duration,
                "silence",
                "after the last word",
                f"{clip.duration - words[-1].end:.2f} s",
                1.0,
                "auto",
            )
        )
    for left, right in zip(words, words[1:], strict=False):
        gap = right.start - left.end
        if gap > max_pause:
            found.append(pause(clip.id, left, right, gap, max_pause, envelope))
    index = 0
    while index < len(words):
        kind = words[index].kind
        stop = index + 1
        while kind == "cue" and stop < len(words) and words[stop].kind == "cue":
            stop += 1
        if kind == "cue":
            found.append(
                Candidate(clip.id, words[index].start, words[stop - 1].end, "cue", "spoken cue", quote(words[index:stop]), 1.0, "auto")
            )
        elif kind == "filler" and remove_fillers:
            found.append(
                Candidate(clip.id, words[index].start, words[index].end, "filler", "filler", quote(words[index:stop]), 1.0, "auto")
            )
        index = stop
    return found


def pause(clip: str, left: Word, right: Word, gap: float, max_pause: float, envelope: Envelope | None) -> Candidate:
    evidence = f"{gap:.2f} s between {quote([left])} and {quote([right])}, limit {max_pause:.2f} s"
    if envelope is not None and envelope.reliable:
        voiced = envelope.speech_seconds(left.end + WORD_DRIFT, right.start - WORD_DRIFT)
        evidence += f", {voiced:.2f} s of speech energy"
        if voiced >= VOICED and gap <= VOICED_GAP:
            return Candidate(clip, left.end, right.start, "silence", VOICED_PAUSE, evidence, 0.5, "review")
    return Candidate(clip, left.end, right.start, "silence", "long pause", evidence, 1.0, "auto")


@lru_cache(maxsize=8192)
def same(a: str, b: str) -> bool:
    """Short tokens must match exactly: "the" and "they" clear any similarity bar."""
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8


def stream(clips: list[Clip], words: dict[str, list[Word]]) -> list[Token]:
    tokens: list[Token] = []
    for clip in clips:
        spoken = [(i, w) for i, w in enumerate(words[clip.id]) if w.kind == "word" and core(w.text)]
        tokens += [Token(clip.id, i, w, core(w.text), rank < CLIP_OPENING) for rank, (i, w) in enumerate(spoken)]
    return tokens


def cut_off(word: Word) -> bool:
    return word.text.rstrip().endswith(CUT_OFF)


def stutters(tokens: list[Token]) -> list[Candidate]:
    """Layer 2: the same word or two said back to back."""
    found: list[Candidate] = []
    index = 0
    while index < len(tokens) - 1:
        here, next_token = tokens[index], tokens[index + 1]
        if here.clip != next_token.clip or next_token.word.start - here.word.end > STUTTER_GAP:
            index += 1
            continue
        if cut_off(here.word) and next_token.norm.startswith(here.norm) and here.norm != next_token.norm:
            found.append(
                Candidate(
                    here.clip,
                    here.word.start,
                    next_token.word.start,
                    "stutter",
                    "broken word",
                    f"{quote([here.word])} then {quote([next_token.word])}",
                    0.95,
                    "auto",
                )
            )
            index += 1
            continue
        stop = index + 1
        while stop < len(tokens) and repeats(tokens[stop - 1], tokens[stop]):
            stop += 1
        if stop - index > 1:
            found.append(doubled(tokens[index:stop]))
            index = stop - 1
            continue
        found += doubled_phrase(tokens, index)
        index += 1
    return found


def repeats(first: Token, second: Token) -> bool:
    """The same word again, close by, inside one sentence."""
    same_word = first.clip == second.clip and first.norm == second.norm
    return same_word and second.word.start - first.word.end <= STUTTER_GAP and first.word.text.rstrip()[-1:] not in SENTENCE_END


def doubled(run: list[Token]) -> Candidate:
    first, last = run[0], run[-1]
    evidence = f"{quote([t.word for t in run])}, {len(run)} times"
    if first.norm in EMPHASIS:
        decision, score, reason = "keep", 0.3, "repeat that reads as emphasis"
    elif first.norm in GRAMMAR_DOUBLES and len(run) == 2:
        decision, score, reason = "review", 0.5, "repeat that can be correct grammar"
    elif first.norm not in STOPWORDS and len(run) == 2:
        decision, score, reason = "review", 0.6, "repeated content word"
    else:
        decision, score, reason = "auto", 0.9, "stutter"
    return Candidate(first.clip, first.word.start, last.word.start, "stutter", reason, evidence, score, decision)  # type: ignore[arg-type]


def doubled_phrase(tokens: list[Token], index: int) -> list[Candidate]:
    for size in (3, 2):
        first, second = tokens[index : index + size], tokens[index + size : index + 2 * size]
        if len(second) < size or {t.clip for t in first + second} != {tokens[index].clip}:
            continue
        if [t.norm for t in first] != [t.norm for t in second] or second[0].word.start - first[-1].word.end > STUTTER_GAP:
            continue
        content = any(t.norm not in STOPWORDS for t in first)
        decision: Decision = "review" if content else "auto"
        return [
            Candidate(
                first[0].clip,
                first[0].word.start,
                second[0].word.start,
                "stutter",
                "phrase said twice",
                quote([t.word for t in first + second]),
                0.7 if content else 0.85,
                decision,
            )
        ]
    return []


def run_length(tokens: list[Token], i: int, j: int) -> tuple[int, int]:
    """Match the take at `i` against the take at `j`. Returns matched tokens and how far `i` got."""
    a, b = i, j
    matched, misses, reach = 0, 0, i
    while a < j and b < len(tokens):
        if same(tokens[a].norm, tokens[b].norm):
            matched += 1
            a, b = a + 1, b + 1
            reach = a
            continue
        misses += 1
        if misses > 1 + matched // 4:
            break
        for skip in range(1, MAX_SKIP + 1):
            if a + skip < j and same(tokens[a + skip].norm, tokens[b].norm):
                a += skip
                break
            if b + skip < len(tokens) and same(tokens[a].norm, tokens[b + skip].norm):
                b += skip
                break
        else:
            a, b = a + 1, b + 1
    return matched, reach


def coverage(tokens: list[Token], i: int, j: int) -> float:
    """How much of the earlier take the later one says again. Low means unique content would go."""
    earlier = [t.norm for t in tokens[i:j]]
    content = [norm for norm in earlier if norm not in STOPWORDS] or earlier
    later = tokens[j : j + (j - i) + 5]
    return sum(any(same(norm, t.norm) for t in later) for norm in content) / len(content)


def span_seconds(tokens: list[Token], i: int, j: int, clips: dict[str, Clip]) -> float:
    earlier, later = tokens[i], tokens[j]
    if earlier.clip == later.clip:
        return later.word.start - earlier.word.start
    return clips[earlier.clip].duration - earlier.word.start + later.word.start


def retakes(tokens: list[Token], clips: list[Clip], words: dict[str, list[Word]]) -> list[Candidate]:
    """Layer 2: a later take that says an earlier one again. The earlier take is the candidate."""
    by_id = {clip.id: clip for clip in clips}
    order = [clip.id for clip in clips]
    found: list[Candidate] = []
    for j in range(1, len(tokens)):
        later = tokens[j]
        previous = tokens[j - 1]
        cross_clip = previous.clip != later.clip
        pause_before = later.word.start if cross_clip else later.word.start - previous.word.end
        spoken_cue = not cross_clip and cue_between(words[later.clip], previous.index, later.index)
        restart = (
            cross_clip
            or later.opening
            or spoken_cue
            or pause_before >= RESTART_PAUSE
            or previous.word.text.rstrip()[-1:] in SENTENCE_END
            or cut_off(previous.word)
        )
        if not restart:
            continue
        best = None
        for i in range(j - 1, max(j - MAX_TOKENS, -1), -1):
            earlier = tokens[i]
            if not same(earlier.norm, later.norm):
                continue
            across = earlier.clip != later.clip
            if across and not (later.opening and order.index(later.clip) == order.index(earlier.clip) + 1):
                continue
            if span_seconds(tokens, i, j, by_id) > MAX_SPAN:
                continue
            candidate = judge(tokens, i, j, by_id, words, pause_before=pause_before, spoken_cue=spoken_cue, across=across)
            if candidate and (best is None or candidate.score > best.score):
                best = candidate
        if best:
            found.append(best)
    return found


def cue_between(words: list[Word], left: int, right: int) -> bool:
    return any(w.kind == "cue" for w in words[left + 1 : right])


def judge(
    tokens: list[Token],
    i: int,
    j: int,
    clips: dict[str, Clip],
    words: dict[str, list[Word]],
    *,
    pause_before: float,
    spoken_cue: bool,
    across: bool,
) -> Candidate | None:
    earlier, later = tokens[i], tokens[j]
    previous = tokens[j - 1]
    matched, reach = run_length(tokens, i, j)
    span = j - i
    short = span <= SHORT_TAKE
    stopped = cut_off(previous.word)
    content = sum(t.norm not in STOPWORDS for t in tokens[i:reach])
    open_ended = previous.word.text.rstrip()[-1:] not in SENTENCE_END
    fragment = stopped or (span <= FRAGMENT and open_ended and pause_before >= RESTART_PAUSE)
    if matched < (2 if fragment else 3) or content < (1 if short else 2):
        return None
    covered = coverage(tokens, i, j)
    seconds = span_seconds(tokens, i, j, clips)
    score = min(1.0, 0.5 * covered + 0.2 * min(1.0, matched / 6) + 0.2 * min(1.0, pause_before / 0.6) + 0.1 * (spoken_cue or stopped))
    evidence = (
        f"{matched} of {span} words said again at {later.clip} {later.word.start:.2f} s, {covered:.0%} of its content covered, "
        f"{pause_before:.2f} s pause before the later take, {seconds:.1f} s apart. Later take: {quote([t.word for t in tokens[j : j + 8]])}"
    )
    if spoken_cue and matched >= 3:
        decision, reason, score = "auto", "retake after a spoken cue", max(score, 0.9)
    elif across:
        decision, reason = "review", "retake in the next clip"
    elif covered >= SURE_COVERAGE and matched >= 3 and (pause_before >= SURE_PAUSE or stopped) and seconds <= SURE_SPAN:
        decision, reason = "auto", "false start" if short else "repeat run"
    elif pause_before < FLUENT_PAUSE and not stopped and previous.word.text.rstrip()[-1:] not in SENTENCE_END:
        decision, reason = "keep", "fluent repetition, reads as intended"
    elif covered < SURE_COVERAGE and not short:
        decision, reason = "review", "earlier take has content the later one does not"
    else:
        decision, reason = "review", "false start" if short else "repeat run"
    clip_words = words[earlier.clip]
    start = clip_words[line_start(clip_words, earlier.index)].start if across else earlier.word.start
    end = clip_words[-1].end if across else later.word.start
    return Candidate(earlier.clip, start, end, "retake", reason, evidence, score, decision)  # type: ignore[arg-type]


def line_start(words: list[Word], index: int) -> int:
    """Walk back to where the line holding this word began."""
    while index:
        before = words[index - 1]
        if before.kind == "cue" or before.text.rstrip()[-1:] in SENTENCE_END or words[index].start - before.end >= LINE_PAUSE:
            break
        index -= 1
    return index


def abandoned(clip: str, words: list[Word], taken: list[Candidate]) -> list[Candidate]:
    """A cue with no later match still says the line before it failed. An agent decides how much."""
    found: list[Candidate] = []
    for index, word in enumerate(words):
        if word.kind != "cue" or not index or words[index - 1].kind == "cue":
            continue
        start = line_start(words, index - 1)
        span = (words[start].start, word.start)
        if not any(c.clip == clip and c.kind == "retake" and c.start <= span[0] + 0.01 and c.end >= span[1] - 0.01 for c in taken):
            found.append(
                Candidate(
                    clip,
                    *span,
                    "retake",
                    "line before a spoken cue",
                    f"no later take matched. Line: {quote(words[start:index])}",
                    0.6,
                    "review",
                )
            )
    return found


def settle(candidates: list[Candidate]) -> list[Candidate]:
    """Where spans overlap heavily, the stronger one speaks for both."""
    rank = {"auto": 2, "review": 1, "keep": 0}
    chosen: list[Candidate] = []
    for item in sorted(candidates, key=lambda c: (rank[c.decision], c.score, c.end - c.start), reverse=True):
        length = max(item.end - item.start, 1e-6)
        shared = max((min(item.end, c.end) - max(item.start, c.start) for c in chosen if c.clip == item.clip), default=0.0)
        if shared / length <= 0.5:
            chosen.append(item)
    return sorted(chosen, key=lambda c: (c.clip, c.start))


def quiet_lines(clip: str, words: list[Word], envelope: Envelope | None) -> list[Candidate]:
    """Short lines far below the clip's speaking level are usually someone off-mic."""
    if envelope is None or not envelope.reliable or not len(envelope.db):
        return []
    lines: list[list[Word]] = []
    for word in words:
        if lines and word.start - lines[-1][-1].end <= QUIET_LINE_GAP:
            lines[-1].append(word)
        else:
            lines.append([word])
    found = []
    for line in lines:
        window = envelope.db[int(line[0].start / HOP) : max(int(line[0].start / HOP) + 1, math.ceil(line[-1].end / HOP))]
        level = float(np.percentile(window, 90)) if len(window) else envelope.loud
        if len(line) <= QUIET_LINE_WORDS and level < envelope.loud - QUIET_LINE_DB:
            evidence = f"{quote(line)} sits {envelope.loud - level:.0f} dB under this clip's speech"
            found.append(Candidate(clip, line[0].start, line[-1].end, "quiet", "Quiet line, likely off-mic", evidence, 0.7, "review"))
    return found


def candidates(clips: list[Clip], words: dict[str, list[Word]], envelopes: dict[str, Envelope | None] | None = None) -> list[Candidate]:
    tokens = stream(clips, words)
    found = settle(stutters(tokens) + retakes(tokens, clips, words))
    for clip in clips:
        found += abandoned(clip.id, words[clip.id], found)
        found += quiet_lines(clip.id, words[clip.id], (envelopes or {}).get(clip.id))
    return sorted(found, key=lambda c: (c.clip, c.start))
