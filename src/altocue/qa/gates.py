"""The fact gates. Each one measures the rendered file and reports. None of them changes it."""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import ffmpeg
from ..errors import AltocueError
from ..models import Clip, Edl, GateResult, Word
from ..project import Project
from . import faces, ocr, rules

NOT_APPLICABLE = "n/a"

LOUDNESS_TOLERANCE = 1.0
PEAK_TOLERANCE = 0.5  # the limiter aims under the ceiling; AAC may overshoot a little
BLACK_FILTER = "blackdetect=d=0.01:pix_th=0.03"  # d under one frame at 60 fps, so a single black frame shows
FREEZE_FILTER = "freezedetect=n=-60dB:d=0.5"
SILENCE_DB = -45
SILENCE_SECONDS = 1.5
CUT_WINDOW = 0.6
CUT_FLOOR_DB = -40.0
CUT_PERCENTILE = 85  # at least 90 ms of the 600 ms window must carry sound
WORDS_NEEDED = 0.95
CAPTIONS_NEEDED = 0.98
REPEAT_LONGEST = 8
OCR_FPS = 4.0
TEXT_SAMPLES = 16
FACE_SAMPLES = 8
TEXT_MATCH = 0.6
FACE_OVERLAP = 0.2
GRAPHIC_OPAQUE = 170  # text and panels. The soft scrim behind a hook stays under this
GRAPHIC_FACE_SHARE = 0.06
INSERT_MOST = 0.4
NO_TIMELINE = "No timeline.json, so what should be on screen is unknown."
INSERT_NOT_BEFORE = 2.0
EDGE_SLACK = 0.005
GRAB_BACKOFF = (0.0, 0.1, 0.5, 1.0)

SILENT_LABELS = ("broll", "b-roll", "silent", "no speech")
HOLD_LABELS = ("hold", "freeze", "still")
HOLD_ITEMS = {"title", "endcard"}

TALL_ZONE = {"left": 0.05, "right": 0.92, "top": 0.09, "bottom": 0.82}
FLAT_ZONE = {"left": 0.04, "right": 0.96, "top": 0.05, "bottom": 0.95}


def skipped(gate: str, reason: str, threshold: Any = None) -> GateResult:
    """Could not run. This is never a pass."""
    return GateResult(id=gate, passed=False, skipped=True, threshold=threshold, detail=reason)


def not_applicable(gate: str, reason: str) -> GateResult:
    """Nothing to check here, and a reader can tell that from a gate that could not run."""
    return GateResult(id=gate, passed=False, skipped=True, value=NOT_APPLICABLE, detail=reason)


