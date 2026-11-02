"""Propose the cut, check it and map the words through it.

Three layers: certain removals, candidates with evidence, and an agent's rulings on anything
marked `review`. Without rulings those stay in. Every edge snaps to real speech, lands on the
frame grid and is refused if it would cut through a word.
"""

from __future__ import annotations

import argparse
import bisect
import math
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..errors import AltocueError
from ..models import Clip, Edl, Removed, Segment, Word
from ..project import Project
from ..transcribe import MIN_WORD, RATE, Envelope
from .detect import VOICED_PAUSE, candidates, certain
from .hand import carry, hand_set
from .review import apply_rulings, covering, load_rulings, removal_id, write_report, write_review
from .script import NO_SPEECH, Key, from_script, in_source_order, play_order, write_script
from .snap import FRAME_SLACK, OVERLAP, POST, PRE, frame_count, last_full_frame, overlap, plays, snap, to_grid

__all__ = [
    "add_arguments",
    "apply_review",
    "expected_words",
    "from_script",
    "map_words",
    "propose",
    "rebuild",
    "removal_id",
    "run",
    "segment_ends",
    "validate",
    "write_script",
]

TIME_SLACK = 0.02  # ffprobe durations can sit a hair under the last sample
LABEL_WORDS = 5


@lru_cache(maxsize=64)
def _envelope(path: str, modified: float) -> Envelope:
    with tempfile.TemporaryDirectory() as folder:
        return Envelope.from_wav(ffmpeg.extract_wav(path, Path(folder) / "audio.wav", rate=RATE))


def envelope_of(clip: Clip) -> Envelope | None:
    if not clip.has_audio:
        return None
    path = Path(clip.path)
    return _envelope(str(path), path.stat().st_mtime) if path.exists() else None


def bounds(
    run: list[int], words: list[Word], clip: Clip, envelope: Envelope | None, fps: int, walls: tuple[Removed, ...] = ()
) -> tuple[float, float]:
    """Where a run of kept words starts and ends: snapped, kept clear of its neighbours, on the grid.

    `walls` are cut pauses that hold an unheard sound, such as an "um". Snapping follows sound, so
    without them an edge would slide back into what was just removed.
    """
    first, last = words[run[0]], words[run[-1]]
    floor = words[run[0] - 1].end if run[0] else 0.0
    ceiling = min(words[run[-1] + 1].start if run[-1] + 1 < len(words) else clip.duration, last_full_frame(clip.duration, fps))
    for wall in walls:
        if wall.start >= last.end - TIME_SLACK:
            ceiling = min(ceiling, wall.start + POST)
        if wall.end <= first.start + TIME_SLACK:
            floor = max(floor, wall.end - PRE)
    start, end = max(first.start - PRE, floor), min(last.end + POST, ceiling)
    if envelope is not None and envelope.reliable:
        start, end = snap(envelope, start, end, first_word_start=first.start, last_word_end=last.end)
        start = min(start, max(first.start, first.end - OVERLAP))
        end = max(end, min(last.end, last.start + OVERLAP))
    return to_grid(max(start, floor), min(end, ceiling), fps, floor=floor, ceiling=ceiling)


