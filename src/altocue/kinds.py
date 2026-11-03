"""Checks a film type carries for itself. A type's record names them under "checks".

They run inside `altocue film check`, before anything renders, and inside `altocue film qa` on the
finished master. None of them is a gate: `altocue qa` reports the same gates for every film.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from . import ffmpeg, storyboard
from .errors import AltocueError
from .project import Project
from .storyboard import Problem

PLATE_TOLERANCE = 6.0  # mean grey difference out of 255 a re-encode may leave where nothing was meant to change
PLATE_WIDTH = 192
COMP_CRF = 12  # the comp is an intermediate, so it stays close to the plate


# Plate Edit: one change to the user's own footage, comped back onto it (b029, k002, b006)


def comps(board: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every footage item that lays a change over its plate, with its scene."""
    return [(scene, item) for scene in board.get("scenes", []) for item in scene.get("items", []) if item.get("type") == "footage" and item.get("comp")]


def region_problem(region: Any) -> str | None:
    if not (isinstance(region, list) and len(region) == 4 and all(isinstance(v, int | float) and not isinstance(v, bool) for v in region)):
        return "region is [x, y, width, height] as shares of the frame, such as [0.3, 0.4, 0.4, 0.3]."
    x, y, w, h = (float(v) for v in region)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1 or y + h > 1:
        return f"region {region} is not inside the frame."
    if w * h >= 0.9:
        return "region covers the whole frame, so nothing of the plate is held still. Name the one area that changes."
    return None


def _length(path: Path) -> float | None:
    """How long the picture runs. A file's own duration can include sound that outlasts it."""
    try:
        info = ffmpeg.probe_json(path)
    except AltocueError:
        return None
    video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), None)
    return float(video.get("duration") or info.get("format", {}).get("duration") or 0) or None if video else None


def plan_plate(film: Any) -> list[Problem]:
    """Lock what stays: one change, confined to a region and a window, on a plate that does not move."""
    found: list[Problem] = []
    root = film.project.root

    def add(where: str, message: str, fix: str) -> None:
        found.append(Problem("error", where, message, fix))

    made = comps(film.board)
    if len(made) != 1:
        add("storyboard", f"A Plate Edit makes one change, and this storyboard has {len(made)}.", 'Put "comp" on the one footage item that changes.')
    for scene, item in made:
        where, comp = str(scene.get("id")), item["comp"]
        problem = region_problem(comp.get("region"))
        if problem:
            add(where, problem, "The region is where the change happens. Everything outside it must stay as shot.")
        start, end = comp.get("from"), comp.get("to")
        if not (isinstance(start, int | float) and isinstance(end, int | float) and 0 <= start < end):
            add(where, "The change needs a window: from the visible cue to the moment it is gone again.", 'Set "from" and "to" in plate seconds.')
        if comp.get("file") == item.get("file"):
            add(where, "The edit pass is the plate itself.", "Point comp.file at the generated pass. The pass is never the shot.")
        if (scene.get("camera") or {}).get("amount") != 0:
            add(where, "A camera move would shift every pixel of the plate.", 'Set "camera": {"move": "drift", "amount": 0}.')
        others = [str(other.get("type")) for other in scene.get("items", []) if other is not item]
        if others:
            add(where, f"{', '.join(others)} would draw over the plate while it must stay as shot.", "Put titles and labels in a scene of their own.")
        plate, edit = root / str(item.get("file", "")), root / str(comp.get("file", ""))
        for path, what in ((plate, "plate"), (edit, "edit pass")):
            if not path.is_file():
                add(where, f"No {what} at {path.relative_to(root) if path.is_relative_to(root) else path}.", "Keep the plate in assets/ and the pass in generated/.")
        if plate.is_file():
            (width, height), (frame_w, frame_h) = ffmpeg.probe_clip(plate).display_size, film.project.size
            if abs(width / height - frame_w / frame_h) > 0.01:
                add(where, f"The plate is {width}x{height} and the film {frame_w}x{frame_h}, so the plate would be cropped.", "Set the film's aspect to the plate's.")
        plate_seconds, edit_seconds = _length(plate), _length(edit)
        if plate_seconds and edit_seconds and abs(plate_seconds - edit_seconds) > 1 / film.project.fps:
            fix = "A source-locked pass keeps the plate's timing. Make it again from the plate."
            add(where, f"The edit pass runs {edit_seconds:.2f} s and the plate {plate_seconds:.2f} s.", fix)
        if plate_seconds and isinstance(end, int | float) and end > plate_seconds:
            add(where, f"The change ends at {end:g} s, after the plate does.", "Return to the original before the plate ends, so it cuts back into untouched footage.")
    return found