def stamp(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


def spans(ranges: list[tuple[float, float]]) -> str:
    return ", ".join(f"{stamp(a)}-{stamp(b)}" for a, b in ranges[:6]) + (f" and {len(ranges) - 6} more" if len(ranges) > 6 else "")


@dataclass
class Shot:
    start: float
    end: float
    label: str = ""

    def marked(self, words: tuple[str, ...]) -> bool:
        """The whole label must be the mark. Labels often quote speech, and speech says "still"."""
        return self.label.strip().lower() in words


@dataclass
class Context:
    """What every gate reads: one probe, one transcript, one set of text boxes."""

    project: Project
    file: Path
    stage: str
    clip: Clip
    probe: dict[str, Any]
    edl: Edl | None
    timeline: dict[str, Any] | None
    rules: rules.Rules | None
    ocr_fps: float = OCR_FPS
    _heard: tuple[list[Word] | None, str] | None = None
    _seen: dict[str, Any] | None = None
    _samples: tuple[np.ndarray, int] | None = field(default=None, repr=False)

    @classmethod
    def open(cls, project: Project, file: str | Path, stage: str = "final", *, rules_path: str | Path | None = None) -> Context:
        target = Path(file)
        if not target.is_file():
            raise AltocueError(f"No file at {target}.", fix="Render it with `altocue render`, then `altocue master`.")
        edl = project.edl() if project.edl_path.exists() else None
        timeline = None
        if project.timeline_path.exists():
            timeline = json.loads(project.timeline_path.read_text())
        return cls(
            project=project,
            file=target,
            stage=stage,
            clip=ffmpeg.probe_clip(target),
            probe=ffmpeg.probe_json(target),
            edl=edl,
            timeline=timeline,
            rules=rules.load(rules_path or project.root / "rules.json"),
        )

    @property
    def plate(self) -> Path | None:
        """The picture before graphics. A caption over the eyes hides a face from any detector."""
        path = self.project.work / ("plate-draft.mp4" if self.stage == "draft" else "plate.mp4")
        return path if path.exists() else None

    @property
    def video(self) -> dict[str, Any]:
        return next(s for s in self.probe["streams"] if s.get("codec_type") == "video")

    @property
    def audio(self) -> dict[str, Any] | None:
        return next((s for s in self.probe["streams"] if s.get("codec_type") == "audio"), None)

    @property
    def fps(self) -> float:
        if self.edl:
            return float(self.edl.fps)
        if self.timeline and self.timeline.get("fps"):
            return float(self.timeline["fps"])
        return self.clip.fps or 30.0

    @property
    def shots(self) -> list[Shot]:
        """Each kept segment in output seconds, rounded to frames as the render rounds them."""
        if not self.edl:
            return []
        frames, shots = 0, []
        for segment in self.edl.segments:
            length = max(1, round((segment.end - segment.start) * self.fps))
            shots.append(Shot(frames / self.fps, (frames + length) / self.fps, segment.label))
            frames += length
        return shots

    @property
    def tail(self) -> float:
        return float(self.timeline.get("tail", 0)) if self.timeline else 0.0

    @property
    def footage_end(self) -> float:
        """Where the edit ends. An end card after this may be flat and dark on purpose."""
        if self.timeline and self.timeline.get("duration"):
            return min(float(self.timeline["duration"]), self.clip.duration)
        return self.clip.duration - self.tail

    def planned(self, labels: tuple[str, ...], items: set[str] | None = None) -> list[tuple[float, float]]:
        found = [(s.start, s.end) for s in self.shots if s.marked(labels)]
        for item in (self.timeline or {}).get("items", []):
            if items and item.get("type") in items:
                found.append((float(item["start"]), float(item["end"])))
        return found

    def inserts(self) -> list[tuple[float, float]]:
        """B-roll is someone else's picture, so a calm or dark shot is not a fault in the edit."""
        return [(float(i["start"]), float(i["end"])) for i in (self.timeline or {}).get("inserts", [])]

    def unplanned(self, ranges: list[tuple[float, float]], planned: list[tuple[float, float]]) -> list[tuple[float, float]]:
        slack = 1 / self.fps
        return [(a, b) for a, b in ranges if not any(a >= p - slack and b <= q + slack for p, q in planned)]

    def samples(self) -> tuple[np.ndarray, int]:
        """The decoded sound as mono floats. Read once, shared by every audio gate."""
        if self._samples is None:
            with tempfile.TemporaryDirectory(prefix="altocue-qa-") as folder:
                self._samples = ffmpeg.read_wav(ffmpeg.extract_wav(self.file, Path(folder) / "sound.wav", rate=16000))
        return self._samples

    def heard(self) -> tuple[list[Word] | None, str]:
        if self._heard is None:
            language = str(self.project.setting("transcribe", "language", "en"))
            words, reason = transcribe(self.file, language)
            self._heard = ([self.project.corrected(w) for w in words] if words is not None else None, reason)
        return self._heard

    def spoken(self) -> list[dict[str, Any]] | None:
        """Words with output times: what the render says, or failing that what the cut mapped."""
        words, _ = self.heard()
        if words is not None:
            return [{"text": w.text, "start": w.start, "end": w.end} for w in words]
        if self.edl:
            mapped, _ = from_cut("map_words", self.project, self.edl)
            return mapped
        return None


def transcribe(path: str | Path, language: str = "en") -> tuple[list[Word] | None, str]:
    """A missing transcriber makes the word gates skip. It can never make them pass."""
    try:
        module = importlib.import_module("altocue.transcribe")
    except ImportError:
        return None, "The transcribe stage is not available, so nothing was re-transcribed."
    if not hasattr(module, "transcribe_file"):
        return None, "The transcribe stage has no way to read a rendered file."
    try:
        return module.transcribe_file(path, language=language), ""
    except AltocueError as error:
        return None, str(error)


def transcript_record(digest: str, words: list[Word] | None, reason: str) -> dict[str, Any]:
    """What was heard from one exact file, so the pack need not transcribe it again."""
    heard = [{"text": w.text, "start": w.start, "end": w.end} for w in words or []]
    return {
        "sha256": digest,
        "available": words is not None,
        "reason": reason,
        "source": "re-transcribed from the rendered file",
        "words": heard,
    }


def from_cut(name: str, project: Project, edl: Edl) -> tuple[Any, str]:
    try:
        return getattr(importlib.import_module("altocue.cut"), name)(project, edl), ""
    except ImportError:
        return None, "The cut stage is not available, so the expected words are unknown."
    except AltocueError as error:
        return None, str(error)


def grab(file: Path, at: float, dest: Path, *, width: int | None = None) -> Path | None:
    """One frame, or None. Seeking past the last picture fails, and sound can outlast picture."""
    for back in GRAB_BACKOFF:
        try:
            ffmpeg.frame(file, max(at - back, 0.0), dest, width=width)
        except AltocueError:
            continue
        if dest.exists():
            return dest
    return None


def log_of(args: list[str]) -> str:
    return ffmpeg.run(args).stderr


def gate_loudness(c: Context) -> list[GateResult]:
    if c.stage == "draft":
        return [not_applicable(g, "Drafts are not mastered, so this waits for the final.") for g in ("loudness", "true_peak")]
    target = float(c.project.setting("audio", "loudness", -14.0))
    ceiling = float(c.project.setting("audio", "true_peak", -1.5)) + PEAK_TOLERANCE
    band = f"{target:g} LUFS +/- {LOUDNESS_TOLERANCE:g}"
    try:
        had_sound = any(clip.has_audio for clip in c.project.clips())
    except AltocueError:
        had_sound = True
    if c.audio is None:
        if not had_sound:
            return [not_applicable(g, "No clip in this project has sound.") for g in ("loudness", "true_peak")]
        return [GateResult(id=g, passed=False, detail="The file has no audio stream.") for g in ("loudness", "true_peak")]
    try:
        measured = ffmpeg.loudness(c.file)
    except AltocueError:
        if not had_sound:
            return [not_applicable(g, "No clip in this project has sound.") for g in ("loudness", "true_peak")]
        why = "The sound track measures as silent."
        return [
            GateResult(id="loudness", passed=False, threshold=band, detail=why),
            GateResult(id="true_peak", passed=False, threshold=ceiling, detail=why),
        ]
    level, peak = measured["integrated"], measured["true_peak"]
    return [
        GateResult(
            id="loudness",
            passed=abs(level - target) <= LOUDNESS_TOLERANCE,
            value=level,
            threshold=band,
            detail=f"{level:.1f} LUFS, range {measured['range']:.1f} LU",
        ),
        GateResult(
            id="true_peak", passed=peak <= ceiling, value=peak, threshold=ceiling, detail=f"{peak:.1f} dBTP measured on the decoded file"
        ),
    ]


def black_runs(file: Path, end: float) -> list[tuple[float, float]]:
    log = log_of(["-t", f"{end:.3f}", "-i", str(file), "-vf", BLACK_FILTER, "-an", "-f", "null", "-"])
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]