def assemble(project: Project, removed: list[Removed], fps: int, order: list[Key] | None = None) -> Edl:
    """Turn removal decisions into segments. Only `auto` items cut; everything else plays.

    Segments follow the recording, or `order` when something moved it.
    """
    cuts = [item for item in removed if item.decision == "auto"]
    found: list[tuple[str, list[int]]] = []
    context: dict[str, tuple[Clip, list[Word], tuple[Removed, ...], Envelope | None]] = {}
    for clip in project.clips():
        here = [item for item in cuts if item.clip == clip.id]
        words = project.words(clip.id)
        if not words:
            if not here:
                found.append((clip.id, []))
            context[clip.id] = (clip, words, (), None)
            continue
        walls = tuple(item for item in here if item.kind == "silence" and item.evidence.startswith(VOICED_PAUSE))
        context[clip.id] = (clip, words, walls, envelope_of(clip))
        found += [(clip.id, run) for run in runs(words, here)]
    segments: list[Segment] = []
    for name, run in arrange(found, order) if order else found:
        clip, words, walls, envelope = context[name]
        if not run:
            segments.append(Segment(id="", clip=name, start=0.0, end=round(last_full_frame(clip.duration, fps), 6), label="no speech"))
            continue
        start, end = bounds(run, words, clip, envelope, fps, walls)
        latest = segments[-1] if segments and segments[-1].clip == name else None
        if latest and latest.start <= start <= latest.end:
            latest.end = end  # speech filled the pause, so there was nothing to take out
        else:
            segments.append(Segment(id="", clip=name, start=start, end=end, label=" ".join(words[i].text for i in run[:LABEL_WORDS])))
    for number, segment in enumerate(segments, start=1):
        segment.id = f"s{number:03d}"
    return Edl(fps=fps, segments=segments, removed=removed)


def arrange(found: list[tuple[str, list[int]]], order: list[Key]) -> list[tuple[str, list[int]]]:
    """Put runs of kept words in play order, splitting a run wherever the order jumps.

    A word with no place in `order` has just come back, so it follows the word before it.
    """
    rank = {key: place for place, key in enumerate(order)}
    known: dict[str, list[int]] = {}
    for clip, index in sorted(key for key in rank if key[1] != NO_SPEECH):
        known.setdefault(clip, []).append(index)

    def beside(clip: str, index: int) -> float:
        placed = known.get(clip, [])
        at = bisect.bisect_left(placed, index)
        if at:
            return rank[(clip, placed[at - 1])] + 0.5
        return rank[(clip, placed[0])] - 0.5 if placed else math.inf

    pieces: list[tuple[float, str, list[int]]] = []
    for clip, run in found:
        if not run:
            pieces.append((rank.get((clip, NO_SPEECH), math.inf), clip, run))
            continue
        current: list[int] = []
        opened: float | None = None
        last: float | None = None
        for index in run:
            place = rank.get((clip, index))
            if place is not None and last is not None and place != last + 1:
                pieces.append((opened if opened is not None else beside(clip, current[0]), clip, current))
                current, opened = [], None
            if place is not None:
                opened = place if opened is None else opened
                last = place
            current.append(index)
        pieces.append((opened if opened is not None else beside(clip, current[0]), clip, current))
    return [(clip, run) for _, clip, run in sorted(pieces, key=lambda piece: piece[0])]


def runs(words: list[Word], cuts: list[Removed]) -> list[list[int]]:
    """Group kept words. A dropped word or a cut pause between two words ends a run."""
    found: list[list[int]] = []
    for index, word in enumerate(words):
        if covering(word, cuts):
            continue
        joined = (
            found
            and found[-1][-1] == index - 1
            and not any(words[index - 1].end - 1e-6 <= item.start and item.end <= word.start + 1e-6 for item in cuts)
        )
        if joined:
            found[-1].append(index)
        else:
            found.append([index])
    return found


def removals(project: Project, within: tuple[int, int] | None = None) -> list[Removed]:
    """Layers 1 and 2, in recording order. `within` looks at one stretch of words, for a clip taken from a longer talk."""
    clips = project.clips()
    words = {clip.id: project.words(clip.id)[slice(*within) if within else slice(None)] for clip in clips}
    max_pause = float(project.setting("cut", "max_pause", 0.4))
    remove_fillers = bool(project.setting("cut", "remove_fillers", True))
    found = [
        item
        for clip in clips
        for item in certain(clip, words[clip.id], max_pause=max_pause, remove_fillers=remove_fillers, envelope=envelope_of(clip))
    ]
    found += candidates(clips, words, {clip.id: envelope_of(clip) for clip in clips})
    return [item.removed() for item in sorted(found, key=lambda c: (c.clip, c.start, c.end))]


