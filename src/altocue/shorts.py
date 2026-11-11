"""Hand a real edit to Higgsfield's Shorts Studio for its motion design, then check what comes back.

Shorts Studio (the `/shorts` command of the Higgsfield MCP server) designs a reel around a talking
take: captions, motion graphics, cutouts and sound, composed in HTML and rendered with its own
engine in Higgsfield's cloud sandbox. Out of the box it casts an AI presenter. Here the take is the
person's real footage instead: the cut Altocue already made, with its clean sound and its grade.

`altocue shorts prepare` turns that cut into a Shorts Studio run folder. The take is supplied, so
nothing generates a presenter. Moments the speaker looks away from the lens become beats that a
full-screen graphic must cover. When a sound pack is named, the run may use only its files.

`altocue shorts check` reads the composition and render that come back and fails a run that used a
generated presenter, played a sound from outside the pack, left a look-away moment showing, wrote a
dash the house style bans, or rendered to the wrong length.

Nothing here talks to Higgsfield. The agent uploads the bundle and runs Shorts Studio's own scripts
in the sandbox, after the owner agrees to send the footage there. None of Higgsfield's scripts or
skill text is copied into Altocue.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from .errors import AltocueError
from .project import Project, write_json

TAKE = "take-01"
MIN_TAKE = 0.6  # a line shorter than this, such as one word, is too short to time or measure: it joins its neighbour
SOUNDS = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aif", ".aiff")
DASHES = {chr(0x2014): "em dash", chr(0x2015): "horizontal bar", chr(0x2013): "en dash"}
TEXT_FILES = (".html", ".js", ".css", ".md", ".json", ".txt")
KIT_FILES = {"kit.js", "fonts.css"}  # Shorts Studio's own files, written by its scaffold. Their comments are not on screen.
SENTENCE = re.compile(r"(?<=[.!?])\s+")
AUDIO_TAG = re.compile(r"<audio\b[^>]*>", re.IGNORECASE)
COVER_TAG = re.compile(r"<[a-z][^>]*\bdata-cover\b[^>]*>", re.IGNORECASE)
HIDDEN_TAG = re.compile(r"<[a-z][^>]*\bdata-layout\s*=\s*\"hidden\"[^>]*>", re.IGNORECASE)  # a scene that hides the take: a cover by layout
VIDEO_TAG = re.compile(r"<video\b", re.IGNORECASE)
SCENE_TAG = re.compile(r"<[a-z][^>]*\bdata-layout\s*=\s*\"[a-z]+\"[^>]*>", re.IGNORECASE)
GRAIN_RULE = re.compile(r"([^{}]*grain[^{}]*)\{([^}]*)\}", re.IGNORECASE)
GRAIN_PER_FRAME = re.compile(r"grain[^;\n]*;[^\n]*frameAt|frameAt[^\n]*grain", re.IGNORECASE)
HOUSE_FILE = "house.json"  # in a kit: the house look a run must keep, read by the check
# What a first draft is held to, unless house.json says otherwise.
HOUSE_DEFAULTS = {
    "silent_face": 0.6,  # seconds the face may show with nothing said
    "min_face": 0.8,  # the shortest look at the face between two graphics
    "min_scene": 0.5,  # anything shorter is a flash cut
    "grain_opacity": 0.04,
    "min_graphic": 1.2,  # the shortest hold for a graphic with any text on it
    "read_rate": 5.0,  # words a viewer reads a second, the voice saying most of them, on top of a short glance
    "max_changes": 2,  # scene changes allowed inside any `change_window` seconds
    "change_window": 4.0,
    "max_sounds": 2,  # sound effects allowed inside any one second
    "join_reach": 0.7,  # a full graphic edge this close to a join in the footage belongs on the join
    "max_scene": 10.0,  # a full graphic longer than this loses the viewer: split it on a spoken word
}
SECTION = re.compile(r"(<section\b[^>]*\bdata-layout\s*=\s*\"[a-z]+\"[^>]*>)(.*?)</section>", re.IGNORECASE | re.DOTALL)
ATTR = re.compile(r'([a-z-]+)\s*=\s*"([^"]*)"', re.IGNORECASE)
CUE_WORDS = 4
LISTEN_RATE = 16000
LISTEN_HOP = 320  # 20 ms
SPEECH_UNDER_PEAK_DB = 26.0  # sound within this of the voice's loudest 20 ms is speech
REAL_PAUSE = 0.25  # a gap between word times with less quiet than this in the sound is not a pause
MISSED_WORD = 0.03  # a caption word shorter than this was not heard: the recognizer gave it no time
FALSE_PAUSE = 0.45  # fix-words listens to any gap between word times longer than this
FRAME_SLACK = 1.0  # frames a cover may start late or end early and still count


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare", help="Write a Shorts Studio run folder from a real edit")
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--project", "-p", help="An Altocue project after `altocue plate`. Its plate and voice become the take")
    source.add_argument("--take", help="A finished talking video to use as the take, with --script")
    prepare.add_argument("--script", help="With --take: a text file holding exactly what the take says")
    prepare.add_argument("--out", help="Run folder to write. Defaults to shorts/<slug> in the project, or ./shorts-<slug>")
    prepare.add_argument("--slug", help="Short kebab-case name for the run. Defaults to the project or take name")
    prepare.add_argument("--sfx-dir", help="Folder of sound effects the run may use. Overrides [shorts] sfx_dir")
    prepare.add_argument("--sfx-any", action="store_true", help="Allow Shorts Studio's own library sounds next to the pack")
    prepare.add_argument("--cover", help="JSON of look-away spans in take seconds, [{start, end}] or {spans: [...]}. Overrides [shorts] cover")
    prepare.add_argument("--kit", help="Folder of house build helpers: .py files go beside build.py, anything else into media/. Overrides [shorts] kit")
    prepare.add_argument("--captions", type=int, help="Words per caption page. 0 turns captions off. Overrides [shorts] caption_words")
    prepare.add_argument("--aspect", help="Frame, such as 9:16. Defaults to the project's")
    prepare.add_argument("--until", type=float, default=0.0, help="With --project: only the cut's first seconds, ending on a whole word. For a preview")
    prepare.add_argument("--force", action="store_true", help="Write over a run folder that already exists")
    fix = sub.add_parser("fix-words", help="After Shorts Studio's measure: give missed words a time and close pauses the sound does not have")
    fix.add_argument("run_dir", help="The run folder, after measure, with takes/ and transcripts/")
    fix.add_argument("--dry", action="store_true", help="Say what would change and change nothing")

    gaze = sub.add_parser("gaze", help="Find where the eyes leave the lens, as cover spans for prepare --cover")
    gaze_source = gaze.add_mutually_exclusive_group(required=True)
    gaze_source.add_argument("--project", "-p", help="An Altocue project after `altocue plate`: its plate is scanned, in cut seconds")
    gaze_source.add_argument("--video", help="Any talking video")
    gaze.add_argument("--out", help="JSON to write. Defaults to gaze.json in the project, or beside the video")
    gaze.add_argument("--lens", type=float, help="Degrees of head plus eye turn when looking down this camera's lens. Overrides [shorts] lens")

    frames = sub.add_parser("frames", help="Timestamped contact sheets of the render, to look at every beat before anyone else does")
    frames.add_argument("run_dir", help="The run folder")
    frames.add_argument("--render", help="The render. Defaults to renders/final.mp4 in the run folder")
    frames.add_argument("--every", type=float, default=1.36, help="Seconds between frames")
    check = sub.add_parser("check", help="Check a Shorts Studio run: presenter, sounds, look-away covers, dashes, length")
    check.add_argument("run_dir", help="The run folder, or the unzipped composition.zip, with reel.json and timeline.json")
    check.add_argument("--render", help="The final render. Defaults to renders/final.mp4 in the run folder")
    check.add_argument("--gaze", action="store_true", help="Also scan the render's eyeline and fail any look-away left on screen. Needs MediaPipe")
    check.add_argument("--lens", type=float, help="With --gaze: degrees of head plus eye turn on the lens. Defaults to [shorts] lens of the project")


# ---------------------------------------------------------------- prepare


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "reel"


def load_spans(path: Path) -> list[dict[str, float]]:
    """Look-away spans in take seconds. A gaze tool's own output works if it keeps start and end per span."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise AltocueError(f"Could not read {path}: {error}", fix="Pass a JSON list of {start, end} spans in seconds.") from error
    items = raw.get("spans", raw.get("away", [])) if isinstance(raw, dict) else raw
    spans = []
    for item in items or []:
        start, end = float(item["start"]), float(item["end"])
        if end > start:
            spans.append({"start": round(start, 3), "end": round(end, 3), "reason": str(item.get("reason", "eyes off the lens"))})
    return sorted(spans, key=lambda span: span["start"])