def comp_key(plate: Path, edit: Path, comp: dict[str, Any]) -> str:
    recipe = {"plate": hashlib.sha256(plate.read_bytes()).hexdigest(), "edit": hashlib.sha256(edit.read_bytes()).hexdigest(), "comp": comp}
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]


def build_comp(plate: Path, edit: Path, comp: dict[str, Any], folder: Path) -> Path:
    """The plate, with the edit pass inside the region and the window only. Kept until either file or the comp changes."""
    dest = folder / f"comp-{comp_key(plate, edit, comp)}.mp4"
    if dest.exists():
        return dest
    width, height = ffmpeg.probe_clip(plate).display_size
    x, y, w, h = (float(v) for v in comp["region"])
    left, top = int(x * width) // 2 * 2, int(y * height) // 2 * 2
    across, down = max(2, int(w * width) // 2 * 2), max(2, int(h * height) // 2 * 2)
    patch = f"[1:v]scale={width}:{height},crop={across}:{down}:{left}:{top}[patch]"
    lay = f"[0:v][patch]overlay=x={left}:y={top}:eof_action=pass:enable='between(t,{float(comp['from'])},{float(comp['to'])})'[out]"
    folder.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part.mp4")
    seconds = _length(plate)
    encode = ["-an", "-c:v", "libx264", "-crf", str(COMP_CRF), "-pix_fmt", "yuv420p", *(["-t", f"{seconds:.3f}"] if seconds else [])]  # the plate's length
    ffmpeg.run(["-i", str(plate), "-i", str(edit), "-filter_complex", f"{patch};{lay}", "-map", "[out]", *encode, str(part)])
    part.rename(dest)
    return dest


def grey(path: Path, rate: float, width: int = PLATE_WIDTH) -> np.ndarray:
    """Small grey frames of a whole file, at one frame rate, so two files line up frame for frame."""
    source_w, source_h = ffmpeg.probe_clip(path).display_size
    tall = max(2, round(width * source_h / source_w / 2) * 2)
    raw = ffmpeg.run_bytes(["-i", str(path), "-vf", f"fps={rate},scale={width}:{tall}:flags=area,format=gray", "-f", "rawvideo", "-"])
    return np.frombuffer(raw, np.uint8).reshape(-1, tall, width).astype(np.float32)


def plate_diff(finished: Path, plate: Path, comp: dict[str, Any], *, offset: float = 0.0, span: float | None = None) -> dict[str, Any]:
    """Whether the pixels nothing was meant to touch still match the plate.

    Outside the region every frame is held to the plate, and outside the window the whole frame is.
    `offset` is where the plate starts in the finished file and `span` how long it stays, which must equal the plate to the frame.
    """
    rate = ffmpeg.probe_clip(finished).fps or 30.0
    shot, source = grey(finished, rate), grey(plate, rate)
    first = round(offset * rate)
    placed = len(shot) - first if span is None else round(span * rate)
    frames = min(len(source), max(len(shot) - first, 0))
    problems = []
    if abs(placed - len(source)) > 1:
        problems.append(f"the plate runs {len(source)} frames and holds {placed} in the film, so its timing changed")
    if frames == 0:
        return {"passed": False, "worst": None, "problems": ["the finished file holds none of the plate"]}
    height, width = source.shape[1:]
    x, y, w, h = (float(v) for v in comp["region"])
    outside = np.ones((height, width), bool)
    outside[int(y * height) : int(np.ceil((y + h) * height)), int(x * width) : int(np.ceil((x + w) * width))] = False
    start, end = float(comp["from"]), float(comp["to"])
    worst = (0.0, 0)
    for index in range(frames):
        change = np.abs(shot[first + index] - source[index])
        inside_window = start <= index / rate <= end
        level = float(change[outside].mean() if inside_window else change.mean())
        worst = max(worst, (level, index))
    if worst[0] > PLATE_TOLERANCE:
        at = worst[1] / rate
        where = "outside the edit region" if start <= at <= end else "outside the edit window"
        problems.append(f"{where} the picture moved {worst[0]:.1f} of 255 from the plate at {at:.2f} s, past {PLATE_TOLERANCE:g}")
    return {"passed": not problems, "worst": {"difference": round(worst[0], 2), "at": round(worst[1] / rate, 3)}, "problems": problems}


def finished_plate(project: Project, film: Any, line: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    rows = []
    for scene, item in comps(film.board):
        placed = next((entry for entry in line["items"] if entry.get("type") == "footage" and entry.get("scene") == scene.get("id")), None)
        start, span = (float(placed["start"]), float(placed["end"]) - float(placed["start"])) if placed else (0.0, None)
        found = plate_diff(path, project.root / item["file"], item["comp"], offset=start, span=span)
        detail = "; ".join(found["problems"]) or f"untouched pixels match the plate, worst {found['worst']['difference']} of 255"
        rows.append({"level": "PASS" if found["passed"] else "FAIL", "check": "plate held", "detail": detail})
    return rows


# Song Cut: owned footage cut to a song, its hits pinned by ear (c203)
#
# Altocue has no stem separator and no onset detector. The renderer's beat grid is its only hit
# finder, and c203 measured a grid 0.32 s off the real crash. So a person pins each hit on the
# drums and bass, says how, and these checks hold the cut to it.

SHORTEST_SHOT = 0.8  # seconds. A 0.5 s insert read as a jolt (c203)
PHONE_DARK = 50.0  # median mean grey out of 255 under which a film is too dark to watch on a phone. A house floor, not calibrated


def hits_of(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [hit for hit in board.get("hits") or [] if isinstance(hit, dict)]


def plan_song(film: Any) -> list[Problem]:
    found: list[Problem] = []

    def add(level: str, where: str, message: str, fix: str) -> None:
        found.append(Problem(level, where, message, fix))

    board = storyboard.fill(film.board)
    track = (board.get("music") or {}).get("path")
    if not track or not (film.project.root / str(track)).is_file():
        fix = 'Set "music": {"path": "assets/music/song.wav"}, a track you have rights to.'
        add("error", "storyboard", f"A Song Cut is cut to your song, and there is none at {track or 'music.path'}.", fix)
    done = storyboard.resolve(board, fps=film.project.fps)
    cuts = [scene["start"] for scene in done["scenes"][1:]]
    frame = 1 / film.project.fps
    hits = hits_of(board)
    if not hits:
        add("error", "storyboard", "No hit points.", 'Add "hits": [{"name": "crash", "at": 12.48, "how": "kick and bass enter, by ear"}].')
    for hit in hits:
        name, at = str(hit.get("name", "hit")), hit.get("at")
        if not storyboard.number(at) or not 0 < at < done["total"]:
            add("error", "storyboard", f'The hit "{name}" has no time inside the film.', "Set its second, read from the song.")
            continue
        nearest = min(cuts, key=lambda cut: abs(cut - at), default=None)
        if nearest is None or abs(nearest - at) > frame:
            off = f"{abs(nearest - at):.2f} s from the nearest cut" if nearest is not None else "with no cut at all"
            add("error", "storyboard", f'The {name} at {at:g} s falls {off}.', "Move a scene boundary onto it, so the cut lands on the hit.")
        if not hit.get("how"):
            fix = 'Add "how", such as pinned by ear on the kick and bass. Altocue has no stem or onset tool, and a beat grid can miss the real hit.'
            add("warning", "storyboard", f"The {name} does not say how it was found.", fix)
    if board.get("snap"):
        message = "Cuts snap to a beat grid, and a grid can miss the real crash by a third of a second."
        add("warning", "storyboard", message, "Turn snap off and cut to the hits you pinned.")
    texts = [item for item in done["items"] if item.get("type") != "footage" and storyboard.on_screen(item)]
    first = min((float(hit["at"]) for hit in hits if storyboard.number(hit.get("at"))), default=None)
    words = sum(len(" ".join(storyboard.on_screen(item)).split()) for item in texts)
    if len(texts) != 1 or words != 1:
        add("error", "storyboard", f"A Song Cut puts one word on screen, and this puts {words} in {len(texts)} items.", "Keep one title word, on the first hit.")
    elif first is not None and abs(texts[0]["start"] - first) > frame:
        add("error", str(texts[0]["scene"]), f"The title lands at {texts[0]['start']:g} s and the first hit is at {first:g} s.", "Start it on the hit.")
    for scene in done["scenes"]:
        if scene["end"] - scene["start"] + frame / 2 < SHORTEST_SHOT:
            message = f"This shot runs {scene['end'] - scene['start']:.2f} s, under {SHORTEST_SHOT:g} s."
            add("error", str(scene["id"]), message, "Shorter shots read as a jolt. Give it longer, or cut it.")
    for scene in board.get("scenes") or []:
        if scene.get("sound"):
            add("warning", str(scene.get("id")), "Sound effects over a song.", "Cut them. The song carries the edit (c203 added nine and removed every one).")
    return found


def finished_song(project: Project, film: Any, line: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    from .qa import film as film_qa

    stats = film_qa.frame_stats(path)
    cuts = film_qa.analyse(stats)["cuts"]
    rows = []
    level = float(np.median(stats["mean"])) if stats["count"] else 0.0
    dark = level < PHONE_DARK
    detail = f"median brightness {level:.0f} of 255, floor {PHONE_DARK:g}" + (", too dark for a phone" if dark else "")
    rows.append({"level": "FAIL" if dark else "PASS", "check": "phone brightness", "detail": detail})
    for hit in hits_of(film.board):
        at = hit.get("at")
        if not storyboard.number(at):
            continue
        near = min(cuts, key=lambda cut: abs(cut - at), default=None)
        landed = near is not None and abs(near - at) <= 1.5 / stats["fps"]
        where = f"the nearest cut in the picture is at {near:.3f} s" if near is not None else "no cut was found in the picture"
        rows.append({"level": "PASS" if landed else "FAIL", "check": f"cut on the {hit.get('name', 'hit')}", "detail": f"hit at {float(at):.3f} s, {where}"})
    return rows


PLAN: dict[str, Callable[[Any], list[Problem]]] = {"plate": plan_plate, "song": plan_song}
FINISHED: dict[str, Callable[[Project, Any, dict[str, Any], Path], list[dict[str, Any]]]] = {"plate": finished_plate, "song": finished_song}


def names(record: dict[str, Any]) -> list[str]:
    return [str(name) for name in record.get("checks", [])]


def plan(film: Any) -> list[Problem]:
    """Every plan check the film's type asks for."""
    return [problem for name in names(film.record) if name in PLAN for problem in PLAN[name](film)]


def finished(project: Project, film: Any, line: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    """Every check the film's type runs on its finished master."""
    return [row for name in names(film.record) if name in FINISHED for row in FINISHED[name](project, film, line, path)]
