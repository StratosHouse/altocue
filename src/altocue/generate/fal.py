"""fal.ai by key: one queue shape for every model, `urllib` only.

Queue: https://fal.ai/docs/model-apis/model-endpoints/queue
Uploads: https://fal.ai/docs/documentation/model-apis/fal-cdn
Errors: https://fal.ai/docs/documentation/model-apis/errors
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..errors import AltocueError
from . import http
from .base import Asset, Cost, GenRequest, JobRef, JobStatus, Moderated, SubmitUnknown
from .catalog import Catalog, priced

KEY_FIX = "Create a key at https://fal.ai/dashboard/keys, then run: export FAL_KEY=your-key"
KEY_HOSTS = (".fal.run", ".fal.ai", ".falrun.com")
MODERATED = "content_policy_violation"


def media_urls(result: dict[str, Any]) -> list[str]:
    found = [result[key]["url"] for key in ("video", "image") if isinstance(result.get(key), dict) and "url" in result[key]]
    for key in ("videos", "images"):
        found += [item["url"] for item in result.get(key) or [] if isinstance(item, dict) and "url" in item]
    return found


def explain(reply: http.Response) -> tuple[str, str]:
    """A provider error as a normalised state and a sentence."""
    data = reply.json()
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list):
        items = [item for item in detail if isinstance(item, dict)]
        text = "; ".join(str(item.get("msg", item)) for item in items) or f"fal answered {reply.status}."
        return ("moderated" if any(item.get("type") == MODERATED for item in items) else "failed"), text
    return "failed", str(detail or f"fal answered {reply.status}.")


class FalProvider:
    id = "fal"
    cost_basis = "catalog"

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        config = catalog.provider(self.id)
        self.queue_url = str(config["queue_url"]).rstrip("/")
        self.rest_url = str(config["rest_url"]).rstrip("/")
        self.poll_seconds = float(config.get("poll_seconds", 3.0))
        self.destination = str(config["destination"])
        self._uploaded: dict[tuple[Path, int], str] = {}

    def preflight(self) -> None:
        self._key()

    def _key(self) -> str:
        key = os.environ.get("FAL_KEY", "")
        if not key:
            raise AltocueError("FAL_KEY is not set.", fix=KEY_FIX)
        return key

    def _auth(self, url: str) -> dict[str, str]:
        """The key only ever goes to fal, or to a local test server."""
        host = urlsplit(url).hostname or ""
        if host not in http.LOOPBACK and not host.endswith(KEY_HOSTS):
            raise AltocueError(f"Refusing to send FAL_KEY to {host}.", fix="Point queue_url and rest_url in models.toml at fal.")
        return {"Authorization": f"Key {self._key()}"}

    def _root(self, job: JobRef) -> str:
        return f"{self.queue_url}/{'/'.join(job.model.split('/')[:2])}/requests/{job.job_id}"

    def _saved(self, job: JobRef, name: str, fallback: str) -> str:
        """fal asks clients to prefer the URLs it returned. Only ones on its own host are used."""
        url = str(job.meta.get(name) or "")
        return url if url and urlsplit(url).netloc == urlsplit(self.queue_url).netloc else fallback

    def upload(self, path: Path) -> str:
        cached = (path, path.stat().st_mtime_ns)
        if cached in self._uploaded:
            return self._uploaded[cached]
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        url = f"{self.rest_url}/storage/upload/initiate?storage_type=fal-cdn-v3"
        reply = http.call("POST", url, headers=self._auth(url), body={"file_name": path.name, "content_type": kind}, attempts=3)
        if not reply.ok:
            raise AltocueError(f"fal refused to take {path.name}: {explain(reply)[1]}", fix=KEY_FIX if reply.status in {401, 403} else None)
        ticket = reply.json()
        sent = http.call("PUT", ticket["upload_url"], headers={"Content-Type": kind}, data=path.read_bytes(), timeout=300, attempts=3)
        if not sent.ok:
            raise AltocueError(f"Could not upload {path.name} to fal: {sent.status}.")
        self._uploaded[cached] = ticket["file_url"]
        return ticket["file_url"]

    def estimate(self, req: GenRequest) -> Cost | None:
        return priced(self.catalog, self.id, "", req)

    def submit(self, req: GenRequest, idem_key: str) -> JobRef:
        """fal documents no idempotency key, so the job store guards the gap around this call."""
        entry = self.catalog.resolve(self.id, req)
        url = f"{self.queue_url}/{entry.model}"
        headers = self._auth(url)
        body = {"prompt": req.prompt, **entry.payload(req, self.upload)}
        reply = http.submit(url, headers=headers, body=body)
        if not reply.ok:
            state, text = explain(reply)
            if state == "moderated":
                raise Moderated(text)
            raise AltocueError(f"fal rejected the request: {text}", fix=KEY_FIX if reply.status in {401, 403} else None)
        data = reply.json()
        if not data.get("request_id"):
            raise SubmitUnknown("fal accepted the request and returned no request id.")
        keep = {name: data[name] for name in ("status_url", "response_url", "cancel_url") if name in data}
        return JobRef(provider=self.id, job_id=data["request_id"], model=entry.model, meta={**keep, "kind": req.kind})

    def poll(self, job: JobRef) -> JobStatus:
        url = self._saved(job, "status_url", f"{self._root(job)}/status")
        reply = http.call("GET", url, headers=self._auth(url), timeout=30)
        if reply.status == 404:
            return JobStatus("failed", "fal has no record of this request.")
        if not reply.ok:
            raise http.Unreachable(f"fal answered {reply.status} to a status check.")
        data = reply.json()
        status = data.get("status")
        if status == "IN_QUEUE":
            return JobStatus("queued")
        if status == "COMPLETED":
            if data.get("error_type") == "client_cancelled":
                return JobStatus("canceled", str(data.get("error", "")))
            return self._finish(job)
        return JobStatus("running", "" if status == "IN_PROGRESS" else f"fal says {status}")

    def _finish(self, job: JobRef) -> JobStatus:
        """Read the result now: fal keeps it for an hour, or six minutes if it is 10 KB or more."""
        url = self._saved(job, "response_url", self._root(job))
        reply = http.call("GET", url, headers=self._auth(url), attempts=3)
        if reply.ok:
            job.meta["result"] = reply.json()
            units = reply.headers.get("x-fal-billable-units")
            if units:
                job.meta["billable_units"] = units
            return JobStatus("succeeded")
        if reply.status == 404:
            return JobStatus("failed", "The result has expired at fal, which keeps it for at most an hour.")
        state, text = explain(reply)
        return JobStatus(state, text)  # type: ignore[arg-type]

    def fetch(self, job: JobRef, dest: Path) -> list[Asset]:
        result = job.meta.get("result") or {}
        urls = media_urls(result)
        if not urls:
            raise AltocueError(f"fal finished {job.job_id} and returned no media.")
        assets = []
        for index, url in enumerate(urls):
            name = dest.name if index == 0 else f"{dest.name}-{index + 1}"
            target = dest.with_name(name + http.suffix_of(url, str(job.meta.get("kind", "video"))))
            http.download(url, target)
            assets.append(Asset(path=str(target), url=url, seed=result.get("seed")))
        return assets

    def cancel(self, job: JobRef) -> None:
        url = self._saved(job, "cancel_url", f"{self._root(job)}/cancel")
        reply = http.call("PUT", url, headers=self._auth(url), attempts=3)
        if not reply.ok:
            raise AltocueError(f"fal would not cancel {job.job_id}: {explain(reply)[1]}")
