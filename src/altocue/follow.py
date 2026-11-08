"""A crop that moves, for the shots one steady crop cannot hold.

`frame` gives every segment one fixed crop, and that is right for a seated speaker. It fails in two
shots: someone who walks across the frame, and a wide shot of two or more people where the tall
crop fits only one. Here the crop follows instead.

- **One person moving.** The crop trails the head with a dead zone, then eases after it. It never
  outruns the picture and never moves while the head is still.
- **People taking turns.** With speaker labels from `altocue transcribe --speakers`, each voice is
  matched to a face by whose mouth moves while that voice talks. The crop cuts to whoever is
  speaking and holds there, and a turn shorter than `SWITCH_HOLD` does not pull it away.
- **A camera change inside a segment.** An edited source can cut from a close-up to a wide shot
  within one segment. Each shot is followed on its own, the crop cuts with the picture, and a
  shot with no face takes the centre crop instead of keeping the last shot's place.

Nothing here is a new model. Faces come from the YuNet detector `frame` already ships, and voices
from the diariser `speakers` already runs. A path is a short list of keyframes in source pixels,
which `plate` turns into sub-pixel motion with ffmpeg's `perspective` filter.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from . import frame

if TYPE_CHECKING:
    from .frame import Face
    from .models import Clip, Segment, Word

MODES = ("auto", "on", "off")
RATE = 5.0  # samples a second. A walk or a turn change is slower than this
DETECT_SIDE = 640
LINK_REACH = 1.2  # a face this many face widths from the last one is the same person
MIN_PRESENCE = 0.3
SMOOTH = 0.45  # seconds. How long the crop takes to settle after the head stops
MAX_SPEED = 0.9  # crop widths a second, so a fast move reads as a pan, not a jolt
DEAD_ZONE = 0.08  # share of the crop the head may move before the crop follows
SWITCH_HOLD = 1.2  # seconds a speaker must hold the floor before the crop goes to them
MIN_TRAVEL = 6.0  # source pixels. A path that moves less than this is a still crop
TOLERANCE = 1.5  # source pixels a simplified path may stray from the smooth one
MAX_KEYS = 40
PATCH = (24, 12)
THUMB = (32, 18)  # grey thumbnail compared between samples to find a camera change
SHOT_CHANGE = 0.5  # histogram correlation below this between two samples is a new shot
SHOT_JUMP = 60.0  # or a mean thumbnail difference above this, out of 255
FACE_JUMP = 2.5  # or the biggest face grows or shrinks this much in one sample, with no face near the last one


@dataclass(frozen=True)
class Look:
    """The faces at one moment, with each face's mouth as a small grey patch."""

    t: float
    faces: tuple[Face, ...]
    mouths: tuple[np.ndarray | None, ...]
    shot: int = 0  # which camera shot of the segment this sample is in


@dataclass
class Person:
    """One face followed through a segment. `at` maps a sample index to its face there."""

    at: dict[int, Face]
    mouths: dict[int, np.ndarray]

    @property
    def size(self) -> float:
        return float(np.median([f.area for f in self.at.values()]))

    def nearest(self, index: int, within: Sequence[int] | None = None) -> Face:
        """The face here, or the closest sample's when the detector missed one, from `within` when given."""
        if index in self.at:
            return self.at[index]
        pool = [i for i in self.at if within is None or i in within] or list(self.at)
        return self.at[min(pool, key=lambda i: abs(i - index))]

    def seen_in(self, indices: Sequence[int]) -> bool:
        return any(i in self.at for i in indices)


def sample_times(segment: Segment, rate: float = RATE) -> list[float]:
    span = max(segment.end - segment.start, 0.0)
    count = max(2, math.ceil(span * rate) + 1)
    return [segment.start + span * i / (count - 1) for i in range(count)]


def mouth_patch(gray: np.ndarray, face: Face) -> np.ndarray | None:
    """The mouth, cut square to the line between its corners and scaled to a fixed size."""
    if len(face.mouth) != 2:
        return None
    (rx, ry), (lx, ly) = face.mouth
    width = math.hypot(lx - rx, ly - ry)
    if width < 4:
        return None
    cx, cy = (rx + lx) / 2, (ry + ly) / 2
    half_w, half_h = width * 0.75, width * 0.45
    top, bottom = int(cy - half_h), int(cy + half_h)
    left, right = int(cx - half_w), int(cx + half_w)
    if top < 0 or left < 0 or bottom > gray.shape[0] or right > gray.shape[1] or bottom - top < 2 or right - left < 2:
        return None
    patch = cv2.resize(gray[top:bottom, left:right], PATCH, interpolation=cv2.INTER_AREA).astype(np.float32)
    spread = float(patch.std())
    return (patch - patch.mean()) / spread if spread > 1e-3 else None


