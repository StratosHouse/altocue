#!/usr/bin/env python3
"""Build the Altocue feature film's project from the tool itself. Dev tool, needs the demo clips and a finished launch film.

Every number on screen is a `{live:...}` token, filled at build time from the tool itself: the format count, the
library, the gates that ship, the looks, real measurements of the demo clips, and the QA and verdicts of a real film.
`live.json` records where each one landed, and `altocue film check` reads them all again, so a stale number fails.

    python scripts/feature_film.py <folder> --demo <demo clips> --reviewed <a reviewed film project> [--stock <key name>]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from altocue import ffmpeg, film, library, live
from altocue.motion import formats
from altocue.project import CONFIG, Project, write_json

ROOT = Path(__file__).resolve().parents[1]
CAP = 90.0
ICONS = ("cut", "clean", "grade", "frame", "broll", "caption", "motion", "direct", "generate", "review")
NAMES = {"broll": "B-roll"}
LOOK_PICKS = (0, 1, 8, 10)  # natural, warm, mono_contrast and teal_orange in the compare sheet
LOOK_NAMES = ("natural", "warm", "mono contrast", "teal orange")
TAKE_B_FROM = 1.05  # seconds into final-b.mp4, after "Here's the best part."


def seconds_of(path: Path) -> float:
    return float(ffmpeg.probe_json(path)["format"]["duration"])


def envelope(path: Path, bars: int = 72) -> list[float]:
    """The real level of a file, in equal slices, on one fixed scale so two files can be compared."""
    done = subprocess.run([ffmpeg.require(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True, check=True)
    samples = np.frombuffer(done.stdout, np.float32)
    parts = np.array_split(samples, bars)
    return [round(float(min(max(np.sqrt(np.mean(part**2)) * 3.2, 0.02), 1.0)), 3) for part in parts]


def matched_frames(raw: Path, final: Path, folder: Path) -> tuple[Path, Path]:
    """The same moment in both files, found by looking, so the colour comparison is a true one."""
    target = folder / "graded.png"
    ffmpeg.frame(final, 5.0, target, width=540)
    with Image.open(target) as image:
        wanted = np.asarray(image.convert("L").resize((96, 170)), np.float32)
    best, best_at = 1e9, 0.0
    probe = folder / "probe.png"
    for at in np.arange(0.5, seconds_of(raw) - 0.5, 0.25):
        ffmpeg.frame(raw, float(at), probe, width=270)
        with Image.open(probe) as image:
            found = np.asarray(image.convert("L").resize((96, 170)), np.float32)
        score = float(np.abs(found - found.mean() - (wanted - wanted.mean())).mean())
        if score < best:
            best, best_at = score, float(at)
    probe.unlink(missing_ok=True)
    source = folder / "raw.png"
    ffmpeg.frame(raw, best_at, source, width=540)
    return source, target


def look_crops(sheet: Path, folder: Path) -> list[Path]:
    """Cut single looks out of the real `altocue grade --compare` sheet."""
    made = []
    with Image.open(sheet) as image:
        width, tall = image.size
        columns, rows = 5, 3
        cell_w, cell_h = width // columns, tall // rows
        label = 30
        for pick in LOOK_PICKS:
            x, y = (pick % columns) * cell_w, (pick // columns) * cell_h
            path = folder / f"look-{pick}.png"
            image.crop((x + 2, y + 2, x + cell_w - 2, y + cell_h - label)).save(path)
            made.append(path)
    return made


def search_rows(catalog: dict[str, Any], words: str) -> list[dict[str, str]]:
    found = library.search(catalog, words)[:4]
    return [{"text": f"{row['id'][:24]}  {row['tier']}", "kind": "ok" if row["tier"] == "built-in" else "dim"} for row in found]


def stock_clip(project: Project, words: str) -> dict[str, Any] | None:
    """A real clip through Altocue's own stock route, with its licence recorded."""
    from altocue import broll, stock

    found = stock.search("pixabay", words, kind="video", shape="landscape", limit=3)
    if not found:
        return None
    option = stock.option(found[0])
    source = project.root / "assets" / "broll.mp4"
    broll.fetch_option(project, option, source)
    return option


