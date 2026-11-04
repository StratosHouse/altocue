"""Shader transitions. gl-transitions need WebGL, so they run on the HyperFrames stage and come back as a clip.

Both clips go to the page as still frames. The page draws one frame per seek, from nothing but
those frames and the frame number, so a render is a pure function of its inputs and needs no
network. A dozen shaders ship with their headers. Any other is fetched into the project once.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import ffmpeg
from ..brand import data_path
from ..errors import AltocueError
from ..locks import heavy
from ..project import Project
from . import compositions
from .hyperframes import HyperFramesRenderer, binary, quiet_env

PACKAGE_VERSION = "2.2.0"
FETCH_URL = "https://cdn.jsdelivr.net/npm/gl-transitions@{version}/transitions/{file}.glsl"
MOST_BYTES = 64_000
LICENCE = re.compile(r"^\s*//\s*license\s*:\s*(MIT|BSD[ -]?[23][ -]?clause)", re.IGNORECASE | re.MULTILINE)
AUTHOR = re.compile(r"^\s*//\s*author\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
SETTING = re.compile(r"^[ \t]*uniform\s+(\w+)\s+(\w+)\s*(?:/\*\s*=\s*(.+?)\s*\*/\s*;|;\s*//\s*=\s*(.+?)\s*$)", re.MULTILINE)
UNIFORM = re.compile(r"^[ \t]*uniform\b.*$", re.MULTILINE)
NAME = re.compile(r"^gl-[a-z0-9]+(?:-[a-z0-9]+)*$")
TO_YUV = "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p"
TO_RGB = "scale=in_color_matrix=bt709:in_range=tv:flags=bicubic+accurate_rnd+full_chroma_int+full_chroma_inp,format=rgb24"
TAGS = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
BLANK = 4  # Mean level under which a frame is black.
PLAIN = "use a built-in transition such as dissolve"


def ready() -> bool:
    return HyperFramesRenderer().available()[0]


def bundled() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in data_path("transitions").glob("*.glsl")))


def file_name(name: str) -> str:
    """`gl-cross-zoom` to the upstream `CrossZoom`."""
    if not NAME.match(name):
        raise AltocueError(f"{name} is not a shader name.", fix="Use gl- and the name in lowercase with hyphens, such as gl-cross-zoom.")
    return "".join(part.capitalize() for part in name[3:].split("-"))


def licence_of(text: str) -> str:
    found = LICENCE.search(text[:1000])
    if not found:
        raise AltocueError("This shader does not state an MIT or BSD licence in its header.", fix="Altocue only runs shaders that do. Pick another.")
    return found.group(1)


def prepare(text: str) -> str:
    """The transition's settings become constants at their stated defaults. The rest of the header stays."""

    def constant(found: re.Match[str]) -> str:
        kind, name, block, line = found.groups()
        value = (block or line).split("//")[0].strip().rstrip(";").strip()
        return f"const {kind} {name} = {value};"

    body = SETTING.sub(constant, text)
    left = UNIFORM.search(body)
    if left:
        needed = left.group(0).strip()
        raise AltocueError(f"This shader needs something Altocue cannot supply: {needed}", fix="Pick another. One with a texture input or no default cannot run here.")
    if not re.search(r"\btransition\s*\(", body):
        raise AltocueError("This is not a gl-transition: it has no transition() function.", fix="Pick another.")
    return body


def source(project: Project | None, name: str) -> str:
    """A fetched copy in the project wins over the bundled one, so a project renders the same later and offline."""
    file_name(name)
    fetched = project.root / compositions.FOLDER / f"{name}.glsl" if project else None
    path = fetched if fetched and fetched.is_file() else data_path("transitions", f"{name}.glsl")
    if not path.is_file():
        raise AltocueError(f"The transition {name} is not in this project.", fix=f"Run `altocue library add {name}`. These need no fetch: {', '.join(bundled())}.")
    text = path.read_text(errors="replace")
    licence_of(text)
    return text


def fetch(project: Project, name: str, file: str | None = None) -> dict[str, Any]:
    """Copy one shader into the project. Its header stays with it, and the licence is checked before it is kept."""
    from ..generate import http

    url = FETCH_URL.format(version=PACKAGE_VERSION, file=file or file_name(name))
    reply = http.call("GET", url, timeout=30, attempts=3)
    if not reply.ok:
        raise AltocueError(f"Could not fetch {name}: HTTP {reply.status}.", fix="Check the name with `altocue library search shader`.")
    if len(reply.body) > MOST_BYTES:
        raise AltocueError(f"{name} is {len(reply.body)} bytes, far too large for a shader.", fix="Pick another.")
    text = reply.body.decode("utf-8", "replace")
    licence = licence_of(text)
    prepare(text)
    folder = project.root / compositions.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.glsl").write_text(text)
    author = AUTHOR.search(text[:1000])
    digest = hashlib.sha256(reply.body).hexdigest()
    return {"name": name, "licence": licence, "author": author.group(1) if author else None, "version": PACKAGE_VERSION, "url": url, "sha256": digest}


def page(text: str, frames: int, fps: int, width: int, height: int) -> str:
    values = {"seconds": frames / fps, "frames": frames, "fps": fps, "width": width, "height": height}
    html = data_path("transitions", "stage.html").read_text()
    for key, value in values.items():
        html = html.replace(f"{{{{{key}}}}}", repr(value))
    return html.replace("{{body}}", json.dumps(prepare(text)).replace("</", "<\\/"))


def _mean_level(path: Path) -> float:
    with Image.open(path) as image:
        return float(np.asarray(image.convert("L")).mean())


def render(name: str, text: str, outgoing: Path, incoming: Path, frames: int, c: Any, encode: list[str], dest: Path) -> Path:
    """Two clips of `frames` frames go in as stills, and one clip comes back in the same encoding."""
    with tempfile.TemporaryDirectory(prefix="shader-", dir=dest.parent) as scratch:
        stage = Path(scratch) / "stage"
        for side, clip in (("a", outgoing), ("b", incoming)):
            (stage / side).mkdir(parents=True)
            ffmpeg.run(["-i", str(clip), "-vf", TO_RGB, "-frames:v", str(frames), str(stage / side / "%04d.png")])
        (stage / "index.html").write_text(page(text, frames, c.fps, c.width, c.height))
        output = Path(scratch) / "out"
        cmd = [str(binary()), "render", str(stage), "--format", "png-sequence", "--output", str(output), "--fps", str(c.fps)]
        cmd += ["--quality", "delivery", "--workers", "1", "--quiet"]
        with heavy("rendering a transition"):
            done = subprocess.run(cmd, check=False, capture_output=True, text=True, env=quiet_env())
        made = sorted(output.glob("*.png")) if output.exists() else []
        if done.returncode != 0 or len(made) < frames:
            tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-10:])
            raise AltocueError(f"The stage could not render {name}, {len(made)} of {frames} frames:\n{tail}", fix=f"Run `altocue doctor`, or {PLAIN}.")
        if _mean_level(made[0]) < BLANK and _mean_level(made[-1]) < BLANK and _mean_level(stage / "a" / "0001.png") >= BLANK:
            raise AltocueError(f"{name} drew nothing. Its shader did not compile here.", fix=f"Pick another, or {PLAIN}.")
        temp = dest.with_suffix(".part.mp4")
        stills = ["-framerate", str(c.fps), "-start_number", "1", "-i", str(output / "frame_%06d.png")]
        ffmpeg.run([*stills, "-vf", f"{TO_YUV},{TAGS}", "-frames:v", str(frames), *encode, str(temp)])
    temp.rename(dest)
    return dest
