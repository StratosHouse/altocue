"""A film with no source clip: a brief and a storyboard go in, a finished film comes out.

`altocue launch <folder> --product "Name"` starts a launch film. `altocue film` does everything else.
The storyboard is the source of truth, and nothing in this module writes to it after `new`.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import brand as brands
from . import ffmpeg, flash, locks, master, mix, score, storyboard, study
from .errors import AltocueError
from .motion import compositions, formats, hyperframes, scenes
from .project import CONFIG, SIZES, Project, write_json

ORDER = ("launch", "explainer", "demo", "ad", "gauge", "case", "line", "music")
LOCK = "storyboard.lock.json"
FOLDERS = ("assets", "assets/voice", "assets/music", "generated", "motion")
DRAFT_FPS = 30
STILL_AT = 0.62  # share of a scene where everything has landed and nothing has left
SHEET_WIDTH = 480
PROOF_FRAMES = 30
REAL_FOOTAGE = (
    (
        "Reference Reel",
        "Your clips in the style of a reel you admire. Measure the reel with `altocue film study`, then edit with `altocue new`, `cut`, `caption`, `motion`.",
    ),
    ("Clip Cut", "One statement from a long recording, as a short. Use `altocue clips`, then `frame`, `caption` and `master`."),
    ("Product Proof Spot", "An ad cut from real product footage. Use `altocue new`, `direct`, `plate`, `grade`, `motion`."),
)


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    if name == "launch":
        parser.add_argument("folder", help="Project folder to create")
        parser.add_argument("--product", required=True, help="The product's name")
        parser.add_argument("--variant", help="Length: 30, 24 (journey), 20 (object) or 15 (workflow)")
        return
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("types", help="List film types, with their promise and cost")
    new = actions.add_parser("new", help="Start a film from a working example")
    new.add_argument("type", help="A type from `altocue film types`")
    new.add_argument("folder", help="Project folder to create")
    new.add_argument("--variant", help="One of the type's lengths or shapes")
    new.add_argument("--product", help="The product or subject's name")
    for name_, summary in (("check", "Check the brief and storyboard"), ("approve", "Accept the current scene order, after the person agrees")):
        one = actions.add_parser(name_, help=summary)
        one.add_argument("--project", "-p", default=".", help="Project folder")
        one.add_argument("--json", action="store_true", help="Print the findings as JSON")
    stills = actions.add_parser("stills", help="One still per scene and a contact sheet, before any full render")
    stills.add_argument("--project", "-p", default=".", help="Project folder")
    stills.add_argument("--aspect", help="An aspect to check. Default is the master's")
    score_ = actions.add_parser("score", help="Write the film's music and report what it is")
    score_.add_argument("--project", "-p", default=".", help="Project folder")
    render = actions.add_parser("render", help="Render the master and any cutdowns")
    render.add_argument("--project", "-p", default=".", help="Project folder")
    render.add_argument("--cutdowns", default="", help="Aspects to re-render from the storyboard, such as 9:16,1:1")
    render.add_argument("--only-cutdowns", action="store_true", help="Leave the master alone, and prove it was left alone")
    render.add_argument("--draft", action="store_true", help="Low frame rate and quick encode, for a look")
    variants = actions.add_parser("variants", help="Render one storyboard many ways, swapping text layers")
    variants.add_argument("plan", help="A variants.json")
    variants.add_argument("--project", "-p", default=".", help="Project folder")
    variants.add_argument("--aspect", help="Render at this aspect. Default is the master's")
    variants.add_argument("--draft", action="store_true", help="Low frame rate and quick encode")
    studied = actions.add_parser("study", help="Measure a reference film into a style card")
    studied.add_argument("video", help="A film to measure")
    studied.add_argument("--output", "-o", help="Where to write the card. Default is style-card.json beside you")
    studied.add_argument("--no-text", action="store_true", help="Skip reading on-screen text")
    motion_ = actions.add_parser("qa", help="Motion and phone-read checks on the finished films")
    motion_.add_argument("--project", "-p", default=".", help="Project folder")


# Types


def type_ids() -> list[str]:
    return [kind for kind in ORDER if brands.data_path("films", f"{kind}.json").is_file()]


def load_type(kind: str) -> dict[str, Any]:
    path = brands.data_path("films", f"{kind}.json")
    if not path.is_file():
        raise AltocueError(f"Unknown film type {kind}.", fix=f"Use one of {', '.join(type_ids())}. `altocue film types` lists them.")
    return json.loads(path.read_text())


def variants_of(record: dict[str, Any]) -> list[str]:
    return list(record.get("variants", {}))


def example(record: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    """A filled brief and storyboard for this type, so a person sees a working film first."""
    chosen = str(name or record["default"])
    if chosen not in record["variants"]:
        raise AltocueError(f"{record['id']} has no variant {chosen}.", fix=f"Use one of {', '.join(variants_of(record))}.")
    variant = record["variants"][chosen]
    made = {"brief": {**copy.deepcopy(record["brief"]), **copy.deepcopy(variant.get("brief", {}))}, "storyboard": copy.deepcopy(variant["storyboard"])}
    made["storyboard"].setdefault("type", record["id"])
    return made


def estimated_voice(board: dict[str, Any]) -> dict[str, float]:
    return {str(scene["id"]): storyboard.estimated(scene) for scene in board.get("scenes", []) if scene.get("seconds") == "voice"}


def run_types() -> int:
    for kind in type_ids():
        record = load_type(kind)
        shapes = ", ".join(variants_of(record))
        print(f"{kind:<10} {record['name']}. {record['promise']}\n{'':<11}Cost: {record['cost']} Generation: {record['generation']}. Lengths: {shapes}")
    print("\nThese need real footage, so they run on the editing stages:")
    for name, how in REAL_FOOTAGE:
        print(f"  {name}: {how}")
    return 0


def new_film(kind: str, folder: str, *, variant: str | None = None, product: str | None = None) -> Project:
    record = load_type(kind)
    made = example(record, variant)
    project = Project.create(folder, aspect=record.get("aspect", "16:9"))
    board, brief = made["storyboard"], made["brief"]
    if product:
        before = str(board.get("layers", {}).get("product", ""))
        board.setdefault("layers", {})["product"] = product
        text = json.dumps(brief, ensure_ascii=False)
        brief = json.loads(text.replace(before, product)) if before else brief
    config = project.root / CONFIG
    config.write_text(re.sub(r"^fps = 0 ", f"fps = {record.get('fps', 30)} ", config.read_text(), count=1, flags=re.M))
    for name in FOLDERS:
        (project.root / name).mkdir(parents=True, exist_ok=True)
    write_json(project.root / "brief.json", brief)
    write_json(project.root / "storyboard.json", board)
    write_json(project.root / LOCK, {"scenes": [scene["id"] for scene in board["scenes"]]})
    for note in record.get("notes", []):
        print(f"Note: {note}")
    print(f"Film ready at {project.root}. Next: write brief.json and storyboard.json for the real product, then `altocue film check`.")
    return Project.open(project.root)


# Loading a project


@dataclass
class Film:
    project: Project
    brief: dict[str, Any]
    board: dict[str, Any]
    record: dict[str, Any]
    approved: list[str] | None
    card: dict[str, Any] | None = None
    voice: dict[str, float] = field(default_factory=dict)


def read(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise AltocueError(f"No {what} at {path}.", fix="Run `altocue film new <type> <folder>` first.")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise AltocueError(f"{path.name} is not valid JSON: {error}.", fix="Fix the file and run this again.") from None


def voice_lengths(project: Project, board: dict[str, Any]) -> dict[str, float]:
    """How long each recorded line really runs. The timeline is built from these."""
    found = {}
    for scene in board.get("scenes", []):
        relative = scene.get("voice")
        if relative and (project.root / relative).is_file():
            found[str(scene["id"])] = ffmpeg.probe_clip(project.root / relative).duration
    return found


def load(project: Project) -> Film:
    board = read(project.root / "storyboard.json", "storyboard")
    lock = project.root / LOCK
    card_name = board.get("style_card")
    card = read(project.root / card_name, "style card") if card_name else None
    return Film(
        project=project,
        brief=read(project.root / "brief.json", "brief"),
        board=board,
        record=load_type(str(board.get("type", "launch"))),
        approved=json.loads(lock.read_text()).get("scenes") if lock.exists() else None,
        card=card,
        voice=voice_lengths(project, board),
    )


def problems_in(film: Film) -> list[storyboard.Problem]:
    voice = {**estimated_voice(film.board), **film.voice}
    return storyboard.check(film.brief, film.board, film.record, approved=film.approved, voice=voice, card=film.card, fps=film.project.fps)


def print_problems(problems: list[storyboard.Problem]) -> int:
    errors = [p for p in problems if p.level == "error"]
    for problem in problems:
        label = "ERROR" if problem.level == "error" else "WARN "
        print(f"{label} {problem.where}: {problem.message}")
        if problem.fix:
            print(f"      Fix: {problem.fix}")
    print(f"{len(errors)} errors, {len(problems) - len(errors)} warnings.")
    return 1 if errors else 0


def require_clean(film: Film) -> None:
    problems = problems_in(film)
    if any(p.level == "error" for p in problems):
        print_problems(problems)
        raise AltocueError("The storyboard does not pass its checks, so nothing was rendered.", fix="Fix what is listed, then run this again.")


def brand_of(film: Film) -> brands.Brand:
    default = json.loads(brands.data_path("brands", "default.json").read_text())
    extra = film.brief.get("brand") or {}
    merged = {
        **default,
        "colors": {**default["colors"], **(extra.get("colors") or {})},
        "font": {**default["font"], **(extra.get("font") or {})},
        "logo": extra.get("logo"),
    }
    if extra.get("name"):
        merged["name"] = extra["name"]
    for key, value in merged["colors"].items():
        if not brands.COLOUR.fullmatch(str(value)):
            raise AltocueError(f"The brand colour {key} is {value!r}.", fix="Use six-digit hex in brief.json, such as #ff7a59.")
    return brands.Brand(merged, film.project.root if extra.get("logo") or extra.get("font") else brands.data_path("brands"))


def music_of(film: Film) -> Path | None:
    """A track the person chose, from the storyboard or from `altocue music get`. None means Altocue writes the score."""
    named = (film.board.get("music") or {}).get("path")
    motion_file = film.project.root / "motion.json"
    if not named and motion_file.exists():
        named = (json.loads(motion_file.read_text()).get("music") or {}).get("path")
    if not named:
        return None
    path = (film.project.root / named).resolve()
    if not path.is_file():
        raise AltocueError(f"No music at {named}.", fix="Check the path in storyboard.json, or remove it to have Altocue write a score.")
    return path


def track_beats(project: Project, track: Path) -> list[float]:
    """The beat map of a chosen track, from the renderer's own detector, kept until the file changes."""
    digest = hashlib.sha256(track.read_bytes()).hexdigest()[:16]
    cache = project.work / "film" / f"beats-{digest}.json"
    if cache.exists():
        return json.loads(cache.read_text())["beats"]
    if not hyperframes.binary().exists():
        raise AltocueError("Beat detection needs the renderer.", fix="Run `altocue setup`.")
    done = subprocess.run([str(hyperframes.binary()), "beats", str(track), "--json"], capture_output=True, text=True, env=hyperframes.quiet_env(), check=False)
    if done.returncode:
        raise AltocueError(f"The renderer could not read beats from {track.name}.", fix="Try another file, or leave `snap` off.")
    found = json.loads(done.stdout)
    write_json(cache, {"beats": found["beatTimes"], "bpm": found.get("bpm")})
    return found["beatTimes"]