def inside_take(spans: list[dict[str, float]], seconds: float) -> list[dict[str, float]]:
    """Spans that start inside the take, each ended by the take at the latest. A preview keeps only its own."""
    return [{**span, "end": min(span["end"], round(seconds, 3))} for span in spans if span["start"] < seconds]


def take_id(number: int) -> str:
    return f"take-{number:02d}"


def lines_of(lengths: list[int], words: list[dict[str, Any]], fps: float) -> list[dict[str, Any]]:
    """One take per recorded line. The cut's own joins split the plate, and nothing else does.

    `lengths` are the cut's segments in frames, in order. A line under MIN_TAKE, or with no word in it,
    joins the line after it (the last one joins the line before), so the join stays inside that take.
    Pauses inside a line are never touched: Shorts Studio trims only the dead air at each take's edges.
    """
    lines: list[list[int]] = []
    frame = 0
    for length in lengths:
        lines.append([frame, frame + length])
        frame += length

    def spoken(line: list[int]) -> bool:
        return any(line[0] <= w["start_frame"] < line[1] for w in words)

    changed = True
    while changed and len(lines) > 1:
        changed = False
        for index, line in enumerate(lines):
            if (line[1] - line[0]) / fps >= MIN_TAKE and spoken(line):
                continue
            if index + 1 < len(lines):
                lines[index + 1][0] = line[0]
            else:
                lines[index - 1][1] = line[1]
            del lines[index]
            changed = True
            break
    takes = []
    for number, (first, last) in enumerate(lines, 1):
        start, end = first / fps, last / fps
        inside = [{**w, "start": round(w["start"] - start, 3), "end": round(min(w["end"], end) - start, 3)} for w in words if first <= w["start_frame"] < last]
        take = {"id": take_id(number), "first_frame": first, "last_frame": last, "from": round(start, 3), "to": round(end, 3)}
        takes.append({**take, "words": inside, "dialogue": dialogue_of(inside)})
    return takes


def project_takes(project: Project, run: Path, until: float = 0.0) -> list[dict[str, Any]]:
    """The plate and its edited voice, cut on the plate's own joins into one take per line, with each take's words."""
    from .cut import map_words
    from .cut.snap import frame_count

    plate, voice = project.work / "plate.mp4", project.work / "voice.wav"
    if not plate.exists() or not voice.exists():
        raise AltocueError("This project has no plate yet.", fix="Run `altocue plate` first. Shorts Studio designs on the finished cut.")
    edl = project.edl()
    words = [w for w in map_words(project, edl) if w.get("kind", "word") == "word"]
    if not words:
        raise AltocueError("The cut keeps no spoken words.", fix="Shorts Studio needs a talking take. Check the cut with `altocue cut`.")
    lengths = [frame_count(segment.start, segment.end, edl.fps) for segment in edl.segments]
    if until:
        kept = [w for w in words if w["end"] <= until] or words[:1]
        stop = min(sum(lengths), round((kept[-1]["end"] + 0.4) * edl.fps))
        words, total, cut_lengths = kept, 0, []
        for length in lengths:
            if total >= stop:
                break
            cut_lengths.append(min(length, stop - total))
            total += cut_lengths[-1]
        lengths = cut_lengths
    takes = lines_of(lengths, words, edl.fps)
    for take in takes:
        take["path"] = run / "media" / "takes" / f"{take['id']}.mp4"
        cut_take(plate, voice, take["path"], take["first_frame"], take["last_frame"], edl.fps)
    return takes