def kept_clip(project: Project) -> dict[str, Any] | None:
    """The clip an earlier build fetched, with the licence it recorded, so a rebuild needs no download."""
    record = project.root / "stock.json"
    if not (project.root / "assets" / "broll.mp4").is_file() or not record.is_file():
        return None
    return json.loads(record.read_text()) or None


def gate_rows() -> list[dict[str, str]]:
    return [
        {"text": "Loudness", "label": "{live:loudness} LUFS"},
        {"text": "True peak", "label": "{live:true_peak} dBTP"},
        {"text": "Black frames", "label": "{live:black}"},
        {"text": "Frozen frames", "label": "{live:frozen}"},
        {"text": "Flashes in the worst second", "label": "{live:flash}"},
    ]


def numbers_for(reviewed: Path, groups: list[str]) -> dict[str, dict[str, object]]:
    """Where every number on screen is read from. Nothing here is a value."""
    launch = str(reviewed)
    made: dict[str, dict[str, object]] = {name: {"source": {"count": name}} for name in ("formats", "library", "gates", "looks")}
    for name in groups:
        made[f"group_{name}"] = {"source": {"count": f"groups.{name}"}}
    for name, read in (
        ("passed", "passed"),
        ("failed", "failed"),
        ("na", "not_applicable"),
        ("loudness", "loudness"),
        ("true_peak", "true_peak"),
        ("black", "black_frames"),
        ("frozen", "frozen_frames"),
        ("flash", "flash_rate"),
    ):
        made[name] = {"source": {"qa": launch, "read": read}}
    for name, number in (("first", 0), ("last", -1)):
        made[f"{name}_verdict"] = {"source": {"review": launch, "round": number, "read": "verdict"}}
        made[f"{name}_score"] = {"source": {"review": launch, "round": number, "read": "weighted"}, "format": ".2f"}
    for name, file in (("raw", "assets/raw-a.mp4"), ("cut", "assets/final-a.mp4")):
        made[f"{name}_seconds"] = {"source": {"file": file, "read": "seconds"}, "format": ".1f"}
        made[f"{name}_lufs"] = {"source": {"file": file, "read": "integrated"}}
        made[f"{name}_peak"] = {"source": {"file": file, "read": "true_peak"}}
    return made


