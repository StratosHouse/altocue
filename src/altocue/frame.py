"""Find faces and choose one steady crop per shot.

Crops never drift. Each EDL segment gets a fixed rectangle in display pixels, and a head
that stays inside the dead zone keeps the crop it had before.

Shot size is planned by beat, not by join. A punch-in arrives or leaves only where a new sentence
starts or a retake or a long pause came out, only once the size on screen has been held for
`min_hold`, and never at a join that an insert covers or that nobody would see. A jump that stays
visible at one size is written to work/frame.json as a bare jump, for an insert or a kept filler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

import cv2
import numpy as np

from . import follow, pace
from .cut.hand import hand_set
from .cut.script import ends_sentence
from .cut.snap import frame_count, plays
from .errors import AltocueError
from .ffmpeg import require
from .models import Clip, Edl, Removed, Segment, Word
from .project import SIZES, Project, write_json

MODEL_PATH = Path(__file__).parent / "data" / "models" / "face_detection_yunet_2026may.onnx"

DETECT_SIDE = 640  # YuNet is trained on faces up to about 300 px, so big frames shrink first.
CLOSE_UP_SIDE = 320
MIN_SCORE = 0.6
READ_TIMEOUT = 300

PUNCH_ZOOM = 1.12
MIN_PUNCH = 1.04  # Below this a punch-in reads as a mistake, not a cut.
ZOOM_ORIGIN = (0.5, 0.42)  # Where the plate stage zooms from, as a share of the crop, when no subject gives an eye point.
DEAD_ZONE = 0.10  # Share of the crop the subject may move before the crop follows.
EYE_LINE = 0.38
HEADROOM = 0.04
HAIR = 0.35  # The face box stops at the forehead. Hair adds about this share of its height.
MIN_TRACK_SHARE = 0.4
SECOND_FACE_AREA = 0.25
SIDE_MARGIN = 0.25
SAMPLES = (3, 6)
VERSION = 3

HOLDS = {"tight": 1.2, "natural": 2.0, "relaxed": 3.0}  # seconds a shot size stays before it may change, by `[cut] pace`
BEAT_PAUSE = 1.0  # a cut pause this long opens a new beat, as a new sentence or a retake does
NEXT_BEAT = 1.2  # a new size needs this long before the beat after it
COVER_FRAMES = 4  # an insert hides a join when it runs this many frames past it on both sides
STILL_SHIFT = 0.02  # a face centre that moves under this share of the crop width across a join,
STILL_GROWTH = 0.03  # and changes height by under this share, makes a join nobody sees
PUSH_CAP = 0.06  # the most a slow push adds to one held shot
SOFT, TOO_SOFT = 1.5, 2.0  # how far the source's pixels are enlarged: say so past the first, drop the punch-in past the second


@dataclass(frozen=True)
class Face:
    """One detection, in pixels of the image it was found in."""

    x: float
    y: float
    w: float
    h: float
    score: float = 1.0
    eye_y: float = 0.0
    eyes: tuple[tuple[float, float], ...] = ()
    mouth: tuple[tuple[float, float], ...] = ()  # its two corners, when the detector gave them

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    def scaled(self, factor: float) -> Face:
        eyes = tuple((x * factor, y * factor) for x, y in self.eyes)
        mouth = tuple((x * factor, y * factor) for x, y in self.mouth)
        return Face(self.x * factor, self.y * factor, self.w * factor, self.h * factor, self.score, self.eye_y * factor, eyes, mouth)


@dataclass(frozen=True)
class Track:
    """One face followed across a segment, as medians."""

    center_x: float
    center_y: float
    w: float
    h: float
    eye_y: float
    hits: int
    spread: float

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass(frozen=True)
class Subject:
    """The box that must stay in frame."""

    left: float
    right: float
    top: float
    bottom: float
    eye_y: float
    faces: int
    spread: float = 0.0
    margin: float = 0.0  # the room `left` and `right` add beside the faces

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def _quiet_opencv() -> None:
    """OpenCV 5 warns about its own backend on every load. The call moved between versions."""
    logging = getattr(getattr(cv2, "utils", None), "logging", None)
    if logging is not None:
        logging.setLogLevel(logging.LOG_LEVEL_ERROR)
    else:
        getattr(cv2, "setLogLevel", lambda level: None)(2)


@lru_cache(maxsize=1)
def _detector() -> cv2.FaceDetectorYN:
    if not MODEL_PATH.exists():
        raise AltocueError(f"The face model is missing: {MODEL_PATH}.", fix="Reinstall Altocue. The model ships with it.")
    _quiet_opencv()
    return cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (320, 320), MIN_SCORE, 0.3, 200)


def _detect_at(image: np.ndarray, side: int) -> list[Face]:
    height, width = image.shape[:2]
    factor = min(1.0, side / max(height, width))
    size = (max(16, round(width * factor)), max(16, round(height * factor)))
    small = cv2.resize(image, size, interpolation=cv2.INTER_AREA) if factor < 1 else image
    detector = _detector()
    detector.setInputSize(size)
    _, rows = detector.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    if rows is None:
        return []
    found = []
    for row in rows:
        x, y, w, h = (float(v) for v in row[:4])
        eye_y = float(row[5] + row[7]) / 2
        eyes = ((float(row[4]), float(row[5])), (float(row[6]), float(row[7])))
        mouth = ((float(row[10]), float(row[11])), (float(row[12]), float(row[13])))
        found.append(Face(x, y, w, h, float(row[14]), eye_y, eyes, mouth).scaled(width / size[0]))
    return found


def detect_faces(image: np.ndarray) -> list[Face]:
    """Faces in an RGB uint8 image, largest first, in that image's pixels."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise AltocueError("detect_faces needs an RGB image.", fix="Pass an array shaped height, width, 3.")
    pixels = image if image.dtype == np.uint8 else (np.clip(image, 0, 1) * 255 + 0.5).astype(np.uint8)
    found = _detect_at(pixels, DETECT_SIDE) or _detect_at(pixels, CLOSE_UP_SIDE)
    return sorted(found, key=lambda f: f.area, reverse=True)