def cut_take(picture: Path, voice: Path, target: Path, first: int, last: int, fps: float) -> None:
    """One line of the plate with its voice, cut on exact frames. The picture is re-encoded so it starts on the frame asked for."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AltocueError("ffmpeg is not on PATH.", fix="Run `altocue doctor`.")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(picture), "-i", str(voice), "-map", "0:v:0", "-map", "1:a:0"]
    command += ["-vf", f"trim=start_frame={first}:end_frame={last},setpts=PTS-STARTPTS"]
    command += ["-af", f"atrim=start={first / fps:.6f}:end={last / fps:.6f},asetpts=PTS-STARTPTS"]
    command += ["-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(target)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AltocueError(f"ffmpeg could not cut {target.stem} from the plate: {result.stderr.strip()[-400:]}")


def dialogue_of(words: list[dict[str, Any]]) -> str:
    return " ".join(str(w["text"]).strip() for w in words if str(w["text"]).strip())


def cue_phrase(words: list[str]) -> str:
    return " ".join(words[:CUE_WORDS])


def take_at(takes: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    """The take holding this moment of the cut, the last one for a moment past the end."""
    return next((take for take in takes if take["from"] <= seconds < take["to"]), takes[-1])


def plan_lines(takes: list[dict[str, Any]], spans: list[dict[str, float]], caption_words: int) -> str:
    """The edit plan's skeleton: one beat per sentence in each take, and one cover beat per look-away span.

    Cue phrases are copied from the take's dialogue word for word, because Shorts Studio times a beat by
    finding its phrase in that take. A sentence that runs across a join gets a beat in each take.
    """
    lines = ["# Edit plan", "", "Real footage. The presenter is the person in the take: never generate, recast or redraw them.", ""]
    if len(takes) > 1:
        lines += [f"{len(takes)} takes, one per recorded line, cut on the joins already in the edit. Keep them in order and trim only", "the dead air at each join.", ""]
    number = 0
    for take in takes:
        for sentence in (s for s in SENTENCE.split(take["dialogue"]) if s.strip()):
            number += 1
            lines += [f"Beat: line-{number:02d}", f'Cue: {take["id"]} "{cue_phrase(sentence.split(" "))}"', ""]
    for index, span in enumerate(spans):
        take = take_at(takes, span["start"])
        words, tokens = take["words"], take["dialogue"].split(" ")
        local = span["start"] - take["from"]
        inside = [i for i, w in enumerate(words) if w["start"] >= local - 0.05 and w["start"] < span["end"] - take["from"]]
        near = inside[0] if inside else min(range(len(words)), key=lambda i: abs(words[i]["start"] - local)) if words else 0
        lines += [
            f"Beat: cover-{index + 1:02d}",
            f'Cue: {take["id"]} "{cue_phrase(tokens[near:])}"',
            f"Cover {span['start']:.2f} to {span['end']:.2f} s of the cut ({span['reason']}), from {local:.2f} s into {take['id']},"
            " with a full-screen graphic that hides the presenter.",
            f'Put the moment in a data-layout="hidden" scene, or mark its element data-cover="cover-{index + 1:02d}",',
            "with data-start and data-duration that span the whole moment.",
            "",
        ]
    lines.append(f"Captions: {caption_words} words" if caption_words else "Captions: none")
    return "\n".join(lines) + "\n"


def copy_pack(pack: Path, run: Path) -> list[dict[str, Any]]:
    if not pack.is_dir():
        raise AltocueError(f"No sound folder at {pack}.", fix="Set [shorts] sfx_dir to a folder of sound files, or pass --sfx-dir.")
    sounds = []
    for path in sorted(p for p in pack.rglob("*") if p.is_file() and p.suffix.lower() in SOUNDS):
        relative = path.relative_to(pack)
        target = run / "media" / "sfx" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        sounds.append({"file": f"media/sfx/{relative.as_posix()}", "name": path.stem, "sha256": sha256(path)})
    if not sounds:
        raise AltocueError(f"{pack} holds no sound files.", fix=f"Point it at a folder of {', '.join(SOUNDS)} files.")
    return sounds


def copy_kit(kit: Path, run: Path) -> list[str]:
    """House build helpers, such as a shared stylesheet and motion module, so every Reel's build.py starts from the same look.

    Python files and house.json go to the run folder, other files to media/, and a subfolder, such as glass/ or
    brand/, to media/ under its own name. The kit's README stays behind.
    """
    if not kit.is_dir():
        raise AltocueError(f"No kit folder at {kit}.", fix="Set [shorts] kit to a folder of build helpers, or pass --kit.")
    copied = []
    files = [p for p in kit.rglob("*") if p.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(kit).parts)]
    for path in sorted(p for p in files if p.relative_to(kit) != Path("README.md")):
        relative = path.relative_to(kit)
        top = len(relative.parts) == 1 and (path.suffix == ".py" or path.name == HOUSE_FILE)
        target = run / path.name if top else run / "media" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(target.relative_to(run).as_posix())
    if not copied:
        raise AltocueError(f"{kit} holds no files.", fix="Put the house helpers, such as shorts_kit.py, in it.")
    return copied


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def look_lines(look: Any) -> list[str]:
    """house.json's look, as one line or several."""
    if isinstance(look, str):
        return [look]
    return [str(line) for line in look or []]


def house_notes(
    sounds: list[dict[str, Any]] | None, sfx_only: bool, spans: list[dict[str, float]], kit: list[str] | None = None, house: dict[str, Any] | None = None
) -> str:
    rules = {**HOUSE_DEFAULTS, **(house or {})}
    notes = [
        "# House rules for this run",
        "",
        "- Shorts Studio upgrades the house look. It does not replace it: keep the house layout, text style, framing and end card.",
        *[f"  {line}" for line in look_lines((house or {}).get("look"))],
        f"- Show the face only while it speaks. No pause over {rules['silent_face']:.1f} s on the face, and no flash of the face",
        f"  under {rules['min_face']:.1f} s between two graphics. No scene under {rules['min_scene']:.1f} s.",
        "- Keep the motion graphics strong. What fails is pacing: give each graphic time to read, one idea on screen at a time,",
        f"  at least {rules['min_graphic']:.1f} s and longer for more words, and no more than {rules['max_changes']} scene changes in any"
        f" {rules['change_window']:.0f} s.",
        f"- A full graphic starts and ends on a join in the footage when one is within {rules['join_reach']:.1f} s, so the picture never"
        " changes twice in a second. Never onto a join inside another look-away.",
        f"- Grain stays at opacity {rules['grain_opacity']:.2f} or less, and never jumps to a new place every frame.",
        "- The take is real footage, supplied. Generate no presenter take, no recast and no lip sync. reel.json keeps `job_id` null on every take.",
        "- Keep the speaker's own words. Trim nothing the measure step does not trim.",
        "- On-screen text uses commas, colons or full stops. Never an em dash or an en dash.",
    ]
    if spans:
        notes.append(f"- {len(spans)} look-away {'moment is a cover beat' if len(spans) == 1 else 'moments are cover beats'} in edit-plan.md.")
        notes.append('  Each sits inside a `data-layout="hidden"` scene, or under a full-screen graphic marked `data-cover`,')
        notes.append("  so the presenter never shows looking off the lens.")
    if sounds is not None:
        rule = "from media/sfx/ first."
        if sfx_only:
            rule = "only from media/sfx/, as listed in sfx-pack.json. Do not place Shorts Studio's library sounds."
        notes.append(f"- Sound effects come {rule} Place each as an <audio> tag in the composition.")
    notes.append("- Show the take with `<img data-video>`. A `<video>` tag fails the render.")
    if kit:
        notes.append(f"- House build helpers: {', '.join(kit)}. Build the composition with them, after measure and a first scaffold.")
    notes.append("- Music only when the brief asks for it.")
    return "\n".join(notes) + "\n"


