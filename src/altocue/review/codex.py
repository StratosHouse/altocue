"""A second opinion through the `codex` CLI. It reads the stills, read-only, from a bare folder."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project
from . import rubric
from .gemini import settings

MAX_IMAGES = 30
TIMEOUT = 900
AUDIO_NOTE = "No sound reaches this reviewer. It reads a transcript and measurements."


def available() -> bool:
    return shutil.which("codex") is not None


def pick_images(pack_dir: Path) -> list[Path]:
    """Sheets and the hook first, because they cover the whole film. Cuts fill what is left."""
    sheets = sorted((pack_dir / "sheets").glob("*.jpg"))
    hook = sorted((pack_dir / "hook").glob("*.jpg"))[::2]
    cuts = sorted((pack_dir / "cuts").glob("*.jpg"))
    chosen = [*sheets, *hook][:MAX_IMAGES]
    room = MAX_IMAGES - len(chosen)
    if room and cuts:
        step = max(1, len(cuts) // room)
        chosen += cuts[::step][:room]
    return chosen


def review(project: Project, prompt: str, *, upload: bool, say: Callable[[str], None] = print) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run `codex exec` in a temp folder holding only frames, so it cannot find the edit reasons."""
    if not (upload or project.setting("review", "allow_upload", False)):
        raise AltocueError(
            "Codex review sends frames from the video to OpenAI, and you have not opted in.",
            fix="Pass --upload, or set allow_upload = true under [review] in altocue.toml. Or use --reviewer pack.",
        )
    binary = shutil.which("codex")
    if not binary:
        raise AltocueError(
            "The codex CLI is not on PATH.", fix="Install it from https://developers.openai.com/codex, or use --reviewer pack."
        )
    images = pick_images(project.review_dir / "pack")
    total = sum(1 for _ in (project.review_dir / "pack").rglob("*.jpg"))
    say(f"Sending {len(images)} frames, the transcript and the measurements to OpenAI through the codex CLI. The video itself stays here.")
    model = settings()["codex"]["model"]
    with tempfile.TemporaryDirectory(prefix="altocue-review-") as folder:
        stage, kept = Path(folder) / "stage", Path(folder) / "kept"
        stage.mkdir()
        kept.mkdir()
        copies = [Path(shutil.copy(image, stage / f"{image.parent.name}_{image.name}")) for image in images]
        schema, answer = kept / "schema.json", kept / "answer.json"
        schema.write_text(json.dumps(rubric.schema()))
        command = [
            binary,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--color",
            "never",
        ]
        if model:
            command += ["-m", model]
        for copy in copies:
            command += ["--image", str(copy)]
        command += ["--output-schema", str(schema), "-o", str(answer), "-C", str(stage), "-"]
        try:
            done = subprocess.run(command, input=prompt, capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
        except subprocess.TimeoutExpired as error:
            raise AltocueError("Codex took too long.", fix="Try again, or use --reviewer pack.") from error
        if done.returncode != 0:
            tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-6:])
            raise AltocueError(f"Codex failed:\n{tail}", fix="Run `codex login`, then try again.")
        if not answer.exists():
            raise AltocueError("Codex wrote no answer.", fix="Try again, or use --reviewer pack.")
        try:
            raw = json.loads(answer.read_text())
        except json.JSONDecodeError as error:
            raise AltocueError("Codex's answer was not JSON.", fix="Try again, or use --reviewer pack.") from error
    return raw, {
        "vendor": "openai",
        "model": model or "codex default",
        "sees": "stills",
        "via": "codex exec",
        "images": len(images),
        "images_in_pack": total,
        "audio_note": AUDIO_NOTE,
    }