def music_settings(film: Film) -> dict[str, Any]:
    chosen = film.board.get("music") or {}
    style = score.style_for(str(film.board.get("type", "launch")), chosen.get("style"))
    seed = int(chosen.get("seed", score.seed_of(str(film.board.get("title") or film.project.root.name))))
    return {"style": style, "seed": seed, "tempo": chosen.get("tempo")}


def timing(film: Film, aspect: str, *, board: dict[str, Any] | None = None, fps: int | None = None) -> dict[str, Any]:
    """Everything in film seconds, with cues on the music's grid where a cue asks for it."""
    filled = storyboard.fill(board or film.board)
    rate = fps or film.project.fps
    voice = {**estimated_voice(filled), **film.voice}
    track = music_of(film)
    wants_grid = filled.get("snap") or any(cue.get("snap") for scene in filled.get("scenes", []) for cue in scene.get("sound", []))
    if track:
        beats = track_beats(film.project, track) if wants_grid else None
        return storyboard.resolve(filled, fps=rate, aspect=aspect, beats=beats, voice=voice)
    done = storyboard.resolve(filled, fps=rate, aspect=aspect, voice=voice)
    if wants_grid:
        settings = music_settings(film)
        beats = score.grid(done["scenes"], style=settings["style"], seed=settings["seed"], tempo=settings["tempo"])["beats"]
        unsnapped = {key: value for key, value in filled.items() if key != "snap"}
        done = storyboard.resolve(unsnapped, fps=rate, aspect=aspect, beats=beats, voice=voice)
    return done