def look_at(clip: Clip, times: Sequence[float]) -> list[Look]:
    """Faces and mouths at each time, in display pixels. Frames decode in parallel, YuNet runs in turn."""

    def load(at: float) -> np.ndarray | None:
        return frame.read_sdr(clip, at, DETECT_SIDE)

    looks, thumbs = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for at, pixels in zip(times, pool.map(load, times), strict=True):
            if pixels is None:
                looks.append(Look(at, (), ()))
                thumbs.append(None)
                continue
            image = frame._to_u8(pixels)
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
            found = frame.detect_faces(image)
            factor = clip.display_size[0] / image.shape[1]
            looks.append(Look(at, tuple(f.scaled(factor) for f in found), tuple(mouth_patch(gray, f) for f in found)))
            thumbs.append(cv2.resize(gray, THUMB, interpolation=cv2.INTER_AREA))
    return [Look(look.t, look.faces, look.mouths, shot) for look, shot in zip(looks, shots(thumbs), strict=True)]


def shot_change(before: np.ndarray, after: np.ndarray) -> bool:
    """A camera change between two grey thumbnails: the tones shift, or most of the picture does."""
    hist = [cv2.calcHist([t], [0], None, [32], [0, 256]) for t in (before, after)]
    correlation = cv2.compareHist(hist[0], hist[1], cv2.HISTCMP_CORREL)
    jump = float(np.abs(before.astype(np.float32) - after.astype(np.float32)).mean())
    return correlation < SHOT_CHANGE or jump > SHOT_JUMP


def face_jump(before: Sequence[Face], after: Sequence[Face]) -> bool:
    """A cut the tones miss, as between two shots of one red wall: the faces jump in size and place."""
    if not before or not after:
        return False
    old, new = max(before, key=lambda f: f.area), max(after, key=lambda f: f.area)
    ratio = max(old.area, new.area) / max(min(old.area, new.area), 1e-6)
    near = any(math.dist(f.center, g.center) < LINK_REACH * max(f.w, g.w) for f in before for g in after)
    return ratio > FACE_JUMP and not near


def split_on_faces(looks: Sequence[Look]) -> list[Look]:
    """Shot numbers again, adding a cut wherever the faces jump between two samples."""
    out, extra, seen = [], 0, None
    for look in looks:
        if seen is not None and look.shot == seen.shot and face_jump(seen.faces, look.faces):
            extra += 1
        out.append(Look(look.t, look.faces, look.mouths, look.shot + extra))
        if look.faces:
            seen = look
    return out


def shots(thumbs: Sequence[np.ndarray | None]) -> list[int]:
    """A shot number for every sample. A sample that could not be read stays in the shot before it."""
    out, shot, last = [], 0, None
    for thumb in thumbs:
        if thumb is not None:
            if last is not None and shot_change(last, thumb):
                shot += 1
            last = thumb
        out.append(shot)
    return out


def link(looks: Sequence[Look]) -> list[Person]:
    """Join detections into people. A face seen in too few samples is noise, unless it is all there is."""
    people: list[Person] = []
    for index, look in enumerate(looks):
        taken: set[int] = set()
        for face, mouth in sorted(zip(look.faces, look.mouths, strict=True), key=lambda pair: -pair[0].area):
            best, gap = None, float("inf")
            for number, person in enumerate(people):
                if number in taken:
                    continue
                last = person.at[max(person.at)]
                distance = math.dist(face.center, last.center)
                if distance < LINK_REACH * max(face.w, last.w) and distance < gap:
                    best, gap = number, distance
            if best is None:
                people.append(Person({}, {}))
                best = len(people) - 1
            taken.add(best)
            people[best].at[index] = face
            if mouth is not None:
                people[best].mouths[index] = mouth
    needed = max(2, math.ceil(len(looks) * MIN_PRESENCE))
    # A face seen once is a false hit on a wall as often as a person, so the fallback needs two sightings.
    kept = [p for p in people if len(p.at) >= needed] or [p for p in sorted(people, key=lambda p: len(p.at), reverse=True)[:1] if len(p.at) >= 2]
    return sorted(kept, key=lambda p: p.size, reverse=True)


def mouth_motion(person: Person, count: int) -> np.ndarray:
    """How much the mouth changed since the sample before, for every sample. NaN where it was not seen."""
    motion = np.full(count, np.nan)
    for index in range(1, count):
        if index in person.mouths and index - 1 in person.mouths:
            motion[index] = float(np.abs(person.mouths[index] - person.mouths[index - 1]).mean())
    return motion


