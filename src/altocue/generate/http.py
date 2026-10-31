"""Standard library HTTP. Redirects never carry a key, and only web URLs are fetched."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import urlsplit

from ..errors import AltocueError
from .base import SubmitUnknown

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_DOWNLOAD = 4_000_000_000
AGENT = "Altocue (+https://github.com/StratosHouse/altocue)"


class Unreachable(AltocueError):
    """The provider could not be reached, or stayed unhealthy through every retry."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        check_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoRedirect)
_fetcher = urllib.request.build_opener(_CheckedRedirect)
_opener.addheaders = _fetcher.addheaders = [("User-Agent", AGENT)]  # Cloudflare turns away urllib's default


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: Message = field(default_factory=Message)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return {}


def check_web_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK):
        return
    raise AltocueError(f"Refusing to fetch {url}: not an https address.")


def check_download_url(url: str) -> None:
    """Project files and provider replies name these URLs, so none may reach into a private network."""
    check_web_url(url)
    host = urlsplit(url).hostname or ""
    if host in LOOPBACK:
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as error:
        raise Unreachable(f"Could not reach {host}: {error}") from error
    if not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise AltocueError(f"Refusing to fetch {url}: it points at a private address.")


def _once(method: str, url: str, headers: dict[str, str], data: bytes | None, timeout: float) -> Response:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=timeout) as reply:
            return Response(reply.status, reply.read(), reply.headers)
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read(), error.headers)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        raise Unreachable(f"Could not reach {urlsplit(url).netloc}: {error}") from error


def call(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    data: bytes | None = None,
    timeout: float = 60,
    attempts: int = 5,
) -> Response:
    """One request. `attempts` above 1 is only for calls that are safe to repeat."""
    check_web_url(url)
    sent = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        sent.setdefault("Content-Type", "application/json")
    for attempt in range(1, attempts + 1):
        try:
            reply = _once(method, url, sent, data, timeout)
        except Unreachable:
            if attempt == attempts:
                raise
        else:
            if reply.status not in RETRY_STATUS or attempt == attempts:
                return reply
        sleep(min(2**attempt, 15))
    raise AssertionError("unreachable")


def submit(url: str, *, headers: dict[str, str], body: Any, timeout: float = 120) -> Response:
    """POST something that costs money. Exactly one attempt, and a lost reply is not a failure."""
    try:
        reply = call("POST", url, headers=headers, body=body, timeout=timeout, attempts=1)
    except Unreachable as error:
        raise SubmitUnknown(f"Lost the connection while submitting: {error}") from error
    if reply.status >= 500:
        raise SubmitUnknown(f"The provider answered {reply.status} to a submit, so the job may or may not exist.")
    return reply


def download(url: str, dest: Path, timeout: float = 300) -> Path:
    """Stream to a part file, then rename, so a crash never leaves half an asset."""
    check_download_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with _fetcher.open(url, timeout=timeout) as reply, part.open("wb") as out:
            while chunk := reply.read(1 << 20):
                written += len(chunk)
                if written > MAX_DOWNLOAD:
                    raise AltocueError(f"The file at {url} is over {MAX_DOWNLOAD // 1_000_000_000} GB.")
                out.write(chunk)
    except AltocueError:
        part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        part.unlink(missing_ok=True)
        raise Unreachable(f"Could not download {url}: {error}") from error
    if part.stat().st_size == 0:
        part.unlink()
        raise AltocueError(f"The file at {url} is empty.")
    os.replace(part, dest)
    return dest


def suffix_of(url: str, kind: str) -> str:
    name = Path(urlsplit(url).path).suffix.lower()
    if name in {".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".webp"}:
        return name
    return ".mp4" if kind == "video" else ".png"
