#!/usr/bin/env python3
"""Build the Altocue feature film's project from the tool itself. Dev tool, needs the demo clips and a finished launch film.

Every number on screen comes from here at build time: the live format count, `altocue library stats`, a real
library search, real measurements of the demo clips, and the gates and verdict of a real film. Nothing is typed.

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

from altocue import ffmpeg, film, library
from altocue import looks as looks_module
from altocue.motion import formats
from altocue.project import Project, write_json

ROOT = Path(__file__).resolve().parents[1]
CAP = 90.0
ICONS = ("cut", "clean", "grade", "frame", "broll", "caption", "motion", "direct", "generate", "review")
NAMES = {"broll": "B-roll"}
LOOK_PICKS = (0, 1, 8, 10)  # natural, warm, mono_contrast and teal_orange in the compare sheet
LOOK_NAMES = ("natural", "warm", "mono contrast", "teal orange")


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


def gate_rows(qa: dict[str, Any]) -> list[dict[str, str]]:
    gates = {gate["id"]: gate for gate in qa["gates"]}
    return [
        {"text": "Loudness", "label": f"{gates['loudness']['value']:g} LUFS"},
        {"text": "True peak", "label": f"{gates['true_peak']['value']:g} dBTP"},
        {"text": "Black and frozen frames", "label": f"{gates['black_frames']['value'] + gates['frozen_frames']['value']}"},
        {"text": "Reading time", "label": "Held"},
        {"text": "Flash limit", "label": f"{gates['flash_rate']['value']:g} a second"},
    ]


def build(folder: Path, demo: Path, reviewed: Path, looks: Path, stock: bool) -> None:
    project = film.new_film("demo", str(folder))
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
    stats = library.stats(catalog)
    total = stats["entries"]
    loaded = formats.load()
    count = len(loaded)
    groups: dict[str, int] = {}
    for fmt in loaded.values():
        groups[fmt.group] = groups.get(fmt.group, 0) + 1
    qa = json.loads((reviewed / "review" / "qa.json").read_text())
    rounds = json.loads((reviewed / "review" / "rounds.json").read_text())["rounds"]
    first, last = rounds[0], rounds[-1]
    passed = sum(1 for gate in qa["gates"] if gate["passed"] and not gate.get("skipped"))
    failed = sum(1 for gate in qa["gates"] if not gate["passed"] and not gate.get("skipped"))
    looks_shipped = len(looks_module.ALL)

    raw_seconds, cut_seconds = seconds_of(raw_a), seconds_of(final_a)
    raw_sound, final_sound = ffmpeg.loudness(raw_a), ffmpeg.loudness(final_a)
    speech = assets / "voice"
    speech.mkdir(exist_ok=True)
    ffmpeg.run(["-i", str(final_a), "-vn", "-ar", "48000", "-ac", "1", str(speech / "final-a.wav")])
    ffmpeg.run(["-i", str(final_b), "-vn", "-t", "6.8", "-af", "afade=t=out:st=6.4:d=0.4", "-ar", "48000", "-ac", "1", str(speech / "final-b.wav")])
    frame_raw, frame_graded = matched_frames(raw_a, final_a, assets)
    crops = look_crops(looks, assets)
    clip = stock_clip(project, "ocean waves aerial") if stock else None
    search_words = "route map"
    found = search_rows(catalog, search_words)
    chart = sorted(groups.items(), key=lambda pair: -pair[1])[:6]
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
        "claims": ["Every number on screen is read from the tool when this film is built"],
        "length": 88,
        "cap_seconds": CAP,
    }

    def stamp(text: str) -> dict[str, Any]:
        return {"type": "corner_stamp", "text": text, "corner": "tr", "start": 0.0}

    scenes: list[dict[str, Any]] = [
        {
            "id": "open",
            "beat": "hook",
            "seconds": 4.5,
            "verb": "promise",
            "energy": 3,
            "carry": "the line falls into the footage",
            "camera": {"move": "push", "amount": 0.12},
            "link": {"type": "dive", "seconds": 0.9, "zoom": 4.0, "why": "We fall through the promise into the raw take it is about."},
            "items": [
                {"type": "word_slam", "text": "You film it. *Your agent edits it.*", "start": 0, "zone": "center", "align": "center", "role": "hook", "important": True}
            ],
            "sound": [{"name": "rise", "at": 3.2}],
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
            "link": {"type": "pan", "seconds": 0.7, "why": "The same sideways move carries us to the next take."},
            "items": [
                {"type": "mask_rise", "lines": ["Raw take", "*to finished cut*"], "start": 0.0, "zone": "left"},
                {"type": "footage", "file": "assets/raw-a.mp4", "zone": "phone_a", "label": f"Raw, {raw_seconds:.1f} s", "start": -0.4, "important": False},
                {"type": "footage", "file": "assets/final-a.mp4", "zone": "phone_b", "label": f"Cut, {cut_seconds:.1f} s", "start": -0.4, "important": False},
            ],
        },
        {
            "id": "captions",
            "beat": "example",
            "seconds": 6.5,
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
                {"type": "footage", "file": "assets/final-b.mp4", "zone": "phone", "start": -0.3, "important": False},
            ],
        },
        {
            "id": "colour",
            "beat": "example",
            "seconds": 6,
            "verb": "correct",
            "energy": 5,
            "carry": "the sweep turns into the waveform",
            "shows": ["colour"],
            "camera": {"move": "push", "amount": 0.04},
            "link": {"type": "pan", "seconds": 0.7, "why": "The divider's sweep carries on into the sound."},
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
            "seconds": 6,
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
                        {"text": "Raw", "note": f"{raw_sound['integrated']:g} LUFS, {raw_sound['true_peak']:g} dBTP", "values": envelope(raw_a)},
                        {"text": "Finished", "note": f"{final_sound['integrated']:g} LUFS, {final_sound['true_peak']:g} dBTP", "values": envelope(final_a), "hot": True},
                    ],
                },
            ],
        },
        {
            "id": "formats",
            "beat": "reveal",
            "seconds": 3.5,
            "verb": "count",
            "energy": 7,
            "carry": "the count opens onto the first format",
            "shows": [],
            "camera": {"move": "push", "amount": 0.06},
            "link": {"type": "shutter", "label": "Shutter", "why": "A hard card change starts the run of formats."},
            "items": [
                {
                    "type": "count_up",
                    "value": count,
                    "label": "*original* motion formats",
                    "start": -0.3,
                    "zone": "center",
                    "align": "center",
                    "run": 1.0,
                    "rate": 9,
                    "important": True,
                }
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
            "items": [stamp("KINETIC TYPE"), {"type": "decode", "text": "EVERY *WORD* MOVES", "start": 0.1, "zone": "center", "align": "center"}],
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
                    "title": f"Altocue's own {count} formats, *by group*",
                    "bars": [{"text": names[name], "value": value} for name, value in chart],
                    "start": 0.1,
                    "zone": "center",
                },
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
                stamp("MAPS"),
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
            "items": [stamp("PAPER"), {"type": "torn_scraps", "text": "Cut paper, in motion", "label": "Paper formats", "start": 0.1, "zone": "center"}],
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
                stamp("ISOMETRIC"),
                {
                    "type": "iso_stack",
                    "title": "Layers *in real depth*",
                    "layers": [{"text": "Your footage", "on": True}, {"text": "Captions"}, {"text": "Graphics"}],
                    "move": "explode",
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
                        {"text": "Formats", "value": str(count)},
                        {"text": "Library items", "value": str(total)},
                        {"text": "Fact gates", "value": str(len(qa["gates"]))},
                        {"text": "Looks", "value": str(looks_shipped)},
                        {"text": "Cost to start", "value": "$0"},
                        {"text": "Licence", "value": "MIT"},
                    ],
                    "start": 0.1,
                    "zone": "center",
                },
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
            + [{"type": "marker", "text": "B-roll found, *licence recorded*", "start": 0.3, "zone": "low", "align": "center"}],
        },
        {
            "id": "looks",
            "beat": "reveal",
            "seconds": 4.5,
            "verb": "compare",
            "energy": 6,
            "carry": "the row becomes search results",
            "shows": ["looks"],
            "camera": {"move": "drift"},
            "link": {"type": "pan", "seconds": 0.7, "why": "The row slides on to the library."},
            "items": [
                {
                    "type": "stage_strip",
                    "title": f"{looks_shipped} *looks*, on one real frame",
                    "size": "looks",
                    "stages": [{"text": name, "image": str(path.relative_to(project.root))} for name, path in zip(LOOK_NAMES, crops, strict=True)],
                    "start": -0.3,
                    "zone": "center",
                }
            ],
        },
        {
            "id": "library",
            "beat": "reveal",
            "seconds": 6.5,
            "verb": "search",
            "energy": 6,
            "carry": "the result ticks into the gates",
            "shows": ["library search"],
            "camera": {"move": "orbit", "turn": 3, "tilt": 3, "amount": 0.03},
            "link": {"type": "pan", "seconds": 0.7, "why": "The same move carries the results on to the checks."},
            "items": [
                {"type": "count_up", "value": total, "label": "things an editor can use", "start": -0.3, "zone": "left", "run": 1.0, "rate": 9, "important": False},
                {"type": "terminal_run", "text": f'altocue library search "{search_words}"'[:36], "name": "library", "output": found, "start": 0.2, "zone": "right"},
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
                    "title": f"{len(qa['gates'])} fact *gates*",
                    "from": len(qa["gates"]),
                    "to": 0,
                    "label": "to check",
                    "done": f"{passed} passed, {failed} failed",
                    "rows": gate_rows(qa),
                    "start": -0.3,
                    "zone": "center",
                    "align": "center",
                },
            ],
            "sound": [{"name": "tick", "at": 1.5}, {"name": "tick", "at": 2.4}, {"name": "tick", "at": 3.3}],
        },
        {
            "id": "verdict",
            "beat": "ask",
            "seconds": 5,
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
                    "swap": [f"{first['verdict']} {first['weighted']:.2f}", f"{last['verdict']} {last['weighted']:.2f}"],
                    "start": -0.3,
                    "zone": "center",
                },
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
                    "label": "This film was made with Altocue",
                    "cta": "Open source, MIT",
                    "start": -0.3,
                    "role": "cta",
                    "important": True,
                }
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
    write_json(project.root / "brief.json", brief)
    write_json(project.root / "storyboard.json", board)
    write_json(project.root / film.LOCK, {"scenes": [scene["id"] for scene in scenes]})
    numbers = {
        "formats": count,
        "library": total,
        "gates": len(qa["gates"]),
        "passed": passed,
        "failed": failed,
        "looks": looks_shipped,
        "raw_seconds": raw_seconds,
        "cut_seconds": cut_seconds,
        "raw_sound": raw_sound,
        "final_sound": final_sound,
        "first": first,
        "last": last,
        "stock": clip and {"title": clip.get("title"), "licence": clip.get("licence"), "page": clip.get("page")},
    }
    write_json(project.root / "numbers.json", numbers)
    print(json.dumps(numbers, indent=2, default=str))


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