def bundle(run: Path) -> Path:
    target = run.parent / f"{run.name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(p for p in run.rglob("*") if p.is_file()):
            archive.write(path, f"{run.name}/{path.relative_to(run).as_posix()}")
    return target


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    project = Project.open(args.project) if args.project else None
    settings = project.config.get("shorts", {}) if project else {}
    base = project.root if project else Path.cwd()
    slug = slugify(args.slug or (project.root.name if project else Path(args.take).stem))
    run = Path(args.out).expanduser().resolve() if args.out else (base / "shorts" / slug if project else base / f"shorts-{slug}")
    if (run / "reel.json").exists() and not args.force:
        raise AltocueError(f"{run} already holds a run.", fix="Pass --out for a new run folder, or --force to write this one again.")
    if args.force and (run / "altocue-shorts.json").exists():  # only ever a folder this command wrote
        shutil.rmtree(run, ignore_errors=True)
        (run.parent / f"{run.name}.zip").unlink(missing_ok=True)
    run.mkdir(parents=True, exist_ok=True)

    if project:
        takes = project_takes(project, run, args.until)
        aspect = args.aspect or project.config["output"]["aspect"]
    else:
        if not args.script:
            raise AltocueError("--take needs --script.", fix="Pass a text file holding exactly what the take says.")
        dialogue = " ".join(Path(args.script).expanduser().read_text().split())
        source = Path(args.take).expanduser()
        if not source.exists():
            raise AltocueError(f"No take at {source}.")
        path = run / "media" / "takes" / f"{TAKE}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)
        takes = [{"id": TAKE, "path": path, "from": 0.0, "to": probe_duration(path), "words": [], "dialogue": dialogue}]
        aspect = args.aspect or "9:16"
    for take in takes:
        take["duration"] = round(probe_duration(take["path"]), 3)
    dialogue = " ".join(take["dialogue"] for take in takes if take["dialogue"])
    seconds = round(sum(take["duration"] for take in takes), 3)

    cover = args.cover or settings.get("cover", "")
    cover_path = (base / cover).expanduser() if cover and not Path(cover).is_absolute() else Path(cover) if cover else None
    spans = load_spans(cover_path) if cover_path else []
    if spans and not project:
        raise AltocueError("Cover spans need word timings.", fix="Use --project, so each cover can be cued on the words spoken during it.")
    spans = inside_take(spans, takes[-1]["to"])
    caption_words = args.captions if args.captions is not None else int(settings.get("caption_words", 3))
    pack = args.sfx_dir or settings.get("sfx_dir", "")
    sfx_only = not args.sfx_any and bool(settings.get("sfx_only", True))
    sounds = copy_pack(Path(pack).expanduser(), run) if pack else None
    kit_dir = args.kit or settings.get("kit", "")
    kit = copy_kit(Path(kit_dir).expanduser(), run) if kit_dir else None

    reel = {
        "language": (project.setting("transcribe", "language", "en") if project else "en"),
        "voice_target_lufs": -14,
        "voice_mode": "native",
        "resolution": "1080p",
        "aspect_ratio": aspect,
        "video_model": "seedance_2_5",
        "takes": [
            {
                "id": take["id"],
                "dialogue": take["dialogue"],
                "pronunciations": [],
                "duration": take["duration"],
                "job_id": None,
                "url": None,
                "local_path": f"media/takes/{take['id']}.mp4",
                "matte": None,
            }
            for take in takes
        ],
        "outro": None,
        "media": [
            {
                "id": "real-take" if len(takes) == 1 else f"real-{take['id']}",
                "kind": "video",
                "source": "supplied",
                "role": "host-source",
                "purpose": "presenter",
                "local_path": f"media/takes/{take['id']}.mp4",
            }
            for take in takes
        ],
    }
    write_json(run / "reel.json", reel)
    (run / "script.md").write_text(dialogue + "\n")
    (run / "edit-plan.md").write_text(plan_lines(takes, spans, caption_words))
    (run / "HOUSE.md").write_text(house_notes(sounds, sfx_only, spans, kit, json.loads((run / HOUSE_FILE).read_text()) if (run / HOUSE_FILE).exists() else None))
    write_json(
        run / "altocue-shorts.json",
        {
            "take": f"media/takes/{takes[0]['id']}.mp4",
            "takes": [{"id": take["id"], "from": take["from"], "to": take["to"]} for take in takes],  # where each take sits in the cut
            "covers": spans,
            "sfx_only": bool(sounds is not None and sfx_only),
            "sounds": sounds or [],
            "kit": kit or [],
            "project": str(project.root) if project else "",
        },
    )
    if sounds is not None:
        write_json(run / "sfx-pack.json", [{"file": s["file"], "name": s["name"]} for s in sounds])
    archive = bundle(run)
    return {"run": run, "bundle": archive, "spans": spans, "sounds": len(sounds or []), "seconds": seconds, "takes": len(takes)}


def probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise AltocueError("ffprobe is not on PATH.", fix="Run `altocue doctor`.")
    result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=False)
    try:
        return float(result.stdout.strip())
    except ValueError as error:
        raise AltocueError(f"Could not read the length of {path}.", fix="Check the file plays.") from error


# ---------------------------------------------------------------- check


def attrs(tag: str) -> dict[str, str]:
    return {key.lower(): value for key, value in ATTR.findall(tag)}


def to_timeline(timeline: dict[str, Any], seconds: float, takes: list[dict[str, Any]] | None = None, end: bool = False) -> float:
    """A moment of the cut on the reel's timeline. `takes` says where each take sits in the cut; a run from before
    takes followed the joins has one take that is the whole cut. An end moment belongs to the take it ends in."""
    placed = takes or [{"id": TAKE, "from": 0.0, "to": float("inf")}]
    holder = next((t for t in placed if t["from"] < seconds <= t["to"]) if end else (t for t in placed if t["from"] <= seconds < t["to"]), placed[-1])
    take = next((t for t in timeline.get("takes", []) if t.get("id") == holder["id"]), None)
    if not take:
        raise AltocueError(f"timeline.json has no {holder['id']}.", fix="Run Shorts Studio's measure step on the run first.")
    return seconds - holder["from"] - float(take.get("source_from", 0.0)) + float(take.get("timeline_start", 0.0))


def check_presenter(reel: dict[str, Any]) -> list[str]:
    return [f"{t.get('id')} was generated (job {t['job_id']}). The presenter must be the real take." for t in reel.get("takes", []) if t.get("job_id")]