def frozen_runs(file: Path, end: float) -> list[tuple[float, float]]:
    log = log_of(["-t", f"{end:.3f}", "-i", str(file), "-vf", FREEZE_FILTER, "-an", "-f", "null", "-"])
    starts = [float(v) for v in re.findall(r"freeze_start: ([\d.]+)", log)]
    ends = [float(v) for v in re.findall(r"freeze_end: ([\d.]+)", log)]
    return [(start, ends[i] if i < len(ends) else end) for i, start in enumerate(starts)]


def silent_runs(file: Path, end: float) -> list[tuple[float, float]]:
    log = log_of(
        ["-t", f"{end:.3f}", "-i", str(file), "-af", f"silencedetect=n={SILENCE_DB}dB:d={SILENCE_SECONDS}", "-vn", "-f", "null", "-"]
    )
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", log)]
    return [(max(start, 0.0), ends[i] if i < len(ends) else end) for i, start in enumerate(starts)]


def gate_black(c: Context) -> GateResult:
    bad = c.unplanned(black_runs(c.file, c.footage_end), [*c.planned(HOLD_LABELS + ("black",)), *c.inserts()])
    return GateResult(
        id="black_frames",
        passed=not bad,
        value=len(bad),
        threshold=0,
        detail=f"black at {spans(bad)}" if bad else "no black frames in the edit",
    )


def gate_frozen(c: Context) -> GateResult:
    bad = c.unplanned(frozen_runs(c.file, c.footage_end), [*c.planned(HOLD_LABELS, HOLD_ITEMS), *c.inserts()])
    return GateResult(
        id="frozen_frames",
        passed=not bad,
        value=len(bad),
        threshold=0,
        detail=f"frozen at {spans(bad)}" if bad else "no frozen picture in the edit",
    )


