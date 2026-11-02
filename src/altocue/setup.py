"""Install what is missing, one step at a time, and only with consent."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from io import BytesIO
from pathlib import Path

from .brand import data_path
from .errors import AltocueError

HOME = Path.home() / ".altocue"


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", "-y", action="store_true", help="Install everything without asking")
    parser.add_argument("--skip-denoiser", action="store_true")
    parser.add_argument("--skip-renderer", action="store_true")
    parser.add_argument("--speakers", action="store_true", help="Fetch the models that tell speakers apart, about 35 MB")
    parser.add_argument("--plugin", action="store_true", help="Also print the Claude Code plugin commands")


def consent(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"Skipped: {question} Run with --yes to accept.")
        return False
    return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def install_renderer(yes: bool) -> None:
    from .motion.hyperframes import quiet_env

    if not shutil.which("npm"):
        print("Skipped the motion renderer: npm is not installed. The basic renderer will be used.")
        return
    target = HOME / "renderer"
    if (target / "node_modules" / ".bin" / "hyperframes").exists():
        print(f"Renderer already installed at {target}")
        return
    if not consent("Install the motion renderer (HyperFrames, Apache-2.0, about 120 MB, plus its own headless Chrome, about 200 MB)?", yes):
        return
    target.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy(data_path("renderer", name), target / name)
    done = subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=target, check=False, capture_output=True, text=True)
    if done.returncode != 0:
        raise AltocueError(f"Could not install the renderer:\n{done.stderr[-600:]}")
    renderer = str(target / "node_modules" / ".bin" / "hyperframes")
    browser = subprocess.run([renderer, "browser", "ensure"], check=False, capture_output=True, text=True, env=quiet_env())
    if browser.returncode != 0:
        print("Could not fetch headless Chrome now. The first render will try again.")
    print(f"Renderer installed at {target}")


def install_denoiser(yes: bool) -> None:
    from .clean import DEEP_FILTER_RELEASES, find_deep_filter

    if find_deep_filter():
        print("deep-filter already installed.")
        return
    key = f"{platform.system().lower()}-{platform.machine().lower()}"
    release = DEEP_FILTER_RELEASES.get(key)
    if not release:
        print(f"No deep-filter build for {key}. afftdn will be used.")
        return
    if not consent(f"Download deep-filter ({release['url'].rsplit('/', 1)[-1]}) for better noise removal?", yes):
        return
    target = HOME / "bin" / "deep-filter"
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(release["url"], timeout=120) as response:
        body = response.read()
    digest = hashlib.sha256(body).hexdigest()
    if digest != release["sha256"]:
        raise AltocueError(f"deep-filter download failed its checksum ({digest}).")
    target.write_bytes(body)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"deep-filter installed at {target}")


def install_speakers(yes: bool) -> None:
    """Fetch every model before keeping any, so a bad checksum leaves nothing half done."""
    from .speakers import MODELS, model_folder, models_ready

    if models_ready():
        print(f"Speaker models already at {model_folder()}")
        return
    listing = "; ".join(f"{model.name}, {model.megabytes:g} MB, {model.licence}" for model in MODELS)
    if not consent(f"Download the speaker models ({listing})?", yes):
        return
    fetched: dict[str, bytes] = {}
    for model in MODELS:
        with urllib.request.urlopen(model.url, timeout=300) as response:
            body = response.read()
        digest = hashlib.sha256(body).hexdigest()
        if digest != model.sha256:
            raise AltocueError(f"The {model.name} model failed its checksum ({digest}).", fix="Run it again. If it repeats, report it.")
        if model.member:
            with tarfile.open(fileobj=BytesIO(body), mode="r:bz2") as archive:  # read by name, so nothing in the archive picks where it lands
                fetched[model.file] = _member(archive, model.member)
                fetched["segmentation-LICENSE"] = _member(archive, model.member.rsplit("/", 1)[0] + "/LICENSE")
        else:
            fetched[model.file] = body
    folder = model_folder()
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in fetched.items():
        (folder / name).write_bytes(body)
    print(f"Speaker models installed at {folder}: {listing}.")


def _member(archive: tarfile.TarFile, name: str) -> bytes:
    found = archive.extractfile(name)
    if found is None:
        raise AltocueError(f"{name} is missing from the download.")
    return found.read()


def run(args: argparse.Namespace) -> int:
    from . import doctor

    ffmpeg_check = doctor.check_ffmpeg()
    if not ffmpeg_check.ok:
        raise AltocueError(f"ffmpeg: {ffmpeg_check.detail}", fix=ffmpeg_check.fix)
    if not args.skip_renderer:
        install_renderer(args.yes)
    if not args.skip_denoiser:
        install_denoiser(args.yes)
    if getattr(args, "speakers", False):
        install_speakers(args.yes)
    if args.plugin:
        print("\nIn Claude Code:\n  /plugin marketplace add StratosHouse/altocue\n  /plugin install altocue@altocue")
    print("\nRun `altocue doctor` to confirm.")
    return 0