def propose(project: Project) -> Edl:
    """Nothing marked `review` is cut until someone rules on it."""
    return assemble(project, removals(project), project.fps)


def rebuild(project: Project, edl: Edl, removed: list[Removed], *, order: list[Key] | None = None) -> Edl:
    """Cut again from new decisions. What was moved stays moved, and hand edits follow their words."""
    if order is None:
        played = play_order(project, edl)
        order = None if in_source_order(project, played) else played
    fresh = assemble(project, removed, edl.fps, order)
    carry(project, edl, fresh)
    return fresh


def apply_review(project: Project, path: str | Path) -> Edl:
    """Layer 3: rule on review items and rebuild the segments from the new decisions."""
    edl = project.edl()
    return rebuild(project, edl, apply_rulings(edl, load_rulings(path)))


def remove_segments(project: Project, targets: list[str], reason: str) -> Edl:
    """Take whole segments out by hand, keeping a record of why."""
    edl = project.edl()
    known = {segment.id: segment for segment in edl.segments}
    missing = [target for target in targets if target not in known]
    if missing:
        raise AltocueError(f"No segment named {', '.join(missing)}.", fix="Segment ids are in work/cut-report.md.")
    added = [Removed(known[t].clip, known[t].start, known[t].end, "manual", reason, "auto") for t in targets]
    return rebuild(project, edl, [*edl.removed, *added])


def cuts_through(word: Word, segment: Segment, envelope: Envelope | None, tolerance: float) -> bool:
    """True when a word plays in part. Transcript edges drift, so audio decides when it can.

    A word that hangs off an edge is fine if the part left out holds no speech, or if the
    part left in holds none. With no usable audio, only the transcript times count.
    """
    inside = overlap(word, segment.start, segment.end)
    head, tail = max(0.0, segment.start - word.start), max(0.0, word.end - segment.end)
    if inside <= tolerance or head + tail <= tolerance:
        return False
    if envelope is None or not envelope.reliable:
        return True
    left_out = (head > tolerance and envelope.has_speech(word.start, segment.start)) or (
        tail > tolerance and envelope.has_speech(segment.end, word.end)
    )
    left_in = envelope.has_speech(max(word.start, segment.start), min(word.end, segment.end))
    return left_out and left_in


def validate(project: Project, edl: Edl) -> list[str]:
    """Every reason this cut must not be used. An empty list means it passes."""
    problems: list[str] = []
    clips = {clip.id: clip for clip in project.clips()}
    if not isinstance(edl.fps, int) or isinstance(edl.fps, bool) or edl.fps <= 0:
        return [f"Frame rate {edl.fps} is not a positive whole number."]
    tolerance = 1 / edl.fps + 1e-6
    spans: dict[str, list[Segment]] = {}
    for segment in edl.segments:
        clip = clips.get(segment.clip)
        if clip is None:
            problems.append(f"{segment.id}: no clip named {segment.clip}.")
            continue
        if not all(math.isfinite(v) for v in (segment.start, segment.end)) or segment.start < 0 or segment.end <= segment.start:
            problems.append(f"{segment.id}: bad range {segment.start} to {segment.end}.")
            continue
        if segment.end > clip.duration + TIME_SLACK:
            problems.append(f"{segment.id}: ends at {segment.end:.3f} s, past the end of {clip.id} at {clip.duration:.3f} s.")
        if abs((segment.end - segment.start) * edl.fps - frame_count(segment.start, segment.end, edl.fps)) > 0.01:
            problems.append(f"{segment.id}: {segment.end - segment.start:.6f} s is not a whole number of frames at {edl.fps} fps.")
        for other in spans.setdefault(clip.id, []):
            if segment.start < other.end - tolerance and other.start < segment.end - tolerance:
                problems.append(f"{segment.id}: overlaps {other.id}, so words would play twice.")
        spans[clip.id].append(segment)
    for clip in clips.values():
        problems += check_words(clip, project.words(clip.id), spans.get(clip.id, []), edl, tolerance)
    return problems


