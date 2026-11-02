"""Motion formats: original graphics a motion.json can name. Each is plain CSS, so every frame is a pure function of time."""

from __future__ import annotations

import hashlib
import importlib
import pkgutil
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ...errors import AltocueError

GROUPS = {
    "kinetic": "Kinetic type",
    "data": "Data",
    "explain": "Explainers",
    "paper": "Paper and stop-motion",
    "product": "Product and UI",
    "maps": "Maps",
    "iso": "Isometric and depth",
    "proof": "Social proof",
    "teach": "Teaching",
    "wipe": "Graphic wipes",
}
JOBS = ("hook", "prove", "explain", "transition", "close")
AREAS = ("panel", "edge", "full")
CORE_TYPES = {"hook", "chip", "lower_third", "stat", "quote", "steps", "title", "endcard", "composition"}
FONT_FILES = {"mono": "GeistMono-Variable.ttf", "serif": "InstrumentSerif-Regular.ttf", "hand": "Caveat-Variable.ttf"}
FONT_STACKS = {"mono": 'ui-monospace,Menlo,monospace', "serif": "Georgia,serif", "hand": "cursive"}

IMAGE_KEYS = ("image", "src")
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".svg")
KEYFRAMES = re.compile(r"@keyframes\s+([\w-]+)")
ANIMATED = re.compile(r"@keyframes\s+[\w-]+|animation(?:-name)?\s*:[^;}]+")
WORD = re.compile(r"(?<![\w-])[A-Za-z_][\w-]*")

BASE_CSS = """
.fmt-panel{left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));top:var(--top)}
.fmt-edge{left:0;right:0}
.fmt-full{inset:0}
.fmt-leaves{animation:fmtout .2s ease-in var(--leave) forwards}
@keyframes fmtout{to{opacity:0}}
.lift{text-shadow:0 2px 6px rgba(0,0,0,.34),0 10px 44px rgba(0,0,0,.5)}
"""


@dataclass(frozen=True)
class Slot:
    """One thing a format needs. A text limit is characters that fit. A list limit is rows, and `each` is characters per row."""

    name: str
    kind: str = "text"  # text, number, list, choice, path
    limit: int = 0
    required: bool = True
    choices: tuple[str, ...] = ()
    each: int = 0


@dataclass(frozen=True)
class Format:
    id: str
    title: str
    summary: str
    group: str
    jobs: tuple[str, ...]
    slots: tuple[Slot, ...]
    example: dict[str, Any]
    draw: Callable[[dict[str, Any]], str]
    css: str
    seconds: float = 3.0
    shortest: float = 1.0
    longest: float = 12.0
    area: str = "panel"
    tall: int = 320  # height at full scale, so placement can keep it off faces
    sound: str | None = "pop"
    fonts: tuple[str, ...] = ()
    leaves: bool = True  # False when the format plays its own exit
    needs_alpha: bool = True  # False when it paints the whole frame


_loaded: dict[str, Format] = {}


def load() -> dict[str, Format]:
    if _loaded:
        return _loaded
    for module in pkgutil.iter_modules(__path__):
        if module.name == "kit":
            continue
        for fmt in importlib.import_module(f"{__name__}.{module.name}").FORMATS:
            if fmt.id in _loaded or fmt.id in CORE_TYPES:
                raise ValueError(f"Format id {fmt.id} is taken")
            _loaded[fmt.id] = fmt
    return _loaded


def find(kind: str | None) -> Format | None:
    return load().get(kind or "")


def full_screen(kind: str) -> bool:
    fmt = find(kind)
    return bool(fmt and fmt.area == "full")


def _bare(text: Any) -> str:
    return str(text).replace("*", "")


def check(item: dict[str, Any]) -> None:
    """Copy that cannot fit is an error here, not an overflow found in the render."""
    fmt = find(item["type"])
    if fmt is None:
        return
    fix = f"See `altocue library show {fmt.id}`."
    for slot in fmt.slots:
        value = item.get(slot.name)
        if value in (None, "", []):
            if slot.required:
                raise AltocueError(f'{fmt.id} needs "{slot.name}".', fix=fix)
            continue
        if slot.kind == "text" and slot.limit and len(_bare(value)) > slot.limit:
            raise AltocueError(f'{fmt.id}: "{slot.name}" is {len(_bare(value))} characters and only {slot.limit} fit.', fix="Shorten the copy.")
        if slot.kind == "choice" and value not in slot.choices:
            raise AltocueError(f'{fmt.id}: "{slot.name}" must be one of {", ".join(slot.choices)}.', fix=fix)
        if slot.kind == "number":
            try:
                float(value)
            except (TypeError, ValueError):
                raise AltocueError(f'{fmt.id}: "{slot.name}" must be a number.', fix=fix) from None
        if slot.kind == "list":
            if not isinstance(value, list):
                raise AltocueError(f'{fmt.id}: "{slot.name}" must be a list.', fix=fix)
            if slot.limit and len(value) > slot.limit:
                raise AltocueError(f'{fmt.id}: "{slot.name}" has {len(value)} rows and only {slot.limit} fit.', fix="Use fewer rows.")
            for row in value if slot.each else []:
                text = row.get("text", "") if isinstance(row, dict) else row
                if len(_bare(text)) > slot.each:
                    raise AltocueError(f'{fmt.id}: a row in "{slot.name}" is {len(_bare(text))} characters and only {slot.each} fit.', fix="Shorten the copy.")


