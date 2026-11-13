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
PRIVATE_NAMES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")  # names no public site has
PUBLIC_ONLY = "Only files on the public web are fetched. Download this one yourself and pass the file."
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


def proxy_for(url: str) -> str:
    """The host of the proxy a fetch of `url` goes through, read the way urllib reads it. Empty when the fetch goes direct."""
    parts = urlsplit(url)
    proxy = urllib.request.getproxies().get(parts.scheme, "")
    if not proxy or urllib.request.proxy_bypass(parts.hostname or ""):
        return ""
    return urlsplit(proxy if "//" in proxy else f"//{proxy}").hostname or "proxy"  # never its user name or password


def literal_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """`host` as an address when it is one, in any spelling a resolver takes without a lookup, such as 2130706433 or 0x7f.1."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(host))
    except (OSError, ValueError, UnicodeError):
        return None


def check_download_url(url: str) -> None:
    """Project files and provider replies name these URLs, so none may reach into a private network.

    Behind a proxy this machine may have no name lookup of its own, and the proxy does the lookup anyway. So the lookup is
    the one check left out there. The scheme, the credentials, a written address and a name that is private by its
    spelling are all still checked.
    """
    check_web_url(url)
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".")
    if parts.username or parts.password:
        raise AltocueError(f"Refusing to fetch from {host}: the address carries a user name or a password.", fix="Use the plain https address of the file.")
    private = AltocueError(f"Refusing to fetch {url}: it points at a private address.", fix=PUBLIC_ONLY)
    if host in LOOPBACK:
        return
    written = literal_address(host)
    if written is not None:
        if not written.is_global:
            raise private
        return
    if proxy_for(url):
        if "." not in host or host.endswith(PRIVATE_NAMES):
            raise private
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as error:
        raise Unreachable(f"Could not reach {host}: {error}") from error
    if not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise private


def not_reached(url: str, error: Exception, verb: str) -> Unreachable:
    """A fetch the proxy refused says so, because "check the web" is the wrong advice on a machine that only has a proxy."""
    parts = urlsplit(url)
    proxy = "" if isinstance(error, urllib.error.HTTPError) else proxy_for(url)  # an HTTP status over https came from the site itself
    what = url if verb == "download" else parts.netloc
    if proxy:
        return Unreachable(f"Could not {verb} {what} through the proxy at {proxy}: {error}. Ask whoever runs the proxy to allow {parts.hostname}.")
    return Unreachable(f"Could not {verb} {what}: {error}")


def open_keyed(request: urllib.request.Request, *, timeout: float) -> Any:
    """Send a request that carries a key. A redirect comes back as an HTTPError, never as a second request with the key on it."""
    return _opener.open(request, timeout=timeout)


def _once(method: str, url: str, headers: dict[str, str], data: bytes | None, timeout: float) -> Response:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=timeout) as reply:
            return Response(reply.status, reply.read(), reply.headers)
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read(), error.headers)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        raise not_reached(url, error, "reach") from error


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


def download(url: str, dest: Path, timeout: float = 300, limit: int = MAX_DOWNLOAD, headers: dict[str, str] | None = None) -> Path:
    """Stream to a part file, then rename, so a crash never leaves half an asset. Redirects keep `headers`, so a key never goes here."""
    check_download_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with _fetcher.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as reply, part.open("wb") as out:
            while chunk := reply.read(1 << 20):
                written += len(chunk)
                if written > limit:
                    raise AltocueError(f"The file at {url} is over {limit:,} bytes.")
                out.write(chunk)
    except AltocueError:
        part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        part.unlink(missing_ok=True)
        raise not_reached(url, error, "download") from error
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