def check_words(clip: Clip, words: list[Word], segments: list[Segment], edl: Edl, tolerance: float) -> list[str]:
    problems: list[str] = []
    envelope = envelope_of(clip)
    cuts = [item for item in edl.removed if item.clip == clip.id and item.decision == "auto"]
    for word in words:
        where = f'"{word.text}" at {clip.id} {word.start:.2f} s'
        playing = [s for s in segments if plays(word, s.start, s.end, tolerance=tolerance)]
        cut = covering(word, cuts)
        for segment in segments:
            if cuts_through(word, segment, envelope, tolerance):
                problems.append(f"{segment.id}: cuts through {where}.")
        if cut and playing:
            problems.append(f"{playing[0].id}: plays {where}, which is marked as removed ({cut.kind}).")
        if not cut and not playing:
            problems.append(f"Dropped {where} with no removal to explain it.")
    return problems


def refuse(project: Project, edl: Edl) -> None:
    problems = validate(project, edl)
    if problems:
        shown = "\n".join(f"  {p}" for p in problems[:12])
        more = f"\n  and {len(problems) - 12} more" if len(problems) > 12 else ""
        raise AltocueError(
            f"Refusing this cut:\n{shown}{more}", fix="Run `altocue cut` again, or correct edl.json so every edge sits between words."
        )


def kept(project: Project, edl: Edl) -> list[tuple[Segment, Word]]:
    """Each played word once, in play order."""
    refuse(project, edl)
    tolerance = 1 / edl.fps + 1e-6
    played: list[tuple[Segment, Word]] = []
    seen: set[tuple[str, int]] = set()
    words = {clip.id: project.words(clip.id) for clip in project.clips()}
    for segment in edl.segments:
        for index, word in enumerate(words[segment.clip]):
            if not plays(word, segment.start, segment.end, tolerance=tolerance):
                continue
            if (segment.clip, index) in seen:
                raise AltocueError(
                    f'"{word.text}" at {segment.clip} {word.start:.2f} s plays twice.', fix="Remove the overlap between segments."
                )
            seen.add((segment.clip, index))
            played.append((segment, word))
    return played


def expected_words(project: Project, edl: Edl) -> list[str]:
    """The kept words in order, for checking a render against what it should say."""
    return [word.text for _, word in kept(project, edl)]


def caption_words(project: Project, edl: Edl) -> list[str]:
    """What the captions should show. A kept filler is heard, and stays out of them."""
    return [word["text"] for word in map_words(project, edl) if word.get("kind", "word") == "word"]


def segment_ends(edl: Edl) -> dict[str, float]:
    """Where each segment stops in output seconds. These are the cuts nothing on screen may hold across."""
    ends: dict[str, float] = {}
    frame = 0
    for segment in edl.segments:
        frame += frame_count(segment.start, segment.end, edl.fps)
        ends[segment.id] = round(frame / edl.fps, 3)
    return ends


