"""Higgsfield through its MIT CLI: browser login, no key.

Commands: https://github.com/higgsfield-ai/cli

`generate create` runs without `--wait`, because `--wait` hides the job id until the job
ends and a crash would lose it. The id is saved first, then `generate get` polls.

The repo ships a binary and no source, so the `--json` shapes below are read leniently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project
from . import http
from .base import Asset, Cost, CostLine, GenRequest, JobRef, JobStatus, Moderated, State, SubmitUnknown
from .catalog import Catalog

INSTALL_FIX = "Run: brew install higgsfield-ai/tap/higgsfield, or: npm install -g @higgsfield/cli. Then run: higgsfield auth login"
LOGIN_FIX = "Run: higgsfield auth login"
LOGIN_WORDS = ("not authenticated", "session expired", "auth login", "unauthorized")
BLOCK_WORDS = ("nsfw", "moderat", "content policy")
STATES: dict[str, State] = {
    "queued": "queued",
    "pending": "queued",
    "waiting": "queued",
    "in_progress": "running",
    "processing": "running",
    "running": "running",
    "completed": "succeeded",
    "succeeded": "succeeded",
    "success": "succeeded",
    "failed": "failed",
    "error": "failed",
    "nsfw": "moderated",
    "moderated": "moderated",
    "blocked": "moderated",
    "canceled": "canceled",
    "cancelled": "canceled",
}


class NeedsLogin(AltocueError):
    """The CLI is installed and not signed in."""


def normalise(status: str) -> State:
    """An unknown status keeps waiting. The run's timeout reports it, and nothing is paid twice."""
    return STATES.get(status.strip().lower(), "running")


