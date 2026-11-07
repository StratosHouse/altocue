"""Film a Stageshow stage into a video insert, frame-exact, for a product UI moment in a reel.

Stageshow (github.com/benjitaylor/skills, MIT) builds believable faux product UI in React: windows,
phones, dashboards and cursor walkthroughs. Its kit reads every clock through the page's globals, so
`data/stageshow/record.mjs` swaps in Stageshow's own virtual clock before the page loads and steps it
exactly one frame at a time. The same stage gives the same frames on every machine, and a beat lands
on a known frame, which is what an edit needs.

The insert is an illustration, not a screen recording. Its sidecar says so, and a reel that shows it
labels it as a mockup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from .errors import AltocueError
from .project import Project, write_json

RECORDER = Path(__file__).parent / "data" / "stageshow" / "record.mjs"
SOURCE = "https://github.com/benjitaylor/skills"
INSTALL = "npx skills add benjitaylor/skills --skill stageshow, then npm install in its folder"
NODE_MIN = (20, 19)
NEEDS = ("esbuild", "playwright-core", "vite")
SELECT = ".ss-stage"
LABEL = "Illustrative mockup"


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    doctor = sub.add_parser("doctor", help="Check that Stageshow, Node, a browser and ffmpeg are ready")
    doctor.add_argument("--stageshow", help="The stageshow folder. Found on its own when installed as a skill")
    record = sub.add_parser("record", help="Film a stage to MP4, one fixed frame at a time")
    where = record.add_mutually_exclusive_group(required=True)
    where.add_argument("--example", help="An example in the stageshow folder, such as hero-demo. Its preview is served for the run")
    where.add_argument("--url", help="Any page that renders a Stageshow stage, already being served")
    record.add_argument("--stageshow", help="The stageshow folder. Found on its own when installed as a skill")
    record.add_argument("--project", "-p", help="Write into this project's inserts/ folder and print the motion.json entry")
    record.add_argument("--out", help="Output file. Defaults to inserts/stageshow-<name>.mp4 in the project, or ./stageshow-<name>.mp4")
    record.add_argument("--name", help="Name for the insert. Defaults to the example or the page")
    record.add_argument("--select", default=SELECT, help=f"CSS selector of the stage to film. Default {SELECT}")
    record.add_argument("--seconds", type=float, default=12.0, help="Length to film. Stageshow loops run 10 to 16 s")
    record.add_argument("--start", type=float, default=0.0, help="Seconds of the stage's own time to skip first")
    record.add_argument("--fps", type=int, default=30, help="Frame rate. Match the project's")
    record.add_argument("--width", type=int, default=1280, help="Browser width in CSS pixels. 390 films a stage's compact design")
    record.add_argument("--scale", type=float, default=2.0, help="Device pixel ratio. 2 keeps text sharp after a punch-in")
    record.add_argument("--dark", action="store_true", help="Prefer the dark theme")
    record.add_argument("--still", action="store_true", help="One PNG of the poster frame instead of a video")
    record.add_argument("--background", default="", help="Page colour behind the stage, such as #0b0b0c")
    record.add_argument("--chrome", help="Chrome or Chromium to drive. Defaults to CHROME_PATH, then Playwright's own")


def candidates() -> list[Path]:
    home = Path.home()
    found = [Path(p) for p in (os.environ.get("ALTOCUE_STAGESHOW"),) if p]
    return found + [home / ".claude/skills/stageshow", home / ".agents/skills/stageshow", home / ".altocue/stageshow", home / ".codex/skills/stageshow"]


def locate(given: str | None) -> Path:
    for folder in [Path(given).expanduser()] if given else candidates():
        if (folder / "kit" / "clock.ts").exists():
            return folder.resolve()
    where = given or ", ".join(str(p) for p in candidates())
    raise AltocueError(f"No Stageshow folder at {where}.", fix=f"Install it: {INSTALL}. Or pass --stageshow <folder>.")


def node_version() -> tuple[int, int] | None:
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    match = re.match(r"v(\d+)\.(\d+)", out)
    return (int(match[1]), int(match[2])) if match else None


def browser(given: str | None) -> str | None:
    """A Chrome to drive. None means Playwright's own download, which record.mjs tries last."""
    for path in (given, os.environ.get("CHROME_PATH"), "/opt/pw-browsers/chromium"):
        if path and Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac) if mac.exists() else None