def count_frames(file: Path) -> int:
    done = subprocess.run(
        [
            ffmpeg.require("ffprobe"),
            "-v",
            "error",
            "-count_packets",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_packets",
            "-of",
            "csv=p=0",
            str(file),
        ],
        capture_output=True,
        text=True,
    )
    return int(done.stdout.strip().strip(",") or 0)


def gate_duration(c: Context) -> GateResult:
    if not c.edl:
        return skipped("duration", "No edl.json, so there is nothing to compare against.")
    fps = c.fps
    want = sum(round((s.end - s.start) * fps) for s in c.edl.segments) + round(c.tail * fps)
    allow = max(1, len(c.edl.segments))
    got = count_frames(c.file)
    problems = [] if abs(got - want) <= allow else [f"{got} frames, the edit implies {want}"]
    if c.audio is not None and c.audio.get("duration"):
        drift = abs(float(c.audio["duration"]) - c.clip.duration)
        if drift > 0.25:
            problems.append(f"sound and picture lengths differ by {drift:.2f}s")
    return GateResult(
        id="duration",
        passed=not problems,
        value=got,
        threshold=f"{want} +/- {allow} frames",
        detail="; ".join(problems) or f"{got} frames, the edit implies {want}",
    )


def opening_level(window: np.ndarray, rate: int) -> float:
    """Sound held for a moment, in dB. The tail of the word before the cut lasts a few ms and must not count."""
    frame = rate // 50
    count = len(window) // frame
    if count == 0:
        return -99.0
    rms = np.sqrt(np.mean(window[: count * frame].reshape(count, frame) ** 2, axis=1))
    return 20 * float(np.log10(np.percentile(rms, CUT_PERCENTILE) + 1e-9))


def gate_cut_sound(c: Context) -> GateResult:
    if not c.edl:
        return skipped("cut_sound", "No edl.json, so the cuts are unknown.")
    if c.audio is None:
        return not_applicable("cut_sound", "The file has no audio stream.")
    samples, rate = c.samples()
    quiet: list[str] = []
    levels: list[float] = []
    for shot in c.shots:
        if shot.marked(SILENT_LABELS):
            continue
        level = opening_level(samples[int(shot.start * rate) : int((shot.start + CUT_WINDOW) * rate)], rate)
        levels.append(level)
        if level < CUT_FLOOR_DB:
            quiet.append(f"{stamp(shot.start)} ({level:.0f} dB)")
    detail = f"quiet openings at {', '.join(quiet)}" if quiet else f"{len(levels)} shot openings, quietest {min(levels, default=0):.1f} dB"
    return GateResult(id="cut_sound", passed=not quiet, value=len(quiet), threshold=CUT_FLOOR_DB, detail=detail)


def gate_silence(c: Context) -> GateResult:
    if c.audio is None:
        return not_applicable("silence", "The file has no audio stream.")
    bad = c.unplanned(silent_runs(c.file, c.footage_end), c.planned(SILENT_LABELS))
    longest = max((b - a for a, b in bad), default=0.0)
    return GateResult(
        id="silence",
        passed=not bad,
        value=round(longest, 2),
        threshold=SILENCE_SECONDS,
        detail=f"unplanned silence at {spans(bad)}" if bad else "no unplanned silence",
    )


def norm(words: list[str]) -> list[str]:
    return [token for word in words for token in rules.tokens(word)]


def distance(ref: list[str], hyp: list[str]) -> int:
    previous = list(range(len(hyp) + 1))
    for i, left in enumerate(ref, 1):
        current = [i]
        for j, right in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def match_rate(expected: list[str], heard: list[str]) -> float:
    """One minus the word error rate, over lowercased words, floored at zero."""
    ref, hyp = norm(expected), norm(heard)
    if not ref:
        return 1.0 if not hyp else 0.0
    return max(0.0, 1 - distance(ref, hyp) / len(ref))


