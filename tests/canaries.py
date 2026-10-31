"""Clips with planted faults, plus clean controls. Everything is generated at test time.

The speech is a synthetic voice-like signal and the transcriber is faked, so no footage
and no model are needed. Each canary names the gate that must fire. `score` reports recall
and false alarms the way a review report would.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from altocue import ffmpeg
from altocue.models import Clip, Edl, GateResult, Segment, Word
from altocue.project import Project, write_json

FPS = 30
WIDTH, HEIGHT = 1080, 1920
SECONDS = 6.0
RATE = 48000
SAFE_Y = 0.68
FACE_Y = 0.34
SCRIPT = (
    "the first thing you need is a plan and the second thing you need is a reason "
    "to keep going when the plan stops working and nobody is watching you do it"
).split()
PHRASE_AT = 12
PHRASE = ["you", "need", "is"]


def freeze(first: int, last: int) -> str:
    return f"split[fa][fb];[fa][fb]freezeframes=first={first}:last={last}:replace={first}"


def doubled(words: list[str], at: int = PHRASE_AT, phrase: list[str] = PHRASE) -> list[str]:
    return words[:at] + phrase + phrase + words[at + len(phrase) :]


@dataclass
class Recipe:
    """What to plant. Every default produces a clean, correctly mastered clip."""

    vf: str = ""
    seconds: float = SECONDS
    tail: float = 0.0
    tail_color: str = "0x0b1a2b"
    gain_db: float = 0.0
    steps: list[tuple[float, float, float]] = field(default_factory=list)
    gaps: list[tuple[float, float]] = field(default_factory=list)
    expected: list[str] | None = None
    heard: list[str] | None = None
    caption_words: list[str] | None = None
    caption_y: float = SAFE_Y
    burn_captions: bool = True
    labels: list[str] = field(default_factory=list)
    rate: int = FPS
    tags: bool = True
    pix_fmt: str = "yuv420p"
    keep_seconds: float | None = None
    rules: dict[str, Any] | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    burn_text: list[tuple[str, float, float, float]] = field(default_factory=list)
    face: bool = True
    plate: bool = False


@dataclass
class Case:
    name: str
    gate: str
    recipe: Recipe
    also: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.gate


CANARIES = [
    Case("black flash, two frames", "black_frames", Recipe(vf="drawbox=color=black:t=fill:enable='between(n,90,91)'")),
    Case("black flash, one frame", "black_frames", Recipe(vf="drawbox=color=black:t=fill:enable='eq(n,90)'")),
    Case("doubled phrase in the expected transcript", "doubled_phrase", Recipe(expected=doubled(SCRIPT), heard=doubled(SCRIPT))),
    Case("doubled phrase only in the render", "doubled_phrase", Recipe(heard=doubled(SCRIPT)), also=("words",)),
    Case("6 LU drop, whole file", "loudness", Recipe(gain_db=-6.0)),
    Case("6 LU drop, one section", "loudness", Recipe(steps=[(3.0, 6.0, -6.0)])),
    Case("caption over the face", "face_clear", Recipe(caption_y=FACE_Y, plate=True)),
    Case("frozen section", "frozen_frames", Recipe(vf=freeze(90, 135))),
    Case("dead air in the middle", "silence", Recipe(gaps=[(2.3, 4.0)])),
    Case("cut opens on silence", "cut_sound", Recipe(gaps=[(2.0, 2.7)])),
    Case("hot master", "true_peak", Recipe(gain_db=11.0), also=("loudness",)),
    Case("short render", "duration", Recipe(keep_seconds=5.0), also=("silence",)),
    Case("wrong frame rate", "format", Recipe(rate=25), also=("duration",)),
    Case("no colour tags", "format", Recipe(tags=False)),
    Case("words lost in the render", "words", Recipe(heard=SCRIPT[:20]), also=("doubled_phrase",)),
    Case("caption text invented", "captions_match", Recipe(caption_words=["free", "money", "for", "everyone"] + SCRIPT[4:])),
    Case(
        "banned word in a graphic",
        "rules_strings",
        Recipe(rules={"banned": ["guaranteed"]}, items=[{"type": "chip", "text": "Guaranteed results", "start": 1.0, "end": 2.0}]),
    ),
    Case(
        "banned word in the picture",
        "rules_ocr",
        Recipe(rules={"banned": ["guaranteed"]}, burn_text=[("GUARANTEED RESULTS", 0.2, 3.0, 4.0)]),
    ),
    Case(
        "required line missing",
        "rules_strings",
        Recipe(rules={"required": [{"id": "free-terms", "when_spoken": "plan", "on_screen": "terms apply"}]}),
        also=("rules_ocr",),
    ),
    Case(
        "graphic outside the safe zone",
        "safe_zone",
        Recipe(
            items=[{"type": "chip", "text": "Follow this plan", "start": 1.0, "end": 3.0}], burn_text=[("Follow this plan", 0.94, 1.0, 3.0)]
        ),
    ),
]

CONTROLS = [
    Case("clean control", "", Recipe(plate=True)),
    Case("clean, dark end card", "", Recipe(tail=1.5)),
    Case("clean, planned silent b-roll", "", Recipe(gaps=[(2.0, 4.0)], labels=["", "broll", ""])),
    Case("clean, planned hold", "", Recipe(vf=freeze(62, 118), labels=["", "hold", ""])),
    Case(
        "clean, rules met",
        "",
        Recipe(
            rules={
                "banned": ["guaranteed"],
                "spellings": ["Altocue"],
                "required": [{"id": "plan-card", "when_spoken": "plan", "on_screen": "free plan"}],
            },
            items=[{"type": "chip", "text": "Altocue free plan", "start": 0.0, "end": 6.0}],
            burn_text=[("Altocue free plan", 0.12, 0.0, 6.0)],
        ),
    ),
]


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def draw_face(canvas: Image.Image) -> None:
    """A soft cartoon face. Blurred, it is enough for YuNet to find."""
    width, height = canvas.size
    cx, cy, r = width // 2, int(height * FACE_Y), 230
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((cx - r, cy - int(r * 1.3), cx + r, cy + int(r * 1.3)), fill=(205, 145, 110, 255))
    d.pieslice((cx - r - 12, cy - int(r * 1.48), cx + r + 12, cy + int(r * 0.35)), 180, 360, fill=(38, 28, 28, 255))
    for side in (-1, 1):
        ex, ey = cx + side * 96, cy + 8
        d.ellipse((ex - 50, ey - 27, ex + 50, ey + 27), fill=(245, 245, 245, 255))
        d.ellipse((ex - 25, ey - 25, ex + 25, ey + 25), fill=(30, 22, 18, 255))
    d.line((cx - 8, cy + 30, cx - 22, cy + 100, cx + 10, cy + 104), fill=(150, 95, 70, 255), width=8)
    d.arc((cx - 95, cy + 100, cx + 95, cy + 205), 20, 160, fill=(170, 60, 75, 255), width=22)
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(5)))


def text_plate(text: str, y: float) -> Image.Image:
    """White text on a dark panel, centred at a height, as a full-size overlay."""
    plate = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(plate)
    face = font(72)
    box = d.textbbox((0, 0), text, font=face)
    w, h = box[2] - box[0], box[3] - box[1]
    x, top = (WIDTH - w) // 2, int(y * HEIGHT)
    d.rectangle((x - 30, top - 26, x + w + 30, top + h + 34), fill=(8, 13, 19, 235))
    d.text((x - box[0], top - box[1]), text, font=face, fill=(255, 255, 255, 255))
    return plate


def voice(seconds: float) -> np.ndarray:
    """Something ebur128 treats like steady speech: voiced harmonics with a syllable rhythm."""
    t = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * f * t) * a for f, a in ((140, 1.0), (280, 0.7), (560, 0.5), (1200, 0.35), (2600, 0.2)))
    return (tone * (0.72 + 0.28 * np.sin(2 * np.pi * 3.7 * t))).astype(np.float32)


def write_audio(path: Path, recipe: Recipe, seconds: float) -> None:
    signal = voice(seconds)
    probe = ffmpeg.write_wav(path, signal * 0.1, RATE)
    gain = 10 ** ((-14.0 - ffmpeg.loudness(probe)["integrated"]) / 20) * 0.1
    signal = signal * gain * 10 ** (recipe.gain_db / 20)
    for start, end, db in recipe.steps:
        signal[int(start * RATE) : int(end * RATE)] *= 10 ** (db / 20)
    for start, end in recipe.gaps:
        signal[int(start * RATE) : int(end * RATE)] = 0
    if recipe.gain_db > 0:
        pcm = (signal * 32767).clip(-32768, 32767).astype("<i2")
        import wave

        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(RATE)
            handle.writeframes(pcm.tobytes())
    else:
        ffmpeg.write_wav(path, signal, RATE)


def pages_from(words: list[str], seconds: float) -> list[dict[str, Any]]:
    pages, size = [], 4
    span = seconds / max(1, len(words))
    for i in range(0, len(words), size):
        chunk = words[i : i + size]
        start = round(i * span, 3)
        pages.append(
            {
                "start": start,
                "end": round((i + len(chunk)) * span, 3),
                "segment": f"s{min(2, int(start // 2)) + 1}",
                "words": [{"text": w, "start": round((i + n) * span, 3), "end": round((i + n + 1) * span, 3)} for n, w in enumerate(chunk)],
            }
        )
    return pages


def render(path: Path, recipe: Recipe, work: Path, pages: list[dict[str, Any]]) -> None:
    seconds = recipe.keep_seconds or recipe.seconds
    total = seconds + recipe.tail
    audio = work / "voice.wav"
    write_audio(audio, recipe, total)
    plate = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    if recipe.face:
        draw_face(plate)
    plate.save(work / "face.png")
    inputs = [
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x7f8c99:s={WIDTH // 4}x{HEIGHT // 4}:r={recipe.rate}:d={total},"
        f"noise=alls=9:allf=t+u,scale={WIDTH}:{HEIGHT}:flags=neighbor",
        "-i",
        str(audio),
        "-loop",
        "1",
        "-r",
        str(recipe.rate),
        "-t",
        str(total),
        "-i",
        str(work / "face.png"),
    ]
    graph = ["[0:v][2:v]overlay=format=auto:shortest=1[v0]"]
    last, next_input, n = "v0", 3, 0
    burn: list[tuple[str, float, float, float]] = list(recipe.burn_text)
    if recipe.burn_captions:
        burn += [(" ".join(w["text"] for w in p["words"]), recipe.caption_y, p["start"], p["end"]) for p in pages]
    for text, y, start, end in burn:
        image = work / f"text{n}.png"
        text_plate(text, y).save(image)
        inputs += ["-loop", "1", "-r", str(recipe.rate), "-t", str(total), "-i", str(image)]
        graph.append(f"[{last}][{next_input}:v]overlay=format=auto:enable='between(t,{start},{end})'[t{n}]")
        last, next_input, n = f"t{n}", next_input + 1, n + 1
    if recipe.vf:
        graph.append(f"[{last}]{recipe.vf}[fx]")
        last = "fx"
    if recipe.tail:
        graph.append(f"[{last}]drawbox=color={recipe.tail_color}:t=fill:enable='gte(t,{seconds})'[end]")
        last = "end"
    if recipe.tags:  # output flags alone are dropped after a filter graph on ffmpeg 8.1
        graph.append(f"[{last}]setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709[tagged]")
        last = "tagged"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", f"[{last}]", "-map", "1:a", "-t", str(total),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", recipe.pix_fmt,
         "-c:a", "aac", "-b:a", "256k", "-ar", str(RATE), str(path)],
        check=True,
    )  # fmt: skip


@dataclass
class Built:
    case: Case
    project: Project
    file: Path
    expected: list[str]
    mapped: list[dict[str, Any]]
    heard: list[Word]


def timed(words: list[str], seconds: float) -> list[tuple[str, float, float]]:
    span = seconds / max(1, len(words))
    return [(w, round(i * span, 3), round((i + 1) * span, 3)) for i, w in enumerate(words)]


def build(case: Case, root: Path) -> Built:
    recipe = case.recipe
    root.mkdir(parents=True, exist_ok=True)
    project = Project.create(root / "project", aspect="9:16")
    work = project.work / "canary"
    work.mkdir(parents=True, exist_ok=True)
    expected = recipe.expected or SCRIPT
    heard = recipe.heard or expected
    caption_words = recipe.caption_words or [w for w in expected]
    pages = pages_from(caption_words, recipe.seconds)
    file = project.out / "final.mp4"
    render(file, recipe, work, pages)
    if recipe.plate:
        render(project.work / "plate.mp4", replace(recipe, burn_captions=False, burn_text=[]), work, pages)

    third = recipe.seconds / 3
    labels = recipe.labels or ["", "", ""]
    edl = Edl(
        fps=FPS, segments=[Segment(id=f"s{i + 1}", clip="take", start=i * third, end=(i + 1) * third, label=labels[i]) for i in range(3)]
    )
    project.save_edl(edl)
    project.save_clips([Clip(id="take", path=str(file), duration=recipe.seconds, fps=FPS, width=WIDTH, height=HEIGHT)])
    write_json(
        project.timeline_path,
        {"width": WIDTH, "height": HEIGHT, "fps": FPS, "duration": recipe.seconds, "tail": recipe.tail,
         "total": recipe.seconds + recipe.tail,
         "captions": pages, "items": recipe.items, "inserts": []},
    )  # fmt: skip
    if recipe.rules is not None:
        (project.root / "rules.json").write_text(json.dumps(recipe.rules))
    mapped = [
        {"text": w, "start": a, "end": b, "segment": f"s{min(2, int(a // third)) + 1}", "kind": "word"}
        for w, a, b in timed(expected, recipe.seconds)
    ]
    return Built(case, project, file, expected, mapped, [Word(text=w, start=a, end=b) for w, a, b in timed(heard, recipe.seconds)])


@contextmanager
def planted(built: Built) -> Iterator[None]:
    """Stand in for the transcriber and the cut, the stages QA only talks to."""
    with fakes(built.expected, built.mapped, built.heard):
        yield


@contextmanager
def fakes(expected: list[str], mapped: list[dict[str, Any]], heard: list[Word] | None) -> Iterator[None]:
    cut = SimpleNamespace(
        expected_words=lambda project, edl: list(expected), caption_words=lambda project, edl: list(expected), map_words=lambda project, edl: list(mapped)
    )
    transcribe = SimpleNamespace(transcribe_file=lambda path, **_: list(heard or []))
    saved = {name: sys.modules.get(name) for name in ("altocue.cut", "altocue.transcribe")}
    sys.modules["altocue.cut"] = cut  # type: ignore[assignment]
    sys.modules["altocue.transcribe"] = transcribe  # type: ignore[assignment]
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def fired(gates: list[GateResult]) -> set[str]:
    return {g.id for g in gates if not g.passed and not g.skipped}


def score(results: dict[str, list[GateResult]]) -> dict[str, Any]:
    """Recall per fault, and every gate that fired on a clean control."""
    caught = {c.name: c.gate in fired(results[c.name]) for c in CANARIES if c.name in results}
    alarms = {c.name: sorted(fired(results[c.name])) for c in CONTROLS if c.name in results and fired(results[c.name])}
    controls = sum(1 for c in CONTROLS if c.name in results)
    return {
        "recall": sum(caught.values()) / max(1, len(caught)),
        "caught": sum(caught.values()),
        "planted": len(caught),
        "missed": [name for name, hit in caught.items() if not hit],
        "controls": controls,
        "false_alarms": alarms,
    }
