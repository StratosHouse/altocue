"""The default renderer. HyperFrames is Apache-2.0, pinned exactly, with telemetry off."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..brand import Brand, data_path
from ..errors import AltocueError
from ..locks import heavy, render_workers
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


def quiet_env() -> dict[str, str]:
    """Every call to the renderer, setup included, runs with telemetry and update checks off."""
    return {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1", "DO_NOT_TRACK": "1"}


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
        shutil.copy(brand.font_file, stage / brand.font_file.name)
        if brand.logo:
            shutil.copy(brand.logo, stage / brand.logo.name)
        (stage / "index.html").write_text(compose(timeline, brand))
        output = dest / ("overlay-draft.mov" if draft else "overlay.mov")
        fps = 15 if draft else timeline["fps"]
        cmd = [str(binary()), "render", str(stage), "--format", "mov", "--output", str(output), "--fps", str(fps), "--quiet"]
        cmd += ["--quality", "draft" if draft else "delivery", "--workers", str(render_workers())]
        with heavy("rendering the graphics"):
            done = subprocess.run(cmd, check=False, capture_output=True, text=True, env=quiet_env())
        if done.returncode != 0 or not output.exists():
            tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-10:])
            raise AltocueError(f"HyperFrames could not render the graphics:\n{tail}", fix="Run `altocue doctor`, or use `--renderer basic`.")
        return Layer(path=output, renderer=self.id)