def film_line(film: Film, aspect: str, *, board: dict[str, Any] | None = None, fps: int | None = None) -> tuple[dict[str, Any], brands.Brand]:
    """The timeline for one aspect. Every aspect comes from the same storyboard, so nothing is ever cropped."""
    if aspect not in SIZES:
        raise AltocueError(f"Unknown aspect {aspect}.", fix=f"Use one of {', '.join(SIZES)}.")
    filled = storyboard.fill(board or film.board)
    done = timing(film, aspect, board=board, fps=fps)
    brand = brand_of(film)
    line = scenes.timeline(done, size=SIZES[aspect], fps=fps or film.project.fps, brand=brand, board=filled)
    line["items"] = [ready(film.project, item) for item in line["items"]]
    line["backdrops"] = [ready(film.project, item) for item in line["backdrops"]]
    return line, brand


def ready(project: Project, item: dict[str, Any]) -> dict[str, Any]:
    """Point a graphic at the files it needs on the stage."""
    if item["type"] == "composition":
        return compositions.resolve(project, item)
    if item["type"] == "footage":
        path = (project.root / item["file"]).resolve()
        if not path.is_file() or not path.is_relative_to(project.root.resolve()):
            raise AltocueError(f"No footage at {item['file']}.", fix="Keep generated clips in generated/ and your own in assets/.")
        return {**item, "stage_images": {formats.stage_name(item["file"]): str(path)}}
    return formats.place_images(project.root, item)