def map_words(project: Project, edl: Edl) -> list[dict[str, Any]]:
    """Kept words in output seconds and frames, at the EDL's frame rate.

    Frames come from one running total of whole segments. Floors and ceilings can make
    neighbours share a frame, so each word starts where the last ended; a word that then
    no longer fits its segment is refused, never squeezed.
    """
    played = kept(project, edl)
    fps = edl.fps
    starts: dict[str, int] = {}
    frame = 0
    for segment in edl.segments:
        starts[segment.id] = frame
        frame += frame_count(segment.start, segment.end, fps)
    mapped: list[dict[str, Any]] = []
    previous: tuple[str, int] | None = None
    for segment, word in played:
        first = starts[segment.id]
        last = first + frame_count(segment.start, segment.end, fps)
        head, tail = max(word.start, segment.start) - segment.start, min(word.end, segment.end) - segment.start
        start_frame = first + math.floor(head * fps + FRAME_SLACK)
        end_frame = first + math.ceil(tail * fps - FRAME_SLACK)
        if previous and previous[0] == segment.id:
            start_frame = max(start_frame, previous[1])
        end_frame = max(end_frame, start_frame + 1)
        if end_frame > last:
            raise AltocueError(
                f'"{word.text}" does not fit {segment.id} at {fps} fps.', fix="Keep more of this segment, or use a higher frame rate."
            )
        previous = (segment.id, end_frame)
        offset = first / fps
        end = max(tail, head + MIN_WORD)
        mapped.append(
            {
                "text": word.text,
                "kind": word.kind,
                "clip": segment.clip,
                "segment": segment.id,
                "start": round(offset + head, 3),
                "end": round(offset + min(end, (last - first) / fps), 3),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "source_start": word.start,
                "source_end": word.end,
            }
        )
    return mapped


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--apply-review", metavar="DECISIONS", default=None, help="JSON rulings on work/cut-review.md items")
    parser.add_argument("--remove", nargs="+", metavar="SEGMENT", default=None, help="Take these segments out of the current cut")
    parser.add_argument("--reason", default="Removed by hand", help="Why, recorded in the cut report")
    parser.add_argument("--from-script", nargs="?", const="", default=None, metavar="FILE", help="Build the cut from an edited script.md, or from FILE")
    parser.add_argument("--write-script", action="store_true", help="Write script.md from the current cut, saving any earlier one")


def save_script(project: Project) -> Path:
    if not project.edl_path.exists():
        project.save_edl(propose(project))
    if project.script_path.exists():
        shutil.copy(project.script_path, project.work / "script-previous.md")
        print("Your earlier script is saved at work/script-previous.md.")
    return write_script(project, project.edl())


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    chosen = getattr(args, "from_script", None)
    if getattr(args, "write_script", False):
        print(f"Script: {save_script(project)}\nEdit it, then run `altocue cut --from-script`.")
        return 0
    if args.remove:
        edl = remove_segments(project, args.remove, args.reason)
    elif chosen is not None:
        if project.edl_path.exists():
            shutil.copy(project.edl_path, project.work / "edl-previous.json")
        edl = from_script(project, Path(chosen) if chosen else project.script_path)
    else:
        if not args.apply_review and project.edl_path.exists():
            shutil.copy(project.edl_path, project.work / "edl-previous.json")
            print("This is a fresh proposal. Your earlier cut and its rulings are saved at work/edl-previous.json.")
            left = sum(len(edits) for edits in hand_set(project, project.edl()).values())
            if left:
                print(f"{left} hand edit{'s stay' if left > 1 else ' stays'} in that file. `--remove`, `--apply-review` and `--from-script` keep them; this does not.")
        edl = apply_review(project, args.apply_review) if args.apply_review else propose(project)
    refuse(project, edl)
    project.save_edl(edl)
    report, review = write_report(project, edl), write_review(project, edl)
    waiting = sum(item.decision == "review" for item in edl.removed)
    print(f"Cut: {edl.duration:.2f} s in {len(edl.segments)} segments, {len(edl.removed)} entries in {project.edl_path.name}")
    print(f"Report: {report}")
    if chosen is not None:
        print("Built from the script. Words it left out are listed in the report.")
    elif project.script_path.exists():
        print("script.md is left as it was. `altocue cut --write-script` writes it again from this cut.")
    else:
        print(f"Script: {write_script(project, edl)}. Keep, delete or move its lines, then run `altocue cut --from-script`.")
    if waiting:
        print(f"{waiting} items wait for a ruling and stay in the video: {review}")
    held = sum(len(segment.held) for segment in edl.segments)
    if held:
        print(f"Kept {held} hand edit{'' if held == 1 else 's'}.")
    for note in edl.lost:
        print(f"Could not keep {note}")
    print("Next: `altocue run` rebuilds the film from this cut.")
    return 0