def first(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return first(data[0]) if data else {}
    return data if isinstance(data, dict) else {}


def job_id_of(data: Any) -> str | None:
    item = first(data)
    for candidate in (item, first(item.get("job")), first(item.get("jobs"))):
        for key in ("job_id", "id", "request_id"):
            if candidate.get(key):
                return str(candidate[key])
    return None


def result_urls(item: dict[str, Any]) -> list[str]:
    found = []
    for key in ("result_url", "url", "video_url", "image_url"):
        if isinstance(item.get(key), str):
            found.append(item[key])
    for key in ("result", "output", "video", "image"):
        if isinstance(item.get(key), dict):
            found += result_urls(item[key])
        elif isinstance(item.get(key), str):
            found.append(item[key])
    for key in ("results", "outputs", "urls", "videos", "images"):
        for entry in item.get(key) or []:
            found += [entry] if isinstance(entry, str) else result_urls(entry) if isinstance(entry, dict) else []
    return list(dict.fromkeys(found))


def credits_of(data: Any) -> float | None:
    if isinstance(data, int | float) and not isinstance(data, bool):
        return float(data)
    item = first(data)
    for key in ("credits", "total_credits", "cost", "amount", "price"):
        if isinstance(item.get(key), int | float):
            return float(item[key])
    return None


def to_flags(payload: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    for key, value in payload.items():
        for item in value if isinstance(value, list) else [value]:
            flags += [f"--{key}", str(item).lower() if isinstance(item, bool) else str(item)]
    return flags


class HiggsfieldProvider:
    id = "higgsfield"
    cost_basis = "credits"

    def __init__(self, catalog: Catalog, *, project: Project | None = None):
        self.catalog = catalog
        config = catalog.provider(self.id)
        self.poll_seconds = float(config.get("poll_seconds", 3.0))
        self.destination = str(config["destination"])
        chosen = project.setting("generate", "usd_per_credit", None) if project else None
        self.usd_per_credit = float(chosen if chosen is not None else config["usd_per_credit"])
        self.rate_as_of = str(config.get("usd_per_credit_as_of", ""))

    def preflight(self) -> None:
        self._binary()

    def _binary(self) -> str:
        found = shutil.which("higgsfield")
        if not found:
            raise AltocueError("The higgsfield CLI is not on PATH.", fix=INSTALL_FIX)
        return found

    def _cli(self, *args: str, timeout: float = 600) -> Any:
        command = [self._binary(), *args, "--json"]
        try:
            done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise http.Unreachable(f"higgsfield {args[0]} {args[1]} did not answer in {timeout:.0f}s.") from error
        if done.returncode != 0:
            text = "\n".join((done.stderr or done.stdout).strip().splitlines()[-4:])
            lowered = text.lower()
            if any(word in lowered for word in LOGIN_WORDS):
                raise NeedsLogin(f"Higgsfield says: {text}", fix=LOGIN_FIX)
            if any(word in lowered for word in BLOCK_WORDS):
                raise Moderated(text)
            raise AltocueError(f"higgsfield {args[0]} {args[1]} failed: {text}")
        try:
            return json.loads(done.stdout)
        except ValueError as error:
            raise AltocueError(f"higgsfield {args[0]} {args[1]} did not print JSON.", fix="Update the CLI: higgsfield version") from error

    def upload(self, path: Path) -> str:
        """The CLI uploads local paths itself when a job is created."""
        return str(path)

    def estimate(self, req: GenRequest) -> Cost | None:
        """The live price, in credits. Unknown means unpriced, and an unpriced shot is never paid for."""
        entry = self.catalog.resolve(self.id, req)
        plain = replace(req, first_frame=None, last_frame=None, refs=[])
        try:
            data = self._cli("generate", "cost", entry.model, *to_flags(entry.payload(plain, str)), timeout=60)
        except NeedsLogin:
            raise
        except AltocueError:
            return None
        credits = credits_of(data)
        if credits is None:
            return None
        usd = credits * self.usd_per_credit
        note = f"{credits:g} credits at ${self.usd_per_credit:g} per credit. Set usd_per_credit under [generate] to match your plan."
        return Cost(usd=usd, lines=[CostLine(shot="", model=entry.model, usd=usd, note=note, as_of=self.rate_as_of)])

    def submit(self, req: GenRequest, idem_key: str) -> JobRef:
        """The CLI documents no idempotency flag, so the job store guards the gap around this call."""
        entry = self.catalog.resolve(self.id, req)
        flags = ["--prompt", req.prompt, *to_flags(entry.payload(req, self.upload))]
        try:
            data = self._cli("generate", "create", entry.model, *flags)
        except http.Unreachable as error:
            raise SubmitUnknown(str(error)) from error
        job_id = job_id_of(data)
        if not job_id:
            raise SubmitUnknown("Higgsfield took the job and printed no job id.")
        return JobRef(provider=self.id, job_id=job_id, model=entry.model, meta={"kind": req.kind})

    def poll(self, job: JobRef) -> JobStatus:
        item = first(self._cli("generate", "get", job.job_id, timeout=60))
        raw = str(item.get("status", ""))
        state = normalise(raw)
        if state == "succeeded":
            job.meta["result"] = item
        detail = str(item.get("error") or item.get("detail") or ("" if raw.lower() in STATES else f"Higgsfield says {raw}"))
        return JobStatus(state, detail)

    def fetch(self, job: JobRef, dest: Path) -> list[Asset]:
        result = job.meta.get("result") or {}
        urls = result_urls(result)
        if not urls:
            fix = f"See it with: higgsfield generate get {job.job_id} --json"
            raise AltocueError(f"Higgsfield finished {job.job_id} and printed no result URL.", fix=fix)
        assets = []
        for index, url in enumerate(urls):
            name = dest.name if index == 0 else f"{dest.name}-{index + 1}"
            target = dest.with_name(name + http.suffix_of(url, str(job.meta.get("kind", "video"))))
            http.download(url, target)
            assets.append(Asset(path=str(target), url=url, seed=result.get("seed")))
        return assets

    def cancel(self, job: JobRef) -> None:
        raise AltocueError("The higgsfield CLI has no cancel command.", fix=f"Cancel job {job.job_id} on higgsfield.ai.")