def slug(aspect: str) -> str:
    return aspect.replace(":", "x")


def stage_for(project: Project, line: dict[str, Any], brand: brands.Brand, place: str) -> Path:
    stage = project.work / "film" / place / "stage"
    page = scenes.compose(line, brand)
    return hyperframes.build_stage(stage, scenes.everything(line), brand, page)


def render_picture(stage: Path, output: Path, *, fps: int, draft: bool, total: float) -> Path:
    """The whole frame, painted by the renderer. A film has no plate under it."""
    command = [str(hyperframes.binary()), "render", str(stage), "--format", "mp4", "--output", str(output), "--fps", str(fps), "--quiet"]
    command += ["--quality", "draft" if draft else "delivery", "--workers", str(hyperframes.render_workers())]
    if draft:
        command += ["--page-motion-blur", "off"]
    with locks.heavy("rendering the film"):
        done = subprocess.run(command, check=False, capture_output=True, text=True, env=hyperframes.quiet_env())
    if done.returncode != 0 or not output.exists():
        ending = "\n".join((done.stdout + done.stderr).strip().splitlines()[-10:])
        raise AltocueError(f"HyperFrames could not render the film:\n{ending}", fix="Run `altocue doctor`.")
    return output


# Sound


def voice_track(project: Project, board: dict[str, Any], done: dict[str, Any]) -> Path | None:
    """Every recorded line at its scene's start. A film with no lines has no voice track, and the music leads."""
    if not any(scene.get("voice") and (project.root / str(scene["voice"])).is_file() for scene in board.get("scenes", [])):
        return None
    count = round(done["total"] * mix.RATE)
    track = np.zeros(count)
    starts = {scene["id"]: scene["start"] for scene in done["scenes"]}
    for scene in board.get("scenes", []):
        path = project.root / str(scene.get("voice", ""))
        if not scene.get("voice") or not path.is_file():
            continue
        wav = project.work / "film" / "voice" / f"{scene['id']}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.extract_wav(path, wav)
        line, _ = ffmpeg.read_wav(wav)
        begin = round(starts[scene["id"]] * mix.RATE)
        end = min(begin + len(line), count)
        track[begin:end] += line[: end - begin]
    dest = project.work / "film" / "voice.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return ffmpeg.write_wav(dest, track, mix.RATE)