def check_sounds(run: Path, html: str, record: dict[str, Any]) -> list[str]:
    if not record.get("sfx_only"):
        return []
    allowed = {s["sha256"] for s in record.get("sounds", [])}
    page = run / "composition"
    problems = []
    for tag in AUDIO_TAG.findall(html):
        found = attrs(tag)
        src, ident = found.get("src", ""), found.get("id", "")
        if ident in {"voice", "music", "bed"} or not src:
            continue
        path = (page / src).resolve()
        if not path.exists():
            problems.append(f"Sound {src} is not in the composition, so it cannot be checked against the pack.")
        elif sha256(path) not in allowed:
            problems.append(f"Sound {src} is not from the run's sound pack.")
    return problems


def check_covers(html: str, timeline: dict[str, Any], record: dict[str, Any]) -> list[str]:
    fps = float(timeline.get("fps", 30))
    slack = FRAME_SLACK / fps
    covers = []
    for tag in COVER_TAG.findall(html) + HIDDEN_TAG.findall(html):
        found = attrs(tag)
        try:
            start = float(found["data-start"])
            covers.append((start, start + float(found["data-duration"])))
        except (KeyError, ValueError):
            continue
    problems = []
    for number, span in enumerate(record.get("covers", [])):
        start, end = to_timeline(timeline, span["start"], record.get("takes")), to_timeline(timeline, span["end"], record.get("takes"), end=True)
        if not any(a <= start + slack and b >= end - slack for a, b in covers):
            problems.append(f"cover-{number + 1:02d} ({start:.2f} to {end:.2f} s) is not inside a hidden scene or a full-screen data-cover element for the whole moment.")
    return problems


def check_video_tags(html: str) -> list[str]:
    if VIDEO_TAG.search(html):
        return ["The composition has a <video> tag, which fails the Shorts Studio render. Use <img data-video> for the take."]
    return []


def scenes_in(html: str) -> list[tuple[str, float, float]]:
    found = []
    for tag in SCENE_TAG.findall(html):
        values = attrs(tag)
        try:
            start = float(values["data-start"])
            found.append((values["data-layout"].lower(), start, start + float(values["data-duration"])))
        except (KeyError, ValueError):
            continue
    return sorted(found, key=lambda scene: scene[1])


def face_spans(scenes: list[tuple[str, float, float]], length: float) -> list[tuple[float, float]]:
    """Where the take shows: the whole reel, less every hidden scene. Base, window and cutout scenes all show the face."""
    hidden = sorted((a, b) for layout, a, b in scenes if layout == "hidden")
    spans, at = [], 0.0
    for a, b in hidden:
        if a > at:
            spans.append((at, min(a, length)))
        at = max(at, b)
    if at < length:
        spans.append((at, length))
    return [(a, b) for a, b in spans if b - a > 1e-3]


