"""What each join keeps: its class, its pause, and where a breath falls.

A join is where one kept run hands over to the next. `snap` finds every edge with the pads it was
proven at. This module only decides how much quiet a join keeps, from its class and the project's
pace, and then moves the found edge by the difference: through quiet, and never into the middle of a breath.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import pace
from ..models import Clip, Removed, Segment, Word
from ..transcribe import HOP, Envelope
from .snap import LAST_WORD_TAIL, OUT_BACK, POST, PRE, last_full_frame

TIME_SLACK = 0.02  # ffprobe durations can sit a hair under the last sample
BREATH_OVER, BREATH_UNDER = 6.0, 20.0  # dB over the clip's floor. A breath sits between them, and under the speech threshold
BREATH_MIN, BREATH_MAX = 0.12, 0.7
BREATH_NEAR = 0.25  # a breath belongs to the line that starts this soon after it
BREATH_CLEAR = 0.04  # quiet kept ahead of a breath that stays
ONSET_REACH = 0.6  # how far past a found in point its first sound is looked for
HOLD_CLEAR = 0.06  # quiet kept between a held shot and the next sound

Shot = tuple[Clip, list[Word], tuple[Removed, ...], Envelope | None]  # a clip, its words, its walls and its sound


@dataclass(frozen=True)
class Pads:
    """How much quiet a run keeps before its first word and after its last."""

    lead: float = PRE
    tail: float = POST
    ends_film: bool = False  # the tail is the end hold. Nothing follows it, so a breath inside it is not half a breath


@dataclass
class Join:
    kind: str  # clause, comma, sentence or topic, or `end` after the film's last word
    left: int  # where the run before it sits in play order
    right: int | None
    item: Removed | None  # the removal that carries this join in the reports


def limits(run: list[int], words: list[Word], clip: Clip, fps: int, walls: tuple[Removed, ...] = ()) -> tuple[float, float]:
    """The earliest a run may open and the latest it may close: its neighbours' words, the clip's last full frame, and any wall."""
    first, last = words[run[0]], words[run[-1]]
    floor = words[run[0] - 1].end if run[0] else 0.0
    ceiling = min(words[run[-1] + 1].start if run[-1] + 1 < len(words) else clip.duration, last_full_frame(clip.duration, fps))
    for wall in walls:
        if wall.start >= last.end - TIME_SLACK:
            ceiling = min(ceiling, wall.start + POST)
        if wall.end <= first.start + TIME_SLACK:
            floor = max(floor, wall.end - PRE)
    return floor, ceiling


def carrier(cuts: list[Removed], left: Word, right: Word | None) -> Removed | None:
    """The removal a join is reported on: what was said between the two words, or failing that the quiet."""
    between = sorted((item for item in cuts if item.start >= left.end - 1e-3 and (right is None or item.end <= right.start + 1e-3)), key=lambda item: item.start)
    said = [item for item in between if item.kind != "silence"]
    return (said[0] if said and right is not None else between[0]) if between else None


def plan(
    ordered: list[tuple[str, list[int]]],
    context: dict[str, Shot],
    cuts: list[Removed],
    fps: int,
    *,
    speed: str,
    max_pause: float,
    end_hold: float,
    language: str,
) -> tuple[list[Pads], list[Join]]:
    """The pads of every run in play order, and the joins between them.

    Two neighbouring words with only a pause between them keep what the pace curve gives that pause.
    Where words were taken out, or the order or the clip changes, there is no source pause to follow,
    so the join keeps its class's own pause, as far as the quiet on each side allows.
    """
    leads, tails = [PRE] * len(ordered), [POST] * len(ordered)
    joins: list[Join] = []
    by_clip: dict[str, list[Removed]] = {}
    for item in cuts:
        by_clip.setdefault(item.clip, []).append(item)
    spoken = [place for place, (_, run) in enumerate(ordered) if run]
    for a, b in zip(spoken, spoken[1:], strict=False):
        if b != a + 1:
            continue  # a clip with no speech plays between them
        (name_a, run_a), (name_b, run_b) = ordered[a], ordered[b]
        clip_a, words_a, walls_a, _ = context[name_a]
        clip_b, words_b, walls_b, _ = context[name_b]
        left, right = words_a[run_a[-1]], words_b[run_b[0]]
        here = by_clip.get(name_a, [])
        if name_a == name_b and run_b[0] == run_a[-1] + 1:
            gap = right.start - left.end
            kind = pace.classify(left.text, right.text, gap, language=language)
            between = next((item for item in here if left.end - 1e-6 <= item.start and item.end <= right.start + 1e-6), None)
            if not any(between is wall for wall in walls_a):  # a wall holds a sound nobody transcribed, so each side keeps the proven pad
                tails[a], leads[b] = pace.split(pace.kept_pause(gap, kind, speed, max_pause))
            joins.append(Join(kind, a, b, between))
            continue
        kind = pace.classify(left.text, right.text, new_clip=name_a != name_b, language=language)
        after = max(limits(run_a, words_a, clip_a, fps, walls_a)[1] - left.end, 0.0)
        before = max(right.start - limits(run_b, words_b, clip_b, fps, walls_b)[0], 0.0)
        keep = min(pace.join_pause(kind, speed), after + before)
        tail = min(pace.split(keep)[0], after)
        lead = min(keep - tail, before)
        tails[a], leads[b] = min(keep - lead, after), lead
        in_order = name_a == name_b and run_b[0] > run_a[-1]
        joins.append(Join(kind, a, b, carrier(here, left, right if in_order else None)))
    last = spoken[-1] if spoken and spoken[-1] == len(ordered) - 1 and end_hold > 0 else None
    if last is not None:
        name, run = ordered[last]
        tails[last] = end_hold
        joins.append(Join("end", last, None, carrier(by_clip.get(name, []), context[name][1][run[-1]], None)))
    return [Pads(lead, tail, place == last) for place, (lead, tail) in enumerate(zip(leads, tails, strict=True))], joins


def stamp(removed: list[Removed], joins: list[Join], ordered: list[tuple[str, list[int]]], context: dict[str, Shot], home: dict[int, Segment]) -> None:
    """Write each join's class and the quiet it kept onto the removal that carries it, measured on the finished segments."""
    for item in removed:
        item.pause, item.kept = "", None
    for join in joins:
        if join.item is None:
            continue
        name, run = ordered[join.left]
        left, before = context[name][1][run[-1]], home[join.left]
        kept = max(before.end - left.end, 0.0)
        if join.right is not None:
            name, run = ordered[join.right]
            right, after = context[name][1][run[0]], home[join.right]
            kept = right.start - left.end if after is before else kept + max(right.start - after.start, 0.0)
        join.item.pause, join.item.kept = join.kind, round(kept, 3)


