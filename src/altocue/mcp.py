"""Altocue for any MCP client: a small server on stdin and stdout that wraps the `altocue` command.

It uses only the standard library and runs on Python 3.8 or later, so the plugin can start it from its own folder before
Altocue is installed. Every edit still goes through the `altocue` command and its consent rules. This server adds no flag
that spends, uploads or installs. It installs Altocue only when the call says the user agreed.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import __version__

NAME = "altocue"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
REPO = "https://github.com/StratosHouse/altocue"
UV_INSTALLER = "https://astral.sh/uv/install.sh"
# Commands light enough to run beside a render. Anything else waits for the running job, because two heavy jobs at once
# have crashed a 16 GB Mac.
LIGHT = {"doctor", "status", "recipes", "credits", "library", "--help", "-h", "--version"}
MAX_WAIT = 600
OUTPUT_CHARS = 16000

INSTRUCTIONS = """Altocue edits raw footage into finished video on this machine: cut, clean, grade, frame, caption, motion graphics, \
sound, review and delivery. It also makes launch films and explainers with no footage.

1. Call altocue_doctor first. If Altocue is missing, ask the user, then call altocue_install with confirm true.
2. Read altocue_guide before editing. It holds the workflow and the hard rules, and each tool has its own guide.
3. Run commands with altocue_run, for example ["new", "my-video", "--input", "/path/take.mov", "--aspect", "9:16"]. \
A long command keeps running in the background: follow it with altocue_job.
4. Never spend money, upload footage or install software without the user's yes for that exact step."""


def bin_dirs() -> list:
    """PATH plus the folders uv, Homebrew and pipx install into. An app started from the Dock often has none of them."""
    home = Path.home()
    extra = [
        os.environ.get("UV_TOOL_BIN_DIR"),
        os.environ.get("XDG_BIN_HOME"),
        str(home / ".local" / "bin"),
        str(home / ".cargo" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/home/linuxbrew/.linuxbrew/bin",
        "/usr/bin",
        "/bin",
    ]
    seen, found = set(), []
    for folder in os.environ.get("PATH", "").split(os.pathsep) + extra:
        if folder and folder not in seen:
            seen.add(folder)
            found.append(folder)
    return found


def child_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(bin_dirs())
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def find(name: str) -> str | None:
    return shutil.which(name, path=os.pathsep.join(bin_dirs()))


def find_altocue() -> str | None:
    chosen = os.environ.get("ALTOCUE_BIN")
    if chosen and os.path.isfile(chosen) and os.access(chosen, os.X_OK):
        return chosen
    return find("altocue")


def version_of(exe: str) -> str | None:
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60, env=child_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+\.\d+\.\d+\S*)", done.stdout + done.stderr)
    return match.group(1) if match else None


def older(found: str | None, wanted: str) -> bool:
    def parts(text: str) -> tuple:
        return tuple(int(piece) for piece in re.findall(r"\d+", text)[:3])

    return bool(found) and parts(found) < parts(wanted)


def state_dir() -> Path:
    root = os.environ.get("ALTOCUE_MCP_HOME") or os.environ.get("CLAUDE_PLUGIN_DATA") or str(Path.home() / ".altocue" / "mcp")
    folder = Path(root) / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def tail(path: Path, lines: int = 200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    kept = "\n".join(text.splitlines()[-max(1, lines) :])
    return kept[-OUTPUT_CHARS:]


class Job:
    """One command running in its own process group, so stopping it also stops the ffmpeg or Chrome it started."""

    def __init__(self, argv: list, cwd: str, kind: str) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.argv, self.cwd, self.kind = argv, cwd, kind
        self.started = time.time()
        self.stopped = False
        folder = state_dir() / self.id
        folder.mkdir(parents=True, exist_ok=True)
        self.log = folder / "output.log"
        with open(self.log, "wb") as out:
            self.process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=child_env(), start_new_session=True)
        (folder / "job.json").write_text(json.dumps({"argv": argv, "cwd": cwd, "started": self.started}, indent=2), encoding="utf-8")

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def wait(self, seconds: float) -> None:
        try:
            self.process.wait(timeout=max(0.0, min(float(seconds), MAX_WAIT)))
        except subprocess.TimeoutExpired:
            pass

    def stop(self) -> None:
        if not self.running:
            return
        self.stopped = True
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except OSError:
                pass

    def report(self, lines: int = 200) -> dict:
        code = self.process.poll()
        state = "running" if code is None else ("stopped" if self.stopped else "finished")
        found = {
            "job": self.id,
            "command": " ".join([Path(self.argv[0]).name] + self.argv[1:]),
            "cwd": self.cwd,
            "state": state,
            "seconds": round(time.time() - self.started, 1),
            "output": tail(self.log, lines),
            "log": str(self.log),
        }
        if code is not None:
            found["exit_code"] = code
        else:
            found["next"] = f"Still running. Call altocue_job with job {self.id} to follow it."
        return found