def listen(path: Path, start: float = 0.0, seconds: float | None = None) -> np.ndarray:
    """The level of a file's sound in 20 ms steps, as RMS."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not path.exists():
        return np.zeros(0)
    window = ["-ss", f"{start:.4f}", *(["-t", f"{seconds:.4f}"] if seconds else [])]
    command = [ffmpeg, "-v", "error", *window, "-i", str(path), "-vn", "-ac", "1", "-ar", str(LISTEN_RATE), "-f", "f32le", "-"]
    samples = np.frombuffer(subprocess.run(command, capture_output=True, check=False).stdout, dtype=np.float32)
    count = len(samples) // LISTEN_HOP
    return np.sqrt((samples[: count * LISTEN_HOP].reshape(count, LISTEN_HOP).astype(np.float64) ** 2).mean(axis=1) + 1e-12)


def longest_quiet(loud: np.ndarray) -> float:
    """The longest run of quiet steps, in seconds."""
    best = run = 0
    for step in loud:
        run = 0 if step else run + 1
        best = max(best, run)
    return best * LISTEN_HOP / LISTEN_RATE


def voice_track(run: Path, html: str) -> Path | None:
    """The reel's voice on the master clock: the composition's `<audio id="voice">`."""
    for tag in AUDIO_TAG.findall(html):
        found = attrs(tag)
        if found.get("id") == "voice" and found.get("src"):
            path = (run / "composition" / found["src"]).resolve()
            return path if path.exists() else None
    return None


def check_face(html: str, timeline: dict[str, Any], house: dict[str, Any], voice: Path | None = None) -> list[str]:
    """The face shows only while it speaks, and never as a flash between two graphics.

    Word times find the pauses. With the reel's voice, each one is heard as well: the recognizer sometimes
    ends a word early, so a gap on paper with no quiet stretch of REAL_PAUSE in the sound is speech, not a pause.
    """
    length = float(timeline.get("master_duration", 0.0))
    words = [(float(w["start"]), float(w["end"])) for w in timeline.get("caption_words") or timeline.get("words") or [] if "start" in w and "end" in w]
    if not length or not words:
        return []
    scenes = scenes_in(html)
    level = listen(voice) if voice else np.zeros(0)
    loud = level > level.max() * 10 ** (-SPEECH_UNDER_PEAK_DB / 20) if len(level) else level
    step = LISTEN_HOP / LISTEN_RATE

    def silent(a: float, b: float) -> bool:
        return not len(loud) or longest_quiet(loud[int(a / step) : max(int(a / step) + 1, int(b / step))]) >= REAL_PAUSE

    problems = []
    for a, b in face_spans(scenes, length):
        between = a > 0 and b < length
        if between and b - a < house["min_face"]:
            problems.append(f"The face shows for only {b - a:.2f} s at {a:.2f} s, between two graphics. Hold the graphic, or show the face for a whole phrase.")
        at = a
        for start, end in sorted(words):
            if end <= a or start >= b:
                continue
            if start - at > house["silent_face"] and silent(at, start):
                problems.append(f"The face shows from {at:.2f} to {start:.2f} s with nothing said. Cover the pause with the graphic, or tighten it in the cut.")
            at = max(at, end)
        if b - at > house["silent_face"] and silent(at, b):
            problems.append(f"The face shows from {at:.2f} to {b:.2f} s with nothing said. Cover the pause with the graphic, or tighten it in the cut.")
    for layout, a, b in scenes:
        if b - a < house["min_scene"]:
            problems.append(f"A {layout} scene at {a:.2f} s lasts {b - a:.2f} s, a flash cut. Merge it into the scene beside it.")
    return problems


def on_screen_words(inner: str) -> int:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", inner, flags=re.IGNORECASE | re.DOTALL)
    return len(re.findall(r"[A-Za-z0-9][\w'%$.,/]*", re.sub(r"<[^>]+>", " ", text)))


def check_timing(html: str, timeline: dict[str, Any], house: dict[str, Any]) -> list[str]:
    """A full graphic short enough to hold attention. A longer one is split on a spoken word near its middle.

    Scenes that show the speaker are left alone: a face carries a long line.

    From six Reels: graphics over 10 s lost the viewer, and splitting them, each half keeping only its own parts, fixed it.
    """
    problems = []
    words = sorted(float(w["start"]) for w in timeline.get("caption_words") or timeline.get("words") or [] if "start" in w)
    for layout, a, b in scenes_in(html):
        if layout == "hidden" and b - a > house["max_scene"] + 1e-3:
            inside = [s for s in words if a + 1.0 < s < b - 1.0]
            middle = (a + b) / 2
            at = min(inside, key=lambda s: abs(s - middle)) if inside else middle
            problems.append(
                f"The full graphic at {a:.2f} s runs {b - a:.1f} s. Split it in two near {at:.2f} s, on a spoken word, each half keeping only its own parts."
            )
    return problems


def check_pacing(html: str, house: dict[str, Any]) -> list[str]:
    """Strong graphics, at a pace a viewer can take in: time to read each one, and not too many changes or sounds at once."""
    problems = []
    for tag, inner in SECTION.findall(html):
        values = attrs(tag)
        try:
            start, seconds = float(values["data-start"]), float(values["data-duration"])
        except (KeyError, ValueError):
            continue
        words = on_screen_words(inner)
        if not words:
            continue
        needed = max(house["min_graphic"], 0.3 + words / house["read_rate"])
        if seconds + 1e-3 < needed:
            problems.append(f"The graphic at {start:.2f} s holds {seconds:.2f} s for {words} words on screen. Hold it {needed:.1f} s, or cut words.")
    changes = sorted({round(a, 3) for _, a, _ in scenes_in(html) if a > 0})
    window = house["change_window"]
    for index, at in enumerate(changes):
        inside = [c for c in changes[index:] if c < at + window]
        if len(inside) > house["max_changes"]:
            problems.append(f"{len(inside)} scene changes between {at:.2f} and {inside[-1]:.2f} s. Nobody can take that in; hold the graphics longer.")
            break
    sounds = []
    for tag in AUDIO_TAG.findall(html):
        values = attrs(tag)
        if values.get("id") in ("voice", "music", "bed") or "data-start" not in values:
            continue
        try:
            sounds.append(float(values["data-start"]))
        except ValueError:
            continue
    sounds.sort()
    for index, at in enumerate(sounds):
        inside = [t for t in sounds[index:] if t < at + 1.0]
        if len(inside) > house["max_sounds"]:
            problems.append(f"{len(inside)} sound effects inside one second at {at:.2f} s. Keep the sounds to the moments that matter.")
            break
    return problems


def check_grain(html: str, house: dict[str, Any]) -> list[str]:
    problems = []
    for selector, body in GRAIN_RULE.findall(html):
        found = re.search(r"opacity\s*:\s*([\d.]+)", body)
        if found and float(found.group(1)) > house["grain_opacity"]:
            problems.append(
                f"Grain on {selector.strip()[-40:]} runs at opacity {float(found.group(1)):.2f}, over the house's {house['grain_opacity']:.2f}. Turn it down."
            )
    if GRAIN_PER_FRAME.search(html):
        problems.append("The grain moves to a new place every frame, which stutters on a phone. Hold it still or drift it slowly.")
    return problems


def check_house(html: str, reel: dict[str, Any], scenes: list[tuple[str, float, float]], length: float, house: dict[str, Any]) -> list[str]:
    """Shorts Studio upgrades the house look. It does not replace it: same end card, same layouts, same faces."""
    problems = []
    outro = house.get("outro")
    if outro and (reel.get("outro") or {}).get("id") != outro:
        problems.append(f"The end card is not the house's {outro!r}. Set reel.json outro to it.")
    if length:
        hidden = sum(b - a for a, b in [(a, b) for layout, a, b in scenes if layout == "hidden"])
        share = {"hidden": hidden / length}
        for layout in {layout for layout, _, _ in scenes} | set(house.get("layouts", {})):
            if layout != "hidden":
                share[layout] = sum(b - a for name, a, b in scenes if name == layout) / length
        for layout, bounds in (house.get("layouts") or {}).items():
            low, high = (float(bounds[0]), float(bounds[1])) if isinstance(bounds, list) else (float(bounds), 1.0)
            if not low <= share.get(layout, 0.0) <= high:
                problems.append(f"The {layout} layout fills {share.get(layout, 0.0):.0%} of the reel; the house keeps it between {low:.0%} and {high:.0%}.")
    allowed = {name.lower() for name in house.get("fonts", [])}
    if allowed:
        used = {name.strip().strip("'\"").lower() for found in re.findall(r"font-family\s*:\s*([^;}\n]+)", html) for name in found.split(",")}
        foreign = sorted(name for name in used - allowed if name and name not in ("sans-serif", "serif", "monospace", "inherit", "system-ui"))
        if foreign:
            problems.append(f"Faces outside the house set: {', '.join(foreign)}.")
    return problems


SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})")
SHORTEST_CUE = 0.3  # seconds; a shorter subtitle cue flashes by unread, and one near zero makes the muxer refuse the film


def check_cues(run: Path, timeline: dict[str, Any]) -> list[str]:
    """Captions and subtitles that nobody can read: a caption word of no length, or a subtitle cue that flashes by."""
    problems = []
    flat = [w for w in timeline.get("caption_words") or [] if float(w.get("end", 0)) - float(w.get("start", 0)) <= 0]
    if flat:
        problems.append(f"{len(flat)} caption words have no length, the first at {float(flat[0]['start']):.2f} s. Measure the run again.")
    for path in sorted(p for p in run.rglob("*.srt") if p.is_file()):
        for found in SRT_TIME.finditer(path.read_text(errors="replace")):
            h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(v) for v in found.groups())
            start, end = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000, h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
            if end - start < SHORTEST_CUE:
                problems.append(f"{path.relative_to(run)} has a {end - start:.3f} s cue at {start:.2f} s, too short to read. Join it to its neighbour.")
    return problems


def check_dashes(run: Path) -> list[str]:
    problems = []
    for path in sorted(p for p in (run / "composition").rglob("*") if p.is_file() and p.suffix in TEXT_FILES and p.name not in KIT_FILES):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for mark, name in DASHES.items():
                if mark in line:
                    problems.append(f"{path.relative_to(run)}:{number} has an {name}. Use a comma, colon or full stop.")
    return problems


def check_joins(html: str, timeline: dict[str, Any], record: dict[str, Any], house: dict[str, Any]) -> list[str]:
    """A full graphic starts and ends on a join in the footage when one is near, so the picture never changes twice
    in a second. Not onto a join inside another look-away: that edge would cut the look-away in two."""
    fps = float(timeline.get("fps", 30))
    slack, reach = 1.5 / fps, float(house["join_reach"])
    length = float(timeline.get("master_duration", 0.0))
    joins = sorted(float(t["timeline_start"]) for t in timeline.get("takes", [])[1:] if "timeline_start" in t)
    if not joins:
        return []
    takes = record.get("takes")
    away = []
    for span in record.get("covers", []):
        try:
            away.append((to_timeline(timeline, span["start"], takes), to_timeline(timeline, span["end"], takes, end=True)))
        except AltocueError:
            continue
    problems = []
    for layout, a, b in scenes_in(html):
        if layout != "hidden":
            continue

        def free(join: float, a: float = a, b: float = b) -> bool:
            return not any(lo < join < hi and not (a <= lo and hi <= b) for lo, hi in away)

        near = [j for j in joins if abs(j - a) <= reach and abs(j - a) > slack and free(j)]
        if near and not any(abs(j - a) <= slack for j in joins):
            j = min(near, key=lambda j: abs(j - a))
            problems.append(f"The graphic at {a:.2f} s starts {abs(j - a):.2f} s from a join in the footage at {j:.2f} s. Start it on the join.")
        near = [j for j in joins if abs(j - b) <= reach and abs(j - b) > slack and free(j) and j < length - 1.0]
        if near and not any(abs(j - b) <= slack for j in joins):
            j = min(near, key=lambda j: abs(j - b))
            problems.append(f"The graphic ending at {b:.2f} s ends {abs(j - b):.2f} s from a join in the footage at {j:.2f} s. End it on the join.")
    return problems


def _span_times(words: list[dict[str, Any]], missed: set[int], times: dict[int, tuple[float, float]], on: float, off: float, length: float) -> list[str]:
    """Give every run of missed words a time from the sound and its heard neighbours. Changes `times` in place."""
    notes, index = [], 0
    while index < len(words):
        if index not in missed:
            index += 1
            continue
        after = index
        while after < len(words) and after in missed:
            after += 1
        run = list(range(index, after))
        said = " ".join(str(words[k].get("word", "")).strip() for k in run)
        if index == 0:  # before the first heard word: from where the sound starts
            b = times[after][0]
            a = max(0.0, min(on, b - 0.12 * len(run)))
            if b - a < 0.08:  # the first heard word already starts at the top: share its time
                s0, e0 = times[after]
                a, b = s0, s0 + (e0 - s0) * 0.45
                times[after] = (b, e0)
            notes.append(f"{said!r} at the start now from {a:.2f} s (the sound starts at {on:.2f} s)")
        elif after == len(words):  # after the last heard word: to where the sound stops
            a = times[index - 1][1]
            b = min(length, max(off, a + 0.12 * len(run)))
            if b - a < 0.08:
                s0, e0 = times[index - 1]
                a, b = s0 + (e0 - s0) * 0.55, e0
                times[index - 1] = (s0, a)
            notes.append(f"{said!r} at the end now to {b:.2f} s (the sound stops at {off:.2f} s)")
        else:  # between two heard words: spread across the gap
            a, b = times[index - 1][1], times[after][0]
            if b - a < 0.06 * len(run):  # no room: borrow from the word before
                a = max(times[index - 1][0] + 0.05, b - 0.08 * len(run))
                times[index - 1] = (times[index - 1][0], a)
            notes.append(f"{said!r} in the line given a time")
        part = (b - a) / len(run)
        for n, k in enumerate(run):
            times[k] = (round(a + n * part, 3), round(a + (n + 1) * part, 3))
        index = after
    return notes


def fix_words(run: Path, dry: bool = False) -> list[str]:
    """Correct the measured word times of each take by its sound. Returns one line per take it changed.

    A word the recognizer did not hear has no length, and when it opens a take, Shorts Studio's measure trims the
    take from the first word it did hear, cutting the missed one off. Such a word gets a time from the sound: from
    where the sound starts, to where it stops, or across the gap between its heard neighbours. A gap between two
    words over the house's silent-face limit with no quiet stretch of REAL_PAUSE in the sound is closed. A changed
    take's transcript is rewritten and its measured file deleted, so the next measure trims it again.
    """
    lines = []
    for measured in sorted((run / "takes").glob("take-*.json")):
        record = json.loads(measured.read_text())
        words, base = record.get("caption_words") or [], float(record.get("source_from") or 0.0)
        missed = {i for i, w in enumerate(words) if w.get("interpolated") and float(w["end"]) - float(w["start"]) < MISSED_WORD}
        if not words or len(missed) == len(words):
            continue
        source = Path(str(record.get("source", "")))
        source = source if source.is_absolute() else run / source
        level = listen(source)
        if not len(level):
            lines.append(f"{measured.stem}: no sound at {source}, left as it was")
            continue
        loud = level > level.max() * 10 ** (-SPEECH_UNDER_PEAK_DB / 20)
        step = LISTEN_HOP / LISTEN_RATE
        steady = [i for i in range(len(loud) - 1) if loud[i] and loud[i + 1]]
        on, off = (steady[0] * step, (steady[-1] + 2) * step) if steady else (0.0, len(level) * step)
        times = {i: (float(w["start"]) + base, float(w["end"]) + base) for i, w in enumerate(words) if i not in missed}
        notes = _span_times(words, missed, times, on, off, len(level) * step)
        for k in range(len(words) - 1):
            (s0, e0), (s1, _) = times[k], times[k + 1]
            if s1 - e0 <= FALSE_PAUSE:
                continue
            quiet = longest_quiet(loud[int(e0 / step) : min(len(loud), int(s1 / step))])
            if quiet < REAL_PAUSE:
                times[k] = (s0, round(s1 - 0.02, 3))
                notes.append(f"the pause after {str(words[k].get('word', '')).strip()!r} closed: {s1 - e0:.2f} s on paper, {quiet:.2f} s quiet in the sound")
        if not notes:
            continue
        lines.append(f"{measured.stem}: " + "; ".join(notes))
        if dry:
            continue
        fixed, previous = [], 0.0
        for k, word in enumerate(words):
            start, end = times[k]
            start = max(start, previous)
            fixed.append({"word": " " + str(word.get("word", "")).strip(), "start": round(start, 3), "end": round(max(end, start), 3), "probability": 1.0})
            previous = start
        transcript_path = run / "transcripts" / f"{measured.stem}.json"
        transcript = json.loads(transcript_path.read_text()) if transcript_path.exists() else {}
        provider = str(transcript.get("provider", "")).replace("+sound-fix", "")
        transcript.update(words=fixed, text=" ".join(str(w.get("word", "")).strip() for w in words), provider=provider + "+sound-fix")
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(json.dumps(transcript, indent=1))
        measured.unlink()
    return lines


def check_gaze(render: Path, html: str, lens: float) -> list[str]:
    """Scan the finished render and fail any look-away the viewer can see. Hidden scenes are left out: a graphic there can read as a face."""
    from . import gaze

    if not render.exists():
        return []
    hidden = [(a, b) for layout, a, b in scenes_in(html) if layout == "hidden"]
    rows = [r for r in gaze.scan(render) if not any(a - gaze.MARGIN <= r["t"] <= b + gaze.MARGIN for a, b in hidden)]
    spans, _ = gaze.away_spans(rows, lens, no_face=False)
    return [f"The speaker looks off the lens from {s['start']:.2f} to {s['end']:.2f} s of the render ({s['reason']}), with no graphic over it." for s in spans]


def find_gaze(args: argparse.Namespace) -> Path:
    from . import gaze

    project = Project.open(args.project) if args.project else None
    video = project.work / "plate.mp4" if project else Path(args.video).expanduser()
    if not video.exists():
        raise AltocueError(f"No video at {video}.", fix="Run `altocue plate` first." if project else "Pass a video that exists.")
    lens = args.lens if args.lens is not None else float((project.config.get("shorts", {}) if project else {}).get("lens", gaze.LENS))
    rows = gaze.scan(video)
    spans, blinks = gaze.away_spans(rows, lens)
    out = Path(args.out).expanduser() if args.out else (project.root / "gaze.json" if project else video.with_name(f"{video.stem}-gaze.json"))
    write_json(out, {"video": str(video), "lens": lens, "samples": len(rows), "spans": spans, "blinks": [list(b) for b in blinks], "rows": rows})
    print(f"{len(spans)} look-away {'span' if len(spans) == 1 else 'spans'} in {len(rows)} samples, {len(blinks)} blinks. Written to {out}")
    for span in spans:
        print(f"  {span['start']:.2f} to {span['end']:.2f} s: {span['reason']}")
    if project:
        print(f"Pass it on: altocue shorts prepare -p {project.root} --cover {out}")
    return out


def check_length(render: Path, timeline: dict[str, Any]) -> list[str]:
    if not render.exists():
        return [f"No render at {render}. A skipped check is not a pass."]
    seconds = probe_duration(render)
    expected = float(timeline.get("master_duration", 0.0))
    outro = float((timeline.get("outro") or {}).get("duration", 0.0) or 0.0)
    if expected and abs(seconds - expected) > 2 / float(timeline.get("fps", 30)) and abs(seconds - expected - outro) > 2 / float(timeline.get("fps", 30)):
        return [f"The render runs {seconds:.2f} s and the measured reel {expected:.2f} s."]
    return []


def check(run: Path, render: Path | None, gaze_lens: float | None = None) -> list[str]:
    def read(name: str) -> dict[str, Any]:
        path = run / name
        if not path.exists():
            raise AltocueError(
                f"No {name} in {run}.",
                fix="Point at the run folder. Shorts Studio's pack leaves out timeline.json, so add it to composition.zip before bringing it back.",
            )
        return json.loads(path.read_text())

    record, reel, timeline = read("altocue-shorts.json"), read("reel.json"), read("timeline.json")
    page = run / "composition" / "index.html"
    if not page.exists():
        raise AltocueError(f"No composition at {page}.", fix="Run Shorts Studio's scaffold and build the composition first.")
    html = page.read_text(errors="replace")
    house = {**HOUSE_DEFAULTS, **(json.loads((run / HOUSE_FILE).read_text()) if (run / HOUSE_FILE).exists() else {})}
    scenes, length = scenes_in(html), float(timeline.get("master_duration", 0.0))
    problems = (
        check_presenter(reel)
        + check_sounds(run, html, record)
        + check_covers(html, timeline, record)
        + check_video_tags(html)
        + check_dashes(run)
        + check_cues(run, timeline)
    )
    problems += check_joins(html, timeline, record, house)
    problems += check_face(html, timeline, house, voice_track(run, html)) + check_pacing(html, house) + check_timing(html, timeline, house) + check_grain(html, house)
    problems += check_house(html, reel, scenes, length, house)
    final = render or run / "renders" / "final.mp4"
    if gaze_lens is not None:
        problems += check_gaze(final, html, gaze_lens)
    return problems + check_length(final, timeline)


def run(args: argparse.Namespace) -> int:
    if args.action == "prepare":
        done = prepare(args)
        print(f"Run folder: {done['run']}")
        takes = "1 take" if done["takes"] == 1 else f"{done['takes']} takes, one per line"
        print(f"Bundle: {done['bundle']} ({done['seconds']:.1f} s in {takes}, {len(done['spans'])} cover beats, {done['sounds']} sounds)")
        print("Uploading the bundle sends the footage to Higgsfield. Do it only once the owner has agreed.")
        return 0
    if args.action == "frames":
        from .sheets import contact_sheets

        run_dir = Path(args.run_dir).expanduser().resolve()
        render = Path(args.render).expanduser().resolve() if args.render else run_dir / "renders" / "final.mp4"
        for sheet in contact_sheets(render, run_dir / "review", args.every):
            print(sheet)
        print("Look at every frame yourself. A passing check is not a review.")
        return 0
    if args.action == "fix-words":
        lines = fix_words(Path(args.run_dir).expanduser().resolve(), args.dry)
        for line in lines:
            print(line)
        if not lines:
            print("Every word already has a time the sound agrees with.")
        elif not args.dry:
            print("Run Shorts Studio's measure step again, so it trims the corrected takes.")
        return 0
    if args.action == "gaze":
        find_gaze(args)
        return 0
    run_dir = Path(args.run_dir).expanduser().resolve()
    lens = None
    if args.gaze:
        from . import gaze

        lens = args.lens
        if lens is None:
            record = json.loads((run_dir / "altocue-shorts.json").read_text()) if (run_dir / "altocue-shorts.json").exists() else {}
            owner = Path(record.get("project") or "")
            config = Project.open(owner).config if record.get("project") and (owner / "altocue.toml").exists() else {}
            lens = float(config.get("shorts", {}).get("lens", gaze.LENS))
    problems = check(run_dir, Path(args.render).expanduser().resolve() if args.render else None, lens)
    for problem in problems:
        print(f"FAIL {problem}")
    print("Shorts check passed." if not problems else f"{len(problems)} problems.")
    return 1 if problems else 0