def breaths(envelope: Envelope, first: int, last: int) -> list[tuple[int, int]]:
    """Runs of envelope frames from `first` up to `last` that read as a breath.

    A breath sits over the room and under speech, lasts 0.12 to 0.7 s, and rises out of quiet.
    A word's decay passes through the same level, but it falls out of speech, so it is not one.
    """
    first = max(first, 0)
    levels = envelope.db[first:last]
    low, high = envelope.floor + BREATH_OVER, min(envelope.floor + BREATH_UNDER, envelope.threshold)
    if high <= low or not len(levels):
        return []
    inside = (levels >= low) & (levels < high)
    if len(inside) > 2:
        inside[1:-1] |= (levels[1:-1] < low) & inside[:-2] & inside[2:]  # one frame of dip does not end a breath
    edges = np.flatnonzero(np.diff(np.r_[0, inside.astype(np.int8), 0]))
    found = []
    for start, end in zip(edges[::2], edges[1::2], strict=True):
        ahead = levels[start - 1] if start else envelope.db[first - 1] if first else -math.inf
        if BREATH_MIN - 1e-9 <= (end - start) * HOP <= BREATH_MAX + 1e-9 and ahead < low:
            found.append((first + int(start), first + int(end)))
    return found


def lead_in(envelope: Envelope | None, start: float, floor: float, lead: float, first: Word, fps: int) -> tuple[float, float]:
    """Where a run opens with `lead` of quiet before its first sound, and a time the frame grid may not round back past.

    That time is the end of a breath that was dropped, and minus infinity when none was.

    `start` is the in point the snap found. It moves only through quiet. A breath before the line
    stays whole when the lead has room for it, and goes whole when it has not: the run never opens inside one.
    """
    if envelope is None:
        return max(first.start - lead, floor), -math.inf
    speech = envelope.speech
    at = min(max(round(start / HOP), 0), len(speech))
    ahead = np.flatnonzero(speech[at : at + round(ONSET_REACH / HOP)])
    if not len(ahead):
        return start, -math.inf
    first_sound = at + int(ahead[0])
    onset, want = first_sound * HOP, start
    if lead > PRE + 1e-9 and first_sound > at:
        want = min(start, onset - lead)
        lowest = max(math.floor(max(want, floor, 0.0) / HOP), 0)
        sound = np.flatnonzero(speech[lowest:at])
        if len(sound):
            want = min(start, max(want, (lowest + int(sound[-1]) + 2) * HOP))  # a frame of quiet after the last sound before it
    elif lead < PRE - 1e-9:
        want = max(start, min(start + (PRE - lead), onset - lead))
    want, opens = max(want, floor), -math.inf
    window = max(math.floor(max(floor, 0.0) / HOP), first_sound - round((BREATH_NEAR + BREATH_MAX) / HOP) - 2)
    near = [found for found in breaths(envelope, window, first_sound) if first_sound - found[1] <= round(BREATH_NEAR / HOP)]
    if near:
        begins, stops = near[-1][0] * HOP, near[-1][1] * HOP
        whole = begins - BREATH_CLEAR
        if begins < want < stops:
            if onset - stops >= pace.LEAD_LEAST + 1 / fps:
                want = opens = stops  # dropped whole. The grid may round on from here, never back into it
            elif whole >= floor and onset - whole <= pace.LEAD_MOST:
                want = whole  # it runs straight into the word, so it stays whole
            else:
                want = max(floor, onset - pace.LEAD_LEAST)  # too long to keep and too close to drop: open on its last moment
    return want, opens