JOBS: dict = {}
JOBS_LOCK = threading.Lock()


def busy() -> Job | None:
    with JOBS_LOCK:
        return next((job for job in JOBS.values() if job.running and job.kind == "heavy"), None)


def start(argv: list, cwd: str, kind: str) -> Job:
    job = Job(argv, cwd, kind)
    with JOBS_LOCK:
        JOBS[job.id] = job
    return job


def stop_all() -> None:
    with JOBS_LOCK:
        jobs = list(JOBS.values())
    for job in jobs:
        job.stop()


class ToolError(Exception):
    """A tool result marked as an error, so the agent reads the reason and the next step."""


class BadParams(Exception):
    """A request the protocol itself rejects, such as a tool that does not exist."""


def wait_seconds(args: dict, default: int) -> int:
    value = args.get("wait_seconds", default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ToolError("wait_seconds must be a whole number of seconds, 0 or more.")
    return min(value, MAX_WAIT)


def not_installed() -> dict:
    found = {
        "installed": False,
        "uv": find("uv"),
        "next": "Altocue is not installed. Ask the user whether to install it, then call altocue_install with confirm true.",
    }
    if not found["uv"]:
        found["next"] += " uv is missing too: say that it comes from Astral's installer, and add install_uv true once they agree."
    return found


def tool_doctor(args: dict) -> dict:
    exe = find_altocue()
    found = {
        "altocue": exe,
        "uv": find("uv"),
        "ffmpeg": find("ffmpeg"),
        "node": find("node"),
        "platform": sys.platform,
        "server_version": __version__,
    }
    if not exe:
        found.update(not_installed())
        return found
    found["installed"] = True
    found["version"] = version_of(exe)
    try:
        done = subprocess.run([exe, "doctor", "--json"], capture_output=True, text=True, timeout=180, env=child_env())
        found["doctor"] = json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        found["doctor_error"] = f"altocue doctor --json did not give a report: {error}"
    if older(found["version"], __version__):
        found["update"] = f"Altocue {found['version']} is older than this server ({__version__}). Ask the user, then call altocue_install with confirm and upgrade true."
    if not found["ffmpeg"]:
        found["ffmpeg_fix"] = "brew install ffmpeg" if sys.platform == "darwin" else "sudo apt install ffmpeg"
    return found


def tool_install(args: dict) -> dict:
    if args.get("confirm") is not True:
        raise ToolError("Ask the user first: Altocue installs from PyPI with uv as a command line tool on this machine. Call again with confirm true once they agree.")
    notes = []
    uv = find("uv")
    if not uv:
        if args.get("install_uv") is not True:
            raise ToolError(
                f"uv is not installed. Ask the user whether to install it with Astral's official installer ({UV_INSTALLER}), "
                "then call again with confirm and install_uv true. Or they can run: curl -LsSf https://astral.sh/uv/install.sh | sh"
            )
        curl, sh = find("curl"), find("sh")
        if not (curl and sh):
            raise ToolError("Installing uv needs curl and sh, and this machine is missing one. See https://docs.astral.sh/uv/getting-started/installation/")
        done = subprocess.run([sh, "-c", f'"{curl}" -LsSf {UV_INSTALLER} | "{sh}"'], capture_output=True, text=True, timeout=300, env=child_env())
        uv = find("uv")
        if done.returncode != 0 or not uv:
            raise ToolError("The uv installer did not finish:\n" + (done.stdout + done.stderr)[-OUTPUT_CHARS:])
        notes.append(f"Installed uv at {uv}.")
    if not find("ffmpeg"):
        brew = find("brew")
        if args.get("install_ffmpeg") is True and brew:
            done = subprocess.run([brew, "install", "ffmpeg"], capture_output=True, text=True, timeout=1800, env=child_env())
            notes.append("Installed ffmpeg with Homebrew." if done.returncode == 0 else "Homebrew could not install ffmpeg:\n" + done.stderr[-4000:])
        else:
            notes.append(
                "ffmpeg is missing. Ask the user, then call again with install_ffmpeg true to use Homebrew."
                if brew
                else "ffmpeg is missing. On a Mac: brew install ffmpeg. On Ubuntu or Debian: sudo apt install ffmpeg."
            )
    if sys.platform == "darwin" and subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
        notes.append("The Xcode command line tools are missing. QA and voice isolation need them. The user runs: xcode-select --install")
    exe = find_altocue()
    speakers = args.get("speakers") is True
    upgrade = args.get("upgrade") is True
    if exe and not (upgrade or speakers):
        return {"installed": True, "altocue": exe, "version": version_of(exe), "notes": notes, "next": "Already installed. Call altocue_doctor."}
    argv = [uv, "tool", "install", "altocue[speakers]" if speakers else "altocue"] + (["--upgrade"] if upgrade else []) + (["--force"] if speakers else [])
    job = start(argv, str(Path.home()), "install")
    job.wait(wait_seconds(args, 240))
    found = job.report(60)
    found["notes"] = notes
    if found["state"] == "finished" and found.get("exit_code") == 0:
        found["next"] = (
            "Installed. Call altocue_doctor. Then offer the optional extras: altocue_run with ['setup', '--yes'] once the user agrees "
            "(the motion renderer needs Node 22 or later), and ['demo'] to prove the machine works with no keys and no spending."
        )
        if speakers:
            found["next"] += " For speakers, run ['setup', '--speakers', '--yes']."
    return found


def tool_run(args: dict) -> dict:
    argv = args.get("args")
    if not isinstance(argv, list) or not argv or not all(isinstance(piece, str) and "\x00" not in piece for piece in argv):
        raise ToolError('args must be a list of strings, the altocue command line without "altocue", for example ["status"].')
    if argv[0] == "mcp":
        raise ToolError("This is the MCP server already.")
    exe = find_altocue()
    if not exe:
        raise ToolError(not_installed()["next"])
    cwd = args.get("cwd") or os.getcwd()
    if not os.path.isdir(cwd):
        raise ToolError(f"cwd {cwd} is not a folder. Pass the project folder or the folder that holds the footage, as an absolute path.")
    kind = "light" if argv[0] in LIGHT else "heavy"
    running = busy() if kind == "heavy" else None
    if running:
        raise ToolError(
            f"Job {running.id} ({running.report(1)['command']}) is still running, and heavy jobs run one at a time. "
            "Follow it with altocue_job, or stop it with altocue_stop."
        )
    job = start([exe] + argv, cwd, kind)
    job.wait(wait_seconds(args, 60))
    return job.report()


def chosen_job(args: dict) -> Job:
    with JOBS_LOCK:
        job = JOBS.get(str(args.get("job", "")))
    if not job:
        raise ToolError("No job with that id is running in this session. Jobs from an earlier session have logs in " + str(state_dir()))
    return job


def tool_job(args: dict) -> dict:
    job = chosen_job(args)
    if job.running:
        job.wait(wait_seconds(args, 0))
    lines = args.get("lines", 80)
    return job.report(lines if isinstance(lines, int) and not isinstance(lines, bool) and lines > 0 else 80)


def tool_stop(args: dict) -> dict:
    job = chosen_job(args)
    job.stop()
    return job.report(40)


def skills_root() -> Path | None:
    here = Path(__file__).resolve().parent
    for folder in (here / "data" / "skills", here.parents[1] / "skills"):
        if (folder / "altocue" / "SKILL.md").is_file():
            return folder
    return None


def front_matter(text: str) -> tuple:
    """The skill's own fields and its body. The fields here are single lines, so no YAML library is needed."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fields = {}
    for line in text[3:end].splitlines():
        key, _, value = line.partition(":")
        if value and not line.startswith(" "):
            fields[key.strip()] = value.strip().strip('"')
    return fields, text[end + 4 :].lstrip("\n")


def skills() -> list:
    root = skills_root()
    if not root:
        return []
    found = []
    for page in sorted(root.glob("*/SKILL.md")):
        fields, _ = front_matter(page.read_text(encoding="utf-8"))
        found.append({"name": fields.get("name", page.parent.name), "description": fields.get("description", ""), "path": page})
    return found


def linked(text: str, page: Path, root: Path) -> str:
    """Relative links point into the repo, which an MCP client cannot open. Point them at the same file on GitHub."""
    home = posixpath.dirname("skills/" + page.relative_to(root).as_posix())

    def absolute(match: re.Match) -> str:
        target = posixpath.normpath(posixpath.join(home, match.group(1)))
        return match.group(0) if target.startswith("..") else f"]({REPO}/blob/main/{target}{match.group(2) or ''})"

    return re.sub(r"\]\((?!https?:|#|mailto:)([^)\s#]+)(#[^)\s]*)?\)", absolute, text)


def tool_guide(args: dict) -> dict:
    root = skills_root()
    if not root:
        raise ToolError(f"The guides are not on this machine. Read them at {REPO}/tree/main/skills")
    topic = str(args.get("topic") or "altocue").strip().strip("/")
    names = [entry["name"] for entry in skills()]
    if topic in ("index", "list", "all"):
        return {"guides": [{"topic": entry["name"], "use_when": entry["description"]} for entry in skills()]}
    page = root / topic if topic.endswith(".md") else root / topic / "SKILL.md"
    try:
        page.resolve().relative_to(root.resolve())
    except ValueError:
        raise ToolError("Name a guide such as altocue, altocue-launch or altocue-cut, or a reference file inside one.") from None
    if not page.is_file():
        raise ToolError(f"No guide called {topic}. The guides: {', '.join(names)}")
    text = linked(page.read_text(encoding="utf-8"), page, root)
    references = sorted(str(path.relative_to(root)) for path in page.parent.glob("references/*.md")) if page.name == "SKILL.md" else []
    return {"topic": topic, "guide": text, "references": references, "other_guides": [name for name in names if name != topic]}


BOOL = {"type": "boolean"}
WAIT = {"type": "integer", "minimum": 0, "maximum": MAX_WAIT, "description": "Seconds to wait for the command before it carries on in the background."}
TOOLS = [
    {
        "name": "altocue_doctor",
        "title": "Check this machine",
        "description": "Says whether Altocue is installed, its version, and what this machine can run, with the fix for each gap. Call it first.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "run": tool_doctor,
    },
    {
        "name": "altocue_install",
        "title": "Install or update Altocue",
        "description": (
            "Installs the altocue command from PyPI with uv. Only call it after the user said yes, and pass confirm true. "
            "install_uv runs Astral's official uv installer, install_ffmpeg uses Homebrew, and each needs its own yes. "
            "upgrade updates an existing install. speakers adds the optional speaker models."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "True only once the user agreed to install Altocue."},
                "upgrade": BOOL,
                "speakers": BOOL,
                "install_uv": BOOL,
                "install_ffmpeg": BOOL,
                "wait_seconds": WAIT,
            },
            "required": ["confirm"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
        "run": tool_install,
    },
    {
        "name": "altocue_run",
        "title": "Run an Altocue command",
        "description": (
            'Runs one altocue command, given as its arguments without "altocue", such as ["new", "my-video", "--input", "/abs/take.mov", "--aspect", "9:16"], '
            '["run", "-p", "/abs/my-video"], ["status", "-p", "/abs/my-video"] or ["demo"]. Use absolute paths, or set cwd. '
            "A long command keeps running and returns a job id for altocue_job. Heavy commands run one at a time. "
            "Never add a flag that spends money, uploads footage or installs software, such as --yes, --upload, --paid-key or --allow-upload, "
            "unless the user agreed to that exact step."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "args": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "The altocue command line, without the word altocue."},
                "cwd": {"type": "string", "description": "Absolute folder to run in. Defaults to the folder the server started in."},
                "wait_seconds": WAIT,
            },
            "required": ["args"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
        "run": tool_run,
    },
    {
        "name": "altocue_job",
        "title": "Follow a running command",
        "description": (
            "Reports a job that altocue_run or altocue_install started: running or finished, its exit code and the end of its output. wait_seconds waits for it first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job": {"type": "string"}, "wait_seconds": WAIT, "lines": {"type": "integer", "minimum": 1}},
            "required": ["job"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "run": tool_job,
    },
    {
        "name": "altocue_stop",
        "title": "Stop a running command",
        "description": "Stops a job and everything it started, such as ffmpeg or a render.",
        "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        "run": tool_stop,
    },
    {
        "name": "altocue_guide",
        "title": "Read an Altocue guide",
        "description": (
            "Returns a guide: the workflow, hard rules and commands for one job. Start with topic altocue. Others include altocue-launch for a "
            "launch video with no footage, altocue-cut, altocue-motion and altocue-review. Topic index lists them all."
        ),
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "run": tool_guide,
    },
]
BY_NAME = {tool["name"]: tool for tool in TOOLS}


def listed_tools() -> list:
    return [{key: value for key, value in tool.items() if key != "run"} for tool in TOOLS]


def listed_prompts() -> list:
    return [
        {
            "name": entry["name"],
            "description": entry["description"],
            "arguments": [{"name": "request", "description": "What you want done, in your own words.", "required": False}],
        }
        for entry in skills()
    ]


def prompt(params: dict) -> dict:
    name = str(params.get("name", ""))
    guide = tool_guide({"topic": name})
    request = (params.get("arguments") or {}).get("request")
    text = guide["guide"] + ("\n\nThe request: " + request if request else "")
    return {"description": f"Altocue guide: {name}", "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


def call(params: dict) -> dict:
    tool = BY_NAME.get(str(params.get("name", "")))
    if not tool:
        raise BadParams(f"No tool called {params.get('name')}")
    arguments = params.get("arguments") or {}
    try:
        found = tool["run"](arguments if isinstance(arguments, dict) else {})
        failed = False
    except ToolError as error:
        found, failed = {"error": str(error)}, True
    except Exception as error:  # noqa: BLE001  a tool must answer with the reason, never hang the client
        found, failed = {"error": f"{type(error).__name__}: {error}"}, True
    if not failed and found.get("exit_code") not in (None, 0):
        failed = True
    return {"content": [{"type": "text", "text": json.dumps(found, indent=2, default=str)}], "structuredContent": found, "isError": failed}


def answer(method: str, params: dict) -> dict:
    if method == "initialize":
        asked = str(params.get("protocolVersion", ""))
        return {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": NAME, "title": "Altocue", "version": __version__, "websiteUrl": "https://www.stratosagency.ai/altocue"},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": listed_tools()}
    if method == "tools/call":
        return call(params)
    if method == "prompts/list":
        return {"prompts": listed_prompts()}
    if method == "prompts/get":
        return prompt(params)
    if method in ("resources/list", "resources/templates/list"):
        return {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
    if method == "logging/setLevel":
        return {}
    raise LookupError(f"Method not found: {method}")


def serve(source=None, sink=None) -> int:
    source = source or sys.stdin.buffer
    sink = sink or sys.stdout.buffer
    lock = threading.Lock()

    def send(message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8") + b"\n"
        with lock:
            sink.write(data)
            sink.flush()

    def handle(message: dict) -> None:
        method, ident = message.get("method"), message.get("id")
        if not isinstance(method, str) or ident is None:
            return  # a notification, or a reply to something this server never asks
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            send({"jsonrpc": "2.0", "id": ident, "result": answer(method, params)})
        except LookupError as error:
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": str(error)}})
        except (BadParams, ToolError) as error:
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": str(error)}})
        except Exception as error:  # noqa: BLE001
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": f"{type(error).__name__}: {error}"}})

    try:
        for raw in source:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
            except ValueError:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            for one in message if isinstance(message, list) else [message]:
                if not isinstance(one, dict):
                    continue
                if one.get("method") in ("tools/call", "prompts/get"):
                    threading.Thread(target=handle, args=(one,), daemon=True).start()
                else:
                    handle(one)
    finally:
        stop_all()
    return 0


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.description = "Serve Altocue to any MCP client over stdin and stdout. The client starts this; you do not run it yourself."


def run(args: argparse.Namespace) -> int:
    """`altocue mcp`: the altocue that started this server is the one it runs, even when no altocue is on PATH, as under uvx."""
    if sys.argv and os.path.isfile(sys.argv[0]) and os.access(sys.argv[0], os.X_OK) and not os.environ.get("ALTOCUE_BIN"):
        os.environ["ALTOCUE_BIN"] = os.path.abspath(sys.argv[0])
    return serve()


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