def cues_of(line: dict[str, Any], done: dict[str, Any]) -> list[dict[str, Any]]:
    """The storyboard's cues and a sound on each link. The music carries every other cut, so nothing else plays by default."""
    made = [{key: value for key, value in cue.items() if key != "planned"} for cue in done["cues"]]
    for link in line.get("links", []):
        fmt = formats.find(link["type"])
        if fmt and fmt.sound and link.get("sound") is not False:
            made.append({"name": fmt.sound, "at": (link["start"] + link["end"]) / 2 - 0.1})
    return sorted(made, key=lambda cue: cue["at"])


def scored(film: Film, done: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """The music for these exact timings. Change a scene and it is written again to fit."""
    settings = music_settings(film)
    silences = done.get("silences") or []
    recipe = {
        "scenes": [[s["id"], s.get("beat"), s.get("energy"), s["start"], s["end"]] for s in done["scenes"]],
        "settings": settings,
        "hero": done.get("hero"),
        "silences": silences,
        "total": done["total"],
    }
    key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:16]
    folder = film.project.work / "film"
    wav, info_file = folder / f"score-{key}.wav", folder / f"score-{key}.json"
    if wav.exists() and info_file.exists():
        return wav, json.loads(info_file.read_text())
    wav, info = score.build(wav, done["scenes"], done["total"], hero=done.get("hero"), silences=silences, **settings)
    write_json(info_file, info)
    return wav, info


def make_sound(film: Film, line: dict[str, Any], done: dict[str, Any], board: dict[str, Any], folder: Path) -> tuple[Path, dict[str, Any]]:
    track = music_of(film)
    voice = voice_track(film.project, board, done)
    report: dict[str, Any]
    if track:
        music, report = track, {"made_by": "you", "label": f"Chosen track: {track.name}."}
    else:
        music, report = scored(film, done)
        warning = score.remember(film.project.root.name, report)
        if warning:
            print(f"Warning: {warning}")
            report = {**report, "warning": warning}
    dest = folder / "mix.wav"
    mix.mix_bed(voice, dest, music=music, sfx=cues_of(line, done), seconds=done["total"], music_end="land" if track else "own")
    return dest, report


# Rendering


