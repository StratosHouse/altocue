"""The default renderer. HyperFrames is Apache-2.0, pinned exactly, with telemetry off."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..brand import Brand, data_path
from ..errors import AltocueError
from ..locks import heavy, render_workers
from . import compositions, formats
from .html import compose
from .layer import Layer


def install_dir() -> Path:
    override = os.environ.get("ALTOCUE_RENDERER_DIR")
    if override:
        return Path(override)
    home = Path.home() / ".altocue" / "renderer"
    return home if home.exists() else data_path("renderer")


def binary() -> Path:
    return install_dir() / "node_modules" / ".bin" / "hyperframes"


def font_tool() -> Path:
    """HyperFrames' own font localiser: HTML in, the same HTML with its web fonts embedded out."""
    return install_dir() / "node_modules" / ".bin" / "hyperframes-localize-fonts"


def quiet_env() -> dict[str, str]:
    """Every call to the renderer, setup included, runs with telemetry and update checks off."""
    return {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1", "DO_NOT_TRACK": "1"}


def renderer_version() -> str:
    """The HyperFrames that is installed, or the one pinned when that cannot be read. A new renderer may draw the same page another way."""
    folder = install_dir()
    for path, keys in ((folder / "node_modules" / "hyperframes" / "package.json", ("version",)), (folder / "package.json", ("dependencies", "hyperframes"))):
        try:
            found: Any = json.loads(path.read_text())
            for key in keys:
                found = found[key]
            return str(found)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ""


def layer_key(stage: Path, **settings: Any) -> str:
    """Everything a layer is drawn from: every file on the stage, byte for byte, and what the renderer is asked to make of it."""
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
    for path in sorted(found for found in stage.rglob("*") if found.is_file()):
        digest.update(path.relative_to(stage).as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def kept_layer(output: Path, key: str) -> bool:
    """True when the layer beside this note was drawn from the same key and is still the file that render wrote."""
    try:
        note = json.loads(output.with_suffix(".json").read_text())
        return note["key"] == key and output.stat().st_size == note["bytes"] > 0
    except (OSError, ValueError, KeyError, TypeError):
        return False


def install_assets(timeline: dict[str, Any], brand: Brand, stage: Path) -> None:
    """Everything the page reads from beside itself. The render and the timeline share this."""
    shutil.copy(brand.font_file, stage / brand.font_file.name)
    if brand.logo:
        shutil.copy(brand.logo, stage / brand.logo.name)
    for font in formats.fonts_for(timeline["items"]):
        shutil.copy(data_path("fonts", formats.FONT_FILES[font]), stage / formats.FONT_FILES[font])
    compositions.install(timeline["items"], stage, install_dir())
    for item in timeline["items"]:
        for name, path in item.get("stage_images", {}).items():
            shutil.copy(path, stage / name)


def build_stage(stage: Path, items: list[dict[str, Any]], brand: Brand, page: str) -> Path:
    """A folder the renderer can load with no network: the page, its fonts, its pictures and any compositions."""
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    install_assets({"items": items}, brand, stage)
    (stage / "index.html").write_text(page)
    return stage


class HyperFramesRenderer:
    id = "hyperframes"
    licence = "Apache-2.0"

    def available(self) -> tuple[bool, str]:
        if not shutil.which("node"):
            return False, "Node 22 or later is not installed."
        if not binary().exists():
            return False, "the renderer is not installed."
        return True, ""

    def render(self, timeline: dict[str, Any], brand: Brand, dest: Path, *, draft: bool = False) -> Layer:
        stage = dest / "hyperframes"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        install_assets(timeline, brand, stage)
        (stage / "index.html").write_text(compose(timeline, brand))
        output = dest / ("overlay-draft.mov" if draft else "overlay.mov")
        fps = 15 if draft else timeline["fps"]
        quality = "draft" if draft else "delivery"
        size = [timeline.get("width"), timeline.get("height"), timeline.get("total")]
        key = layer_key(stage, fps=fps, quality=quality, format="mov", size=size, renderer=renderer_version())
        if kept_layer(output, key):  # a new music bed or insert, or the same edit rendered again, changes nothing the layer shows
            print("The graphics have not changed, so the last layer is used.", flush=True)
            return Layer(path=output, renderer=self.id)
        note = output.with_suffix(".json")
        note.unlink(missing_ok=True)  # a render that stops halfway must not leave the old key beside a new file
        cmd = [str(binary()), "render", str(stage), "--format", "mov", "--output", str(output), "--fps", str(fps), "--quiet"]
        cmd += ["--quality", quality, "--workers", str(render_workers())]
        with heavy("rendering the graphics"):
            done = subprocess.run(cmd, check=False, capture_output=True, text=True, env=quiet_env())
        if done.returncode != 0 or not output.exists():
            tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-10:])
            raise AltocueError(f"HyperFrames could not render the graphics:\n{tail}", fix="Run `altocue doctor`, or use `--renderer basic`.")
        note.write_text(json.dumps({"key": key, "bytes": output.stat().st_size}) + "\n")
        return Layer(path=output, renderer=self.id)
