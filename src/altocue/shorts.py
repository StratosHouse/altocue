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
            f'Mark its element data-cover="cover-{number + 1:02d}" with data-start and data-duration that span the whole moment.',
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def house_notes(sounds: list[dict[str, Any]] | None, sfx_only: bool, spans: list[dict[str, float]]) -> str:
    notes = [
        "# House rules for this run",
        "",
        "- The take is real footage, supplied. Generate no presenter take, no recast and no lip sync. reel.json keeps `job_id` null on every take.",
        "- Keep the speaker's own words. Trim nothing the measure step does not trim.",
        "- On-screen text uses commas, colons or full stops. Never an em dash or an en dash.",
    ]
    if spans:
        notes.append(f"- {len(spans)} look-away {'moment is a cover beat' if len(spans) == 1 else 'moments are cover beats'} in edit-plan.md.")
        notes.append("  Each gets a full-screen graphic marked `data-cover`, so the presenter never shows looking off the lens.")
    if sounds is not None:
        rule = "from media/sfx/ first."
        if sfx_only:
            rule = "only from media/sfx/, as listed in sfx-pack.json. Do not place Shorts Studio's library sounds."
        notes.append(f"- Sound effects come {rule} Place each as an <audio> tag in the composition.")
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
    if args.force:
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
    (run / "HOUSE.md").write_text(house_notes(sounds, sfx_only, spans))
    write_json(
        run / "altocue-shorts.json",
        {
            "take": f"media/takes/{TAKE}.mp4",
            "covers": spans,
            "sfx_only": bool(sounds is not None and sfx_only),
            "sounds": sounds or [],
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
    for tag in COVER_TAG.findall(html):
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
            problems.append(f"cover-{number + 1:02d} ({start:.2f} to {end:.2f} s) has no full-screen data-cover element over the whole moment.")
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
            raise AltocueError(f"No {name} in {run}.", fix="Point at the run folder, after Shorts Studio's measure and render.")
        return json.loads(path.read_text())

    record, reel, timeline = read("altocue-shorts.json"), read("reel.json"), read("timeline.json")
    page = run / "composition" / "index.html"
    if not page.exists():
        raise AltocueError(f"No composition at {page}.", fix="Run Shorts Studio's scaffold and build the composition first.")
    html = page.read_text(errors="replace")
    problems = check_presenter(reel) + check_sounds(run, html, record) + check_covers(html, timeline, record) + check_dashes(run)
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
