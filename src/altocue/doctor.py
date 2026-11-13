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
from pathlib import Path
from typing import Any

from . import ffmpeg
from .errors import AltocueError
from .locks import GB, memory_gb, render_workers

REQUIRED_FILTERS = (
    "lut3d",
    "loudnorm",
    "overlay",
    "alimiter",
    "acompressor",
    "perspective",
    "xfade",
    "tpad",
    "blend",
    "noise",
    "vignette",
    "ebur128",
    "blackdetect",
    "freezedetect",
    "highpass",
    "bandreject",
    "equalizer",
    "afftdn",
    "asendcmd",
    "aresample",
    "apad",
    "atrim",
    "silencedetect",
    "astats",
)
NODE_MINIMUM = 22
MEMORY_NEEDED = 12  # GB
DISK_NEEDED = 5  # GB: 2.5 for the speech model, 0.3 for the renderer, and room to render
ALWAYS = "ships with Altocue"


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Machine-readable output, with every check, the version and whether this machine is ready")
    parser.add_argument("--all", action="store_true", help="Also list the hosted services that have no key or tool here. They are optional, so they are left out")


@dataclass
class Check:
    id: str
    ok: bool
    required: bool
    detail: str
    fix: str = ""
    hosted: bool = False  # a paid or keyed service somewhere else. Never needed to edit, so never part of the verdict


@dataclass
class Capability:
    feature: str
    ok: bool
    how: str
    fix: str = ""  # one command, ready to paste
    hosted: bool = False


def _version(cmd: list[str]) -> str:
    done = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return (done.stdout or done.stderr).strip().splitlines()[0] if done.returncode == 0 else ""


def check_ffmpeg() -> Check:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return Check("ffmpeg", False, True, "not found", ffmpeg.INSTALL_FIX)
    missing = [name for name in REQUIRED_FILTERS if not ffmpeg.has_filter(name)]
    if missing:
        return Check("ffmpeg", False, True, f"missing filters: {', '.join(missing)}", ffmpeg.INSTALL_FIX)
    return Check("ffmpeg", True, True, _version(["ffmpeg", "-version"]).split(" Copyright")[0])


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
    from . import transcribe

    return Check("transcriber", False, True, "none installed", transcribe.ENGINES[transcribe.auto_order()[0]].install_fix())


def check_denoiser() -> Check:
    """Which engine `altocue clean` will use on this machine, and the step up if there is one."""
    from .clean import choose_denoiser, isolator_model

    name, binary, _ = choose_denoiser()
    if name == "apple":
        return Check("denoiser", True, False, f"Apple voice isolation, {isolator_model()} model, built into this Mac")
    if name == "deep-filter":
        return Check("denoiser", True, False, f"deep-filter at {binary}")
    return Check("denoiser", False, False, "afftdn: stock ffmpeg, the plainest of the three", "Run `altocue setup` for better noise removal.")


def check_speakers() -> Check:
    from .speakers import model_folder, models_ready

    if importlib.util.find_spec("sherpa_onnx") is None:
        fix = 'Run `uv tool install --force "altocue[speakers]"`, then `altocue setup --speakers`.'
        return Check("speakers", False, False, "not installed: words will have no speakers", fix)
    if not models_ready():
        return Check("speakers", False, False, "models not fetched: words will have no speakers", "Run `altocue setup --speakers`.")
    return Check("speakers", True, False, f"sherpa-onnx, models at {model_folder()}")


def check_stems() -> Check:
    from .stems import have_demucs

    if not have_demucs():
        return Check("music removal", False, False, "Demucs not installed: music under a voice stays", 'Run `uv tool install --force altocue --with "demucs>=4.1,<5"`.')
    return Check("music removal", True, False, "Demucs, run on this machine when music is measured under the voice")


def check_gaze() -> Check:
    from .gaze import INSTALL

    if importlib.util.find_spec("mediapipe") is None:
        return Check("eyeline", False, False, "MediaPipe not installed: `altocue shorts gaze` cannot find look-aways", f"Run `{INSTALL}`.")
    return Check("eyeline", True, False, "MediaPipe face landmarker, run on this machine for `altocue shorts gaze`")


def ocr_reader() -> tuple[str | None, str]:
    from .qa import ocr

    return ocr.reader()


def check_ocr() -> Check:
    """QA reads text in the picture. A skipped gate blocks SHIP, so this asks the reader QA will use, and a Mac's `swift` shim proves nothing."""
    reader, _ = ocr_reader()
    if reader:
        return Check("ocr", True, False, "Apple Vision" if reader == "vision" else reader)
    fix = "Install tesseract: brew install tesseract, or sudo apt install tesseract-ocr."
    return Check("ocr", False, False, "not found: text gates will skip, and a skipped gate blocks SHIP", fix)


def check_memory() -> Check:
    size = memory_gb()
    if size < MEMORY_NEEDED:
        detail = f"{size:.0f} GB: transcribing needs about 4 GB free, so close other apps"
        return Check("memory", False, False, detail, "Run one job at a time and use `--renderer basic`.")
    return Check("memory", True, False, f"{size:.0f} GB, {render_workers()} render workers")