def strings(item: dict[str, Any]) -> list[str]:
    """Every string this graphic puts on screen, so the QA rules can read them."""
    fmt = find(item["type"])
    found: list[str] = []
    for slot in fmt.slots if fmt else ():
        value = item.get(slot.name)
        if slot.kind == "text" and value:
            found.append(_bare(value))
        if slot.kind == "list":
            for row in value or []:
                text = (row.get("text") or row.get("label")) if isinstance(row, dict) else row
                if isinstance(text, str) and text:
                    found.append(_bare(text))
    return found


def render(item: dict[str, Any], attrs: str, style: str) -> str:
    fmt = load()[item["type"]]
    leaves = " fmt-leaves" if fmt.leaves else ""
    return f'<div class="clip fmt-{fmt.area}{leaves} f-{fmt.id}" {attrs} {style}>{_scoped(fmt, fmt.draw(item))}</div>'


def used(items: Iterable[dict[str, Any]]) -> list[Format]:
    seen = {item["type"] for item in items}
    return [fmt for fmt in load().values() if fmt.id in seen]


def fonts_for(items: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({font for fmt in used(items) for font in fmt.fonts})


def _scoped(fmt: Format, text: str) -> str:
    names = set(KEYFRAMES.findall(fmt.css))

    def rename(found: re.Match[str]) -> str:
        return WORD.sub(lambda word: f"{fmt.id}--{word.group(0)}" if word.group(0) in names else word.group(0), found.group(0))

    return ANIMATED.sub(rename, text)


def scoped_css(fmt: Format) -> str:
    """Keyframes carry the format's id, so two formats in one film can each have their own `pop`."""
    return _scoped(fmt, fmt.css)


def stage_name(reference: str) -> str:
    """A flat name for a project image beside the page, so two folders' logo.png never collide."""
    return f"img-{hashlib.sha256(str(reference).encode()).hexdigest()[:16]}{PurePosixPath(str(reference)).suffix.lower()}"


def place_images(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Check every image a graphic names is a real picture in the project, and point the graphic at its name on stage."""
    if find(item.get("type")) is None:
        return item
    placed = dict(item)
    stage: dict[str, str] = {}

    def place(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        for key in IMAGE_KEYS:
            if row.get(key):
                path = (root / str(row[key])).resolve()
                if not path.is_file() or not path.is_relative_to(root.resolve()):
                    raise AltocueError(f"{item['type']}: the image {row[key]} is not in this project.", fix="Point it at an image file inside the project.")
                if path.suffix.lower() not in IMAGE_TYPES:
                    raise AltocueError(f"{item['type']}: {row[key]} is not an image this renderer can show.", fix=f"Use one of {', '.join(IMAGE_TYPES)}.")
                row[key] = stage_name(row[key])
                stage[row[key]] = str(path)
        return row

    placed = place(placed)
    for key, value in item.items():
        if isinstance(value, list):
            placed[key] = [place(row) if isinstance(row, dict) else row for row in value]
    return {**placed, "stage_images": stage} if stage else placed


def stylesheet(items: Iterable[dict[str, Any]]) -> str:
    """Only what this film uses. Faces load from files the renderer copies next to the page."""
    items = list(items)
    formats = used(items)
    if not formats:
        return ""
    faces = "".join(f'@font-face{{font-family:"{name}";src:url("{FONT_FILES[name]}");font-weight:100 900}}\n' for name in fonts_for(items))
    stacks = "".join(f'--{name}:"{name}",{FONT_STACKS[name]};' for name in fonts_for(items))
    return faces + (f":root{{{stacks}}}\n" if stacks else "") + BASE_CSS + "\n".join(scoped_css(fmt) for fmt in formats)
