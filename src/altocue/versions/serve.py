"""A tiny local server for the versions page, so a note is saved straight into the project with no export step.

Standard library only. It listens on 127.0.0.1 unless told otherwise and does three things: it serves the page,
it serves each version's film.mp4, and it takes notes by POST. Everything else is a 404. No path from a request
is ever joined onto a folder: a request either has one of those three shapes or it is refused, and a film is
checked again to sit inside versions/ once links are followed. A note is held to the same schema as an ingested
file. It prints its address and opens nothing.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from ..errors import AltocueError
from ..project import Project
from . import FILM, folder, ids, notes

LOCAL = ("127.0.0.1", "localhost", "::1")
MOST_BODY = 1_000_000  # bytes of notes in one request: hundreds of notes with drawings, and nothing larger
FILM_PATH = re.compile(r"/(v\d{3,})/film\.mp4")
RANGE = re.compile(r"bytes=(\d*)-(\d*)")
BLOCK = 1 << 16
TOKEN_HEADER = "X-Altocue-Token"


class Refusal(Exception):
    """A request this server will not serve, with the status to answer it with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def warning(host: str) -> str | None:
    """What to say before listening anywhere but this machine. None for an address only this machine can reach."""
    if host in LOCAL:
        return None
    return f"Warning: {host} is not 127.0.0.1, so anyone on that network can watch the films and add notes. There is no password."


def film_for(project: Project, url_path: str) -> Path | None:
    """The film a request names, or None. Only `/v003/film.mp4` has the shape, and the file must really sit inside versions/."""
    found = FILM_PATH.fullmatch(url_path)
    if not found:
        return None
    home = folder(project).resolve()
    target = (home / found.group(1) / FILM).resolve()
    return target if target.is_file() and target.is_relative_to(home) else None


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The first and last byte a Range header asks for. None means the whole file, and ValueError a range the file does not have."""
    found = RANGE.fullmatch((header or "").strip())
    if not found or not (found.group(1) or found.group(2)):
        return None
    if not found.group(1):
        return (max(size - int(found.group(2)), 0), size - 1)
    start = int(found.group(1))
    end = min(int(found.group(2)), size - 1) if found.group(2) else size - 1
    if start >= size or end < start:
        raise ValueError(header)
    return (start, end)


def host_allowed(header: str | None, bound: str, port: int) -> bool:
    """On a local address the Host must be local too, so a web page cannot reach this server under a name of its own."""
    if bound not in LOCAL:
        return True
    name, _, given = (header or "").rpartition(":")
    return name.strip("[]") in LOCAL and given == str(port)


class Headers(Protocol):
    """What a request's headers must do here. A plain dict in a test and the server's own message both do."""

    def get(self, name: str, default: Any = None, /) -> Any: ...


class Body(Protocol):
    """Anything that hands over a counted number of bytes."""

    def read(self, size: int = -1, /) -> bytes: ...


def read_json(headers: Headers, body: Body) -> Any:
    """A request's JSON, read only when it says it is JSON and says how long it is, and never past the cap."""
    if not str(headers.get("Content-Type", "")).lower().startswith("application/json"):
        raise Refusal(415, "Notes must be sent as application/json.")
    length = str(headers.get("Content-Length", ""))
    if not length.isdigit():
        raise Refusal(411, "The request must say how long it is.")
    if int(length) > MOST_BODY:
        raise Refusal(413, f"That is more than {MOST_BODY} bytes of notes in one request.")
    try:
        return json.loads(body.read(int(length)))
    except (ValueError, UnicodeDecodeError) as error:
        raise Refusal(400, "That is not JSON.") from error


def take_notes(project: Project, headers: Headers, body: Body, token: str) -> dict[str, Any]:
    """Check one POST of notes and keep what fits. The token proves the request came from the page this server sent."""
    if not hmac.compare_digest(str(headers.get(TOKEN_HEADER, "")), token):
        raise Refusal(403, "This request did not come from the versions page.")
    raw = read_json(headers, body)
    try:
        added, everything = notes.merge(project, raw, ids(project))
    except AltocueError as error:
        raise Refusal(400, str(error).splitlines()[0]) from error
    return {"added": [note["id"] for note in added], "notes": everything}


def handler_for(project: Project, token: str, bound: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "altocue"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  the base class's own name for it
            return

        def answer(self, status: int, body: bytes, kind: str, extra: Mapping[str, str] | None = None) -> None:
            self.send_response(status)
            headers = {"Content-Type": kind, "Content-Length": str(len(body)), "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", **(extra or {})}
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def refuse(self, status: int, message: str) -> None:
            self.answer(status, json.dumps({"error": message}).encode(), "application/json")

        def local(self) -> bool:
            if host_allowed(self.headers.get("Host"), bound, self.server.server_port):  # type: ignore[attr-defined]
                return True
            self.refuse(403, "This server only answers on the address it printed.")
            return False

        def send_film(self, film: Path) -> None:
            size = film.stat().st_size
            try:
                span = byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.answer(416, b"", "video/mp4", {"Content-Range": f"bytes */{size}"})
                return
            start, end = span or (0, size - 1)
            self.send_response(206 if span else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(end - start + 1, 0)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if span:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            left = end - start + 1
            with film.open("rb") as handle:
                handle.seek(start)
                while left > 0:
                    block = handle.read(min(BLOCK, left))
                    if not block:
                        break
                    try:
                        self.wfile.write(block)
                    except (BrokenPipeError, ConnectionResetError):
                        return  # a browser drops the connection whenever someone seeks
                    left -= len(block)

        def do_GET(self) -> None:  # noqa: N802  the name http.server calls
            if not self.local():
                return
            asked = urlsplit(self.path).path
            if asked in ("/", "/index.html"):
                from . import page

                rules = {"Content-Security-Policy": page.POLICY, "Referrer-Policy": "no-referrer"}
                self.answer(200, page.compose(project, token).encode(), "text/html; charset=utf-8", rules)
                return
            film = film_for(project, asked)
            if film:
                self.send_film(film)
            else:
                self.refuse(404, "Nothing here. This server has the page, each version's film and the notes address.")

        def do_POST(self) -> None:  # noqa: N802
            if not self.local():
                return
            if urlsplit(self.path).path != "/notes":
                self.refuse(404, "Notes are posted to /notes.")
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.refuse(403, "This request did not come from the versions page.")
                return
            try:
                kept = take_notes(project, self.headers, self.rfile, token)
            except Refusal as refusal:
                self.refuse(refusal.status, str(refusal))
                return
            self.answer(200, json.dumps(kept, ensure_ascii=False).encode(), "application/json")

    return Handler


def start(project: Project, *, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """A server ready to run, and the token its page carries. Nothing is listening for requests until `serve_forever`."""
    token = secrets.token_urlsafe(24)
    try:
        server = ThreadingHTTPServer((host, port), handler_for(project, token, host))
    except OSError as error:
        fix = "Pick another port with --port, or --port 0 for any free one."
        raise AltocueError(f"Cannot listen on {host} port {port}: {error.strerror or error}.", fix=fix) from error
    server.daemon_threads = True
    return server, token


def address(server: ThreadingHTTPServer, host: str) -> str:
    return f"http://{f'[{host}]' if ':' in host else host}:{server.server_port}/"


def run(project: Project, *, host: str, port: int) -> int:
    said = warning(host)
    if said:
        print(said)
    server, _ = start(project, host=host, port=port)
    print(f"The versions page is at {address(server, host)}")
    print("A note added there is saved straight into versions/notes.json. Nothing was opened: put the address in a browser yourself. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0
