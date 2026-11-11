"""See the edit on a timeline, in the pinned HyperFrames studio.

The stage is the renderer's own page with the footage, the inserts and the sound laid under it, so
what shows here is what renders. The studio is a view: a render reads only edl.json and motion.json,
so nothing changed in the studio reaches the film. If the page comes back edited, that copy is kept
and named, so an edit is never lost and never quietly believed.

The studio gets `--no-open` on every call. Nothing here opens a browser or any other application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np

from . import brand as brands
from . import caption, ffmpeg, frame, locks, plate, render
from . import timeline as timelines
from .cut.snap import frame_count
from .errors import AltocueError
from .models import Edl
from .motion.html import compose
from .motion.hyperframes import HyperFramesRenderer, binary, install_assets, quiet_env
from .project import Project, write_json

FOLDER = "timeline"
FOOTAGE = "footage.mp4"
HOLD = "hold.jpg"
RECORD = "stage.json"
STUDIO_STAMP = re.compile(r'\sdata-hf-id="[^"]*"')
STARTUP_SECONDS = 120
FILL = "inset:0;width:100%;height:100%;object-fit:cover"  # how the render fills the frame with an insert
VIEW_NOTE = (
    "The studio is a view. A render reads only edl.json and motion.json, so a change made in the studio does not reach the film. "
    "Change those files, then run `altocue timeline` again."
)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--port", type=int, help="Port for the studio. HyperFrames picks one if this is left out")
    parser.add_argument("--stop", action="store_true", help="Stop this project's studio")


def home_of(project: Project) -> Path:
    return project.work / FOLDER


def stage_of(project: Project) -> Path:
    """The folder's name is the project's name in the studio's address."""
    return home_of(project) / (re.sub(r"[^a-z0-9]+", "-", project.root.name.lower()).strip("-") or "film")


def _digest(page: str) -> str:
    """The studio stamps its own ids on the page as it starts, so those do not count as an edit."""
    return hashlib.sha256(STUDIO_STAMP.sub("", page).encode()).hexdigest()


def studio_edit(project: Project) -> Path | None:
    """If the page is not the one Altocue wrote, the studio changed it. Keep that copy, and return where it went."""
    page, record = stage_of(project) / "index.html", home_of(project) / RECORD
    if not page.exists() or not record.exists():
        return None
    found = _digest(page.read_text())
    if found == json.loads(record.read_text())["page"]:
        return None
    kept = home_of(project) / f"studio-edit-{found[:12]}.html"
    shutil.copy2(page, kept)
    return kept


def _span(start: float, end: float) -> str:
    """Both edges rounded once, so a clip ends exactly where the next one starts."""
    first, last = round(start, 3), round(end, 3)
    return f'data-start="{first:.3f}" data-duration="{last - first:.3f}"'


def _link(target: Path, link: Path) -> None:
    """Media stays where it is. The stage only points at it."""
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(target.resolve())


def _shots(edl: Edl, stage: Path, picture: Path) -> list[str]:
    """One clip per shot, all from the plate, so the cuts show and every frame is the render's own."""
    _link(picture, stage / FOOTAGE)
    clips, played = [], 0
    for segment in edl.segments:
        start = played / edl.fps
        played += frame_count(segment.start, segment.end, edl.fps)
        clips.append(
            f'<video id="shot-{escape(segment.id)}" class="clip" src="{FOOTAGE}" {_span(start, played / edl.fps)} data-media-start="{round(start, 3):.3f}" '
            f'data-track-index="0" muted playsinline style="{FILL}"></video>'
        )
    return clips


def _hold(timeline: dict[str, Any], picture: Path, stage: Path) -> list[str]:
    """Past the last shot the render holds its last frame, so the end card sits on that here too."""
    if not timeline["tail"]:
        return []
    ffmpeg.run(["-sseof", "-0.2", "-i", str(picture), "-update", "1", "-q:v", "3", str(stage / HOLD)])
    return [f'<img id="hold" class="clip" src="{HOLD}" alt="" {_span(timeline["duration"], timeline["total"])} data-track-index="0" style="{FILL}">']


def _inserts(project: Project, inserts: list[dict[str, Any]], stage: Path, first_track: int) -> tuple[list[str], int]:
    """Each insert on its real file, or on the render's own clip when it arrives or leaves by a transition.

    Inserts may overlap in time, and a track may not, so each takes the first free lane.
    """
    clips: list[str] = []
    lanes: list[float] = []
    for number, insert in enumerate(inserts, 1):
        path = (project.root / insert.get("baked", insert["file"])).resolve()
        still = path.suffix.lower() in timelines.STILL_TYPES
        start = float(insert["start"])
        whole = still or "baked" in insert  # a baked clip holds its last frame, so it fills the span
        end = float(insert["end"]) if whole else min(float(insert["end"]), start + ffmpeg.probe_clip(path).duration)  # past its end, the render shows what is under it
        lane = next((index for index, free in enumerate(lanes) if free <= start), None)
        if lane is None:
            lanes.append(end)
            lane = len(lanes) - 1
        lanes[lane] = end
        name = f"{number}-{path.name}"
        _link(path, stage / "inserts" / name)
        common = f'id="insert-{number}" class="clip" src="{quote(f"inserts/{name}")}" {_span(start, end)} data-track-index="{first_track + lane}"'
        clips.append(f'<img {common} alt="" style="{FILL}">' if still else f'<video {common} data-media-start="0.000" muted playsinline style="{FILL}"></video>')
    return clips, first_track + len(lanes)


