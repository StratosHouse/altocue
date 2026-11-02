"""Any HyperFrames composition as a graphic: one you wrote, or one fetched from the registry."""

from __future__ import annotations

import json
import re
import shutil
from html import escape
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project

FOLDER = "motion/fetched"
STAGE_FOLDER = "compositions"
GSAP_CDN = re.compile(r"https://cdn\.jsdelivr\.net/npm/gsap@[\d.]+/dist/gsap\.min\.js")
OWN_ID = re.compile(r'<div[^>]*\bdata-composition-id="([^"]+)"')


def resolve(project: Project, item: dict[str, Any]) -> dict[str, Any]:
    """Find the file, and read the id the renderer needs to mount it."""
    named = item.get("name")
    relative = item.get("file") or (f"{FOLDER}/{named}.html" if named else None)
    if not relative:
        raise AltocueError('A composition needs "name" or "file".', fix='Use {"type": "composition", "name": "..."} after `altocue library add <name>`.')
    path = (project.root / relative).resolve()
    if not path.is_file() or not path.is_relative_to(project.root.resolve()):
        fix = f"Run `altocue library add {named}`." if named else "Point `file` at an HTML file inside the project."
        raise AltocueError(f"The composition {relative} is not in this project.", fix=fix)
    found = OWN_ID.search(path.read_text(errors="replace"))
    if not found:
        raise AltocueError(f"{relative} is not a HyperFrames composition.", fix='It needs a <template> holding a div with data-composition-id="...".')
    return {**item, "file": relative, "path": str(path), "own_id": found.group(1)}


def mount(item: dict[str, Any], attrs: str) -> str:
    values = escape(json.dumps(item.get("vars", {})), quote=True)
    source = f"{STAGE_FOLDER}/{item['own_id']}.html"
    size = f'data-width="{item.get("width", 1080)}" data-height="{item.get("height", 1920)}"'
    own = escape(item["own_id"])
    return f'<div class="clip" style="inset:0" data-composition-id="{own}" data-composition-src="{source}" {attrs} {size} data-variable-values="{values}"></div>'


def install(items: list[dict[str, Any]], stage: Path, renderer_dir: Path) -> None:
    """Copy each file beside the page. GSAP comes from the renderer's own copy, so a render never needs the network."""
    chosen = [item for item in items if item["type"] == "composition"]
    if not chosen:
        return
    folder = stage / STAGE_FOLDER
    folder.mkdir(exist_ok=True)
    local = next(iter(sorted(renderer_dir.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"))), None)
    if local:
        shutil.copy(local, folder / "gsap.min.js")
    for item in chosen:
        text = Path(item["path"]).read_text(errors="replace")
        if local:
            text = GSAP_CDN.sub(f"{STAGE_FOLDER}/gsap.min.js", text)
        (folder / f"{item['own_id']}.html").write_text(text)
