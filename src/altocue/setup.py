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
# How each step ended, so the last line can say what really happened.
INSTALLED, ALREADY, DECLINED, NOT_ASKED, UNAVAILABLE = "installed", "already there", "declined", "not asked", "unavailable"


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", "-y", action="store_true", help="Install everything without asking")
    parser.add_argument("--skip-denoiser", action="store_true")
    parser.add_argument("--deep-filter", action="store_true", help="Fetch deep-filter even on a Mac with Apple voice isolation")
    parser.add_argument("--skip-renderer", action="store_true")
    parser.add_argument("--speakers", action="store_true", help="Fetch the models that tell speakers apart, about 35 MB")
    parser.add_argument("--plugin", action="store_true", help="Also print the commands that add Altocue to Claude Code, Codex, Gemini CLI or any MCP client")


def consent(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"Skipped: {question} Run with --yes to accept.")
        return False
    return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def refused(yes: bool) -> str:
    """Why a step with no consent did not run: somebody said no, or there was nobody to ask."""
    return DECLINED if yes or sys.stdin.isatty() else NOT_ASKED


def install_renderer(yes: bool) -> str:
    from .motion.hyperframes import quiet_env

    if not shutil.which("npm"):
        print("Skipped the motion renderer: npm is not installed. The basic renderer will be used.")
        return UNAVAILABLE
    target = HOME / "renderer"
    if (target / "node_modules" / ".bin" / "hyperframes").exists():
        print(f"Renderer already installed at {target}")
        return ALREADY
    if not consent("Install the motion renderer (HyperFrames, Apache-2.0, about 120 MB, plus its own headless Chrome, about 200 MB)?", yes):
        return refused(yes)
    print("Installing the motion renderer: about 320 MB from the npm registry and Chrome's download host. This can take a few minutes.", flush=True)
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
    return INSTALLED


def install_denoiser(yes: bool, even_on_a_mac: bool = False) -> str:
    from .clean import DEEP_FILTER_RELEASES, find_deep_filter, find_isolator

    if find_isolator() and not even_on_a_mac:
        print("Apple voice isolation is built into this Mac, so nothing to download.")
        return ALREADY
    if find_deep_filter():
        print("deep-filter already installed.")
        return ALREADY
    key = f"{platform.system().lower()}-{platform.machine().lower()}"
    release = DEEP_FILTER_RELEASES.get(key)
    if not release:
        print(f"No deep-filter build for {key}. afftdn will be used.")
        return UNAVAILABLE
    if not consent(f"Download deep-filter ({release['url'].rsplit('/', 1)[-1]}) for better noise removal?", yes):
        return refused(yes)
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
    return INSTALLED


def install_speakers(yes: bool) -> str:
    """Fetch every model before keeping any, so a bad checksum leaves nothing half done."""
    from .speakers import MODELS, model_folder, models_ready

    if models_ready():
        print(f"Speaker models already at {model_folder()}")
        return ALREADY
    listing = "; ".join(f"{model.name}, {model.megabytes:g} MB, {model.licence}" for model in MODELS)
    if not consent(f"Download the speaker models ({listing})?", yes):
        return refused(yes)
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
    return INSTALLED


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
    ended: list[str] = []
    if not args.skip_renderer:
        ended.append(install_renderer(args.yes))
    if not args.skip_denoiser:
        ended.append(install_denoiser(args.yes, even_on_a_mac=args.deep_filter))
    if getattr(args, "speakers", False):
        ended.append(install_speakers(args.yes))
    if args.plugin:
        print(
            "\nIn Claude Code:\n  /plugin marketplace add StratosHouse/altocue\n  /plugin install altocue@altocue\n  /altocue:setup"
            "\n\nIn Codex:\n  codex plugin marketplace add StratosHouse/altocue\n  codex plugin add altocue@altocue"
            "\n\nIn Gemini CLI:\n  gemini extensions install https://github.com/StratosHouse/altocue"
            '\n\nIn any MCP client:\n  {"mcpServers": {"altocue": {"command": "uvx", "args": ["altocue", "mcp"]}}}'
        )
    print(f"\n{last_line(ended)}")
    return 0


def last_line(ended: list[str]) -> str:
    """The line an agent reads last. A run that installed nothing says so first, and never reads as done.

    The exit code stays 0: the extras are optional, and the skills run a plain `altocue setup` straight after the install.
    """
    if INSTALLED in ended:
        return "Run `altocue doctor` to confirm."
    if NOT_ASKED in ended:
        return (
            "Nothing was installed: there is no terminal to ask at, and --yes was not given. Altocue still edits without these extras. "
            "Ask the user, then run `altocue setup --yes`."
        )
    if DECLINED in ended:
        return "Nothing was installed, as you chose. Altocue still edits without these extras, and `altocue setup` asks again any time."
    return "Nothing was installed, because nothing was missing that this machine can take. Run `altocue doctor` to see what is ready."