def tail_out(envelope: Envelope | None, end: float, ceiling: float, tail: float, last: Word, *, ends_film: bool = False) -> float:
    """Where a run closes with `tail` of quiet after its last sound.

    `end` is the out point the snap found. A longer tail stops short of the next sound, and of a
    breath unless the film ends here. A shorter one never comes back inside the word.
    """
    if envelope is None:
        return min(last.end + tail, ceiling)
    speech = envelope.speech
    at = min(max(round(end / HOP), 0), len(speech))
    want = end
    if tail > POST + 1e-9:
        want = end + (tail - POST)
        stop = min(math.ceil(min(want, ceiling) / HOP) + 3, len(speech))
        sustained = np.flatnonzero(np.convolve(speech[at:stop].astype(int), np.ones(3, dtype=int), "valid") == 3) if stop - at >= 3 else []
        if len(sustained):
            want = min(want, max(end, (at + int(sustained[0])) * HOP - HOLD_CLEAR))
        taken = [] if ends_film else [found for found in breaths(envelope, at, stop) if found[0] * HOP < want]
        if taken:
            want = min(want, max(end, taken[0][0] * HOP))
    elif tail < POST - 1e-9:
        back = max(at - round(OUT_BACK / HOP), 0)
        sound = np.flatnonzero(speech[back:at])
        sound_end = (back + int(sound[-1]) + 1) * HOP if len(sound) else last.end
        want = min(end, max(end - (POST - tail), sound_end + tail, last.end + LAST_WORD_TAIL))
    return min(want, ceiling)