def problems(folder: Path, chrome: str | None) -> list[str]:
    found = []
    version = node_version()
    if version is None:
        found.append("Node is not on PATH. Stageshow needs Node 20.19 or later.")
    elif version < NODE_MIN:
        found.append(f"Node {version[0]}.{version[1]} is too old. Stageshow needs 20.19 or later.")
    missing = [name for name in NEEDS if not (folder / "node_modules" / name).exists()]
    if missing:
        found.append(f"{', '.join(missing)} not installed in {folder}. Run npm install there.")
    if not shutil.which("ffmpeg"):
        found.append("ffmpeg is not on PATH.")
    if not chrome:
        found.append("No Chrome found. Set CHROME_PATH, or pass --chrome.")
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(folder: Path) -> tuple[subprocess.Popen[bytes], str]:
    """Stageshow's own preview, on a port nobody else holds, for the length of one recording."""
    port = free_port()
    vite = folder / "node_modules" / "vite" / "bin" / "vite.js"
    command = ["node", str(vite), "playground", "--config", "playground/vite.config.ts", "--host", "127.0.0.1", "--port", str(port), "--strictPort"]
    server = subprocess.Popen(command, cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AltocueError("Stageshow's preview did not start.", fix=f"Run npm install in {folder}, then try again.")
        try:
            urllib.request.urlopen(base, timeout=2).close()
            return server, base
        except OSError:
            time.sleep(0.4)
    server.terminate()
    raise AltocueError("Stageshow's preview did not answer within a minute.", fix=f"Check `npm run dev` in {folder}.")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "stage"


def destination(args: argparse.Namespace, name: str) -> tuple[Path, Project | None]:
    suffix = ".png" if args.still else ".mp4"
    project = Project.open(Path(args.project)) if args.project else None
    if args.out:
        return Path(args.out).expanduser().resolve(), project
    folder = project.root / "inserts" if project else Path.cwd()
    return folder / f"stageshow-{name}{suffix}", project


def record(args: argparse.Namespace) -> dict[str, Any]:
    folder = locate(args.stageshow)
    chrome = browser(args.chrome)
    blocking = [p for p in problems(folder, chrome) if not p.startswith("No Chrome")]
    if blocking:
        raise AltocueError(blocking[0], fix=f"`altocue stageshow doctor` lists everything. Install: {INSTALL}.")
    name = slug(args.name or args.example or re.sub(r"^https?://", "", args.url))
    out, project = destination(args, name)
    out.parent.mkdir(parents=True, exist_ok=True)
    server = None
    try:
        if args.example:
            if not (folder / "examples" / args.example / "index.tsx").exists():
                raise AltocueError(f"No example called {args.example}.", fix=f"Pick one from {folder / 'examples'}.")
            server, base = serve(folder)
            url = f"{base}?capture={args.example}"
        else:
            url = args.url
        command = [
            "node",
            str(RECORDER),
            "--stageshow",
            str(folder),
            "--url",
            url,
            "--out",
            str(out),
            "--select",
            args.select,
            "--seconds",
            str(args.seconds),
            "--start",
            str(args.start),
            "--fps",
            str(args.fps),
            "--width",
            str(args.width),
            "--scale",
            str(args.scale),
        ]
        command += ["--chrome", chrome] if chrome else []
        command += ["--background", args.background] if args.background else []
        command += [flag for flag, on in (("--dark", args.dark), ("--still", args.still)) if on]
        done = subprocess.run(command, capture_output=True, text=True)
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if done.returncode not in (0, 1) or not lines:
        raise AltocueError(f"Recording failed: {(done.stderr or done.stdout).strip().splitlines()[-1:] or ['no output']}", fix="Run `altocue stageshow doctor`.")
    result = json.loads(lines[-1])
    if result["errors"]:
        raise AltocueError(f"The page logged errors while filming: {result['errors'][0]}", fix="Fix the stage until the preview runs clean, then film again.")
    sidecar = {
        "source": "stageshow",
        "source_url": SOURCE,
        "page": url if args.url else f"example:{args.example}",
        "illustrative": True,
        "label": LABEL,
        "frames": result["frames"],
        "fps": result["fps"],
        "seconds": result["seconds"],
        "size": result["size"],
        "dark": args.dark,
    }
    write_json(out.with_suffix(".json"), sidecar)
    return {"out": out, "project": project, **sidecar}


def run(args: argparse.Namespace) -> int:
    if args.action == "doctor":
        folder = locate(args.stageshow)
        chrome = browser(None)
        found = problems(folder, chrome)
        print(f"Stageshow: {folder}")
        print(f"Browser:   {chrome or 'Playwright default'}")
        for line in found:
            print(f"  FIX  {line}")
        print("Ready to film." if not found else f"{len(found)} thing(s) to fix.")
        return 0 if not found else 1
    made = record(args)
    out, project = made["out"], made["project"]
    print(f"Filmed {made['frames']} frames at {made['fps']} fps ({made['size']}): {out}")
    if project and not args.still:
        file = str(out.relative_to(project.root)) if out.is_relative_to(project.root) else str(out)
        entry = {"file": file, "start": "<word or seconds>", "duration": round(min(made["seconds"], 4.0), 2), "in": "fade", "out": "fade"}
        print("Place it with an insert in motion.json, and label it as a mockup on screen:")
        print(json.dumps({"inserts": [entry]}, indent=2))
    return 0
