"""One generation run: plan, show the cost, wait for a yes, submit, poll, download, record."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from .. import __version__, ffmpeg
from ..direct import IMAGE_SUFFIXES, Review, Shot, full_prompt, load_shots, require_valid, run_check
from ..errors import AltocueError
from ..project import Project
from . import store
from .base import TERMINAL, Cost, CostLine, GenProvider, GenRequest, JobRef, JobStatus, Moderated, SubmitUnknown
from .catalog import Catalog, load_catalog
from .fal import FalProvider
from .higgsfield import HiggsfieldProvider

STALE_DAYS = 30
Out = Callable[[str], None]


def today() -> str:
    return date.today().isoformat()


def get_provider(name: str, catalog: Catalog, *, project: Project | None = None) -> GenProvider:
    if name == "fal":
        return FalProvider(catalog)
    if name == "higgsfield":
        return HiggsfieldProvider(catalog, project=project)
    raise AltocueError(f"Unknown provider {name}.", fix="Use fal or higgsfield.")


def catalog_for(project: Project) -> Catalog:
    chosen = project.setting("generate", "models", "")
    return load_catalog(project.root / chosen if chosen else None)


def open_run(project: Project, shots_file: str = "shots.json", provider: str | None = None) -> tuple[Review, GenProvider, Catalog]:
    review = load_shots(project.root / shots_file, root=project.root)
    name = provider or project.setting("generate", "provider", "")
    if not name:
        fix = 'Set provider to "fal" or "higgsfield" under [generate] in altocue.toml.'
        raise AltocueError("No provider is set, so nothing generates.", fix=fix)
    catalog = catalog_for(project)
    return review, get_provider(name, catalog, project=project), catalog


def shot_request(shot: Shot, style: str = "", root: Path | None = None) -> GenRequest:
    def where(value: str) -> Path:
        return Path(value) if value.startswith("@") or root is None else root / value

    return GenRequest(
        kind=shot.kind,  # type: ignore[arg-type]
        tier=shot.tier,  # type: ignore[arg-type]
        prompt=full_prompt(shot, style),
        aspect=shot.aspect,
        duration_s=math.ceil(shot.duration),
        audio=shot.audio,
        first_frame=where(shot.first_frame) if shot.first_frame else None,
        last_frame=where(shot.last_frame) if shot.last_frame else None,
        refs=[where(value) for value in shot.refs],
        seed=shot.seed,
        extra=shot.extra,
    )


def estimate(shots: Sequence[Shot], provider: str | GenProvider = "fal", *, catalog: Catalog | None = None, style: str = "") -> Cost:
    """What these shots would cost. A shot no route can serve is an error, not a cheaper shot."""
    source = get_provider(provider, catalog or load_catalog()) if isinstance(provider, str) else provider
    total = Cost()
    for shot in shots:
        cost = source.estimate(shot_request(shot, style))
        if cost is None:
            total.unpriced.append(shot.id)
            total.lines.append(CostLine(shot=shot.id, model="", usd=None, note="could not be priced"))
            continue
        total.usd += cost.usd
        total.unpriced += [shot.id for _ in cost.unpriced]
        total.lines += [CostLine(shot.id, line.model, line.usd, line.note, line.as_of) for line in cost.lines]
    return total


@dataclass
class Planned:
    shot: Shot
    req: GenRequest
    hash: str
    status: str
    model: str
    params: dict[str, Any]
    upstream: list[str]
    record: dict[str, Any] | None = None
    cost: Cost = field(default_factory=Cost)
    job: JobRef | None = None
    state: str = ""
    started: float = 0.0
    restore: bool = False


@dataclass
class Outcome:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    moderated: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    running: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.failed or self.moderated or self.blocked or self.rejected or self.running)


def _fingerprint(provider: str, model: str, params: dict[str, Any], req: GenRequest, upstream: dict[str, str]) -> str:
    """Same inputs, same asset. A changed reference file or an upstream still changes the digest."""

    def media(path: Path | None) -> str | None:
        if path is None:
            return None
        text = str(path)
        return upstream[text[1:]] if text.startswith("@") else store.sha256(path)

    material = {
        "provider": provider,
        "model": model,
        "params": params,
        "kind": req.kind,
        "tier": req.tier,
        "prompt": req.prompt,
        "aspect": req.aspect,
        "duration": req.duration_s,
        "audio": req.audio,
        "seed": req.seed,
        "extra": req.extra,
        "first_frame": media(req.first_frame),
        "last_frame": media(req.last_frame),
        "refs": [media(path) for path in req.refs],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _status(project: Project, shot: Shot, digest: str, record: dict[str, Any] | None, resubmit: Iterable[str]) -> str:
    entry = store.read_manifest(project).get(shot.id)
    if entry and (entry["hash"] == digest or entry.get("imported")) and (project.root / entry["asset"]).exists():
        return "done"
    if record is None:
        return "new"
    if record["state"] == "done":
        return "restore" if (project.root / record.get("asset", "\0")).exists() else "new"
    if record["state"] == "moderated":
        return "moderated"
    if record["state"] == "submitting" and not record.get("job"):
        return "new" if shot.id in resubmit else "unknown"
    return "new" if record["state"] in TERMINAL - {"succeeded"} else "resume"


def build_plan(
    project: Project,
    review: Review,
    provider: GenProvider,
    *,
    only: Sequence[str] | None = None,
    resubmit: Sequence[str] = (),
) -> list[Planned]:
    catalog = provider.catalog
    jobs = store.JobStore(project)
    known = {shot.id for shot in review.shots}
    strangers = sorted(set(only or []) - known)
    if strangers:
        raise AltocueError(f"No shot named {', '.join(strangers)}.", fix=f"The shots are {', '.join(sorted(known))}.")
    digests: dict[str, str] = {}
    plan: list[Planned] = []
    for shot in review.shots:
        req = shot_request(shot, review.style, project.root)
        entry = catalog.resolve(provider.id, req)
        digest = _fingerprint(provider.id, entry.model, entry.params, req, digests)
        digests[shot.id] = digest
        if only and shot.id not in only:
            continue
        record = jobs.load(shot.id, digest)
        status = _status(project, shot, digest, record, resubmit)
        item = Planned(shot, req, digest, status, entry.model, entry.params, shot.shot_refs(), record)
        if status == "restore":
            item.status, item.restore = "done", True
        if status == "new":
            item.cost = estimate([shot], provider, style=review.style)
        if status == "resume" and record:
            item.job = JobRef.from_dict(record["job"])
            item.state = record["state"]
        plan.append(item)
    return plan


def _uploads(plan: list[Planned], root: Path) -> list[str]:
    names = []
    for item in plan:
        if item.status != "new":
            continue
        for path in filter(None, [item.req.first_frame, item.req.last_frame, *item.req.refs]):
            text = str(path)
            label = f"the still from {text[1:]}" if text.startswith("@") else os.path.relpath(path, root)
            names.append(f"{label} (for {item.shot.id})")
    return names


def show_plan(plan: list[Planned], provider: GenProvider, root: Path, out: Out) -> Cost:
    total = Cost()
    for item in plan:
        if item.status == "done":
            out(f"  {item.shot.id}: already generated")
        elif item.status == "resume":
            out(f"  {item.shot.id}: resuming job {item.job.job_id if item.job else ''}, already paid for")
        elif item.status == "moderated":
            out(f"  {item.shot.id}: moderated earlier, so it stays stopped. Change the prompt to try again.")
        elif item.status == "new":
            total.usd += item.cost.usd
            total.lines += item.cost.lines
            total.unpriced += item.cost.unpriced
            price = "price unknown" if item.cost.unpriced else f"${item.cost.usd:.2f}"
            length = "" if item.shot.kind == "image" else f", {item.req.duration_s}s"
            out(f"  {item.shot.id}: {price}  {item.model} ({item.shot.tier} {item.shot.kind}{length})")
            for line in item.cost.lines:
                if line.note:
                    out(f"    {line.note}")
            if item.req.seed is not None and not _takes_seed(provider, item):
                out(f"    {item.model} ignores the seed.")
    if total.lines:
        dates = sorted({line.as_of for line in total.lines if line.as_of})
        out(f"Total: ${total.usd:.2f} on {provider.id} (prices as of {dates[0] if dates else 'unknown'})")
        if dates and (date.fromisoformat(today()) - date.fromisoformat(dates[0])).days > STALE_DAYS:
            out("Warning: these prices are older than 30 days. Check models.toml against the provider's pages.")
    names = _uploads(plan, root)
    if names:
        out(f"These files go to {provider.destination}:")
        for name in names:
            out(f"  {name}")
    return total


def spend_gate(project: Project, cost: Cost, provider: GenProvider, *, yes: bool) -> None:
    """Nothing paid happens unless it is priced, under the cap and agreed to."""
    if cost.unpriced:
        fix = "Make the provider quote this shot, then run again."
        raise AltocueError(f"{', '.join(cost.unpriced)} could not be priced, so nothing was spent.", fix=fix)
    cap = float(project.setting("generate", "spend_cap_usd", 0) or 0)
    if cap <= 0 < cost.usd:
        raise AltocueError(
            f"The spend cap is $0, so every paid call is blocked. This run needs ${cost.usd:.2f}.",
            fix=f"Set spend_cap_usd to at least {math.ceil(cost.usd * 100) / 100:.2f} under [generate] in altocue.toml.",
        )
    if cost.usd > cap:
        raise AltocueError(
            f"This run would cost ${cost.usd:.2f} and the cap is ${cap:.2f}.",
            fix="Raise spend_cap_usd under [generate] in altocue.toml, use a cheaper tier, or pick shots with --only.",
        )
    if yes:
        return
    if not sys.stdin.isatty():
        raise AltocueError("Paid generation needs a yes.", fix="Run again with --yes, or run it in a terminal and answer the question.")
    if input(f"Spend up to ${cost.usd:.2f} on {provider.id}? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise AltocueError("Stopped. Nothing was spent.")


def _ready(project: Project, item: Planned) -> bool:
    return all(_still(project, shot) for shot in item.upstream)


def _still(project: Project, shot: str) -> Path | None:
    entry = store.read_manifest(project).get(shot)
    if entry and entry.get("check_passed") is False:
        return None
    return store.generated_asset(project, shot, missing_ok=True)


def _resolved(project: Project, req: GenRequest) -> GenRequest:
    def real(path: Path | None) -> Path | None:
        if path is None or not str(path).startswith("@"):
            return path
        return store.generated_asset(project, str(path)[1:])

    refs = [p for p in (real(path) for path in req.refs) if p]
    return replace(req, first_frame=real(req.first_frame), last_frame=real(req.last_frame), refs=refs)


class Run:
    def __init__(self, project: Project, provider: GenProvider, *, timeout_s: float, out: Out):
        self.project = project
        self.provider = provider
        self.timeout_s = timeout_s
        self.out = out
        self.jobs = store.JobStore(project)
        self.outcome = Outcome()
        self.id = uuid.uuid4().hex[:8]

    def _save(self, item: Planned, state: str, attempt: int | None = None, detail: str = "", asset: str = "") -> None:
        cost = item.record["cost_usd"] if item.record and "cost_usd" in item.record else item.cost.usd
        tried = attempt or (item.record or {}).get("attempt", 1)
        record = {
            "shot": item.shot.id,
            "hash": item.hash,
            "provider": self.provider.id,
            "model": item.model,
            "state": state,
            "attempt": tried,
            "cost_usd": cost,
            "job": item.job.to_dict() if item.job else None,
            "detail": detail,
            "asset": asset,
            "check_passed": (item.record or {}).get("check_passed"),
            "updated": store.now(),
        }
        self.jobs.save(record)
        item.record = record
        item.state = state

    def _log(self, item: Planned, state: str, usd: float) -> None:
        job = item.job
        row = {"run": self.id, "shot": item.shot.id, "provider": self.provider.id, "model": item.model, "usd": usd, "state": state}
        row |= {"basis": self.provider.cost_basis if usd else "no output", "job_id": job.job_id if job else ""}
        if job and job.meta.get("billable_units"):
            row["billable_units"] = job.meta["billable_units"]
        store.log_spend(self.project, row)

    def _stop(self, item: Planned, state: str, detail: str) -> None:
        """A job that ended without an asset. Moderated jobs stay stopped until the prompt changes."""
        self._save(item, state, detail=detail)
        self._log(item, state, 0)
        if state == "moderated":
            self.outcome.moderated.append(item.shot.id)
            self.out(f"{item.shot.id}: moderated by {self.provider.id}. Not retried. Change the prompt to try again. {detail}".rstrip())
        else:
            self.outcome.failed.append(item.shot.id)
            self.out(f"{item.shot.id}: {state}. {detail}".rstrip())

    def submit(self, item: Planned) -> bool:
        attempt = (item.record or {}).get("attempt", 0) + 1
        item.record = None
        self._save(item, "submitting", attempt)
        try:
            item.job = self.provider.submit(_resolved(self.project, item.req), f"{item.hash}-{attempt}")
        except Moderated as error:
            self._stop(item, "moderated", str(error))
            return False
        except SubmitUnknown as error:
            fix = f"Check {self.provider.id} for the job. If it is not there, run `altocue generate --resubmit {item.shot.id}`."
            raise AltocueError(str(error), fix=fix) from error
        except AltocueError as error:
            self._stop(item, "failed", str(error))
            return False
        self._save(item, "queued", attempt)
        item.started = monotonic()
        self.out(f"{item.shot.id}: submitted as {item.job.job_id}")
        return True

    def finish(self, item: Planned) -> None:
        assert item.job
        target = store.generated_dir(self.project) / f"{item.shot.id}-{item.hash}"
        try:
            assets = self.provider.fetch(item.job, target)
        except AltocueError as error:
            self.outcome.failed.append(item.shot.id)
            self.out(f"{item.shot.id}: finished at {self.provider.id} but could not be saved. {error}")
            return
        cost = float((item.record or {}).get("cost_usd", item.cost.usd))
        lead = Path(assets[0].path)
        check = run_check(item.shot.check, lead, store.generated_dir(self.project) / "checks" / item.shot.id)
        for asset in assets:
            store.write_provenance(
                Path(asset.path),
                _provenance(
                    item.shot,
                    prompt=item.req.prompt,
                    provider=self.provider.id,
                    model=item.model,
                    seed=asset.seed if asset.seed is not None else item.req.seed if _takes_seed(self.provider, item) else None,
                    cost=cost,
                    terms=str(self.provider.catalog.provider(self.provider.id)["terms_url"]),
                    route="cli" if self.provider.id == "higgsfield" else "api",
                    extra={
                        "tier": item.shot.tier,
                        "params": item.params,
                        "seed_requested": item.req.seed,
                        "job_id": item.job.job_id,
                        "billable_units": item.job.meta.get("billable_units"),
                        "source_url": asset.url,
                        "first_frame": item.shot.first_frame,
                        "last_frame": item.shot.last_frame,
                        "refs": item.shot.refs,
                        "check": check,
                    },
                ),
            )
        self._log(item, "succeeded", cost)
        store.record_asset(self.project, item.shot.id, asset=lead, digest=item.hash, imported=False, check_passed=check["passed"])
        if item.record is not None:
            item.record["check_passed"] = check["passed"]
        self._save(item, "done", asset=str(lead.relative_to(self.project.root)))
        if check["passed"] is False:
            self.outcome.rejected.append(item.shot.id)
            self.out(f"{item.shot.id}: saved but it failed its {item.shot.check} check, so it will not be placed. {check['detail']}")
            return
        self.outcome.done.append(item.shot.id)
        self.out(f"{item.shot.id}: saved to {lead.relative_to(self.project.root)}")

    def tick(self, item: Planned, active: list[Planned]) -> None:
        assert item.job
        if item.state == "succeeded":
            status = JobStatus("succeeded")
        else:
            try:
                status = self.provider.poll(item.job)
            except AltocueError as error:
                raise AltocueError(
                    f"Lost contact with {self.provider.id}: {error.args[0]}",
                    fix=error.fix or "Your jobs are saved. Re-run `altocue generate` to pick them up without paying again.",
                ) from error
            if status.state != item.state:
                self._save(item, status.state, detail=status.detail)
        if status.state == "succeeded":
            active.remove(item)
            self.finish(item)
        elif status.state in TERMINAL:
            active.remove(item)
            self._stop(item, status.state, status.detail)
        elif monotonic() - item.started > self.timeout_s:
            active.remove(item)
            self.outcome.running.append(item.shot.id)
            self.out(
                f"{item.shot.id}: still running at {self.provider.id}. Run `altocue generate` later to collect it. {status.detail}".rstrip()
            )

    def drive(self, plan: list[Planned]) -> Outcome:
        waiting = [item for item in plan if item.status == "new"]
        active = [item for item in plan if item.status == "resume"]
        for item in active:
            item.started = monotonic()
        while waiting or active:
            lost = {*self.outcome.failed, *self.outcome.moderated, *self.outcome.rejected}
            for item in list(waiting):
                if lost & set(item.upstream):
                    waiting.remove(item)
                    self.outcome.blocked.append(item.shot.id)
                    self.out(f"{item.shot.id}: not submitted, because the still it needs did not come through.")
                elif _ready(self.project, item):
                    waiting.remove(item)
                    if self.submit(item):
                        active.append(item)
            if not active:
                for item in waiting:
                    self.outcome.blocked.append(item.shot.id)
                    self.out(f"{item.shot.id}: not submitted, because its still does not exist. Generate {', '.join(item.upstream)} first.")
                break
            for item in list(active):
                self.tick(item, active)
            if active:
                sleep(self.provider.poll_seconds)
        return self.outcome


def _takes_seed(provider: GenProvider, item: Planned) -> bool:
    return "seed" in provider.catalog.resolve(provider.id, item.req).fields


def _provenance(
    shot: Shot | None,
    *,
    prompt: str,
    provider: str,
    model: str,
    seed: int | None,
    cost: float,
    terms: str,
    route: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "shot": shot.id if shot else "",
        "purpose": shot.purpose if shot else "",
        "kind": shot.kind if shot else "",
        "prompt": prompt,
        "provider": provider,
        "model": model,
        "seed": seed,
        "cost_usd": cost,
        "date": store.now(),
        "terms_url": terms,
        "route": route,
        "altocue_version": __version__,
        **(extra or {}),
    }


def generate_shots(
    project: Project,
    *,
    shots_file: str = "shots.json",
    provider: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
    only: Sequence[str] | None = None,
    resubmit: Sequence[str] = (),
    timeout_s: float = 1800,
    out: Out = print,
) -> Outcome:
    review, chosen, _ = open_run(project, shots_file, provider)
    require_valid(review)
    for finding in review.warnings:
        out(f"warning [{finding.rule}] {finding.shot}: {finding.message}")
    plan = build_plan(project, review, chosen, only=only, resubmit=resubmit)
    unknown = [item.shot.id for item in plan if item.status == "unknown"]
    if unknown:
        names = " ".join(unknown)
        raise AltocueError(
            f"{names} may already be paid for: a submit started and no job id was saved.",
            fix=f"Check {chosen.id} for the job. If it is not there, run `altocue generate --resubmit {names}`.",
        )
    total = show_plan(plan, chosen, project.root, out)
    outcome = Outcome()
    outcome.moderated = [item.shot.id for item in plan if item.status == "moderated"]
    outcome.done = [item.shot.id for item in plan if item.status == "done"]
    if dry_run:
        outcome.moderated = []
        return outcome
    chosen.preflight()
    for item in plan:
        if item.restore and item.record:
            passed = item.record.get("check_passed")
            store.record_asset(
                project, item.shot.id, asset=project.root / item.record["asset"], digest=item.hash, imported=False, check_passed=passed
            )
    if any(item.status == "new" for item in plan):
        spend_gate(project, total, chosen, yes=yes)
    run = Run(project, chosen, timeout_s=timeout_s, out=out)
    run.outcome = outcome
    return run.drive(plan)


def cancel_shot(project: Project, shot: str, *, out: Out = print) -> None:
    open_jobs = [r for r in store.JobStore(project).for_shot(shot) if r.get("job") and r["state"] not in {*TERMINAL, "done"}]
    if not open_jobs:
        raise AltocueError(f"{shot} has no job running.")
    catalog = catalog_for(project)
    for record in open_jobs:
        job = JobRef.from_dict(record["job"])
        get_provider(record["provider"], catalog, project=project).cancel(job)
        store.JobStore(project).save({**record, "state": "canceled", "updated": store.now()})
        out(f"{shot}: asked {record['provider']} to cancel {job.job_id}. A job already running may still finish and be billed.")


def _kind_of(path: Path) -> str:
    return "image" if path.suffix.lower() in IMAGE_SUFFIXES else "video"


def import_asset(
    project: Project,
    *,
    file: str | Path,
    shot: str,
    prompt: str,
    model: str,
    cost: float,
    provider: str = "higgsfield",
    seed: int | None = None,
    job_id: str = "",
    terms_url: str | None = None,
    shots_file: str = "shots.json",
) -> Path:
    """Register a file that was generated elsewhere, such as through a provider's MCP server."""
    source = Path(file).expanduser().resolve()
    if not source.is_file():
        raise AltocueError(f"Not found: {source}")
    if cost < 0:
        raise AltocueError("--cost cannot be negative.")
    ffmpeg.probe_json(source)
    known = None
    if (project.root / shots_file).exists():
        known = next((s for s in load_shots(project.root / shots_file).shots if s.id == shot), None)
    kind = _kind_of(source)
    if known and known.kind != kind:
        fix = "Import the right file, or change the shot's kind."
        raise AltocueError(f"{shot} is a {known.kind} shot and {source.name} is a {kind}.", fix=fix)
    terms = terms_url or str(catalog_for(project).provider(provider)["terms_url"])
    digest = store.sha256(source)[:12]
    asset = store.generated_dir(project) / f"{shot}-{digest}{source.suffix.lower()}"
    asset.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, asset)
    check = run_check(known.check if known else "none", asset, store.generated_dir(project) / "checks" / shot)
    extra = {"job_id": job_id, "imported_from": source.name, "check": check}
    data = _provenance(known, prompt=prompt, provider=provider, model=model, seed=seed, cost=cost, terms=terms, route="import", extra=extra)
    store.write_provenance(asset, {**data, "shot": shot, "kind": kind})
    store.record_asset(project, shot, asset=asset, digest=digest, imported=True, check_passed=check["passed"])
    row = {"run": "import", "shot": shot, "provider": provider, "model": model, "usd": cost}
    store.log_spend(project, {**row, "state": "succeeded", "basis": "import", "job_id": job_id})
    return asset
