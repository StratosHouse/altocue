"""The `generate` stage: images and video from model providers, behind a spend gate."""

from __future__ import annotations

import argparse

from ..direct import TIERS
from ..errors import AltocueError
from ..project import Project
from .base import CAPABILITIES, TERMINAL, Asset, Cost, CostLine, GenProvider, GenRequest, JobRef, JobStatus, Moderated, SubmitUnknown
from .catalog import Catalog, ModelEntry, capability_of, load_catalog
from .runner import cancel_shot, estimate, generate_shots, get_provider, import_asset
from .store import generated_asset

__all__ = [
    "CAPABILITIES",
    "TERMINAL",
    "TIERS",
    "Asset",
    "Catalog",
    "Cost",
    "CostLine",
    "GenProvider",
    "GenRequest",
    "JobRef",
    "JobStatus",
    "ModelEntry",
    "Moderated",
    "SubmitUnknown",
    "add_arguments",
    "cancel_shot",
    "capability_of",
    "estimate",
    "generate_shots",
    "generated_asset",
    "get_provider",
    "import_asset",
    "load_catalog",
    "run",
]


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--shots", default="shots.json", help="Shot list, inside the project")
    parser.add_argument("--provider", choices=["fal", "higgsfield"], help="Overrides provider under [generate]")
    parser.add_argument("--yes", action="store_true", help="Agree to the shown cost without being asked")
    parser.add_argument("--dry-run", action="store_true", help="Show the cost and stop")
    parser.add_argument("--only", nargs="+", metavar="SHOT", help="Only these shots")
    parser.add_argument("--resubmit", nargs="+", default=[], metavar="SHOT", help="Submit again after checking the job does not exist")
    parser.add_argument("--timeout", type=float, default=30, help="Minutes to wait for jobs. They keep running and can be collected later")
    parser.add_argument("--cancel", metavar="SHOT", help="Cancel a running job")
    parser.add_argument("--import", dest="import_file", metavar="FILE", help="Register a file made elsewhere, with its provenance")
    parser.add_argument("--shot", help="With --import: the shot this file is for")
    parser.add_argument("--prompt", help="With --import: the prompt that made it")
    parser.add_argument("--model", help="With --import: the model that made it")
    parser.add_argument("--cost", type=float, help="With --import: what it cost in USD")
    parser.add_argument("--seed", type=int, help="With --import: the seed, if known")
    parser.add_argument("--job-id", default="", help="With --import: the provider's job id")
    parser.add_argument("--from", dest="source", default="higgsfield", help="With --import: the provider. Default higgsfield")
    parser.add_argument("--terms-url", help="With --import: terms URL, if the provider is not in models.toml")


def _import(args: argparse.Namespace, project: Project) -> int:
    given = {"--shot": args.shot, "--prompt": args.prompt, "--model": args.model, "--cost": args.cost}
    missing = [flag for flag, value in given.items() if value is None]
    if missing:
        raise AltocueError(f"--import needs {', '.join(missing)}.", fix="Provenance is the point of an import, so every field is required.")
    asset = import_asset(
        project,
        file=args.import_file,
        shot=args.shot,
        prompt=args.prompt,
        model=args.model,
        cost=args.cost,
        provider=args.source,
        seed=args.seed,
        job_id=args.job_id,
        terms_url=args.terms_url,
        shots_file=args.shots,
    )
    print(f"{args.shot}: registered {asset.relative_to(project.root)} at ${args.cost:.2f}")
    return 0


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    if args.import_file:
        return _import(args, project)
    if args.cancel:
        cancel_shot(project, args.cancel)
        return 0
    outcome = generate_shots(
        project,
        shots_file=args.shots,
        provider=args.provider,
        yes=args.yes,
        dry_run=args.dry_run,
        only=args.only,
        resubmit=args.resubmit,
        timeout_s=args.timeout * 60,
    )
    return 0 if outcome.ok else 1