def check_disk() -> Check:
    free = shutil.disk_usage(Path.home()).free / GB
    if free < DISK_NEEDED:
        detail = f"{free:.1f} GB free: under {DISK_NEEDED} GB. The speech model takes 2.5 GB, the renderer about 0.3 GB, and each render needs room"
        return Check("disk", False, False, detail, "Free some space, then run `altocue doctor` again.")
    return Check("disk", True, False, f"{free:.0f} GB free")


def check_keys() -> list[Check]:
    keys = (
        ("fal", "FAL_KEY", "generation through fal.ai"),
        ("pexels", "PEXELS_API_KEY", "free stock B-roll from Pexels"),
        ("pixabay", "PIXABAY_API_KEY", "free stock B-roll from Pixabay"),
        ("gemini", "GEMINI_API_KEY", "Editor in Chief through Gemini"),
        ("elevenlabs", "ELEVENLABS_API_KEY", "hosted transcription"),
        ("freesound", "FREESOUND_API_KEY", "sound effects from Freesound"),
        ("1of10", "ONEOF10_API_KEY", "benchmarks through 1of10"),
        ("spyglass", "SPYGLASS_API_KEY", "benchmarks through Spyglass"),
        ("youtube", "YOUTUBE_API_KEY", "a raw YouTube list in benchmark"),
    )
    return [Check(name, bool(os.environ.get(env)), False, f"{env} {'set' if os.environ.get(env) else 'not set'}: {use}", hosted=True) for name, env, use in keys]


def check_clis() -> list[Check]:
    tools = (("codex", "Editor in Chief through Codex"), ("higgsfield", "generation through Higgsfield"))
    return [Check(name, bool(shutil.which(name)), False, f"{name} CLI {'found' if shutil.which(name) else 'not found'}: {use}", hosted=True) for name, use in tools]


def core_checks() -> list[Check]:
    return [check_ffmpeg(), check_node(), check_renderer(), check_transcriber(), check_denoiser(), check_speakers(), check_stems(), check_gaze(), check_ocr()]


def checks(core: list[Check] | None = None) -> list[Check]:
    return [*(core_checks() if core is None else core), check_memory(), check_disk(), *check_keys(), *check_clis()]


def install(brew: str, apt: str) -> str:
    return f"brew install {brew}" if platform.system() == "Darwin" else f"sudo apt install {apt}"


def install_node() -> str:
    """Apt's Node is older than 22 on current Ubuntu LTS, so it would leave the same gap."""
    return "brew install node" if platform.system() == "Darwin" else "sudo snap install node --channel=22/stable --classic"


def face_model() -> bool:
    from .qa import faces

    return faces.model_path() is not None


def model_cached(engine: Any) -> bool:
    """Whether the first transcription has to download anything. Only a real model folder counts."""
    repo = getattr(engine, "repo", "") or getattr(engine, "english", "")
    if "/" not in repo:
        repo = f"Systran/faster-whisper-{repo}"
    home = os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface"
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path(home) / "hub")
    return (hub / f"models--{repo.replace('/', '--')}").is_dir()


def transcribing(found: dict[str, Check]) -> Capability:
    from . import transcribe

    check = found["transcriber"]
    if not check.ok:
        name = transcribe.auto_order()[0]
        return Capability("Transcribe on this machine", False, check.detail, f'uv tool install --force "altocue[{name}]"')
    engine = transcribe.ENGINES[check.detail]
    download = f"the model downloads on first use, {getattr(engine, 'download', 'size unknown')}"
    return Capability("Transcribe on this machine", True, f"{check.detail}, " + ("model on disk" if model_cached(engine) else download))


def motion_row(found: dict[str, Check]) -> Capability:
    if not found["node"].ok:
        return Capability("Full motion graphics", False, found["node"].detail, install_node())
    if not found["renderer"].ok:
        return Capability("Full motion graphics", False, found["renderer"].detail, "altocue setup --yes")
    return Capability("Full motion graphics", True, f"HyperFrames, Node {found['node'].detail}")


def speakers_fix(check: Check) -> str:
    """The package first, because the models it fetches are useless without it."""

    return "altocue setup --speakers" if "not fetched" in check.detail else 'uv tool install --force "altocue[speakers]"'


def speakers_how(check: Check) -> str:
    """The fix is one command, so the second step it needs is said here and not lost."""
    return f"{check.detail}. After the fix, altocue setup --speakers fetches the models" if "not installed" in check.detail else check.detail


def row(feature: str, ok: bool, how: str, fix: str) -> Capability:
    return Capability(feature, ok, how, "" if ok else fix)


