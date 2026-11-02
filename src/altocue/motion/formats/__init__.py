"""Motion formats: original graphics a motion.json can name. Each is plain CSS, so every frame is a pure function of time."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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
    kind: str = "text"  # text, number, list, choice
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
    return f'<div class="clip fmt-{fmt.area}{leaves} f-{fmt.id}" {attrs} {style}>{fmt.draw(item)}</div>'


def used(items: Iterable[dict[str, Any]]) -> list[Format]:
    seen = {item["type"] for item in items}
    return [fmt for fmt in load().values() if fmt.id in seen]


def fonts_for(items: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({font for fmt in used(items) for font in fmt.fonts})


def stylesheet(items: Iterable[dict[str, Any]]) -> str:
    """Only what this film uses. Faces load from files the renderer copies next to the page."""
    items = list(items)
    formats = used(items)
    if not formats:
        return ""
    faces = "".join(f'@font-face{{font-family:"{name}";src:url("{FONT_FILES[name]}");font-weight:100 900}}\n' for name in fonts_for(items))
    stacks = "".join(f'--{name}:"{name}",{FONT_STACKS[name]};' for name in fonts_for(items))
    return faces + (f":root{{{stacks}}}\n" if stacks else "") + BASE_CSS + "\n".join(fmt.css for fmt in formats)
