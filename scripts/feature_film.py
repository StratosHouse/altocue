#!/usr/bin/env python3
"""Build the Altocue feature film's project from the tool itself. Dev tool, needs the demo clips and a finished launch film.

Every number on screen is a `{live:...}` token, filled at build time from the tool itself: the format count, the gates
that ship, the review's own scale, real measurements of the demo clips, and the QA and verdicts of a real film.
`live.json` records where each one landed, and `altocue film check` reads them all again, so a stale number fails.

The issue the film shows the reviewer catching is a real note from the launch film's first round, read from that
round's own verdict, never written here.

    python scripts/feature_film.py <folder> --demo <demo clips> --reviewed <a reviewed film project> --formats <frames>
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from altocue import ffmpeg, film, live
from altocue.errors import AltocueError
from altocue.project import CONFIG, Project, write_json

ROOT = Path(__file__).resolve().parents[1]
CAP = 90.0
TAKE_B_FROM = 1.05  # seconds into final-b.mp4, after "Here's the best part."
WIPE_KEEP = 0.58  # share of the frame's height kept for the colour comparison, above the clip's own captions
FORMAT_FRAMES = (("kinetic", "Words"), ("map", "Maps"), ("paper", "Paper"), ("iso", "Depth"))  # frames from a real render, with no numbers in them
CAUGHT = "End button: a placeholder address, too small to read."  # the round's major note, in fewer words
FIXED = "Fixed: one clear Start free today button."


def seconds_of(path: Path) -> float:
    return float(ffmpeg.probe_json(path)["format"]["duration"])


def matched_frames(raw: Path, final: Path, folder: Path, name: str) -> tuple[Path, Path]:
    """The same moment in both files, found by looking, so the colour comparison is a true one."""
    target = folder / f"{name}-graded.png"
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
    source = folder / f"{name}-raw.png"
    ffmpeg.frame(raw, best_at, source, width=540)
    return source, target


def round_one_note(reviewed: Path) -> dict[str, Any]:
    """The major note from the launch film's first review. Its verdict must be for round one's own file."""
    rounds = json.loads((reviewed / "review" / "rounds.json").read_text())["rounds"]
    verdict = json.loads((reviewed / "review" / "round-1-verdict.json").read_text())
    if verdict["file_sha256"] != rounds[0]["file_sha256"]:
        raise AltocueError("round-1-verdict.json is not the verdict on round one's file.", fix="Recover the round-one verdict again.")
    note = next((note for note in verdict["notes"] if note["severity"] == "major" and note["dimension"] == "ending"), None)
    if note is None or "placeholder" not in note["observed"] or "small" not in note["observed"]:
        raise AltocueError("The round-one verdict has no major note on the end card's placeholder address.", fix="Pick the issue from the notes it has.")
    return note


def gate_rows() -> list[dict[str, str]]:
    return [
        {"text": "Loudness", "label": "{live:loudness} LUFS"},
        {"text": "True peak", "label": "{live:true_peak} dBTP"},
        {"text": "Black frames", "label": "{live:black}"},
        {"text": "Frozen frames", "label": "{live:frozen}"},
        {"text": "Flashes in the worst second", "label": "{live:flash}"},
    ]


def numbers_for(reviewed: Path) -> dict[str, dict[str, object]]:
    """Where every number on screen is read from. Nothing here is a value."""
    launch = str(reviewed)
    made: dict[str, dict[str, object]] = {name: {"source": {"count": name}} for name in ("formats", "gates", "ship_at", "score_top")}
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
    made["b_lufs"] = {"source": {"file": "assets/final-b.mp4", "read": "integrated"}}
    return made


def build(folder: Path, demo: Path, reviewed: Path, frames: Path) -> None:
    # built again in place: the last render and its review stay until a new render replaces them
    project = Project.open(folder) if (folder / CONFIG).is_file() else film.new_film("demo", str(folder))
    assets = project.root / "assets"
    for name in ("raw-a", "final-a", "raw-b", "final-b"):
        shutil.copy(demo / f"{name}.mp4", assets / f"{name}.mp4")
    (assets / "logos").mkdir(exist_ok=True)
    shutil.copy(ROOT / "assets" / "logos" / "altocue.svg", assets / "logos" / "altocue.svg")
    (assets / "formats").mkdir(exist_ok=True)
    for name, _ in FORMAT_FRAMES:
        shutil.copy(frames / f"{name}.png", assets / "formats" / f"{name}.png")
    final_a, raw_b, final_b = (assets / f"{name}.mp4" for name in ("final-a", "raw-b", "final-b"))

    speech = assets / "voice"
    speech.mkdir(exist_ok=True)
    ffmpeg.run(["-i", str(final_a), "-vn", "-ar", "48000", "-ac", "1", str(speech / "final-a.wav")])
    # the second take starts at "It fixes the sound": its opening line adds nothing the captions need
    ffmpeg.run(["-ss", f"{TAKE_B_FROM:.2f}", "-i", str(final_b), "-vn", "-ar", "48000", "-ac", "1", str(speech / "final-b.wav")])
    frame_raw, frame_graded = matched_frames(raw_b, final_b, assets, "take-b")
    for path in (frame_raw, frame_graded):  # the finished take carries its own captions lower down, which a divider would cut through
        with Image.open(path) as image:
            image.crop((0, 0, image.width, round(image.height * WIPE_KEEP))).save(path)
    round_one_note(reviewed)

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
            "carry": "a straight cut to the raw take",
            "camera": {"move": "push", "amount": 0.1},
            "link": {"type": "cut", "why": "A clean cut, so the hook never sits over the phones."},
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
            "shows": ["raw take to finished cut", "captions"],
            "voice": "assets/voice/final-a.wav",
            "camera": {"move": "drift"},
            "link": {"type": "dissolve", "seconds": 0.7, "why": "Two bright phones sliding off measured at the flash limit, so the takes fade."},
            "items": [
                {"type": "mask_rise", "lines": ["Raw take", "*to finished cut*"], "start": 0.0, "zone": "left"},
                {"type": "footage", "file": "assets/raw-a.mp4", "zone": "phone_a", "label": "Raw, {live:raw_seconds} s", "start": 0.0, "important": False},
                {
                    "type": "footage",
                    "file": "assets/final-a.mp4",
                    "zone": "phone_b",
                    "label": "Cut, {live:cut_seconds} s",
                    "start": 0.0,
                    "important": False,
                    "captions": {"spell": {"AltaQ": "Altocue"}, "place": "frame"},  # the transcriber hears the name this way
                },
            ],
        },
        {
            "id": "finish",
            "beat": "example",
            "seconds": 9.5,
            "verb": "correct",
            "energy": 5,
            "carry": "the finished take gives way to the formats",
            "shows": ["colour", "audio clean-up"],
            "voice": "assets/voice/final-b.wav",
            "voice_offset": -0.3,
            "camera": {"move": "push", "amount": 0.04},
            "link": {"type": "through", "seconds": 0.7, "why": "We push through the finished take into what Altocue draws."},
            "items": [
                {"type": "mask_rise", "lines": ["Colour, sound", "*fixed per clip*"], "start": -0.3, "zone": "left"},
                {
                    "type": "split_wipe",
                    "frames": [
                        {"text": "Raw", "image": str(frame_raw.relative_to(project.root))},
                        {"text": "Finished", "image": str(frame_graded.relative_to(project.root))},
                    ],
                    "start": -0.3,
                    "zone": "phone_a",
                    "important": False,
                },
                {
                    "type": "footage",
                    "file": "assets/final-b.mp4",
                    "zone": "phone_b",
                    "from": TAKE_B_FROM,
                    "label": "Mastered, {live:b_lufs} LUFS",
                    "start": -0.3,
                    "important": False,
                    "captions": {"spell": {"color": "colour"}, "place": "frame"},  # British spelling, as the clip's own caption has it
                },
            ],
        },
        {
            "id": "formats",
            "beat": "reveal",
            "seconds": 5,
            "verb": "show",
            "energy": 7,
            "carry": "the frames give way to the checks",
            "shows": ["kinetic type", "maps", "paper", "isometric", "transitions"],
            "camera": {"move": "push", "amount": 0.04},
            "link": {"type": "curtain", "label": "Curtain", "why": "A wipe from the library hands over to the checks."},
            "items": [
                {
                    "type": "stage_strip",
                    "title": "Drawn in code: {live:formats} *formats*",
                    "size": "frames",
                    "scale": 0.75,
                    "stages": [{"text": label, "image": f"assets/formats/{name}.png"} for name, label in FORMAT_FRAMES],
                    "start": -0.2,
                    "zone": "center",
                },
            ],
        },
        {
            "id": "gates",
            "beat": "reveal",
            "seconds": 6,
            "verb": "check",
            "energy": 8,
            "carry": "a clean board goes to the reviewer",
            "shows": ["QA gates"],
            "silences": [{"from": 5.1, "to": 5.55}],
            "camera": {
                "poses": [{"at": 0, "scale": 1.0, "ry": -5, "rx": 4}, {"at": 4.2, "scale": 1.05, "ry": 2, "rx": 2}, {"at": 5.9, "scale": 0.94}],
                "origin": "center",
            },
            "link": {"type": "through", "seconds": 0.7, "why": "The pull-back turns into the reviewer's note."},
            "items": [
                {
                    "type": "work_cascade",
                    "title": "Measured, not *guessed*",
                    "from": "{live:gates}",
                    "to": 0,
                    "label": "to check",
                    "aspects": {"1:1": {"scale": 0.8}},
                    "done": "{live:passed} passed, {live:failed} failed, {live:na} not needed",
                    "rows": gate_rows(),
                    "start": 0.0,
                    "zone": "center",
                    "align": "center",
                },
                say("{live:gates} fact gates, each one a measurement.", start=0.6),
            ],
            "sound": [{"name": "tick", "at": 1.5}, {"name": "tick", "at": 2.4}, {"name": "tick", "at": 3.3}],
        },
        {
            "id": "caught",
            "beat": "ask",
            "seconds": 5.5,
            "verb": "catch",
            "energy": 7,
            "carry": "the note gives way to the score it moved",
            "shows": ["SHIP or FIX"],
            "silences": [{"from": 4.85, "to": 5.3}],
            "camera": {"move": "push", "amount": 0.04},
            "link": {"type": "dissolve", "seconds": 0.6, "why": "The note fades into the score it moved."},
            "items": [
                {"type": "comment_card", "text": CAUGHT, "name": "Editor in Chief", "note": "Launch film, round 1", "start": -0.3, "zone": "center"},
                say(FIXED, start=2.4),
            ],
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
                {
                    "type": "strike_replace",
                    "lead": "Our launch film:",
                    "swap": ["{live:first_verdict} {live:first_score}", "{live:last_verdict} {live:last_score}"],
                    "start": -0.3,
                    "zone": "center",
                },
                say("Scored out of {live:score_top}. SHIP needs {live:ship_at}."),
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
                    "cta": "Get Altocue on GitHub",
                    "start": -0.3,
                    "role": "cta",
                    "important": True,
                },
                say("github.com/StratosHouse/altocue", start=0.3, aspects={"9:16": {"scale": 0.7}, "1:1": {"scale": 0.7}}),
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
            "maps",
            "paper",
            "isometric",
            "transitions",
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
    filled, record = live.fill(board, numbers_for(reviewed), project.root)
    record["numbers"] = [entry for entry in record["numbers"] if entry["places"]]
    write_json(project.root / "brief.json", brief)
    write_json(project.root / "storyboard.json", filled)
    write_json(project.root / live.FILE, record)
    write_json(project.root / film.LOCK, {"scenes": [scene["id"] for scene in scenes]})
    for stale in ("numbers.json", "stock.json"):
        (project.root / stale).unlink(missing_ok=True)
    print(json.dumps({entry["name"]: entry["shown"] for entry in record["numbers"]}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--demo", required=True, help="Folder with raw-a, raw-b, final-a and final-b")
    parser.add_argument("--reviewed", required=True, help="A film project with review/qa.json, review/rounds.json and review/round-1-verdict.json")
    parser.add_argument("--formats", required=True, help="Folder with kinetic, map, paper and iso frames from a real render, with no numbers in them")
    args = parser.parse_args()
    build(Path(args.folder).resolve(), Path(args.demo).expanduser(), Path(args.reviewed).expanduser(), Path(args.formats).expanduser())


if __name__ == "__main__":
    main()