def capabilities(core: list[Check] | None = None) -> list[Capability]:
    """Every feature and whether it runs here. A gap carries the one command that closes it."""

    found = {check.id: check for check in (core_checks() if core is None else core)}

    def key(name: str, use: str) -> Capability:
        have = bool(os.environ.get(name))
        return Capability(use, have, f"{name} {'set' if have else 'not set'}", "" if have else f"export {name}=your-key", hosted=True)

    def tool(name: str, use: str, fix: str) -> Capability:
        have = bool(shutil.which(name))
        return Capability(use, have, f"{name} CLI {'found' if have else 'not found'}", "" if have else fix, hosted=True)

    faces_ready = face_model()
    return [
        row("Cut, clean, grade, frame, render and master", found["ffmpeg"].ok, found["ffmpeg"].detail, install("ffmpeg", "ffmpeg")),
        transcribing(found),
        Capability("Captions and cards, basic renderer", True, ALWAYS),
        motion_row(found),
        row("Stronger noise removal", found["denoiser"].ok, found["denoiser"].detail, "altocue setup --yes"),
        row("Tell speakers apart", found["speakers"].ok, speakers_how(found["speakers"]), speakers_fix(found["speakers"])),
        row("Text gates in QA", found["ocr"].ok, found["ocr"].detail, install("tesseract", "tesseract-ocr")),
        row("Face gates in QA", faces_ready, "YuNet, ships with Altocue" if faces_ready else "the face model is missing", "uv tool install --force altocue"),
        Capability("Review pack for a fresh agent", True, ALWAYS),
        Capability("Review page", True, ALWAYS),
        key("GEMINI_API_KEY", "Editor in Chief through Gemini"),
        tool("codex", "Editor in Chief through Codex", "npm install -g @openai/codex"),
        key("ELEVENLABS_API_KEY", "Hosted transcription through ElevenLabs"),
        key("PIXABAY_API_KEY", "Stock from Pixabay"),
        key("PEXELS_API_KEY", "Stock from Pexels"),
        key("FAL_KEY", "Generation through fal.ai"),
        tool("higgsfield", "Generation through Higgsfield", "npm install -g @higgsfield/cli"),
        key("FREESOUND_API_KEY", "Sound effects from Freesound"),
        key("ONEOF10_API_KEY", "Benchmarks through 1of10"),
        key("SPYGLASS_API_KEY", "Benchmarks through Spyglass"),
        key("YOUTUBE_API_KEY", "A raw YouTube list in benchmark"),
    ]


def print_capabilities(rows: list[Capability]) -> None:
    print("\nWhat this machine can run")
    width = max(len(row.feature) for row in rows)
    for row in rows:
        print(f"{'yes' if row.ok else 'no ':<3}  {row.feature:<{width}}  {row.how}")
        if row.fix:
            print(f"     fix: {row.fix}")


def and_list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


WARNINGS = ("memory", "disk")


def verdict(results: list[Check], rows: list[Capability]) -> tuple[bool, str]:
    """The answer first: whether this machine can edit, what for, and what is optional and still missing."""
    missing = [check.id for check in results if check.required and not check.ok]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        return False, f"Not ready: {and_list(missing)} {verb} missing, so nothing can be edited yet. The fix is on each line marked !! below."
    gaps = [check.id for check in results if not check.ok and not check.hosted and check.id not in WARNINGS]
    gaps += ["face gates" for row in rows if row.feature == "Face gates in QA" and not row.ok]
    blind = [gap for gap in gaps if gap in ("ocr", "face gates")]
    hosted = sum(1 for row in rows if row.hosted and not row.ok)
    said = "Ready: this machine can transcribe, cut, clean, grade, frame, caption, render and master" + (", and run QA." if not blind else ".")
    if blind:
        said += f" QA runs, but without {and_list(blind)} some gates skip, and a skipped gate blocks SHIP."
    if gaps:
        said += f" Optional and not set up, with a fix below: {and_list(gaps)}."
    warned = [check.id for check in results if not check.ok and check.id in WARNINGS]
    if warned:
        said += f" Low on {and_list(warned)}, see below."
    if hosted:
        said += f" {hosted} hosted service{' has' if hosted == 1 else 's have'} no key or tool, which is fine: `altocue doctor --all` lists them."
    return True, said


def run(args: argparse.Namespace) -> int:
    from . import __version__

    core = core_checks()
    results, rows = checks(core), capabilities(core)
    ready, said = verdict(results, rows)
    if args.json:
        report = {
            "version": __version__,
            "ready": ready,
            "verdict": said,
            "ok": ready,
            "checks": [asdict(c) for c in results],
            "capabilities": [asdict(row) for row in rows],
        }
        print(json.dumps(report, indent=2))
    else:
        everything = getattr(args, "all", False)
        print(f"altocue {__version__}. {said}\n")
        for check in results:
            if check.hosted and not check.ok and not everything:
                continue
            mark = "ok " if check.ok else ("!! " if check.required else "-- ")
            print(f"{mark}{check.id:<12} {check.detail}")
            if check.fix:
                print(f"    fix: {check.fix}")
        print_capabilities([row for row in rows if everything or row.ok or not row.hosted])
    if not ready:
        raise AltocueError("Required tools are missing.", fix="See the lines marked !! above.")
    return 0