def source_matrix(clip: Clip) -> str:
    """The YUV matrix a clip was coded with, read from its primaries."""
    if clip.primaries == "bt2020":
        return "bt2020"
    if clip.primaries in {"smpte170m", "bt470bg"}:
        return "bt601"
    return "bt709"


def source_range(clip: Clip) -> str:
    """Full range when the format says so. Otherwise the frame's own tag decides, and no tag means limited."""
    return "pc" if clip.pix_fmt.startswith("yuvj") else "auto"


def sample_size(clip: Clip, side: int) -> tuple[int, int]:
    """A small even size with the display's shape."""
    width, height = clip.display_size
    factor = min(1.0, side / max(width, height))
    return max(2, round(width * factor / 2) * 2), max(2, round(height * factor / 2) * 2)


def read_rgb(clip: Clip, at: float, side: int = DETECT_SIDE) -> np.ndarray | None:
    """One frame as coded non-linear RGB floats, turned upright. None past the end of the clip."""
    width, height = sample_size(clip, side)
    colour = f"in_color_matrix={source_matrix(clip)}:in_range={source_range(clip)}"
    chain = f"scale={width}:{height}:{colour}:flags=bilinear+accurate_rnd+full_chroma_int,format=gbrp16le"
    cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(at, 0):.3f}", "-i", clip.path]
    cmd += ["-frames:v", "1", "-vf", chain, "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=READ_TIMEOUT)
    except subprocess.TimeoutExpired as error:
        raise AltocueError(f"Reading {clip.path} stalled.", fix="If the file lives in iCloud, download it first, then run this again.") from error
    expected = width * height * 3 * 2
    if done.returncode != 0 or len(done.stdout) < expected:
        return None
    planes = np.frombuffer(done.stdout[:expected], dtype="<u2").reshape(3, height, width).astype(np.float32) / 65535
    return np.stack((planes[2], planes[0], planes[1]), axis=-1)


def read_sdr(clip: Clip, at: float, side: int = DETECT_SIDE) -> np.ndarray | None:
    """One frame as display-ready BT.709 floats, tone mapped when the clip is HDR."""
    pixels = read_rgb(clip, at, side)
    if pixels is None or not clip.is_hdr:
        return pixels
    from .grade import hdr_to_sdr

    return hdr_to_sdr(pixels, clip.transfer)


def _to_u8(pixels: np.ndarray) -> np.ndarray:
    return (np.clip(pixels, 0, 1) * 255 + 0.5).astype(np.uint8)


def crop_size(source: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    """The largest even rectangle with the target's shape."""
    src_w, src_h = source
    aspect = target[0] / target[1]
    if src_w / src_h > aspect:
        return (min(src_w, round(src_h * aspect / 2) * 2), src_h - src_h % 2)
    return (src_w - src_w % 2, min(src_h, round(src_w / aspect / 2) * 2))


def matches_aspect(source: tuple[int, int], target: tuple[int, int]) -> bool:
    return abs(source[0] / source[1] - target[0] / target[1]) < 0.01


def find_tracks(samples: list[list[Face]]) -> list[Track]:
    """Follow faces across samples. A detection seen in too few samples is noise."""
    groups: list[list[Face]] = []
    for faces in samples:
        for found in faces:
            cx, cy = found.center
            home = None
            for group in groups:
                last_x, last_y = group[-1].center
                if math.hypot(cx - last_x, cy - last_y) < max(found.w, group[-1].w):
                    home = group
                    break
            if home is None:
                groups.append([found])
            else:
                home.append(found)
    needed = max(1, math.ceil(len(samples) * MIN_TRACK_SHARE))
    kept = [group for group in groups if len(group) >= needed]
    if not kept and groups:
        # A head moving fast splits into short runs. The longest run still beats the centre.
        longest = max(groups, key=len)
        kept = [longest] if len(longest) >= 2 else []
    tracks = []
    for group in kept:
        xs = [f.center[0] for f in group]
        tracks.append(
            Track(
                center_x=median(xs),
                center_y=median(f.center[1] for f in group),
                w=median(f.w for f in group),
                h=median(f.h for f in group),
                eye_y=median(f.eye_y for f in group),
                hits=len(group),
                spread=max(xs) - min(xs),
            )
        )
    return sorted(tracks, key=lambda t: t.area, reverse=True)


def hold_through_gaps(segments: list[Segment], subjects: list[Subject | None]) -> list[Subject | None]:
    """A segment with no face keeps its clip's nearest subject, so the crop does not jump to centre."""
    held = list(subjects)
    for order in (range(len(held)), range(len(held) - 1, -1, -1)):
        last: dict[str, Subject] = {}
        for index in order:
            clip = segments[index].clip
            if held[index] is not None:
                last[clip] = held[index]  # type: ignore[assignment]
            elif clip in last:
                held[index] = last[clip]
    return held


def _subject(tracks: list[Track]) -> Subject:
    margin = max(t.w for t in tracks) * SIDE_MARGIN
    return Subject(
        left=min(t.center_x - t.w / 2 for t in tracks) - margin,
        right=max(t.center_x + t.w / 2 for t in tracks) + margin,
        top=min(t.center_y - t.h / 2 - t.h * HAIR for t in tracks),
        bottom=max(t.center_y + t.h / 2 for t in tracks),
        eye_y=min(t.eye_y for t in tracks),
        faces=len(tracks),
        spread=max(t.spread for t in tracks),
        margin=margin,
    )


def choose_subject(tracks: list[Track], crop_w: float, crop_h: float) -> Subject | None:
    """Keep every sizeable face that fits. When they do not fit, keep the biggest."""
    if not tracks:
        return None
    kept = [tracks[0]]
    for track in tracks[1:]:
        if track.area < tracks[0].area * SECOND_FACE_AREA:
            continue
        wider = _subject([*kept, track])
        if wider.right - wider.left <= crop_w and wider.bottom - wider.top <= crop_h:
            kept.append(track)
    return _subject(kept)


def _clamp_even(value: float, low: float, high: float) -> float:
    return float(min(max(round(value / 2) * 2, low), high))


def place_crop(source: tuple[int, int], size: tuple[int, int], subject: Subject | None) -> list[float]:
    """Centre the subject, put the eyes near the upper third, stay inside the picture."""
    src_w, src_h = source
    width, height = size
    room_x, room_y = src_w - width, src_h - height
    if subject is None:
        return [_clamp_even(room_x / 2, 0, room_x), _clamp_even(room_y * ZOOM_ORIGIN[1], 0, room_y), float(width), float(height)]
    y = subject.eye_y - height * EYE_LINE
    y = min(y, subject.top - height * HEADROOM)
    y = max(y, subject.bottom + height * HEADROOM - height)
    return [_clamp_even(subject.center_x - width / 2, 0, room_x), _clamp_even(y, 0, room_y), float(width), float(height)]


def settle_crop(previous: list[float] | None, subject: Subject | None, source: tuple[int, int], size: tuple[int, int]) -> list[float]:
    """Hold the last crop of this clip while the subject stays in the dead zone."""
    wanted = place_crop(source, size, subject)
    if previous is None or subject is None:
        return wanted
    x, y, width, height = previous
    moved_x = abs(wanted[0] - x) / width
    moved_y = abs(wanted[1] - y) / height
    inside = x <= subject.left and subject.right <= x + width and y <= subject.top and subject.bottom <= y + height
    return previous if moved_x <= DEAD_ZONE and moved_y <= DEAD_ZONE and inside else wanted


def eye_point(subject: Subject | None) -> list[float] | None:
    """Where the subject's eyes are, in display pixels. This is what a segment stores as its zoom origin."""
    return None if subject is None else [round(subject.center_x, 1), round(subject.eye_y, 1)]


def eye_origin(crop: list[float] | tuple[float, ...], subject: Subject | None) -> list[float] | None:
    """The subject's eye point as shares of the crop, kept inside it, as the plate reads a segment's origin.

    A zoom about this point shows it at the same place in the wide shot and the tight one, which is
    what makes a punch-in read as a cut. A point at share p of the crop is at share p of every
    window zoomed about p, so the window can never leave the crop either.
    """
    point = eye_point(subject)
    if point is None:
        return None
    x, y, width, height = crop
    return [min(max((point[0] - x) / width, 0.0), 1.0), min(max((point[1] - y) / height, 0.0), 1.0)]


def safe_zoom(crop: list[float], subject: Subject | None, wanted: float, origin: Sequence[float] | None = None) -> float:
    """The largest zoom up to `wanted` that keeps the subject in the plate's frame, zooming about `origin`.

    About the eyes, a side margin the crop already cuts is not the punch-in's to keep: a speaker at the
    edge of the picture has none on that side, and there the face itself is what must stay in.
    """
    if subject is None or wanted <= 1.0:
        return wanted
    x, y, width, height = crop
    across, down = origin or ZOOM_ORIGIN
    left, right = subject.left, subject.right
    if origin is not None and left <= x:
        left = min(left + subject.margin, subject.center_x)
    if origin is not None and right >= x + width:
        right = max(right - subject.margin, subject.center_x)
    shares = [
        (0, across, (left - x) / width),
        (1, across, (right - x) / width),
        (0, down, (subject.top - y) / height),
        (1, down, (subject.bottom - y) / height),
    ]
    best = wanted
    for edge, still, share in shares:
        share = min(max(share, 0.0), 1.0)
        if share == still:
            continue
        limit = (edge - still) / (share - still)
        if limit > 0:
            best = min(best, max(limit, 1.0))
    return best if best >= MIN_PUNCH else 1.0


def timecode(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def jumps(segments: list[Segment], fps: float) -> list[int]:
    """The segments that open on a jump: the same clip as the one before, picked up somewhere else."""
    frame = 1 / max(fps, 1)
    return [i for i in range(1, len(segments)) if segments[i].clip == segments[i - 1].clip and abs(segments[i].start - segments[i - 1].end) > frame]


def output_starts(segments: list[Segment], fps: float) -> list[float]:
    """Where each segment starts in the film, in seconds, with the film's end as one more entry."""
    frames, starts = 0, [0.0]
    for segment in segments:
        frames += frame_count(segment.start, segment.end, max(fps, 1))  # type: ignore[arg-type]
        starts.append(frames / max(fps, 1))
    return starts


def covered(at: float, inserts: Sequence[tuple[float, float]], fps: float) -> bool:
    """Whether an insert hides the join at `at` seconds of the film: it must run `COVER_FRAMES` past it on both sides."""
    reach = COVER_FRAMES / max(fps, 1)
    return any(start <= at - reach + 1e-6 and at + reach <= end + 1e-6 for start, end in inserts)


def jump_shows(before: list[Face], after: list[Face], crop_width: float) -> bool:
    """Whether a viewer would see the join between two frames. With no face on either side nobody can say, so it shows."""
    if not before or not after:
        return True
    was = before[0]
    now = min(after, key=lambda found: math.hypot(found.center[0] - was.center[0], found.center[1] - was.center[1]))
    shift = math.hypot(now.center[0] - was.center[0], now.center[1] - was.center[1])
    return shift >= STILL_SHIFT * crop_width or abs(now.h - was.h) >= STILL_GROWTH * was.h


def opens_beat(previous: Segment, segment: Segment, words: list[Word] | None, removed: Sequence[Removed]) -> bool:
    """A join opens a beat when the word after it starts a sentence, or a retake or a long pause came out there.

    With no words to read, the size of what came out is all there is to go on.
    """
    last = next((word for word in reversed(words or []) if plays(word, previous.start, previous.end)), None)
    if last is not None and ends_sentence(last):
        return True
    out = [item for item in removed if item.decision == "auto" and item.clip == segment.clip and item.start < segment.start - 1e-6 and item.end > previous.end + 1e-6]
    if any(item.kind == "retake" or (item.kind == "silence" and item.end - item.start >= BEAT_PAUSE - 1e-6) for item in out):
        return True
    return last is None and not out and abs(segment.start - previous.end) >= BEAT_PAUSE - 1e-6


def plan_shots(
    segments: list[Segment],
    fps: float,
    *,
    min_hold: float = HOLDS["natural"],
    words: dict[str, list[Word]] | None = None,
    removed: Sequence[Removed] = (),
    inserts: Sequence[tuple[float, float]] = (),
    shows: dict[int, bool] | None = None,
    emphasis: Collection[str] = (),
) -> tuple[list[bool], list[dict[str, Any]]]:
    """Which segments play tight, and what became of every jump.

    `words` is each clip's transcript, `removed` the cut's removals, `inserts` spans of the film an
    insert covers, `shows` whether each jump is visible (by the index of the segment it opens, and
    a jump not named is taken to show), and `emphasis` the ids of segments whose beat must be tight.

    The size changes only where a beat opens on a jump that shows, once the size on screen has been
    held for `min_hold` and the beat after is `NEXT_BEAT` away. Each clip starts wide.
    """
    rate = max(fps, 1)
    starts = output_starts(segments, rate)
    slack = 0.5 / rate
    said = words or {}
    joins: dict[int, dict[str, Any]] = {}
    opens = [index for index in range(len(segments)) if not index or segments[index].clip != segments[index - 1].clip]
    for index in jumps(segments, rate):
        beat = opens_beat(segments[index - 1], segments[index], said.get(segments[index].clip), removed)
        joins[index] = {"beat": beat, "covered": covered(starts[index], inserts, rate), "face_moved": (shows or {}).get(index)}
        if beat:
            opens.append(index)
    opens.sort()

    tight = [False] * len(segments)
    notes: dict[int, str] = {}
    punched, since = False, 0.0
    for place, index in enumerate(opens):
        stop = opens[place + 1] if place + 1 < len(opens) else len(segments)
        key = any(segment.id in emphasis for segment in segments[index:stop])
        join = joins.get(index)
        if join is None:  # a new clip is a new shot: wide, unless it opens on the line that matters
            punched, since = key, starts[index]
        else:
            held, ahead = starts[index] - since, starts[stop] - starts[index]
            wanted = punched
            if key:
                wanted, notes[index] = True, "this beat and the one before are both tight" if punched else "emphasis"
            elif join["covered"] or join["face_moved"] is False:
                pass
            elif held < min_hold - slack:
                notes[index] = f"this size had been on screen {held:.1f} s, under min_hold"
            elif ahead < NEXT_BEAT - slack:
                notes[index] = f"the next beat is {ahead:.1f} s away"
            else:
                wanted, notes[index] = not punched, "a new beat"
            if wanted != punched:
                punched, since = wanted, starts[index]
        tight[index:stop] = [punched] * (stop - index)

    rows = []
    for index in sorted(joins):
        join = joins[index]
        row = {"before": segments[index - 1].id, "after": segments[index].id, "at": round(starts[index], 3), "stamp": timecode(starts[index]), **join}
        if tight[index] != tight[index - 1]:
            row |= {"kind": "toggle", "why": notes[index]}
        else:
            row |= settle_join(join, notes.get(index, "inside a beat"))
        rows.append(row)
    return tight, rows


def settle_join(join: dict[str, Any], why: str) -> dict[str, str]:
    """What a join that keeps its size is to the viewer: hidden by an insert, too small to see, or a bare jump."""
    if join["covered"]:
        return {"kind": "covered", "why": "an insert covers it"}
    if join["face_moved"] is False:
        return {"kind": "invisible", "why": "the face barely moved"}
    return {"kind": "bare", "why": why}


def plan_zooms(segments: list[Segment], fps: float, **known: Any) -> list[float]:
    """1.0 or the punch-in for each segment. See `plan_shots` for the rule and for what `known` may hold."""
    return [PUNCH_ZOOM if tight else 1.0 for tight in plan_shots(segments, fps, **known)[0]]


def plan_push(segments: list[Segment], fps: float, levels: list[float], rate: float) -> list[tuple[float, float, list[float] | None]]:
    """A slow push at `rate` a second: each segment's zoom at its first and last edge, and where it sits in the push.

    One push runs over each held shot, which is a run of segments of one clip at one size, so the
    scale carries across the joins inside it and never snaps back. It eases in and out, and stops
    `PUSH_CAP` above where it began.
    """
    from .plate import PUSH_EASE, push_travel  # the plate draws this same curve

    lengths = [frame_count(s.start, s.end, max(fps, 1)) / max(fps, 1) for s in segments]  # type: ignore[arg-type]
    reach = PUSH_CAP / rate
    solved: list[tuple[float, float, list[float] | None]] = []
    index = 0
    while index < len(segments):
        stop = index + 1
        while stop < len(segments) and segments[stop].clip == segments[index].clip and levels[stop] == levels[index]:
            stop += 1
        over = min(sum(lengths[index:stop]), reach + PUSH_EASE if reach >= PUSH_EASE else 2 * reach)
        at = 0.0
        for place in range(index, stop):
            first = round(levels[place] + rate * push_travel(at, over), 4)
            last = round(levels[place] + rate * push_travel(at + lengths[place], over), 4)
            solved.append((first, last, [round(at, 4), round(over, 4)] if last != first else None))
            at += lengths[place]
        index = stop
    return solved


def _sample_times(segment: Segment) -> list[float]:
    span = max(segment.end - segment.start, 0.0)
    count = min(max(math.ceil(span * 1.5), SAMPLES[0]), SAMPLES[1])
    return [segment.start + span * (i + 0.5) / count for i in range(count)]


def _edge_times(segments: list[Segment], fps: float) -> dict[int, tuple[float, float]]:
    """For each jump, when to read the last frame before it and the first after. Half a frame early, so the seek lands on that frame."""
    frame = 1 / max(fps, 1)
    return {i: (round(max(segments[i - 1].end - 1.5 * frame, 0.0), 3), round(max(segments[i].start - 0.5 * frame, 0.0), 3)) for i in jumps(segments, fps)}


def _faces_at(clips: dict[str, Clip], wanted: list[tuple[str, float]]) -> dict[tuple[str, float], list[Face]]:
    """Faces at each clip and second, in display pixels. Pixels decode in parallel, YuNet runs in turn."""
    keys = sorted(set(wanted))

    def load(key: tuple[str, float]) -> np.ndarray | None:
        return read_sdr(clips[key[0]], key[1])

    found: dict[tuple[str, float], list[Face]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, pixels in zip(keys, pool.map(load, keys), strict=True):
            if pixels is None:
                found[key] = []
                continue
            factor = clips[key[0]].display_size[0] / pixels.shape[1]
            found[key] = [f.scaled(factor) for f in detect_faces(_to_u8(pixels))]
    return found


def _number(project: Project, key: str, default: float) -> float:
    value = project.setting("frame", key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise AltocueError(f"[frame] {key} is {value!r}.", fix="Use a number of 0 or more, or remove the key.")
    return float(value)


def min_hold_of(project: Project) -> float:
    """Seconds a shot size stays on screen before it may change: `[frame] min_hold`, or else what `[cut] pace` gives."""
    if project.setting("frame", "min_hold") is None:
        return HOLDS[pace.pace_of(project)]
    return _number(project, "min_hold", 0.0)


def _read(path: Path) -> dict[str, Any]:
    """A JSON file another stage owns. Missing or unreadable is empty here, because that stage reports on its own files."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _raw_inserts(project: Project) -> list[dict[str, Any]]:
    """Every insert the film lays over the speaker: the B-roll chosen for this cut, and those in motion.json."""
    chosen = _read(project.root / "broll.json")
    current = chosen.get("cut") == project.cut_mark()  # B-roll chosen on another cut sits on other words, and render refuses it
    found = [*((chosen.get("inserts") or []) if current else []), *(_read(project.root / "motion.json").get("inserts") or [])]
    return [item for item in found if isinstance(item, dict)]


def insert_spans(project: Project, segments: list[Segment], fps: float, words: dict[str, list[Word]]) -> list[tuple[float, float]]:
    """Where inserts cover the picture, in output seconds. One placed on a word follows that word, as it will at render."""
    from .timeline import resolve_time  # late, because the timeline imports most of the package

    starts = output_starts(segments, fps)
    played = [(starts[i], s, w) for i, s in enumerate(segments) for w in words.get(s.clip, []) if plays(w, s.start, s.end)]
    heard = [{"text": w.text, "start": begins + max(w.start, s.start) - s.start} for begins, s, w in played]
    spans = []
    for item in _raw_inserts(project):
        try:
            start = resolve_time(item["start"], heard)
            end = resolve_time(item["end"], heard) if "end" in item else start + float(item.get("duration", 2.5))  # the timeline's own default length
        except (AltocueError, KeyError, TypeError, ValueError):
            continue  # render refuses a bad insert in its own words
        spans.append((start, end))
    return spans


def _bare(text: str) -> str:
    return re.sub(r"[^\w']+", "", text.replace("\u2019", "'").lower())


def emphasised(phrases: list[str], segments: list[Segment], words: dict[str, list[Word]]) -> tuple[set[str], list[str]]:
    """The segments that say each phrase, and the phrases nobody says in this cut."""
    spoken = [(s.id, _bare(w.text)) for s in segments for w in words.get(s.clip, []) if plays(w, s.start, s.end)]
    spoken = [pair for pair in spoken if pair[1]]
    ids: set[str] = set()
    missing = []
    for phrase in phrases:
        wanted = [part for part in map(_bare, phrase.split()) if part]
        hits = [i for i in range(len(spoken) - len(wanted) + 1) if all(spoken[i + k][1] == wanted[k] for k in range(len(wanted)))] if wanted else []
        ids |= {spoken[i + k][0] for i in hits for k in range(len(wanted))}
        if not hits:
            missing.append(phrase)
    return ids, missing


def _fingerprint(project: Project, edl: Edl, target: tuple[int, int]) -> str:
    """Everything the solved framing rests on. A source swapped for a re-export of the same length is a new picture."""
    cuts = [[s.id, s.clip, s.start, s.end] for s in edl.segments]
    settings = {key: project.setting("frame", key) for key in ("punch_in", "push_in", "push_rate", "follow", "emphasis")}
    settings["min_hold"] = min_hold_of(project)
    used = {s.clip for s in edl.segments}
    files = []
    for clip in project.clips() if project.media_path.exists() else []:
        if clip.id in used:
            info = Path(clip.path).stat() if Path(clip.path).exists() else None
            files.append([clip.id, info.st_size if info else None, info.st_mtime_ns if info else None])
    hidden: list[str] = []  # a join under an insert never changes size, so the plan rests on which joins are covered, not on every insert
    if _raw_inserts(project):
        words = {name: _words(project, name) for name in used}
        spans, starts = insert_spans(project, edl.segments, edl.fps, words), output_starts(edl.segments, edl.fps)
        hidden = [edl.segments[index].id for index in jumps(edl.segments, edl.fps) if covered(starts[index], spans, edl.fps)]
    text = json.dumps({"version": VERSION, "cuts": cuts, "target": target, "settings": settings, "files": files, "covered": hidden}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _report_path(project: Project) -> Path:
    return project.work / "frame.json"


def up_to_date(project: Project) -> bool:
    """True when the saved framing was solved for this cut, aspect, these sources and settings, and the same joins under inserts."""
    path = _report_path(project)
    return path.exists() and json.loads(path.read_text()).get("fingerprint") == _fingerprint(project, project.edl(), project.size)


def fits_shape(crop: list[float] | None, target: tuple[int, int]) -> bool:
    """A crop chosen for another shape would stretch, so that one is solved again."""
    return crop is None or abs(crop[2] / crop[3] - target[0] / target[1]) < 0.02


@dataclass
class Shot:
    """What was solved for one segment, before any hand edit is laid over it."""

    crop: list[float] | None
    box: list[float]  # the crop the zoom was solved in. The whole picture when no crop is needed
    subject: Subject | None
    direct: Subject | None
    tight: bool
    level: float = 1.0
    moving: list[list[float]] | None = None
    seen: dict[str, Any] | None = None
    moved: bool = False

    @property
    def eyes(self) -> Subject | None:
        return None if self.moving else self.subject  # a crop that moves has no one point to hold


def softness(target: tuple[int, int], clips: dict[str, Clip], soft: dict[str, float], dropped: dict[str, float]) -> list[str]:
    """One line for each clip without the pixels for its crop, with a way out where there is one."""
    lines = []
    for name in sorted({*soft, *dropped}):
        parts = []
        if name in soft:
            parts.append(f"the picture is enlarged {soft[name]:.2f} times to fill the frame, past {SOFT:g}, so it will look soft")
        if name in dropped:
            parts.append(f"a punch-in would enlarge it {dropped[name]:.2f} times, past {TOO_SOFT:g}, so this clip plays without one")
        display, wide = clips[name].display_size, SIZES["4:5"]
        helps = wide[0] / crop_size(display, wide)[0] < target[0] / crop_size(display, target)[0] - 0.01
        tip = 'Set aspect = "4:5" under [output] for a wider crop, or use a source with more pixels.' if helps else "A source with more pixels would hold up."
        lines.append(f"{name}: {', and '.join(parts)}. {tip}")
    return lines


def frame_project(project: Project, force: bool = False) -> Edl:
    """Write crops and zooms into the EDL. Hand edits stay, through a re-cut too, until `force` clears them.

    The report keeps what was solved, not what was held, because the difference is how a hand edit is known.
    """
    from .plate import fit_crop  # late: the plate imports the grade, which imports this module

    edl = project.edl()
    if not project.setting("frame", "enabled", True):
        return edl
    before = edl.to_dict()
    target = project.size
    stamp = _fingerprint(project, edl, target)
    path = _report_path(project)
    untouched = all(s.crop is None and s.zoom_from == 1.0 and s.zoom_to == 1.0 for s in edl.segments)  # a new cut starts blank
    if not force and not untouched and path.exists() and json.loads(path.read_text()).get("fingerprint") == stamp:
        return edl
    clips = {clip.id: clip for clip in project.clips()}
    missing = sorted({s.clip for s in edl.segments} - set(clips))
    if missing:
        raise AltocueError(f"The cut names clips that are not in this project: {', '.join(missing)}.", fix="Run `altocue ingest`, then `altocue cut`.")

    edits = {} if force else hand_set(project, edl)
    punch = float(project.setting("frame", "punch_in", PUNCH_ZOOM))
    push = float(project.setting("frame", "push_in", 0.0))
    push_rate = _number(project, "push_rate", 0.0)
    hold = min_hold_of(project)
    mode = str(project.setting("frame", "follow", "auto"))
    if mode not in follow.MODES:
        raise AltocueError(f"[frame] follow is {mode!r}.", fix=f"Use one of {', '.join(follow.MODES)}.")
    phrases = project.setting("frame", "emphasis", [])
    if not isinstance(phrases, list) or not all(isinstance(phrase, str) for phrase in phrases):
        raise AltocueError("[frame] emphasis is not a list of spoken phrases.", fix='Write it as emphasis = ["the line that matters"].')
    segments = edl.segments
    words = {name: _words(project, name) for name in {s.clip for s in segments}}
    key_lines, unheard = emphasised(phrases, segments, words)

    sample_keys = [[(s.clip, round(t, 3)) for t in _sample_times(s)] for s in segments]
    edges = _edge_times(segments, edl.fps)
    faces = _faces_at(clips, [key for keys in sample_keys for key in keys] + [(segments[i].clip, at) for i, pair in edges.items() for at in pair])
    shows = {
        i: jump_shows(faces[(segments[i].clip, out)], faces[(segments[i].clip, back)], crop_size(clips[segments[i].clip].display_size, target)[0])
        for i, (out, back) in edges.items()
    }
    inserts = insert_spans(project, segments, edl.fps, words)
    tight, joins = plan_shots(segments, edl.fps, min_hold=hold, words=words, removed=edl.removed, inserts=inserts, shows=shows, emphasis=key_lines)

    tracked = [find_tracks([faces[key] for key in keys]) for keys in sample_keys]
    found = [choose_subject(tracks, *crop_size(clips[s.clip].display_size, target)) for s, tracks in zip(segments, tracked, strict=True)]
    subjects = hold_through_gaps(segments, found)
    last_crop: dict[str, list[float]] = {}
    shots: list[Shot] = []
    for segment, wanted, subject, direct, tracks in zip(segments, tight, subjects, found, tracked, strict=True):
        source = clips[segment.clip].display_size
        size = crop_size(source, target)
        moving, seen, moved = None, None, False
        if matches_aspect(source, target):
            crop = None
            frame_box = [0.0, 0.0, float(source[0]), float(source[1])]
        else:
            frame_box = settle_crop(last_crop.get(segment.clip), subject, source, size)
            # This segment's own faces, not a subject held over from a neighbour: an empty shot has nothing to follow.
            moved = bool(direct and direct.spread > DEAD_ZONE * frame_box[2])
            sizeable = [t for t in tracks if t.area >= tracks[0].area * SECOND_FACE_AREA] if tracks else []
            crowded = bool(direct) and len(sizeable) > direct.faces  # type: ignore[union-attr]
            if follow.needed(mode, moved, crowded):
                times = follow.sample_times(segment)
                voices = follow.speaking(words[segment.clip], times)
                moving, seen = follow.solve(follow.look_at(clips[segment.clip], times), voices, source, size, segment.start)
            if moving:
                frame_box = [moving[0][1], moving[0][2], float(size[0]), float(size[1])]
                last_crop[segment.clip] = [moving[-1][1], moving[-1][2], float(size[0]), float(size[1])]
            else:
                last_crop[segment.clip] = frame_box
            crop = frame_box
        shot = Shot(crop, frame_box, subject, direct, wanted, moving=moving, seen=seen, moved=moved)
        shot.level = safe_zoom(frame_box, subject, punch, eye_origin(frame_box, shot.eyes)) if wanted else 1.0
        shots.append(shot)

    # One size for a run of tight shots: where one of them had to back off to keep its subject in, all of them do.
    for index, shot in enumerate(shots):
        if shot.tight and index and shots[index - 1].tight and segments[index].clip == segments[index - 1].clip:
            shot.level = min(shot.level, shots[index - 1].level)
    for index in range(len(shots) - 2, -1, -1):
        if shots[index].tight and shots[index + 1].tight and segments[index].clip == segments[index + 1].clip:
            shots[index].level = shots[index + 1].level

    # A source without the pixels for the punch-in loses it for the whole clip, so its shots stay one sharpness.
    dropped: dict[str, float] = {}
    for segment, shot in zip(segments, shots, strict=True):
        enlarged = target[0] * shot.level / shot.box[2]
        if shot.level > 1 and enlarged > TOO_SOFT:
            dropped[segment.clip] = max(dropped.get(segment.clip, 0.0), enlarged)
    for segment, shot in zip(segments, shots, strict=True):
        if segment.clip in dropped:
            shot.level = 1.0
    place = {segment.id: index for index, segment in enumerate(segments)}
    for join in joins:
        index = place[join["after"]]
        if join["kind"] == "toggle" and shots[index].level == shots[index - 1].level:  # the plan asked for a new size and none came
            join |= settle_join(join, "the punch-in was dropped on this clip" if segments[index].clip in dropped else "no punch-in fits this shot")

    if push_rate > 0:
        moves = plan_push(segments, edl.fps, [round(shot.level, 4) for shot in shots], push_rate)
    else:
        moves = [(round(shot.level, 4), round(shot.level + push, 4), None) for shot in shots]

    rows = []
    soft: dict[str, float] = {}
    for segment, shot, (solved_from, solved_to, ease) in zip(segments, shots, moves, strict=True):
        hand = edits.get(segment.id, {})
        if "crop" in hand and not fits_shape(hand["crop"], target):
            hand = {name: value for name, value in hand.items() if name != "crop"}
        segment.crop = hand.get("crop", shot.crop)
        segment.path = None if "crop" in hand else shot.moving
        segment.zoom_from, segment.zoom_to = hand.get("zoom", (solved_from, solved_to))
        segment.ease = None if "zoom" in hand else ease
        box = list(fit_crop(clips[segment.clip].display_size, target, segment.crop)) if "crop" in hand else shot.box
        segment.origin = None if segment.path else eye_point(shot.subject)
        segment.held = sorted(hand)
        enlarged = round(target[0] * max(segment.zoom_from, segment.zoom_to) / box[2], 3)
        if enlarged > SOFT:
            soft[segment.clip] = max(soft.get(segment.clip, 0.0), enlarged)
        rows.append(
            {
                "id": segment.id,
                "clip": segment.clip,
                "start": segment.start,
                "end": segment.end,
                "crop": shot.crop,
                "zoom_from": solved_from,
                "zoom_to": solved_to,
                "origin": eye_point(shot.eyes),
                "upscale": enlarged,
                "faces": shot.direct.faces if shot.direct else 0,
                "moved": shot.moved,
                "path": shot.moving,
                "follow": shot.seen,
            }
        )
    if edl.to_dict() != before:  # every render frames first, and a rewrite with nothing new made a finished film look older than its cut
        project.save_edl(edl)
    warnings = softness(target, clips, soft, dropped)
    warnings += [f'Nobody says "{phrase}" in this cut, so [frame] emphasis found no beat for it.' for phrase in unheard]
    report = {
        "fingerprint": stamp,
        "target": list(target),
        "min_hold": hold,
        "upscale": {
            "warn_over": SOFT,
            "drop_over": TOO_SOFT,
            "worst": max((row["upscale"] for row in rows), default=0.0),
            "soft": [row["id"] for row in rows if row["upscale"] > SOFT],
            "punch_dropped": sorted(dropped),
        },
        "bare_jumps": [{name: join[name] for name in ("at", "stamp", "before", "after", "why")} for join in joins if join["kind"] == "bare"],
        "warnings": warnings,
        "joins": joins,
        "segments": rows,
    }
    write_json(path, report)
    return edl


def _words(project: Project, clip_id: str) -> list:
    """A clip's transcript, when it has one. Without one the crop follows the biggest face, and beats go by what was cut."""
    try:
        return project.words(clip_id)
    except AltocueError:
        return []


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--force", action="store_true", help="Solve again, replacing hand edits")


FACELESS_WARN = 2.0  # seconds of a moving crop with no face before frame says to look
BARE_SHOWN = 8


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    edl = frame_project(project, force=args.force)
    report = _read(_report_path(project))
    rows = {row["id"]: row for row in report.get("segments", [])}
    for segment in edl.segments:
        note = "no crop needed" if segment.crop is None else "crop " + " ".join(str(int(v)) for v in segment.crop)
        row = rows.get(segment.id, {})
        faces = row.get("faces", 0)
        extra = f", zoom {segment.zoom_from:g}" if segment.zoom_from != 1 else ""
        seen = row.get("follow") or {}
        if row.get("path"):
            cuts = seen.get("switches", 0)
            extra += f", crop follows {'the speaker' if seen.get('voices') else 'the head'}" + (f" with {cuts} cut{'' if cuts == 1 else 's'}" if cuts else "")
            if seen.get("faceless", 0) >= FACELESS_WARN:
                extra += f", {seen['faceless']:g} s with no face found: check it"  # the crop takes the centre there
        elif row.get("moved"):
            extra += ", head moved past the dead zone"
        if row.get("upscale", 0) > SOFT:
            extra += f", enlarged {row['upscale']:.2f} times"
        print(f"{segment.id} ({segment.clip}): {note}, {faces} face{'' if faces == 1 else 's'}{extra}")
    print(f"Framed {len(edl.segments)} segments for {project.setting('output', 'aspect')}.")
    for warning in report.get("warnings", []):
        print(f"Warning: {warning}")
    bare = report.get("bare_jumps", [])
    if bare:
        shown = "; ".join(f"{jump['stamp']} ({jump['before']} to {jump['after']}, {jump['why']})" for jump in bare[:BARE_SHOWN])
        more = f"; and {len(bare) - BARE_SHOWN} more" if len(bare) > BARE_SHOWN else ""
        print(f"{len(bare)} jump cut{'' if len(bare) == 1 else 's'} left bare, the same size on both sides: {shown}{more}.")
        print("Cover each with an insert (`altocue broll`), or keep what was cut there. They are listed in work/frame.json.")
    return 0
