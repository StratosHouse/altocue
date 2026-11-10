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

from .errors import AltocueError
from .project import Project, write_json

TAKE = "take-01"
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
}
SECTION = re.compile(r"(<section\b[^>]*\bdata-layout\s*=\s*\"[a-z]+\"[^>]*>)(.*?)</section>", re.IGNORECASE | re.DOTALL)
ATTR = re.compile(r'([a-z-]+)\s*=\s*"([^"]*)"', re.IGNORECASE)
CUE_WORDS = 4
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
    check = sub.add_parser("check", help="Check a Shorts Studio run: presenter, sounds, look-away covers, dashes, length")
    check.add_argument("run_dir", help="The run folder, or the unzipped composition.zip, with reel.json and timeline.json")
    check.add_argument("--render", help="The final render. Defaults to renders/final.mp4 in the run folder")


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


def project_take(project: Project, run: Path, until: float = 0.0) -> tuple[Path, list[dict[str, Any]]]:
    """The plate and its edited voice, muxed into one take, plus the kept words in take seconds."""
    from . import cut

    plate, voice = project.work / "plate.mp4", project.work / "voice.wav"
    if not plate.exists() or not voice.exists():
        raise AltocueError("This project has no plate yet.", fix="Run `altocue plate` first. Shorts Studio designs on the finished cut.")
    words = [w for w in cut.map_words(project, project.edl()) if w.get("kind", "word") == "word"]
    if not words:
        raise AltocueError("The cut keeps no spoken words.", fix="Shorts Studio needs a talking take. Check the cut with `altocue cut`.")
    seconds = 0.0
    if until:
        words = [w for w in words if w["end"] <= until] or words[:1]
        seconds = words[-1]["end"] + 0.4
    target = run / "media" / "takes" / f"{TAKE}.mp4"
    mux(plate, voice, target, seconds)
    return target, words


