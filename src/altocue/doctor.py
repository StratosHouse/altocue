"""Check this machine and say exactly what is missing and how to fix it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass

from . import ffmpeg
from .errors import AltocueError
from .locks import memory_gb, render_workers

REQUIRED_FILTERS = ("lut3d", "loudnorm", "overlay", "alimiter", "acompressor", "sidechaincompress", "perspective", "ebur128", "blackdetect", "freezedetect")
NODE_MINIMUM = 22
MEMORY_NEEDED = 12  # GB


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Machine-readable output")


@dataclass
class Check:
    id: str
    ok: bool
    required: bool
    detail: str
    fix: str = ""


def _version(cmd: list[str]) -> str:
    done = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return (done.stdout or done.stderr).strip().splitlines()[0] if done.returncode == 0 else ""


def check_ffmpeg() -> Check:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return Check("ffmpeg", False, True, "not found", ffmpeg.INSTALL_FIX)
    missing = [name for name in REQUIRED_FILTERS if not ffmpeg.has_filter(name)]
    if missing:
        return Check("ffmpeg", False, True, f"missing filters: {', '.join(missing)}", ffmpeg.INSTALL_FIX)
    return Check("ffmpeg", True, True, _version(["ffmpeg", "-version"])[:40])


def check_node() -> Check:
    if not shutil.which("node"):
        return Check("node", False, False, "not found: the basic renderer still works", "Install Node 22 or later for full motion graphics.")
    version = _version(["node", "-v"])
    major = int(re.sub(r"\D", "", version.split(".")[0]) or 0)
    if major < NODE_MINIMUM:
        return Check("node", False, False, f"{version} is too old for full motion graphics", "Install Node 22 or later.")
    return Check("node", True, False, version)


def check_renderer() -> Check:
    from .motion.hyperframes import HyperFramesRenderer, binary

    ok, why = HyperFramesRenderer().available()
    return Check("renderer", ok, False, str(binary()) if ok else why, "" if ok else "Run `altocue setup`.")


def check_transcriber() -> Check:
    for module, label in (("parakeet_mlx", "parakeet"), ("mlx_whisper", "mlx-whisper"), ("faster_whisper", "faster-whisper")):
        if importlib.util.find_spec(module):
            return Check("transcriber", True, True, label)
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    package = "parakeet-mlx" if apple else "faster-whisper"
    return Check("transcriber", False, True, "none installed", f"Reinstall Altocue, or add it with `uv pip install {package}`.")


def check_denoiser() -> Check:
    from .clean import find_deep_filter

    found = find_deep_filter()
    detail = str(found) if found else "not found: afftdn will be used"
    return Check("deep-filter", bool(found), False, detail, "" if found else "Run `altocue setup` for better noise removal.")


def check_ocr() -> Check:
    """QA reads text in the picture. A skipped gate blocks SHIP, so this one matters."""
    if shutil.which("tesseract"):
        return Check("ocr", True, False, "tesseract")
    if platform.system() == "Darwin" and shutil.which("swift"):
        return Check("ocr", True, False, "Apple Vision")
    fix = "Install tesseract: brew install tesseract, or sudo apt install tesseract-ocr."
    return Check("ocr", False, False, "not found: text gates will skip, and a skipped gate blocks SHIP", fix)


def check_memory() -> Check:
    size = memory_gb()
    if size < MEMORY_NEEDED:
        detail = f"{size:.0f} GB: transcribing needs about 4 GB free, so close other apps"
        return Check("memory", False, False, detail, "Run one job at a time and use `--renderer basic`.")
    return Check("memory", True, False, f"{size:.0f} GB, {render_workers()} render workers")


def check_keys() -> list[Check]:
    keys = (
        ("fal", "FAL_KEY", "generation through fal.ai"),
        ("pexels", "PEXELS_API_KEY", "free stock B-roll from Pexels"),
        ("pixabay", "PIXABAY_API_KEY", "free stock B-roll from Pixabay"),
        ("gemini", "GEMINI_API_KEY", "Editor in Chief through Gemini"),
        ("elevenlabs", "ELEVENLABS_API_KEY", "hosted transcription"),
    )
    return [Check(name, bool(os.environ.get(env)), False, f"{env} {'set' if os.environ.get(env) else 'not set'}: {use}") for name, env, use in keys]


def check_clis() -> list[Check]:
    tools = (("codex", "Editor in Chief through Codex"), ("higgsfield", "generation through Higgsfield"))
    return [Check(name, bool(shutil.which(name)), False, f"{name} CLI {'found' if shutil.which(name) else 'not found'}: {use}") for name, use in tools]


def checks() -> list[Check]:
    return [check_ffmpeg(), check_node(), check_renderer(), check_transcriber(), check_denoiser(), check_ocr(), check_memory(), *check_keys(), *check_clis()]


def run(args: argparse.Namespace) -> int:
    results = checks()
    if args.json:
        print(json.dumps({"ok": all(c.ok for c in results if c.required), "checks": [asdict(c) for c in results]}, indent=2))
    else:
        for check in results:
            mark = "ok " if check.ok else ("!! " if check.required else "-- ")
            print(f"{mark}{check.id:<12} {check.detail}")
            if check.fix:
                print(f"    fix: {check.fix}")
    if not all(c.ok for c in results if c.required):
        raise AltocueError("Required tools are missing.", fix="See the lines marked !! above.")
    return 0
