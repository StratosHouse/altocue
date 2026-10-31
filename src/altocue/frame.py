"""Find faces and choose one steady crop per shot.

Crops never drift. Each EDL segment gets a fixed rectangle in display pixels, and a head
that stays inside the dead zone keeps the crop it had before. Jump cuts inside one clip
alternate between the full crop and a punch-in, which hides the jump.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median

import cv2
import numpy as np

from .errors import AltocueError
from .ffmpeg import require
from .models import Clip, Edl, Segment
from .project import Project, write_json

MODEL_PATH = Path(__file__).parent / "data" / "models" / "face_detection_yunet_2026may.onnx"

DETECT_SIDE = 640  # YuNet is trained on faces up to about 300 px, so big frames shrink first.
CLOSE_UP_SIDE = 320
MIN_SCORE = 0.6
READ_TIMEOUT = 300

PUNCH_ZOOM = 1.12
MIN_PUNCH = 1.04  # Below this a punch-in reads as a mistake, not a cut.
ZOOM_ORIGIN = (0.5, 0.42)  # Where the plate stage zooms from, as a share of the crop.
DEAD_ZONE = 0.10  # Share of the crop the subject may move before the crop follows.
EYE_LINE = 0.38
HEADROOM = 0.04
HAIR = 0.35  # The face box stops at the forehead. Hair adds about this share of its height.
MIN_TRACK_SHARE = 0.4
SECOND_FACE_AREA = 0.25
SIDE_MARGIN = 0.25
SAMPLES = (3, 6)
VERSION = 1


@dataclass(frozen=True)
class Face:
    """One detection, in pixels of the image it was found in."""

    x: float
    y: float
    w: float
    h: float
    score: float = 1.0
    eye_y: float = 0.0

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    def scaled(self, factor: float) -> Face:
        return Face(self.x * factor, self.y * factor, self.w * factor, self.h * factor, self.score, self.eye_y * factor)


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
        found.append(Face(x, y, w, h, float(row[14]), eye_y).scaled(width / size[0]))
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
        raise AltocueError(
            f"Reading {clip.path} stalled.", fix="If the file lives in iCloud, download it first, then run this again."
        ) from error
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


def safe_zoom(crop: list[float], subject: Subject | None, wanted: float) -> float:
    """The largest zoom up to `wanted` that keeps the subject in the plate's frame."""
    if subject is None or wanted <= 1.0:
        return wanted
    x, y, width, height = crop
    shares = [
        (0, ZOOM_ORIGIN[0], (subject.left - x) / width),
        (1, ZOOM_ORIGIN[0], (subject.right - x) / width),
        (0, ZOOM_ORIGIN[1], (subject.top - y) / height),
        (1, ZOOM_ORIGIN[1], (subject.bottom - y) / height),
    ]
    best = wanted
    for edge, origin, share in shares:
        share = min(max(share, 0.0), 1.0)
        if share == origin:
            continue
        limit = (edge - origin) / (share - origin)
        if limit > 0:
            best = min(best, max(limit, 1.0))
    return best if best >= MIN_PUNCH else 1.0


def plan_zooms(segments: list[Segment], fps: float) -> list[float]:
    """Alternate 1.0 and the punch-in on every jump inside a clip. Each clip starts wide."""
    zooms: list[float] = []
    punched = False
    previous: Segment | None = None
    for segment in segments:
        if previous is None or previous.clip != segment.clip:
            punched = False
        elif abs(segment.start - previous.end) > 1 / max(fps, 1):
            punched = not punched
        zooms.append(PUNCH_ZOOM if punched else 1.0)
        previous = segment
    return zooms


def _sample_times(segment: Segment) -> list[float]:
    span = max(segment.end - segment.start, 0.0)
    count = min(max(math.ceil(span * 1.5), SAMPLES[0]), SAMPLES[1])
    return [segment.start + span * (i + 0.5) / count for i in range(count)]


def _sample_faces(clips: dict[str, Clip], segments: list[Segment]) -> list[list[list[Face]]]:
    """Faces per sample per segment, in display pixels. Pixels decode in parallel, YuNet runs in turn."""
    wanted = [(s.clip, round(t, 3)) for s in segments for t in _sample_times(s)]
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
    return [[found[(s.clip, round(t, 3))] for t in _sample_times(s)] for s in segments]