def mux(picture: Path, voice: Path, target: Path, seconds: float = 0.0) -> None:
    """Join picture and voice. A trim re-encodes the picture, so it ends on the frame asked for, not the next keyframe."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AltocueError("ffmpeg is not on PATH.", fix="Run `altocue doctor`.")
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(picture), "-i", str(voice), "-map", "0:v:0", "-map", "1:a:0"]
    picture_codec = ["-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p", "-t", f"{seconds:.3f}"] if seconds else ["-c:v", "copy"]
    command += [*picture_codec, "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(target)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AltocueError(f"ffmpeg could not join the plate and voice: {result.stderr.strip()[-400:]}")


def dialogue_of(words: list[dict[str, Any]]) -> str:
    return " ".join(str(w["text"]).strip() for w in words if str(w["text"]).strip())


def cue_phrase(words: list[str]) -> str:
    return " ".join(words[:CUE_WORDS])


def plan_lines(words: list[dict[str, Any]], dialogue: str, spans: list[dict[str, float]], caption_words: int) -> str:
    """The edit plan's skeleton: one beat per sentence, and one cover beat per look-away span.

    Cue phrases are copied from the dialogue word for word, because Shorts Studio times a beat by
    finding its phrase in the take.
    """
    lines = ["# Edit plan", "", "Real footage. The presenter is the person in the take: never generate, recast or redraw them.", ""]
    tokens = dialogue.split(" ")
    for number, sentence in enumerate(s for s in SENTENCE.split(dialogue) if s.strip()):
        lines += [f"Beat: line-{number + 1:02d}", f'Cue: {TAKE} "{cue_phrase(sentence.split(" "))}"', ""]
    for number, span in enumerate(spans):
        inside = [i for i, w in enumerate(words) if w["start"] >= span["start"] - 0.05 and w["start"] < span["end"]]
        near = inside[0] if inside else min(range(len(words)), key=lambda i: abs(words[i]["start"] - span["start"]))
        phrase = cue_phrase(tokens[near:])
        lines += [
            f"Beat: cover-{number + 1:02d}",
            f'Cue: {TAKE} "{phrase}"',
            f"Cover {span['start']:.2f} to {span['end']:.2f} s of the take ({span['reason']}) with a full-screen graphic that hides the presenter.",
            f'Put the moment in a data-layout="hidden" scene, or mark its element data-cover="cover-{number + 1:02d}",',
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
    """House build helpers, such as a shared stylesheet and motion module, so every Reel's build.py starts from the same look."""
    if not kit.is_dir():
        raise AltocueError(f"No kit folder at {kit}.", fix="Set [shorts] kit to a folder of build helpers, or pass --kit.")
    copied = []
    for path in sorted(p for p in kit.iterdir() if p.is_file() and not p.name.startswith(".")):
        target = run / path.name if path.suffix == ".py" or path.name == HOUSE_FILE else run / "media" / path.name
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
        take, words = project_take(project, run, args.until)
        dialogue = dialogue_of(words)
        aspect = args.aspect or project.config["output"]["aspect"]
    else:
        if not args.script:
            raise AltocueError("--take needs --script.", fix="Pass a text file holding exactly what the take says.")
        dialogue = " ".join(Path(args.script).expanduser().read_text().split())
        source = Path(args.take).expanduser()
        if not source.exists():
            raise AltocueError(f"No take at {source}.")
        take = run / "media" / "takes" / f"{TAKE}.mp4"
        take.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, take)
        words = []
        aspect = args.aspect or "9:16"

    cover = args.cover or settings.get("cover", "")
    cover_path = (base / cover).expanduser() if cover and not Path(cover).is_absolute() else Path(cover) if cover else None
    spans = load_spans(cover_path) if cover_path else []
    if spans and not words:
        raise AltocueError("Cover spans need word timings.", fix="Use --project, so each cover can be cued on the words spoken during it.")
    spans = inside_take(spans, probe_duration(take))
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
                "id": TAKE,
                "dialogue": dialogue,
                "pronunciations": [],
                "duration": round(probe_duration(take), 3),
                "job_id": None,
                "url": None,
                "local_path": f"media/takes/{TAKE}.mp4",
                "matte": None,
            }
        ],
        "outro": None,
        "media": [{"id": "real-take", "kind": "video", "source": "supplied", "role": "host-source", "purpose": "presenter", "local_path": f"media/takes/{TAKE}.mp4"}],
    }
    write_json(run / "reel.json", reel)
    (run / "script.md").write_text(dialogue + "\n")
    (run / "edit-plan.md").write_text(plan_lines(words, dialogue, spans, caption_words) if words else plan_lines([], dialogue, [], caption_words))
    (run / "HOUSE.md").write_text(house_notes(sounds, sfx_only, spans, kit, json.loads((run / HOUSE_FILE).read_text()) if (run / HOUSE_FILE).exists() else None))
    write_json(
        run / "altocue-shorts.json",
        {
            "take": f"media/takes/{TAKE}.mp4",
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
    return {"run": run, "bundle": archive, "spans": spans, "sounds": len(sounds or []), "seconds": reel["takes"][0]["duration"]}


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


def to_timeline(timeline: dict[str, Any], seconds: float) -> float:
    take = next((t for t in timeline.get("takes", []) if t.get("id") == TAKE), None)
    if not take:
        raise AltocueError(f"timeline.json has no {TAKE}.", fix="Run Shorts Studio's measure step on the run first.")
    return seconds - float(take.get("source_from", 0.0)) + float(take.get("timeline_start", 0.0))


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
        start, end = to_timeline(timeline, span["start"]), to_timeline(timeline, span["end"])
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


def check_face(html: str, timeline: dict[str, Any], house: dict[str, Any]) -> list[str]:
    """The face shows only while it speaks, and never as a flash between two graphics."""
    length = float(timeline.get("master_duration", 0.0))
    words = [(float(w["start"]), float(w["end"])) for w in timeline.get("caption_words") or timeline.get("words") or [] if "start" in w and "end" in w]
    if not length or not words:
        return []
    scenes = scenes_in(html)
    problems = []
    for a, b in face_spans(scenes, length):
        between = a > 0 and b < length
        if between and b - a < house["min_face"]:
            problems.append(f"The face shows for only {b - a:.2f} s at {a:.2f} s, between two graphics. Hold the graphic, or show the face for a whole phrase.")
        at = a
        for start, end in sorted(words):
            if end <= a or start >= b:
                continue
            if start - at > house["silent_face"]:
                problems.append(f"The face shows from {at:.2f} to {start:.2f} s with nothing said. Cover the pause with the graphic, or tighten it in the cut.")
            at = max(at, end)
        if b - at > house["silent_face"]:
            problems.append(f"The face shows from {at:.2f} to {b:.2f} s with nothing said. Cover the pause with the graphic, or tighten it in the cut.")
    for layout, a, b in scenes:
        if b - a < house["min_scene"]:
            problems.append(f"A {layout} scene at {a:.2f} s lasts {b - a:.2f} s, a flash cut. Merge it into the scene beside it.")
    return problems


def on_screen_words(inner: str) -> int:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", inner, flags=re.IGNORECASE | re.DOTALL)
    return len(re.findall(r"[A-Za-z0-9][\w'%$.,/]*", re.sub(r"<[^>]+>", " ", text)))


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


def check_dashes(run: Path) -> list[str]:
    problems = []
    for path in sorted(p for p in (run / "composition").rglob("*") if p.is_file() and p.suffix in TEXT_FILES and p.name not in KIT_FILES):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for mark, name in DASHES.items():
                if mark in line:
                    problems.append(f"{path.relative_to(run)}:{number} has an {name}. Use a comma, colon or full stop.")
    return problems


def check_length(render: Path, timeline: dict[str, Any]) -> list[str]:
    if not render.exists():
        return [f"No render at {render}. A skipped check is not a pass."]
    seconds = probe_duration(render)
    expected = float(timeline.get("master_duration", 0.0))
    outro = float((timeline.get("outro") or {}).get("duration", 0.0) or 0.0)
    if expected and abs(seconds - expected) > 2 / float(timeline.get("fps", 30)) and abs(seconds - expected - outro) > 2 / float(timeline.get("fps", 30)):
        return [f"The render runs {seconds:.2f} s and the measured reel {expected:.2f} s."]
    return []


def check(run: Path, render: Path | None) -> list[str]:
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
    problems = check_presenter(reel) + check_sounds(run, html, record) + check_covers(html, timeline, record) + check_video_tags(html) + check_dashes(run)
    problems += check_face(html, timeline, house) + check_pacing(html, house) + check_grain(html, house) + check_house(html, reel, scenes, length, house)
    return problems + check_length(render or run / "renders" / "final.mp4", timeline)


def run(args: argparse.Namespace) -> int:
    if args.action == "prepare":
        done = prepare(args)
        print(f"Run folder: {done['run']}")
        print(f"Bundle: {done['bundle']} ({done['seconds']:.1f} s take, {len(done['spans'])} cover beats, {done['sounds']} sounds)")
        print("Uploading the bundle sends the footage to Higgsfield. Do it only once the owner has agreed.")
        return 0
    problems = check(Path(args.run_dir).expanduser().resolve(), Path(args.render).expanduser().resolve() if args.render else None)
    for problem in problems:
        print(f"FAIL {problem}")
    print("Shorts check passed." if not problems else f"{len(problems)} problems.")
    return 1 if problems else 0