def build(folder: Path, demo: Path, reviewed: Path, looks: Path, stock: bool) -> None:
    # built again in place: the last render and its review stay until a new render replaces them
    project = Project.open(folder) if (folder / CONFIG).is_file() else film.new_film("demo", str(folder))
    assets = project.root / "assets"
    for name in ("raw-a", "final-a", "raw-b", "final-b"):
        shutil.copy(demo / f"{name}.mp4", assets / f"{name}.mp4")
    logos = assets / "logos"
    logos.mkdir(exist_ok=True)
    shutil.copy(ROOT / "assets" / "logos" / "altocue.svg", logos / "altocue.svg")
    for name in ICONS:
        shutil.copy(ROOT / "assets" / "logos" / f"{name}-tile.svg", logos / f"{name}-tile.svg")
    raw_a, final_a, raw_b, final_b = (assets / f"{name}.mp4" for name in ("raw-a", "final-a", "raw-b", "final-b"))

    catalog = library.current()
    groups: dict[str, int] = {}
    for fmt in formats.load().values():
        groups[fmt.group] = groups.get(fmt.group, 0) + 1
    chart = [name for name, _ in sorted(groups.items(), key=lambda pair: -pair[1])[:6]]

    speech = assets / "voice"
    speech.mkdir(exist_ok=True)
    ffmpeg.run(["-i", str(final_a), "-vn", "-ar", "48000", "-ac", "1", str(speech / "final-a.wav")])
    # the second take starts at "It fixes the sound": under the score, the transcriber QA uses cannot hear its opening line
    ffmpeg.run(["-ss", f"{TAKE_B_FROM:.2f}", "-i", str(final_b), "-vn", "-ar", "48000", "-ac", "1", str(speech / "final-b.wav")])
    frame_raw, frame_graded = matched_frames(raw_a, final_a, assets)
    crops = look_crops(looks, assets)
    clip = stock_clip(project, "ocean waves aerial") if stock else kept_clip(project)
    search_words = "route map"
    found = search_rows(catalog, search_words)
    names = {
        "kinetic": "Type",
        "product": "UI",
        "paper": "Paper",
        "data": "Data",
        "explain": "Ideas",
        "proof": "Proof",
        "teach": "Teach",
        "maps": "Maps",
        "iso": "Iso",
        "film": "Film",
        "wipe": "Wipes",
        "backdrop": "Scenes",
    }

    def stamp(text: str, **over: Any) -> dict[str, Any]:
        return {"type": "corner_stamp", "text": text, "corner": "tr", "start": 0.0, **over}

    def say(text: str, **over: Any) -> dict[str, Any]:
        """The line a narrator would say, for a viewer with the sound off."""
        return {"type": "say_line", "text": text, "zone": "foot", "start": 0.2, **over}

    scenes: list[dict[str, Any]] = [
        {
            "id": "open",
            "beat": "hook",
            "seconds": 2.6,
            "verb": "promise",
            "energy": 3,
            "carry": "the line falls into the footage",
            "camera": {"move": "push", "amount": 0.1},
            "link": {"type": "dive", "seconds": 0.9, "zoom": 1.6, "why": "We fall through the promise into the raw take it is about."},
            "items": [
                {
                    "type": "word_slam",
                    "text": "You film it. *Your agent edits it.*",
                    "gap": 0.2,
                    "lead": 0.3,
                    "start": 0,
                    "zone": "center",
                    "align": "center",
                    "role": "hook",
                    "important": True,
                }
            ],
            "sound": [{"name": "rise", "at": 1.5}],
        },
        {
            "id": "cut",
            "beat": "example",
            "seconds": 10.6,
            "verb": "cut",
            "energy": 5,
            "carry": "the same speaker keeps talking",
            "shows": ["raw take to finished cut"],
            "voice": "assets/voice/final-a.wav",
            "voice_offset": -0.4,
            "camera": {"move": "drift"},
            "link": {"type": "dissolve", "seconds": 0.7, "why": "Two bright phones sliding off measured at the flash limit, so the takes fade."},
            "items": [
                {"type": "mask_rise", "lines": ["Raw take", "*to finished cut*"], "start": 0.0, "zone": "left"},
                {"type": "footage", "file": "assets/raw-a.mp4", "zone": "phone_a", "label": "Raw, {live:raw_seconds} s", "start": -0.4, "important": False},
                {
                    "type": "footage",
                    "file": "assets/final-a.mp4",
                    "zone": "phone_b",
                    "label": "Cut, {live:cut_seconds} s",
                    "start": -0.4,
                    "important": False,
                    "captions": {"spell": {"AltaQ": "Altocue"}},  # the transcriber hears the name this way
                },
            ],
        },
        {
            "id": "captions",
            "beat": "example",
            "seconds": 9.5,
            "verb": "caption",
            "energy": 5,
            "carry": "the words move to the colour",
            "shows": ["captions"],
            "voice": "assets/voice/final-b.wav",
            "voice_offset": -0.3,
            "camera": {"move": "push", "amount": 0.04},
            "link": {"type": "through", "seconds": 0.7, "why": "We push through the face into the same frame, corrected."},
            "items": [
                {"type": "mask_rise", "lines": ["Captions", "*to the word*"], "start": -0.3, "zone": "left"},
                {
                    "type": "footage",
                    "file": "assets/final-b.mp4",
                    "zone": "phone",
                    "from": TAKE_B_FROM,
                    "start": -0.3,
                    "important": False,
                    "captions": {"spell": {"color": "colour"}},  # British spelling, as the clip's own caption has it
                },
            ],
        },
        {
            "id": "colour",
            "beat": "example",
            "seconds": 5.5,
            "verb": "correct",
            "energy": 5,
            "carry": "the sweep turns into the waveform",
            "shows": ["colour"],
            "camera": {"move": "push", "amount": 0.04},
            "link": {"type": "dissolve", "seconds": 0.7, "why": "A bright face sliding off flashed, so the picture fades into the sound."},
            "items": [
                {"type": "mask_rise", "lines": ["Colour", "*per clip*"], "start": -0.3, "zone": "left"},
                {
                    "type": "split_wipe",
                    "frames": [
                        {"text": "Raw", "image": str(frame_raw.relative_to(project.root))},
                        {"text": "Finished", "image": str(frame_graded.relative_to(project.root))},
                    ],
                    "start": -0.3,
                    "zone": "right",
                    "important": False,
                },
            ],
        },
        {
            "id": "sound",
            "beat": "example",
            "seconds": 5,
            "verb": "clean",
            "energy": 6,
            "carry": "the level steps up into the formats",
            "shows": ["audio clean-up"],
            "camera": {"move": "orbit", "turn": -3, "tilt": 3, "amount": 0.03},
            "link": {"type": "through", "seconds": 0.7, "why": "The louder take pushes into the graphics."},
            "items": [
                {
                    "type": "waveform",
                    "title": "Sound *cleaned and mastered*",
                    "start": -0.3,
                    "zone": "center",
                    "rows": [
                        {"text": "Raw", "note": "{live:raw_lufs} LUFS, {live:raw_peak} dBTP", "values": envelope(raw_a)},
                        {"text": "Finished", "note": "{live:cut_lufs} LUFS, {live:cut_peak} dBTP", "values": envelope(final_a), "hot": True},
                    ],
                },
                say("The same take, cleaned and set to {live:cut_lufs} LUFS."),
            ],
        },
        {
            "id": "formats",
            "beat": "reveal",
            "seconds": 3,
            "verb": "count",
            "energy": 7,
            "carry": "the count opens onto the first format",
            "shows": [],
            "camera": {"move": "push", "amount": 0.06},
            "link": {"type": "shutter", "label": "Shutter", "why": "A hard card change starts the run of formats."},
            "items": [
                {
                    "type": "count_up",
                    "value": "{live:formats}",
                    "label": "*original* motion formats",
                    "start": -0.3,
                    "zone": "center",
                    "align": "center",
                    "run": 1.0,
                    "rate": 9,
                    "important": True,
                },
                say("Each one is drawn in code, not stock."),
            ],
            "sound": [{"name": "tick", "at": 0.1}],
        },
        {
            "id": "kinetic",
            "beat": "reveal",
            "seconds": 3,
            "verb": "show",
            "energy": 8,
            "carry": "the wipe",
            "shows": ["kinetic type"],
            "link": {"type": "iris", "label": "Iris", "why": "Each format hands over with a wipe from the library."},
            "items": [
                stamp("KINETIC TYPE"),
                {"type": "decode", "text": "EVERY *WORD* MOVES", "start": 0.1, "zone": "center", "align": "center"},
                say("Type that moves, in your colours."),
            ],
        },
        {
            "id": "chart",
            "beat": "reveal",
            "seconds": 3.5,
            "verb": "show",
            "energy": 8,
            "carry": "the wipe",
            "shows": ["charts"],
            "link": {"type": "curtain", "label": "Curtain", "why": "Each format hands over with a wipe from the library."},
            "items": [
                stamp("CHARTS"),
                {
                    "type": "bar_build",
                    "title": "Altocue's own {live:formats} formats, *by group*",
                    "bars": [{"text": names[name], "value": f"{{live:group_{name}}}"} for name in chart],
                    "start": 0.1,
                    "zone": "center",
                },
                say("Charts drawn from real numbers, like these."),
            ],
        },
        {
            "id": "map",
            "beat": "reveal",
            "seconds": 3.5,
            "verb": "show",
            "energy": 8,
            "carry": "the wipe",
            "shows": ["maps"],
            "link": {"type": "shape_match", "label": "Shape match", "why": "Each format hands over with a wipe from the library."},
            "items": [
                {
                    "type": "flight_route",
                    "text": "Real maps, *drawn in code*",
                    "stops": [
                        {"text": "Lisbon", "lon": -9.14, "lat": 38.72},
                        {"text": "Nairobi", "lon": 36.82, "lat": -1.29},
                        {"text": "Singapore", "lon": 103.82, "lat": 1.35},
                    ],
                    "start": 0.0,
                },
                say("Routes drawn on real coordinates."),
            ],
        },
        {
            "id": "paper",
            "beat": "reveal",
            "seconds": 3,
            "verb": "show",
            "energy": 8,
            "carry": "the wipe",
            "shows": ["paper"],
            "link": {"type": "shutter", "label": "Shutter", "why": "Each format hands over with a wipe from the library."},
            "items": [
                stamp("PAPER"),
                {"type": "torn_scraps", "text": "Cut paper, in motion", "label": "Paper formats", "start": 0.1, "zone": "center"},
                say("Cut paper, for a softer story."),
            ],
        },
        {
            "id": "iso",
            "beat": "reveal",
            "seconds": 3.5,
            "verb": "show",
            "energy": 8,
            "carry": "the wipe",
            "shows": ["isometric"],
            "link": {"type": "iris", "label": "Iris", "why": "Each format hands over with a wipe from the library."},
            "items": [
                stamp("ISOMETRIC", aspects={"1:1": {"corner": "bl"}}),
                {
                    "type": "iso_stack",
                    "title": "Depth, for ideas *built in layers*",  # the stack reaches the foot, so its title carries the line
                    "layers": [{"text": "Your footage", "on": True}, {"text": "Captions"}, {"text": "Graphics"}],
                    "move": "explode",
                    "aspects": {"1:1": {"zone": "full"}},
                    "start": 0.0,
                },
            ],
        },
        {
            "id": "product",
            "beat": "reveal",
            "seconds": 3.5,
            "verb": "show",
            "energy": 8,
            "carry": "the last card opens onto real footage",
            "shows": ["product UI", "transitions"],
            "link": {"type": "curtain", "label": "Curtain", "why": "Each format hands over with a wipe from the library."},
            "items": [
                stamp("PRODUCT UI"),
                {
                    "type": "bento_grid",
                    "tiles": [
                        {"text": "Formats", "value": "{live:formats}"},
                        {"text": "Library items", "value": "{live:library}"},
                        {"text": "Fact gates", "value": "{live:gates}"},
                        {"text": "Looks", "value": "{live:looks}"},
                        {"text": "Cost to start", "value": "$0"},
                        {"text": "Licence", "value": "MIT"},
                    ],
                    "start": 0.1,
                    "zone": "center",
                    "scale": 0.88,
                },
                say("Every count here is read from the tool."),
            ],
        },
        {
            "id": "broll",
            "beat": "reveal",
            "seconds": 4,
            "verb": "cover",
            "energy": 6,
            "carry": "the shot gives way to the looks",
            "shows": ["B-roll"],
            "camera": {"move": "push", "amount": 0.05},
            "link": {"type": "through", "seconds": 0.7, "why": "We push through the shot into the looks."},
            "items": ([{"type": "footage", "file": "assets/broll.mp4", "start": 0.0, "important": False}] if clip else [])
            + [say("B-roll found, with its *licence recorded*", start=0.3)],
        },
        {
            "id": "looks",
            "beat": "reveal",
            "seconds": 4,
            "verb": "compare",
            "energy": 6,
            "carry": "the row becomes search results",
            "shows": ["looks"],
            "camera": {"move": "drift"},
            "link": {"type": "dissolve", "seconds": 0.7, "why": "Four bright frames sliding off flashed, so the row fades into the library."},
            "items": [
                {
                    "type": "stage_strip",
                    "title": "{live:looks} *looks*, on one real frame",
                    "size": "looks",
                    "stages": [{"text": name, "image": str(path.relative_to(project.root))} for name, path in zip(LOOK_NAMES, crops, strict=True)],
                    "start": -0.3,
                    "zone": "center",
                    "scale": 0.92,
                },
                say("Compare every look on your own footage."),
            ],
        },
        {
            "id": "library",
            "beat": "reveal",
            "seconds": 6,
            "verb": "search",
            "energy": 6,
            "carry": "the result ticks into the gates",
            "shows": ["library search"],
            "camera": {"move": "orbit", "turn": 3, "tilt": 3, "amount": 0.03},
            "link": {"type": "pan", "seconds": 0.7, "why": "The same move carries the results on to the checks."},
            "items": [
                {
                    "type": "count_up",
                    "value": "{live:library}",
                    "label": "things an editor can use",
                    "aspects": {"1:1": {"scale": 0.8}},
                    "start": -0.3,
                    "zone": "left",
                    "run": 1.0,
                    "rate": 9,
                    "important": False,
                },
                {
                    "type": "terminal_run",
                    "text": f'altocue library search "{search_words}"'[:36],
                    "name": "library",
                    "output": found,
                    "start": 0.2,
                    "zone": "right",
                    "aspects": {"1:1": {"scale": 0.8}},
                },
            ],
        },
        {
            "id": "gates",
            "beat": "reveal",
            "seconds": 6.5,
            "verb": "check",
            "energy": 8,
            "carry": "a clean board goes to the reviewer",
            "shows": ["QA gates"],
            "silences": [{"from": 5.6, "to": 6.05}],
            "camera": {
                "poses": [{"at": 0, "scale": 1.0, "ry": -5, "rx": 4}, {"at": 4.6, "scale": 1.05, "ry": 2, "rx": 2}, {"at": 6.4, "scale": 0.92}],
                "origin": "center",
            },
            "link": {"type": "through", "seconds": 0.7, "why": "The pull-back turns into the verdict."},
            "items": [
                {
                    "type": "work_cascade",
                    "title": "{live:gates} fact *gates*",
                    "from": "{live:gates}",
                    "to": 0,
                    "label": "to check",
                    "aspects": {"1:1": {"scale": 0.8}},
                    "done": "{live:passed} passed, {live:failed} failed, {live:na} not needed",
                    "rows": gate_rows(),
                    "start": -0.3,
                    "zone": "center",
                    "align": "center",
                },
                say("Measurements, not opinions.", start=0.6),
            ],
            "sound": [{"name": "tick", "at": 1.5}, {"name": "tick", "at": 2.4}, {"name": "tick", "at": 3.3}],
        },
        {
            "id": "verdict",
            "beat": "ask",
            "seconds": 4.5,
            "verb": "decide",
            "energy": 9,
            "carry": "the verdict lands on the mark",
            "shows": ["SHIP or FIX"],
            "camera": {"move": "push", "amount": 0.05},
            "link": {"type": "through", "seconds": 0.8, "why": "The verdict pushes into the mark."},
            "items": [
                stamp("EDITOR IN CHIEF"),
                {
                    "type": "strike_replace",
                    "lead": "Our launch film:",
                    "swap": ["{live:first_verdict} {live:first_score}", "{live:last_verdict} {live:last_score}"],
                    "start": -0.3,
                    "zone": "center",
                },
                say("Then a reviewer model says SHIP or FIX."),
            ],
            "sound": [{"name": "boom", "at": 1.4}],
        },
        {
            "id": "close",
            "beat": "close",
            "seconds": 4,
            "verb": "land",
            "energy": 6,
            "camera": {"move": "push", "amount": 0.03},
            "backdrop": [{"type": "particle_field", "seed": 4, "count": 70}],
            "items": [
                {
                    "type": "logo_sting",
                    "text": "Altocue",
                    "image": "assets/logos/altocue.svg",
                    "cta": "github.com/StratosHouse/altocue",
                    "start": -0.3,
                    "role": "cta",
                    "important": True,
                },
                say("This film was made with Altocue. MIT licence.", start=0.4),
            ],
            "sound": [{"name": "boom", "at": 0.1}],
        },
    ]
    board = {
        "title": "Altocue feature film",
        "type": "demo",
        "route": "hybrid",
        "clock": "music",
        "beats": ["hook", "example", "reveal", "ask", "close"],
        "hero": {"scene": "verdict", "offset": 1.4},
        "music": {"style": "glass", "seed": 11},
        "layers": {},
        "backdrop": [
            {"type": "soft_gradient", "seed": 5, "tones": "#2fb48f,#2c55d6,#8fd4e0"},
            {"type": "dot_grid", "size": 46},
            {"type": "grain_field", "strength": 0.05, "over": True},
        ],
        "scenes": scenes,
    }
    brief = {
        "company": "Altocue is an open video editing suite that coding agents drive.",
        "audience": "Anyone who makes video with a coding agent",
        "goal": "Install Altocue",
        "message": "Raw footage in, a finished and checked film out.",
        "must_show": [
            "raw take to finished cut",
            "captions",
            "colour",
            "audio clean-up",
            "kinetic type",
            "charts",
            "maps",
            "paper",
            "isometric",
            "product UI",
            "B-roll",
            "transitions",
            "looks",
            "library search",
            "QA gates",
            "SHIP or FIX",
        ],
        "terms": ["your agent"],
        "tone": "Calm, exact, a little dry",
        "brand": {"colors": {"ink": "#f3f7fa", "soft": "#a3b1bd", "accent": "#b5f4d9", "panel": "#080d13", "surface": "#111b24"}, "name": "Altocue"},
        "deliverables": ["16:9", "9:16", "1:1"],
        "claims": ["Every number on screen is read from the tool when this film is built, and `altocue film check` reads it again"],
        "length": round(sum(float(scene["seconds"]) for scene in scenes)),
        "cap_seconds": CAP,
    }
    filled, record = live.fill(board, numbers_for(reviewed, chart), project.root)
    write_json(project.root / "brief.json", brief)
    write_json(project.root / "storyboard.json", filled)
    write_json(project.root / live.FILE, record)
    write_json(project.root / film.LOCK, {"scenes": [scene["id"] for scene in scenes]})
    write_json(project.root / "stock.json", clip and {"title": clip.get("title"), "licence": clip.get("licence"), "page": clip.get("page")})
    (project.root / "numbers.json").unlink(missing_ok=True)
    print(json.dumps({entry["name"]: entry["shown"] for entry in record["numbers"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--demo", required=True, help="Folder with raw-a, raw-b, final-a and final-b")
    parser.add_argument("--reviewed", required=True, help="A film project with review/qa.json and review/rounds.json")
    parser.add_argument("--looks", required=True, help="A sheet from `altocue grade --compare`")
    parser.add_argument("--stock", action="store_true", help="Fetch one clip through the Pixabay route. Needs PIXABAY_API_KEY")
    args = parser.parse_args()
    build(Path(args.folder).resolve(), Path(args.demo).expanduser(), Path(args.reviewed).expanduser(), Path(args.looks).expanduser(), args.stock)


if __name__ == "__main__":
    main()
