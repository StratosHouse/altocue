"""Transitions between two runs of picture: ffmpeg's xfade set, or a shader on the browser stage.

Picture only. A transition sits across a cut and borrows frames from past both edges, so the film
keeps its length and no word moves. Nothing here touches sound.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import ffmpeg
from .errors import AltocueError

XFADE = (
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright",
    "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
    "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin", "fadefast",
    "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
)  # fmt: skip
RENAMED = {"dissolve": "fade", "stipple": "dissolve"}  # ffmpeg's `dissolve` is a pixel stipple. To an editor, a dissolve is a smooth mix.
NAMES = (*XFADE, "stipple")
DEFAULT_SECONDS = 0.4
SHORTEST = 0.04
LONGEST = 2.0
FALLBACK = "dissolve"  # What a shader becomes on a machine with no browser. The film still finishes.
SHADER_PREFIX = "gl-"


def parse(raw: Any) -> dict[str, Any]:
    """A name, or `{"type": "...", "duration": 0.4}`."""
    entry = {"type": raw} if isinstance(raw, str) else raw
    if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
        raise AltocueError(f"Cannot read the transition {raw!r}.", fix='Use a name, or {"type": "dissolve", "duration": 0.4}.')
    name = entry["type"]
    if name not in NAMES and not name.startswith(SHADER_PREFIX):
        raise AltocueError(f"Unknown transition {name}.", fix=f"Use a shader named gl-..., or one of {', '.join(NAMES)}.")
    length = float(entry.get("duration", DEFAULT_SECONDS))
    if not SHORTEST <= length <= LONGEST:
        raise AltocueError(f"A {name} of {length:g} s cannot be played.", fix=f"Use {SHORTEST} to {LONGEST} seconds. Most want 0.2 to 0.6.")
    return {"type": name, "duration": length}


def blend(outgoing: Path, incoming: Path, name: str, frames: int, fps: int, encode: list[str], dest: Path) -> Path:
    """Two clips of `frames` frames become one. The first frame is all outgoing and the last nearly all incoming."""
    mix = f"[0:v][1:v]xfade=transition={RENAMED.get(name, name)}:duration={frames / fps:.6f}:offset=0,format=yuv420p[v]"
    temp = dest.with_suffix(".part.mp4")
    try:
        ffmpeg.run(["-i", str(outgoing), "-i", str(incoming), "-filter_complex", mix, "-map", "[v]", "-frames:v", str(frames), *encode, str(temp)])
    except AltocueError as error:  # the set grew over ffmpeg's versions, and this machine's may stop short of this name
        raise AltocueError(str(error), fix=f"If this ffmpeg lacks {name}, use dissolve or update ffmpeg. {ffmpeg.INSTALL_FIX}") from error
    temp.rename(dest)
    return dest


def join(outgoing: Path, incoming: Path, name: str, frames: int, c: Any, encode: list[str], folder: Path, project: Any = None) -> Path:
    """One transition, cached by what went into it. A shader needs a browser. Without one, the film still finishes."""
    route, text = name, ""
    if name.startswith(SHADER_PREFIX):
        from .motion import shaders

        if shaders.ready():
            text = shaders.source(project, name)
        else:
            print(f"{name} needs the renderer, which is not installed, so this cut gets a {FALLBACK}. Run `altocue setup` for the shader.")
            route = FALLBACK
    key = hashlib.sha256(f"{outgoing.name}|{incoming.name}|{route}|{frames}|{encode}|{text}".encode()).hexdigest()[:16]
    dest = folder / f"join-{key}.mp4"
    if dest.exists():
        return dest
    if route in NAMES:
        return blend(outgoing, incoming, route, frames, c.fps, encode, dest)
    return shaders.render(route, text, outgoing, incoming, frames, c, encode, dest)