def _fingerprint(project: Project, edl: Edl, target: tuple[int, int]) -> str:
    cuts = [[s.id, s.clip, s.start, s.end] for s in edl.segments]
    settings = {key: project.setting("frame", key) for key in ("punch_in", "push_in")}
    text = json.dumps({"version": VERSION, "cuts": cuts, "target": target, "settings": settings}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _report_path(project: Project) -> Path:
    return project.work / "frame.json"


def frame_project(project: Project, force: bool = False) -> Edl:
    """Write crops and zooms into the EDL. A re-run with the same cut keeps hand edits."""
    edl = project.edl()
    if not project.setting("frame", "enabled", True):
        return edl
    target = project.size
    stamp = _fingerprint(project, edl, target)
    path = _report_path(project)
    untouched = all(s.crop is None and s.zoom_from == 1.0 and s.zoom_to == 1.0 for s in edl.segments)  # a new cut starts blank
    if not force and not untouched and path.exists() and json.loads(path.read_text()).get("fingerprint") == stamp:
        return edl
    clips = {clip.id: clip for clip in project.clips()}
    missing = sorted({s.clip for s in edl.segments} - set(clips))
    if missing:
        raise AltocueError(
            f"The cut names clips that are not in this project: {', '.join(missing)}.", fix="Run `altocue ingest`, then `altocue cut`."
        )

    punch = float(project.setting("frame", "punch_in", PUNCH_ZOOM))
    push = float(project.setting("frame", "push_in", 0.0))
    zooms = plan_zooms(edl.segments, edl.fps)
    samples = _sample_faces(clips, edl.segments)
    found = [
        choose_subject(find_tracks(faces), *crop_size(clips[s.clip].display_size, target))
        for s, faces in zip(edl.segments, samples, strict=True)
    ]
    subjects = hold_through_gaps(edl.segments, found)
    last_crop: dict[str, list[float]] = {}
    rows = []
    for segment, zoom, subject, direct in zip(edl.segments, zooms, subjects, found, strict=True):
        source = clips[segment.clip].display_size
        size = crop_size(source, target)
        if matches_aspect(source, target):
            crop = None
            frame_box = [0.0, 0.0, float(source[0]), float(source[1])]
        else:
            frame_box = settle_crop(last_crop.get(segment.clip), subject, source, size)
            last_crop[segment.clip] = frame_box
            crop = frame_box
        level = safe_zoom(frame_box, subject, punch) if zoom > 1 else 1.0
        segment.crop = crop
        segment.zoom_from = round(level, 4)
        segment.zoom_to = round(level + push, 4)
        moved = bool(subject and subject.spread > DEAD_ZONE * frame_box[2])
        rows.append(
            {
                "id": segment.id,
                "clip": segment.clip,
                "crop": crop,
                "zoom_from": segment.zoom_from,
                "zoom_to": segment.zoom_to,
                "faces": direct.faces if direct else 0,
                "moved": moved,
            }
        )
    project.save_edl(edl)
    write_json(path, {"fingerprint": stamp, "target": list(target), "segments": rows})
    return edl


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--force", action="store_true", help="Solve again, replacing hand edits")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    edl = frame_project(project, force=args.force)
    rows = {}
    if _report_path(project).exists():
        rows = {row["id"]: row for row in json.loads(_report_path(project).read_text())["segments"]}
    for segment in edl.segments:
        note = "no crop needed" if segment.crop is None else "crop " + " ".join(str(int(v)) for v in segment.crop)
        row = rows.get(segment.id, {})
        faces = row.get("faces", 0)
        extra = f", zoom {segment.zoom_from:g}" if segment.zoom_from != 1 else ""
        extra += ", head moved past the dead zone" if row.get("moved") else ""
        print(f"{segment.id} ({segment.clip}): {note}, {faces} face{'' if faces == 1 else 's'}{extra}")
    print(f"Framed {len(edl.segments)} segments for {project.setting('output', 'aspect')}.")
    return 0
