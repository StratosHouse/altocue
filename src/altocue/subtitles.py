"""Subtitles from the words really spoken. SRT and VTT share one set of cues, and no cue crosses a cut.

Line length, line count, reading speed and cue length follow Netflix's timed text guides: 42 characters
a line, two lines, 20 characters a second, five-sixths of a second to seven seconds per cue. A cut
outranks all of them, so a cue that lands against one can read fast or sit short, and the report says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from itertools import groupby
from typing import Any

from .caption import HOLD_AFTER, SENTENCE_END, strip_punctuation

LINE = 42
LINES = 2
READING = 20.0  # characters a second
SHORTEST = 5 / 6
LONGEST = 7.0
PAUSE = 0.6  # a silence this long inside a shot starts a new cue
GAP_FRAMES = 2
READABLE = 0.3  # a cue shorter than this flashes by unread; it joins a neighbour in its shot, or goes if it is under a frame
CLAUSE_END = ",;:"
BREAK_BEFORE = {"and", "but", "or", "so", "because", "that", "which", "who", "when", "to", "of", "in", "on", "at", "for", "with", "from", "by"}
KEEP_WITH_NEXT = {"a", "an", "the", "my", "your", "his", "her", "its", "our", "their", "this", "these", "those", "i", "we", "you", "they", "he", "she", "it"}
# Chinese and Japanese write words with no space between them. Korean spaces its words, so Hangul is not here.
UNSPACED = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]
    segment: str

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def _break_cost(words: list[str], at: int) -> float:
    """Netflix prefers a bottom-heavy pair: break after punctuation or before a joining word, never after an article."""
    top, bottom = " ".join(words[:at]), " ".join(words[at:])
    cost = abs(len(top) - len(bottom)) + (len(top) - len(bottom) if len(top) > len(bottom) else 0)
    if words[at - 1][-1] in CLAUSE_END + SENTENCE_END:
        cost -= 24
    if words[at].lower() in BREAK_BEFORE:
        cost -= 8
    if words[at - 1].lower() in KEEP_WITH_NEXT:
        cost += 40
    if at <= 2:
        cost += 12
    return cost


def wrap(text: str) -> list[str]:
    """One line if it fits, else the best two. Only a single word over the limit can overrun."""
    if len(text) <= LINE:
        return [text]
    words = text.split(" ")
    fitting = [at for at in range(1, len(words)) if len(" ".join(words[:at])) <= LINE and len(" ".join(words[at:])) <= LINE]
    if not fitting:
        return [text] if len(words) == 1 else [" ".join(words[: len(words) // 2]), " ".join(words[len(words) // 2 :])]
    best = min(fitting, key=lambda at: _break_cost(words, at))
    return [" ".join(words[:best]), " ".join(words[best:])]


def fits(text: str) -> bool:
    lines = wrap(text)
    return len(lines) <= LINES and all(len(line) <= LINE for line in lines)


def _joined(words: list[dict[str, Any]]) -> str:
    text = ""
    for word in (w["text"].strip() for w in words):
        unspaced = bool(text) and bool(word) and UNSPACED.match(text[-1]) and UNSPACED.match(word[0])
        text += word if not text or unspaced else f" {word}"
    return text


def _clause_break(words: list[dict[str, Any]]) -> int:
    """Where to split a cue that ran out of room: the last clause that leaves a good first half."""
    for at in range(len(words) - 1, 0, -1):
        if words[at - 1]["text"].rstrip()[-1:] in CLAUSE_END and len(_joined(words[:at])) >= LINE / 2:
            return at
    return len(words)


def _ends_sentence(word: dict[str, Any]) -> bool:
    return word["text"].rstrip(" \"'”’)")[-1:] in SENTENCE_END


def _split_shot(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """One shot's words as groups, each a cue that fits. Nothing here can cross a cut."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if current:
            finished = _ends_sentence(current[-1]) or word["start"] - current[-1]["end"] >= PAUSE
            full = not fits(_joined([*current, word])) or word["end"] - current[0]["start"] > LONGEST
            if finished:
                groups.append(current)
                current = []
            elif full:
                at = _clause_break(current)
                groups.append(current[:at])
                current = current[at:]
        current.append(word)
    if current:
        groups.append(current)
    return groups