def frame_hashes(path: Path, count: int = PROOF_FRAMES) -> list[str]:
    """A checksum of frames spread across a film, so a claim that it did not change can be checked."""
    clip = ffmpeg.probe_clip(path)
    hashes = []
    for index in range(count):
        at = clip.duration * (index + 0.5) / count
        done = subprocess.run(
            [ffmpeg.require(), "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=320:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True,
            check=True,
        )
        hashes.append(hashlib.sha256(done.stdout).hexdigest()[:12])
    return hashes


def render_film(project: Project, *, board: dict[str, Any], name: str, aspect: str, draft: bool, sound: tuple[Path, dict[str, Any]] | None = None) -> Path:
    """Picture, sound and master for one storyboard at one aspect."""
    film = load(project)
    fps = DRAFT_FPS if draft else project.fps
    line, brand = film_line(film, aspect, board=board, fps=fps)
    place = slug(aspect)
    stage = stage_for(project, line, brand, place)
    folder = stage.parent
    done = timing(film, aspect, board=board, fps=fps)
    filled = storyboard.fill(board)
    audio, report = sound or make_sound(film, line, done, filled, folder)
    picture = render_picture(stage, folder / "picture.mp4", fps=fps, draft=draft, total=line["total"])
    muxed = folder / "render.mp4"
    ffmpeg.run(
        [
            "-i",
            str(picture),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "alac",
            "-t",
            f"{line['total']:.3f}",
            "-movflags",
            "+faststart",
            str(muxed.with_suffix(".mov")),
        ]
    )
    dest = project.out / f"{name}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    loudness, peak = master.project_target(project)
    result = master.master_file(muxed.with_suffix(".mov"), dest, loudness=loudness, true_peak=peak)
    rows = {
        "file": str(dest.relative_to(project.root)),
        "aspect": aspect,
        "fps": fps,
        "draft": draft,
        "seconds": line["total"],
        "scenes": [[s["id"], s["start"], s["end"]] for s in line["scenes"]],
        "master": result,
        "score": report,
    }
    rows["flash"] = flash.measure(dest) if not _stub(picture) else None
    write_json(project.review_dir / f"film-{name.replace('/', '-')}.json", rows)
    return dest


def _stub(path: Path) -> bool:
    return not path.exists() or path.stat().st_size < 1024


def run_render(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    film = load(project)
    require_clean(film)
    master_aspect = str(project.setting("output", "aspect", "16:9"))
    cutdowns = [part.strip() for part in args.cutdowns.split(",") if part.strip()]
    for aspect in cutdowns:
        if aspect not in SIZES:
            raise AltocueError(f"Unknown aspect {aspect}.", fix=f"Use one of {', '.join(SIZES)}.")
    final = project.out / "final.mp4"
    report: dict[str, Any] = {"films": []}
    if not args.only_cutdowns:
        render_film(project, board=film.board, name="final", aspect=master_aspect, draft=args.draft)
        report["films"].append("final.mp4")
        line, _ = film_line(film, master_aspect, fps=DRAFT_FPS if args.draft else project.fps)
        write_json(project.timeline_path, line)
    if cutdowns and not final.exists():
        raise AltocueError("There is no master to cut down from.", fix="Run `altocue film render` first.")
    before = hashlib.sha256(final.read_bytes()).hexdigest() if cutdowns else ""
    frames_before = frame_hashes(final) if cutdowns else []
    for aspect in cutdowns:
        render_film(project, board=film.board, name=f"final-{slug(aspect)}", aspect=aspect, draft=args.draft)
        report["films"].append(f"final-{slug(aspect)}.mp4")
    if cutdowns:
        same = before == hashlib.sha256(final.read_bytes()).hexdigest() and frames_before == frame_hashes(final)
        report["master_unchanged"] = {"frames_checked": PROOF_FRAMES, "same": same}
        if not same:
            raise AltocueError("A cutdown changed the master.", fix="Re-render the master, then report this as a bug.")
    summary = json.loads((project.review_dir / "film-final.json").read_text())
    report.update(score=summary.get("score"), master=summary.get("master"), flash=summary.get("flash"))
    write_json(project.review_dir / "film.json", report)
    for name in report["films"]:
        print(f"Rendered out/{name}")
    music = report.get("score") or {}
    if music.get("made_by") == "altocue":
        print(f"{music['label']} {music['style']}, {music['bpm']:g} bpm, seed {music['seed']}. {music['listen']}")
    if report.get("master_unchanged"):
        print(f"The master did not change: {PROOF_FRAMES} frames checked.")
    return 0 if (report.get("master") or {}).get("passed", True) else 1


# Stills


def still_times(line: dict[str, Any]) -> list[float]:
    times = []
    for scene in line["scenes"]:
        chosen = scene.get("still")
        share = float(chosen) if storyboard.number(chosen) else (scene["end"] - scene["start"]) * STILL_AT
        times.append(round(scene["start"] + share, 3))
    return times


def snapshot(stage: Path, times: list[float], out: Path) -> list[Path]:
    """Exact instants from the renderer, with the page seeked as it will be in the film."""
    out.mkdir(parents=True, exist_ok=True)
    moments = ",".join(f"{at:.3f}" for at in times)
    command = [str(hyperframes.binary()), "snapshot", str(stage), "--at", moments, "--output", str(out), "--no-end", "--keep"]
    with locks.heavy("taking stills"):
        done = subprocess.run(command, capture_output=True, text=True, env=hyperframes.quiet_env(), check=False)
    if done.returncode:
        raise AltocueError(f"The renderer could not take stills:\n{(done.stdout + done.stderr)[-800:]}", fix="Run `altocue doctor`.")
    return [out / f"at-{f'{at:.3f}'.rstrip('0').rstrip('.')}s.png" for at in times]


def sheet(frames: list[Path], labels: list[str], dest: Path, *, columns: int = 3, width: int = SHEET_WIDTH) -> Path:
    tiles = []
    for path, label in zip(frames, labels, strict=True):
        with Image.open(path) as image:
            tile = image.convert("RGB")
        tile = tile.resize((width, round(width * tile.height / tile.width)), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(tile)
        draw.rectangle((0, 0, width, 22), fill=(0, 0, 0))
        draw.text((6, 5), label, fill=(255, 255, 255))
        tiles.append(tile)
    tall = tiles[0].height
    rows = math.ceil(len(tiles) / columns)
    canvas = Image.new("RGB", (columns * width + (columns - 1) * 4, rows * tall + (rows - 1) * 4), (40, 40, 40))
    for index, tile in enumerate(tiles):
        canvas.paste(tile, ((index % columns) * (width + 4), (index // columns) * (tall + 4)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, quality=88)
    return dest


def layout_report(stage: Path, times: list[float]) -> list[dict[str, Any]]:
    """What the renderer finds at each still: text cut off, boxes overflowing, anything past the edge of the frame."""
    at = ",".join(f"{moment:.3f}" for moment in times)
    command = [str(hyperframes.binary()), "check", str(stage), "--json", "--at", at, "--no-contrast"]
    with locks.heavy("checking the layout"):
        done = subprocess.run(command, capture_output=True, text=True, env=hyperframes.quiet_env(), check=False)
    start = done.stdout.find("{")
    if start < 0:
        return [{"code": "check_failed", "message": (done.stdout + done.stderr)[-400:]}]
    try:
        found = json.loads(done.stdout[start:])
    except json.JSONDecodeError:
        return [{"code": "check_failed", "message": done.stdout[-400:]}]
    return layout_issues(found)


def layout_issues(found: dict[str, Any]) -> list[dict[str, Any]]:
    keep = []
    for section in ("runtime", "layout", "motion", "craft"):
        for row in (found.get(section) or {}).get("findings") or []:
            if row.get("severity") in ("warning", "error"):
                keep.append(
                    {
                        "check": section,
                        "code": row.get("code"),
                        "message": row.get("message"),
                        "at": row.get("time"),
                        "selector": row.get("selector"),
                        "severity": row.get("severity"),
                        "text": row.get("text"),
                    }
                )
    return keep


def run_stills(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    film = load(project)
    require_clean(film)
    aspect = args.aspect or str(project.setting("output", "aspect", "16:9"))
    line, brand = film_line(film, aspect)
    stage = stage_for(project, line, brand, slug(aspect))
    times = still_times(line)
    out = project.out / "stills" / slug(aspect)
    frames = snapshot(stage, times, out)
    labels = [f"{scene['id']}  {at:.2f}s" for scene, at in zip(line["scenes"], times, strict=True)]
    contact = sheet(frames, labels, out / "contact.jpg")
    issues = layout_report(stage, times)
    from .qa import film as film_qa

    readable = film_qa.phone_read(project, line, frames, times)
    write_json(
        project.review_dir / f"stills-{slug(aspect)}.json", {"times": times, "contact": str(contact.relative_to(project.root)), "layout": issues, "phone": readable}
    )
    print(f"One still per scene in {out}\nContact sheet: {contact}")
    for issue in issues:
        print(f"LAYOUT {issue.get('at', '')}: {issue.get('code')}: {issue.get('message')}")
    failed = [row for row in readable["rows"] if not row["passed"]]
    for row in readable["rows"]:
        print(f'{"PASS" if row["passed"] else "FAIL"} {row["scene"]}: "{row["text"][:40]}" {row["detail"]}')
    print(f"{len(issues)} layout findings, {len(failed)} phone read failures. Open the contact sheet before any full render.")
    return 1 if failed or any(issue.get("severity") == "error" for issue in issues) else 0


# Variants


def apply_variant(board: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    """One storyboard, one change: only the named layers move, and the scenes stay exactly as they are."""
    layers = board.get("layers") or {}
    for name in variant.get("set") or {}:
        if name not in layers:
            raise AltocueError(f'Variant {variant.get("id")} sets the layer "{name}", which the storyboard does not have.', fix=f"Use one of {', '.join(layers)}.")
    made = copy.deepcopy(board)
    made["layers"] = {**layers, **variant.get("set", {})}
    return made


def copy_table(board: dict[str, Any], plan: list[dict[str, Any]]) -> str:
    layers = board.get("layers") or {}
    rows = [
        "# Copy table",
        "",
        "Read this before anything renders.",
        "",
        "| Layer | Master | " + " | ".join(v["id"] for v in plan) + " |",
        "|---|---|" + "---|" * len(plan),
    ]
    for name, value in layers.items():
        cells = [f"**{v['set'][name]}**" if name in v.get("set", {}) else "" for v in plan]
        rows.append(f"| {name} | {value} | " + " | ".join(cells) + " |")
    return "\n".join(rows) + "\n"


def run_variants(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    film = load(project)
    require_clean(film)
    plan = read(Path(args.plan), "variants file").get("variants") or []
    if not plan:
        raise AltocueError("No variants in that file.", fix='Write {"variants": [{"id": "demo", "set": {"cta": "Book a demo"}}]}.')
    ids = [str(variant.get("id", "")) for variant in plan]
    if len(set(ids)) != len(ids) or not all(re.fullmatch(r"[\w-]+", name) for name in ids):
        raise AltocueError("Every variant needs its own plain id.", fix="Use letters, numbers and hyphens.")
    boards = [apply_variant(film.board, variant) for variant in plan]
    failures = []
    for variant, board in zip(plan, boards, strict=True):
        found = storyboard.check(film.brief, board, film.record, voice={**estimated_voice(board), **film.voice}, fps=project.fps)
        failures += [f"{variant['id']}: {problem.message}" for problem in found if problem.level == "error"]
    if failures:
        print("\n".join(failures))
        raise AltocueError("A variant does not fit, so nothing was rendered.", fix="Shorten the copy in the variants file.")
    table = project.review_dir / "variants.md"
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text(copy_table(film.board, plan))
    print(f"Copy table: {table}")
    for variant in plan:
        if len(variant.get("set", {})) > 1:
            print(f"Note: {variant['id']} changes more than one layer, so its results cannot be compared with one change at a time.")
    aspect = args.aspect or str(project.setting("output", "aspect", "16:9"))
    for variant, board in zip(plan, boards, strict=True):
        dest = render_film(project, board=board, name=f"variants/{variant['id']}", aspect=aspect, draft=args.draft)
        print(f"Rendered {dest}")
    return 0


def run_score(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    film = load(project)
    done = timing(film, str(project.setting("output", "aspect", "16:9")))
    if music_of(film):
        print("This film uses a chosen track, so no score was written.")
        return 0
    path, info = scored(film, done)
    print(f"{path}\n{info['label']} {info['style']}, {info['bpm']:g} bpm, key of {info['key']}, seed {info['seed']}.")
    print(f"Styles: {', '.join(score.STYLES)}. Set one under music in storyboard.json.\n{info['listen']}")
    warning = score.remember(project.root.name, info)
    if warning:
        print(f"Warning: {warning}")
    return 0


def run_study(args: argparse.Namespace) -> int:
    card = study.study(args.video, read_text=not args.no_text)
    dest = Path(args.output or "style-card.json")
    write_json(dest, card)
    print(f"Style card: {dest}")
    print(f"{card['shots']} shots, {card['average_shot']} s on average, {card['cuts_per_10s']} cuts per 10 s.")
    print(f"Palette: {', '.join(card['palette'])}")
    print(study.RULE)
    return 0


def run_qa(args: argparse.Namespace) -> int:
    from .qa import film as film_qa

    project = Project.open(args.project)
    film = load(project)
    return film_qa.run_film(project, film)


def run(args: argparse.Namespace) -> int:
    if args.command == "launch":
        new_film("launch", args.folder, variant=args.variant, product=args.product)
        return 0
    action = args.action
    if action == "types":
        return run_types()
    if action == "new":
        new_film(args.type, args.folder, variant=args.variant, product=args.product)
        return 0
    if action == "study":
        return run_study(args)
    if action in ("check", "approve"):
        project = Project.open(args.project)
        film = load(project)
        if action == "approve":
            write_json(project.root / LOCK, {"scenes": [scene["id"] for scene in film.board.get("scenes", [])]})
            print("The current scene order is now approved.")
            return 0
        problems = problems_in(film)
        if args.json:
            print(json.dumps([problem.__dict__ for problem in problems], indent=2))
            return 1 if any(p.level == "error" for p in problems) else 0
        return print_problems(problems)
    return {"stills": run_stills, "render": run_render, "variants": run_variants, "score": run_score, "qa": run_qa}[action](args)