def _sounds(project: Project, timeline: dict[str, Any], voice: Path, stage: Path, first_track: int) -> list[str]:
    """The film's own mix, one lane a stem. A stem with nothing in it stays off the timeline."""
    clips = []
    for path in render.write_stems(project, timeline, voice, stage):
        if np.abs(ffmpeg.read_wav(path)[0]).max(initial=0.0) == 0:
            path.unlink()
            continue
        track = first_track + len(clips)
        clips.append(f'<audio id="sound-{path.stem}" src="{path.name}" {_span(0.0, timeline["total"])} data-track-index="{track}" data-volume="1"></audio>')
    return clips


def _baked(project: Project, timeline: dict[str, Any], picture: Path, draft: bool) -> list[dict[str, Any]]:
    """The render's own clip for an insert with a transition at either edge, from the same cache the render fills."""
    c = plate.canvas(project, draft=draft)
    folder = project.work / ("inserts-draft" if draft else "inserts")
    baked = []
    for insert in timeline["inserts"]:
        if insert.get("in") or insert.get("out"):
            folder.mkdir(parents=True, exist_ok=True)
            insert = {**insert, "baked": render.bake_insert(project, insert, picture, c, folder)}
        baked.append(insert)
    return baked


def _not_drawn(timeline: dict[str, Any]) -> str | None:
    """Overlays are live ffmpeg blends with no page form, so the studio leaves them off, and says which."""
    kinds = list(dict.fromkeys(item["type"].replace("_", " ") for item in timeline.get("overlay", [])))
    if not kinds:
        return None
    named = kinds[0] if len(kinds) == 1 else f"{', '.join(kinds[:-1])} and {kinds[-1]}"
    return f"The {named} overlay{'s are' if len(kinds) > 1 else ' is'} added at render and not drawn in the studio."


def write_stage(project: Project) -> tuple[Path, Path | None, str | None]:
    """Write the stage. Return it, any studio edit that was saved from being overwritten, and anything the studio cannot show."""
    edl = project.edl()
    if not edl.segments:
        raise AltocueError("The cut is empty.", fix="Run `altocue cut` and check edl.json.")
    stage = stage_of(project)
    stage.mkdir(parents=True, exist_ok=True)
    kept = studio_edit(project)
    with locks.heavy("getting the timeline ready"):
        frame.frame_project(project)  # what a render would do first, or the punch-ins would not show
        draft = not (project.work / "plate.mp4").exists()
        picture, voice = plate.build(project, draft=draft)
        edl = project.edl()
        caption.build(project)
        timeline = timelines.build(project, draft=draft)
        brand = brands.load(project)
        install_assets(timeline, brand, stage)
        layers = _shots(edl, stage, picture) + _hold(timeline, picture, stage)
        inserts, next_track = _inserts(project, _baked(project, timeline, picture, draft), stage, 2 + len(timeline["items"]))  # captions take track 1, then each graphic
        layers += inserts + _sounds(project, timeline, voice, stage, next_track)
    page = compose(timeline, brand, under="\n".join(layers))
    (stage / "index.html").write_text(page)
    write_json(home_of(project) / RECORD, {"page": _digest(page)})
    return stage, kept, _not_drawn(timeline)


def _require_studio() -> None:
    ready, why = HyperFramesRenderer().available()
    if not ready:
        raise AltocueError(f"The timeline opens in HyperFrames, and {why}", fix="Run `altocue setup`.")


def _studio(stage: Path, *flags: str) -> dict[str, Any]:
    """Call the pinned CLI, and read its JSON answer. Telemetry stays off through `quiet_env`.

    HyperFrames' preview has no --host flag. Its studio server binds to HYPERFRAMES_PREVIEW_HOST when that is set,
    so a shell that sets it to 0.0.0.0 would open the studio to the network. It is pinned to this machine here.
    """
    command = [str(binary()), "preview", str(stage), *flags, "--json"]
    env = {**quiet_env(), "HYPERFRAMES_PREVIEW_HOST": "127.0.0.1"}
    try:
        done = subprocess.run(command, check=False, capture_output=True, text=True, env=env, timeout=STARTUP_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise AltocueError(f"The studio did not answer in {STARTUP_SECONDS} seconds.", fix="Run `altocue doctor`.") from error
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as error:
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
        raise AltocueError(f"The studio did not start:\n{tail}", fix="Run `altocue doctor`.") from error
    if not answer.get("ok"):
        why = answer.get("error", {})
        raise AltocueError(f"The studio did not start: {why.get('message', why) if isinstance(why, dict) else why}", fix="Run `altocue doctor`.")
    return answer.get("result", {})


def _say_edit(kept: Path | None) -> None:
    if kept:
        print(f"The studio changed the page, and that change is not in edl.json or motion.json, so a render will not have it. The edited page is saved as {kept}.")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    _require_studio()
    if args.stop:
        stage = stage_of(project)
        stage.mkdir(parents=True, exist_ok=True)
        _studio(stage, "--stop")
        print("The studio has stopped.")
        _say_edit(studio_edit(project))
        return 0
    stage, kept, unseen = write_stage(project)
    flags = ["--no-open", "--background", *(["--port", str(args.port)] if args.port else [])]
    address = _studio(stage, *flags).get("studioUrl")
    if not address:
        raise AltocueError("The studio started but gave no address.", fix="Run `altocue doctor`.")
    print(f"Timeline: {address}")
    print(VIEW_NOTE)
    if unseen:
        print(unseen)
    print("Stop it with `altocue timeline --stop`.")
    _say_edit(kept)
    return 0