def _joins_next(group: list[dict[str, Any]], following: list[dict[str, Any]]) -> bool:
    """A quick word like "Right." flashes by alone, so it joins the next cue when both still fit."""
    room = following[0]["start"] - group[0]["start"]
    return (
        len(_joined(group)) / max(room, 0.001) > READING
        and following[0]["start"] - group[-1]["end"] < PAUSE
        and fits(_joined([*group, *following]))
        and following[-1]["end"] - group[0]["start"] <= LONGEST
    )


def _merged(groups: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    for group in groups:
        if out and _joins_next(out[-1], group):
            out[-1] = [*out[-1], *group]
        else:
            out.append(group)
    return out


def build_cues(words: list[dict[str, Any]], ends: dict[str, float], *, fps: float = 30) -> list[Cue]:
    """Words mapped through the cut become cues. `ends` gives each segment's last moment in output seconds."""
    spoken = [w for w in words if strip_punctuation(w["text"])]
    groups = [group for _, shot in groupby(spoken, key=lambda w: w["segment"]) for group in _merged(_split_shot(list(shot)))]
    cues = []
    for index, group in enumerate(groups):
        start, last = group[0]["start"], group[-1]["end"]
        text = _joined(group)
        wall = ends.get(group[0]["segment"], float("inf"))
        following = groups[index + 1][0]["start"] if index + 1 < len(groups) else float("inf")
        wanted = max(last + HOLD_AFTER, start + SHORTEST, start + len(text) / READING)
        end = min(wanted, start + LONGEST, wall, following - GAP_FRAMES / fps)
        cues.append(Cue(round(start, 3), round(max(end, min(last, wall)), 3), wrap(text), group[0]["segment"]))
    return readable(cues, fps)


def _join(first: str, second: str) -> str:
    return first + ("" if UNSPACED.search(first[-1:] + second[:1]) else " ") + second


def readable(cues: list[Cue], fps: float = 30) -> list[Cue]:
    """No cue too short to read. Where a cut leaves a cue almost no room it joins a neighbour in the same shot,
    and one under a frame with nowhere to go is dropped: a muxer refuses it, and nobody could read it anyway."""
    frame = 1 / fps
    out: list[Cue] = []
    pending = list(cues)
    while pending:
        cue = pending.pop(0)
        if cue.end - cue.start >= READABLE:
            out.append(cue)
            continue
        before = out[-1] if out and out[-1].segment == cue.segment else None
        after = pending[0] if pending and pending[0].segment == cue.segment else None
        if before and fits(_join(before.text, cue.text)):
            out[-1] = Cue(before.start, max(before.end, cue.end), wrap(_join(before.text, cue.text)), cue.segment)
        elif after and fits(_join(cue.text, after.text)):
            pending[0] = Cue(cue.start, max(after.end, cue.end), wrap(_join(cue.text, after.text)), cue.segment)
        elif cue.end - cue.start >= frame:
            out.append(cue)
    return out


def _stamp(seconds: float, point: str) -> str:
    total = round(seconds * 1000)
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    whole, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}{point}{millis:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [f"{n}\n{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}\n" + "\n".join(c.lines) + "\n" for n, c in enumerate(cues, 1)]
    return "\n".join(blocks)


def to_vtt(cues: list[Cue]) -> str:
    blocks = [f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}\n" + "\n".join(escape(line, quote=False) for line in c.lines) + "\n" for c in cues]
    return "\n".join(["WEBVTT\n", *blocks])


def reading_report(cues: list[Cue]) -> dict[str, Any]:
    """What a reader gets: the fastest cue, the longest line, and how many cues a cut forced past the limit."""
    speeds = [len(c.text) / max(c.end - c.start, 0.001) for c in cues]
    return {
        "cues": len(cues),
        "fastest_cps": round(max(speeds, default=0.0), 1),
        "limit_cps": READING,
        "over_limit": sum(round(speed, 1) > READING for speed in speeds),
        "longest_line": max((len(line) for c in cues for line in c.lines), default=0),
        "limit_line": LINE,
    }