def speaking(words: Sequence[Word], times: Sequence[float]) -> list[str]:
    """Who is talking at each sample: the speaker of the word under it, or else of the last word before it."""
    labelled = sorted((w for w in words if w.speaker and w.kind == "word"), key=lambda w: w.start)
    starts = [w.start for w in labelled]
    out = []
    for at in times:
        index = bisect.bisect_right(starts, at) - 1
        out.append(labelled[index].speaker if index >= 0 else "")
    return out


def match_voices(people: Sequence[Person], voices: Sequence[str]) -> dict[str, int]:
    """Give each voice the face whose mouth moves most while that voice talks, against while it does not."""
    labels = sorted({v for v in voices if v})
    if len(labels) < 2 or len(people) < 2:
        return {}
    count = len(voices)
    motions = [mouth_motion(p, count) for p in people]
    scores = []
    for label in labels:
        talking = np.array([v == label for v in voices])
        for number, motion in enumerate(motions):
            on, off = motion[talking & ~np.isnan(motion)], motion[~talking & ~np.isnan(motion)]
            if len(on) >= 2 and len(off) >= 2:
                scores.append((float(on.mean() - off.mean()), label, number))
    chosen: dict[str, int] = {}
    for score, label, number in sorted(scores, reverse=True):
        if score > 0 and label not in chosen and number not in chosen.values():
            chosen[label] = number
    return chosen


def _runs(choice: Sequence[int]) -> list[tuple[int, int]]:
    """(first, last) index of each stretch with one choice."""
    runs: list[tuple[int, int]] = []
    for index, who in enumerate(choice):
        if runs and choice[runs[-1][0]] == who:
            runs[-1] = (runs[-1][0], index)
        else:
            runs.append((index, index))
    return runs


def steady_turns(choice: list[int], times: Sequence[float], hold: float = SWITCH_HOLD) -> list[int]:
    """Drop turns shorter than `hold`, so a quick "yeah" does not swing the crop away and back.

    The shortest such turn goes first, to the person before it, or after it when it opens the segment.
    """
    out = list(choice)
    step = (times[-1] - times[0]) / (len(times) - 1) if len(times) > 1 else 0.0
    while True:
        runs = _runs(out)
        if len(runs) < 2:
            return out
        lengths = [times[last] - times[first] + step for first, last in runs]
        shortest = min(range(len(runs)), key=lambda i: lengths[i])
        if lengths[shortest] >= hold:
            return out
        first, last = runs[shortest]
        into = out[first - 1] if shortest > 0 else out[last + 1]
        out[first : last + 1] = [into] * (last - first + 1)


def track_of(face: Face) -> frame.Track:
    return frame.Track(face.center[0], face.center[1], face.w, face.h, face.eye_y, 1, 0.0)


def median_track(person: Person) -> frame.Track:
    faces = list(person.at.values())
    return frame.Track(
        float(np.median([f.center[0] for f in faces])),
        float(np.median([f.center[1] for f in faces])),
        float(np.median([f.w for f in faces])),
        float(np.median([f.h for f in faces])),
        float(np.median([f.eye_y for f in faces])),
        len(faces),
        0.0,
    )


def trail(wanted: Sequence[float], zone: float) -> list[float]:
    """Hold still until the target leaves the dead zone, then trail it from the zone's edge."""
    out, at = [], wanted[0]
    for value in wanted:
        if value > at + zone:
            at = value - zone
        elif value < at - zone:
            at = value + zone
        out.append(at)
    return out


def ease(values: Sequence[float], times: Sequence[float], width: float) -> list[float]:
    """Smooth both ways, so the crop does not lag, then cap the speed so it pans and never jumps."""
    if len(values) < 3:
        return list(values)
    step = (times[-1] - times[0]) / (len(times) - 1)
    alpha = 1 - math.exp(-step / SMOOTH)
    forward = [values[0]]
    for value in values[1:]:
        forward.append(forward[-1] + alpha * (value - forward[-1]))
    both = [forward[-1]]
    for value in reversed(forward[:-1]):
        both.append(both[-1] + alpha * (value - both[-1]))
    both.reverse()
    reach = MAX_SPEED * width * step
    capped = [both[0]]
    for value in both[1:]:
        capped.append(capped[-1] + max(-reach, min(reach, value - capped[-1])))
    return capped