def doubled_phrases(words: list[str], allow: list[str] | None = None) -> list[str]:
    """Runs of two or more words said twice in a row. One word repeated is often just emphasis."""
    allowed = {" ".join(rules.tokens(a)) for a in allow or []}
    found: list[str] = []
    i = 0
    while i < len(words):
        for size in range(min(REPEAT_LONGEST, (len(words) - i) // 2), 1, -1):
            run = words[i : i + size]
            if run == words[i + size : i + 2 * size] and len(set(run)) > 1:
                phrase = " ".join(run)
                if phrase not in allowed:
                    found.append(phrase)
                i += size - 1
                break
        i += 1
    return found


def word_gates(c: Context) -> list[GateResult]:
    expected, why_not = (None, "No edl.json, so the expected words are unknown.")
    if c.edl:
        expected, why_not = from_cut("expected_words", c.project, c.edl)
    captioned, _ = from_cut("caption_words", c.project, c.edl) if c.edl else (None, "")
    heard, why_deaf = c.heard()
    results: list[GateResult] = []

    if heard is None:
        results.append(skipped("words", why_deaf, WORDS_NEEDED))
        results.append(skipped("doubled_phrase", why_deaf))
    else:
        tokens = norm([w.text for w in heard])
        if expected is None:
            results.append(skipped("words", why_not, WORDS_NEEDED))
        else:
            rate = match_rate(expected, tokens)
            results.append(
                GateResult(
                    id="words",
                    passed=rate >= WORDS_NEEDED,
                    value=round(rate, 4),
                    threshold=WORDS_NEEDED,
                    detail=f"{rate:.1%} of {len(norm(expected))} expected words",
                )
            )
        doubled = doubled_phrases(tokens, c.rules.allow_repeats if c.rules else None)
        results.append(
            GateResult(
                id="doubled_phrase",
                passed=not doubled,
                value=len(doubled),
                threshold=0,
                detail=f"said twice: {'; '.join(doubled[:5])}" if doubled else "no phrase said twice in a row",
            )
        )

    if c.timeline is None:
        results.append(skipped("captions_match", NO_TIMELINE, CAPTIONS_NEEDED))
    elif not c.timeline.get("captions"):
        results.append(not_applicable("captions_match", "This project has no captions."))
    else:
        reference = captioned if captioned is not None else ([w.text for w in heard] if heard is not None else None)
        if reference is None:
            results.append(skipped("captions_match", why_not, CAPTIONS_NEEDED))
        else:
            shown = [w["text"] for page in c.timeline["captions"] for w in page["words"]]
            rate = match_rate(reference, shown)
            results.append(
                GateResult(
                    id="captions_match",
                    passed=rate >= CAPTIONS_NEEDED,
                    value=round(rate, 4),
                    threshold=CAPTIONS_NEEDED,
                    detail=f"{rate:.1%} of {len(norm(reference))} words",
                )
            )
    return results


def zone_for(c: Context) -> dict[str, float]:
    if c.rules and c.rules.safe_zone:
        return c.rules.safe_zone
    width, height = c.project.size
    return TALL_ZONE if height > width else FLAT_ZONE


def sightings(c: Context) -> dict[str, Any]:
    """Find each planned string in the real picture, once, and note the faces beside it."""
    if c._seen is not None:
        return c._seen
    if c.timeline is None:
        c._seen = {"reason": NO_TIMELINE}
        return c._seen
    strings = rules.on_screen(c.timeline)
    if not strings:
        c._seen = {"empty": True}
        return c._seen
    chosen, reason = ocr.reader()
    if chosen is None:
        c._seen = {"reason": reason}
        return c._seen
    picks = strings if len(strings) <= TEXT_SAMPLES else [strings[i * len(strings) // TEXT_SAMPLES] for i in range(TEXT_SAMPLES)]
    boxes: list[dict[str, Any]] = []
    found_faces: list[dict[str, Any]] = []
    can_see_faces = faces.model_path() is not None
    with tempfile.TemporaryDirectory(prefix="altocue-qa-") as folder:
        shots: dict[Path, dict[str, Any]] = {}
        for index, item in enumerate(picks):
            at = min((item["start"] + item["end"]) / 2, max(c.clip.duration - 0.05, 0.0))
            grabbed = grab(c.file, at, Path(folder) / f"text_{index:04d}.jpg")
            if grabbed:
                shots[grabbed] = {**item, "at": at}
        by_file: dict[str, list[dict[str, Any]]] = {}
        for record in ocr.read_images(list(shots)):
            by_file.setdefault(record["file"], []).append(record)
        read = 0
        for path, item in shots.items():
            wanted = set(rules.tokens(item["text"]))
            hits = []
            for record in by_file.get(str(path), []):
                said = rules.tokens(record["text"])
                if said and sum(len(t) for t in said) >= 3 and len(wanted.intersection(said)) / len(said) >= TEXT_MATCH:
                    hits.append(record)
            read += bool(hits)
            for record in hits:
                boxes.append({"text": record["text"], "box": record["box"], "at": item["at"], "layer": item["layer"], "frame": path})
            if hits and can_see_faces and len(found_faces) < FACE_SAMPLES:
                clear = path
                if c.plate and item["at"] < c.footage_end:
                    clear = grab(c.plate, item["at"], Path(folder) / f"plate_{path.name}") or path
                found_faces.append({"at": item["at"], "boxes": faces.find(clear), "hits": [r["box"] for r in hits], "layer": item["layer"]})
    c._seen = {"boxes": boxes, "read": read, "total": len(picks), "faces": found_faces, "can_see_faces": can_see_faces}
    return c._seen


def gate_safe_zone(c: Context) -> GateResult:
    seen = sightings(c)
    if seen.get("empty"):
        return not_applicable("safe_zone", "Nothing on screen to place.")
    if "reason" in seen:
        return skipped("safe_zone", seen["reason"])
    if not seen["read"]:
        return skipped("safe_zone", f"Could not read any of the {seen['total']} sampled strings in the picture.")
    zone = zone_for(c)
    outside = []
    for hit in seen["boxes"]:
        x, y, w, h = hit["box"]
        if (
            x < zone["left"] - EDGE_SLACK
            or x + w > zone["right"] + EDGE_SLACK
            or y < zone["top"] - EDGE_SLACK
            or y + h > zone["bottom"] + EDGE_SLACK
        ):
            outside.append(f"{hit['layer']} at {stamp(hit['at'])}")
    detail = (
        f"outside the safe zone: {', '.join(sorted(set(outside))[:6])}"
        if outside
        else f"read {seen['read']} of {seen['total']} sampled strings, all inside"
    )
    return GateResult(id="safe_zone", passed=not outside, value=len(outside), threshold=zone, detail=detail)


def overlap(a: list[float], b: list[float]) -> float:
    """Shared area as a share of the smaller box."""
    wide = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    tall = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if wide <= 0 or tall <= 0:
        return 0.0
    return wide * tall / max(min(a[2] * a[3], b[2] * b[3]), 1e-9)


def gate_face_clear(c: Context) -> GateResult:
    seen = sightings(c)
    if seen.get("empty"):
        return not_applicable("face_clear", "Nothing on screen to place.")
    if "reason" in seen:
        return skipped("face_clear", seen["reason"])
    if not seen["can_see_faces"]:
        return skipped("face_clear", "The face model is missing, so captions were not checked against faces.")
    if not seen["read"]:
        return skipped("face_clear", f"Could not read any of the {seen['total']} sampled strings in the picture.")
    covered = sorted(
        {
            f"{s['layer']} at {stamp(s['at'])}"
            for s in seen["faces"]
            if any(overlap(hit, face) >= FACE_OVERLAP for hit in s["hits"] for face in s["boxes"])
        }
    )
    with_faces = sum(1 for s in seen["faces"] if s["boxes"])
    detail = f"text over a face: {', '.join(covered[:6])}" if covered else f"{with_faces} sampled frames had a face, none covered"
    if not c.plate:
        detail += ". No plate on disk, so faces were looked for under the graphics, where a caption can hide one"
    return GateResult(id="face_clear", passed=not covered, value=len(covered), threshold=FACE_OVERLAP, detail=detail)


def gate_inserts(c: Context) -> GateResult:
    """Too much B-roll hides the speaker, and an early insert loses the hook."""
    if c.timeline is None:
        return skipped("inserts", NO_TIMELINE, INSERT_MOST)
    inserts = c.timeline.get("inserts", [])
    if not inserts:
        return not_applicable("inserts", "No B-roll in this edit.")
    total = max(float((c.timeline or {}).get("duration", 0)), 0.001)
    share = sum(float(i["end"]) - float(i["start"]) for i in inserts) / total
    early = [stamp(float(i["start"])) for i in inserts if float(i["start"]) < INSERT_NOT_BEFORE]
    problems = [f"opens on B-roll at {', '.join(early)}"] if early else []
    if share > INSERT_MOST:
        problems.append(f"B-roll covers {share:.0%} of the edit")
    detail = "; ".join(problems) or f"B-roll covers {share:.0%} of the edit"
    return GateResult(id="inserts", passed=not problems, value=round(share, 3), threshold=INSERT_MOST, detail=detail)


def layer_frame(c: Context, at: float, dest: Path) -> bool:
    """One frame of the graphics layer, with its transparency, whichever renderer made it."""
    folder = c.project.layers_dir
    draft = c.stage == "draft"
    movie, listing = folder / ("overlay-draft.mov" if draft else "overlay.mov"), folder / "basic" / "layer.ffconcat"
    if movie.exists():
        source = ["-i", str(movie)]
    elif listing.exists():
        source = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    else:
        return False
    ffmpeg.run([*source, "-ss", f"{at:.3f}", "-frames:v", "1", "-pix_fmt", "rgba", str(dest)])
    return dest.exists()


def gate_graphics_clear(c: Context) -> GateResult:
    """Text boxes miss a title across someone's eyes. The layer's own transparency does not."""
    if c.timeline is None:
        return skipped("graphics_clear", NO_TIMELINE)
    items = [i for i in c.timeline.get("items", []) if i.get("type") not in HOLD_ITEMS]
    if not items:
        return not_applicable("graphics_clear", "No graphics over the picture.")
    if not c.plate:
        return skipped("graphics_clear", "No plate on disk, so faces could not be seen under the graphics.")
    if not faces.model_path():
        return skipped("graphics_clear", "The face model is missing.")
    covered, looked = [], 0
    with tempfile.TemporaryDirectory(prefix="altocue-qa-") as scratch:
        folder = Path(scratch)
        for item in items:
            start, end = float(item["start"]), float(item["end"])
            for at in (start + min(0.5, (end - start) / 2), (start + end) / 2):
                under, over = folder / "under.png", folder / "over.png"
                ffmpeg.frame(c.plate, at, under)
                if not under.exists() or not layer_frame(c, at, over):
                    continue
                looked += 1
                with Image.open(over) as image:
                    alpha = np.asarray(image.convert("RGBA"))[..., 3]
                height, width = alpha.shape
                for x, y, w, h in faces.find(under):
                    box = alpha[int(y * height) : int((y + h) * height) + 1, int(x * width) : int((x + w) * width) + 1]
                    if box.size and float((box > GRAPHIC_OPAQUE).mean()) > GRAPHIC_FACE_SHARE:
                        covered.append(f"{item['type']} at {stamp(at)}")
    if not looked:
        return skipped("graphics_clear", "Could not read the graphics layer.")
    names = sorted(set(covered))
    detail = f"over a face: {', '.join(names[:6])}" if names else f"{looked} frames checked, no graphic on a face"
    return GateResult(id="graphics_clear", passed=not names, value=len(names), threshold=GRAPHIC_FACE_SHARE, detail=detail)


def gate_format(c: Context) -> GateResult:
    width, height = c.project.size
    if c.stage == "draft":
        width, height = width // 2 - (width // 2) % 2, height // 2 - (height // 2) % 2
    video, problems = c.video, []
    got = c.clip.display_size
    if got != (width, height):
        problems.append(f"size {got[0]}x{got[1]}, wanted {width}x{height}")
    fps = float(c.edl.fps) if c.edl else None
    if fps is None:
        try:
            fps = float(c.project.fps)
        except AltocueError:
            fps = None
    if fps and abs(c.clip.fps - fps) > 0.01:
        problems.append(f"{c.clip.fps:.3f} fps, wanted {fps:g}")
    if c.clip.variable_frame_rate:
        problems.append("variable frame rate")
    if video.get("codec_name") != "h264":
        problems.append(f"codec {video.get('codec_name')}, wanted h264")
    if video.get("pix_fmt") != "yuv420p":
        problems.append(f"pixel format {video.get('pix_fmt')}, wanted yuv420p")
    for field_name in ("color_primaries", "color_transfer", "color_space"):
        value = video.get(field_name, "untagged")
        if value != "bt709":
            problems.append(f"{field_name} is {value}, wanted bt709")
    if video.get("color_range", "tv") not in ("tv", "unknown"):
        problems.append(f"colour range {video.get('color_range')}, wanted tv")
    if c.clip.is_hdr:
        problems.append("tagged HDR")
    if c.audio is not None and c.audio.get("codec_name") != "aac":
        problems.append(f"audio codec {c.audio.get('codec_name')}, wanted aac")
    want = f"{width}x{height}, {fps or 'any'} fps, h264, yuv420p, bt709"
    return GateResult(
        id="format",
        passed=not problems,
        value=f"{got[0]}x{got[1]} {c.clip.fps:.3f} fps {video.get('codec_name')} {video.get('pix_fmt')}",
        threshold=want,
        detail="; ".join(problems) or "matches the target",
    )


def describe(finding: dict[str, Any]) -> str:
    why = f" ({finding['why']})" if finding.get("why") else ""
    return f"{finding['kind']} {finding['term']!r} in {finding['layer']} at {stamp(finding['start'])}{why}"


def gate_rules_strings(c: Context) -> GateResult:
    if c.rules is None:
        return not_applicable("rules_strings", "No rules.json in this project.")
    if c.timeline is None:
        return skipped("rules_strings", "No timeline.json, so the on-screen strings are unknown.")
    spoken = c.spoken()
    strings = rules.on_screen(c.timeline)
    if spoken is None and c.rules.required:
        check_without_words = rules.Rules(banned=c.rules.banned, spellings=c.rules.spellings)
        found = rules.check(strings, check_without_words, [])
        if not found:
            return skipped("rules_strings", "Required lines depend on what is said, and no transcript is available.")
    else:
        found = rules.check(strings, c.rules, spoken or [])
    detail = "; ".join(describe(f) for f in found[:5]) or f"{len(strings)} on-screen strings clean"
    return GateResult(id="rules_strings", passed=not found, value=len(found), threshold=0, detail=detail)


def gate_rules_ocr(c: Context) -> GateResult:
    if c.rules is None:
        return not_applicable("rules_ocr", "No rules.json in this project.")
    chosen, reason = ocr.reader()
    if chosen is None:
        return skipped("rules_ocr", reason)
    step = 1 / c.ocr_fps
    with tempfile.TemporaryDirectory(prefix="altocue-ocr-") as folder:
        records = ocr.read_video(c.file, c.ocr_fps, [s.start for s in c.shots[1:]], Path(folder))
    hits: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        for term in rules.match_terms(record["text"], c.rules.banned):
            hits.setdefault(("banned", term), []).append(record)
        for term in rules.misspelled(record["text"], c.rules.spellings):
            hits.setdefault(("spelling", term), []).append(record)
    firm, doubtful = [], []
    for (kind, term), seen in hits.items():
        ranges = ocr.merge_ranges([h["time"] for h in seen], step)
        line = f"{kind} {term!r} seen at {spans([(a, b) for a, b in ranges])}"
        # OCR misreads a letter often enough that a spelling finding always needs a person.
        if kind == "banned" and ocr.confidence(seen) == "high":
            firm.append(line)
        else:
            doubtful.append(line)
    spoken = c.spoken()
    strings = [{"text": r["text"], "start": r["time"], "end": r["time"] + step} for r in records]
    for rule in c.rules.required:
        if spoken is None:
            doubtful.append(f"required line {rule.get('id', rule['when_spoken'])!r} not checked: no transcript")
            continue
        missing = rules.missing_required(rule, strings, spoken, overlap=True)
        if missing:
            firm.append(f"required {missing['term']!r} at {stamp(missing['start'])}: {missing['why']}")
    detail = "; ".join(firm[:5]) or f"{len(records)} text boxes read by {chosen}, clean"
    if doubtful:
        detail += f". Needs a human look: {'; '.join(doubtful[:5])}"
    return GateResult(id="rules_ocr", passed=not firm, value={"findings": len(firm), "review": len(doubtful)}, threshold=0, detail=detail)


def guarded(c: Context, ids: tuple[str, ...], measure: Callable[[Context], GateResult | list[GateResult]]) -> list[GateResult]:
    """A gate that cannot finish is skipped, and says why. QA must survive the files it exists to catch."""
    try:
        found = measure(c)
    except AltocueError as error:
        return [skipped(gate, f"Could not run: {error}") for gate in ids]
    return found if isinstance(found, list) else [found]


def run_all(c: Context) -> list[GateResult]:
    plan: list[tuple[tuple[str, ...], Callable[[Context], GateResult | list[GateResult]]]] = [
        (("loudness", "true_peak"), gate_loudness),
        (("black_frames",), gate_black),
        (("frozen_frames",), gate_frozen),
        (("duration",), gate_duration),
        (("cut_sound",), gate_cut_sound),
        (("silence",), gate_silence),
        (("words", "doubled_phrase", "captions_match"), word_gates),
        (("safe_zone",), gate_safe_zone),
        (("face_clear",), gate_face_clear),
        (("graphics_clear",), gate_graphics_clear),
        (("inserts",), gate_inserts),
        (("format",), gate_format),
        (("rules_strings",), gate_rules_strings),
        (("rules_ocr",), gate_rules_ocr),
    ]
    return [result for ids, measure in plan for result in guarded(c, ids, measure)]