def simplify(points: list[tuple[float, float, float]], tolerance: float = TOLERANCE) -> list[tuple[float, float, float]]:
    """Fewest keyframes whose straight lines stay within `tolerance` of the path. Ramer, Douglas and Peucker."""
    if len(points) < 3:
        return list(points)
    (t0, x0, y0), (t1, x1, y1) = points[0], points[-1]
    worst, where = 0.0, 0
    for index in range(1, len(points) - 1):
        t, x, y = points[index]
        share = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        miss = max(abs(x - (x0 + (x1 - x0) * share)), abs(y - (y0 + (y1 - y0) * share)))
        if miss > worst:
            worst, where = miss, index
    if worst <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[: where + 1], tolerance)[:-1] + simplify(points[where:], tolerance)


def solve(
    looks: Sequence[Look],
    voices: Sequence[str],
    source: tuple[int, int],
    size: tuple[int, int],
    start: float,
) -> tuple[list[list[float]] | None, dict[str, object]]:
    """A path of [seconds into the segment, x, y] keyframes, or None when a still crop will do.

    The note says what it saw: how many people, which voice went to which face, and how many cuts.
    """
    looks = split_on_faces(looks)
    times = [look.t for look in looks]
    shot_of = [look.shot for look in looks]
    members = {shot: [i for i, s in enumerate(shot_of) if s == shot] for shot in set(shot_of)}
    people = link(looks)
    note: dict[str, object] = {"people": len(people), "voices": {}, "switches": 0, "shots": len(members)}
    if not people:
        return None, note
    width, height = size
    together = frame._subject([median_track(p) for p in people])
    fits = together.right - together.left <= width and together.bottom - together.top <= height
    matched = match_voices(people, voices) if len(people) > 1 and not fits else {}
    note["voices"] = dict(matched)
    choice = [0] * len(looks)
    if matched:
        current = matched.get(next((v for v in voices if v in matched), ""), 0)
        for index, voice in enumerate(voices):
            current = matched.get(voice, current)
            choice[index] = current
        choice = steady_turns(choice, times)
    wanted, label = [], []
    for index, who in enumerate(choice):
        same = members[shot_of[index]]
        here = [number for number, p in enumerate(people) if p.seen_in(same)]
        if not here:
            # Nobody's face in this shot: the centre, not wherever the last shot left the crop.
            subject = None
            who = -1
        elif fits or len(members) > 1 and who not in here:
            faces = sorted((people[n].nearest(index, same) for n in here), key=lambda f: -f.area)
            subject = frame.choose_subject([track_of(f) for f in faces], width, height)
            who = who if fits else here[0]
        else:
            subject = frame._subject([track_of(people[who].nearest(index, same))])
        wanted.append(frame.place_crop(source, size, subject))
        label.append(shot_of[index] * (len(people) + 1) + who + 1)
    step = 1 / RATE if len(times) < 2 else (times[-1] - times[0]) / (len(times) - 1)
    # Every sample where none of the people followed was found: an empty shot, a wide shot with only
    # stray background faces, or a handheld camera panning off the subject.
    note["faceless"] = round(step * sum(1 for index in range(len(looks)) if not any(index in p.at for p in people)), 1)
    room_x, room_y = source[0] - width, source[1] - height
    runs = _runs(label)
    note["switches"] = len(runs) - 1
    smooth = []
    for first, last in runs:
        span = times[first : last + 1]
        xs = ease(trail([w[0] for w in wanted[first : last + 1]], DEAD_ZONE * width), span, width)
        ys = ease(trail([w[1] for w in wanted[first : last + 1]], DEAD_ZONE * height), span, height)
        smooth.append([(t - start, min(max(x, 0.0), room_x), min(max(y, 0.0), room_y)) for t, x, y in zip(span, xs, ys, strict=True)])
    every = [point for run in smooth for point in run]
    still_x = max(p[1] for p in every) - min(p[1] for p in every) < MIN_TRAVEL
    still_y = max(p[2] for p in every) - min(p[2] for p in every) < MIN_TRAVEL
    if still_x and still_y:
        return None, note
    tolerance = TOLERANCE
    while True:
        keys: list[tuple[float, float, float]] = []
        for points in smooth:
            if keys:
                # A new speaker or a camera change is a cut: hold the old crop to just before the next sample, then jump.
                keys.append((max(points[0][0] - 1e-3, keys[-1][0]), keys[-1][1], keys[-1][2]))
            keys += simplify(points, tolerance)
        if len(keys) <= MAX_KEYS:
            break
        tolerance *= 2
    return [[round(t, 3), round(x, 2), round(y, 2)] for t, x, y in keys], note


def needed(mode: str, moved: bool, crowded: bool) -> bool:
    """`on` follows every cropped shot, `auto` only those a still crop fails, `off` none."""
    if mode == "on":
        return True
    return mode == "auto" and (moved or crowded)
