"""The `generate` stage: images and video from model providers, behind a spend gate."""

from __future__ import annotations

import argparse

from ..direct import TIERS
from ..errors import AltocueError
from ..project import Project
from . import uploads
from .base import CAPABILITIES, TERMINAL, Asset, Cost, CostLine, GenProvider, GenRequest, JobRef, JobStatus, Moderated, SubmitUnknown
from .catalog import Catalog, ModelEntry, capability_of, catalog_for, load_catalog
from .runner import cancel_shot, estimate, generate_shots, get_provider, import_asset, release, reserve, spend_report
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
    "release",
    "reserve",
    "run",
    "spend_report",
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
    parser.add_argument("--reserve", metavar="SHOT", help="Book money against the cap before spending through a provider's MCP server")
    parser.add_argument("--release", metavar="SHOT", help="Give back a reservation that will not be spent")
    parser.add_argument("--report", action="store_true", help="Show the cap, the spend, open reservations and unreserved imports")
    parser.add_argument("--allow-upload", metavar="PROVIDER", help="Let this project send its own files to a provider, after its warning")
    parser.add_argument("--credits", type=float, help="With --reserve or --import: the credits. Dollars follow from usd_per_credit")
    parser.add_argument("--cost", type=float, help="With --reserve or --import: the price in USD")
    parser.add_argument("--seed", type=int, help="With --import: the seed, if known")
    parser.add_argument("--job-id", default="", help="With --import: the provider's job id")
    parser.add_argument("--from", dest="source", default="higgsfield", help="With --import: the provider. Default higgsfield")
    parser.add_argument("--terms-url", help="With --import: terms URL, if the provider is not in models.toml")
    parser.add_argument("--surface", default="", choices=["", "mcp", "cli", "api", "web"], help="With --import: how it was made")
    parser.add_argument("--recipe", default="", help="With --import: the recipe that made it, from `altocue recipes`")
    parser.add_argument("--upstream", default="", help="With --import: job ids this was built from, comma separated")
    parser.add_argument("--synthetic", default="", help="With --import: what is generated: person, voice, scene, product, comma separated")
    parser.add_argument("--claims", default="", help="With --import: a file of approved claims, or a statement such as observable-only")
    parser.add_argument("--consent", default="", help="With --import: whose likeness or voice this is, such as none-generated-adult")


def _allow(project: Project, provider: str) -> int:
    catalog = catalog_for(project)
    warning = uploads.warning(catalog, provider)
    if not warning:
        print(f"{provider} needs no opt-in. Its uploads are named before they happen.")
        return 0
    entry = uploads.allow(project, catalog, provider)
    print(f"Warning: {warning}")
    print(f"This project now allows uploads to {provider}, under terms dated {entry['terms_as_of']}. New terms will ask again.")
    print(f"Recorded in {uploads.CONSENT}. Delete that entry to take it back.")
    return 0


def _listed(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _import(args: argparse.Namespace, project: Project) -> int:
    given = {"--shot": args.shot, "--prompt": args.prompt, "--model": args.model}
    missing = [flag for flag, value in given.items() if value is None]
    if args.cost is None and args.credits is None:
        missing.append("--cost or --credits")
    if missing:
        raise AltocueError(f"--import needs {', '.join(missing)}.", fix="Provenance is the point of an import, so every field is required.")
    asset = import_asset(
        project,
        file=args.import_file,
        shot=args.shot,
        prompt=args.prompt,
        model=args.model,
        cost=args.cost,
        credits=args.credits,
        provider=args.source,
        seed=args.seed,
        job_id=args.job_id,
        terms_url=args.terms_url,
        shots_file=args.shots,
        surface=args.surface,
        recipe=args.recipe,
        upstream=_listed(args.upstream),
        synthetic=_listed(args.synthetic),
        claims=args.claims,
        consent=args.consent,
    )
    print(f"{args.shot}: registered {asset.relative_to(project.root)}")
    return 0


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    if args.report:
        print("\n".join(spend_report(project)))
        return 0
    if args.allow_upload:
        return _allow(project, args.allow_upload)
    if args.reserve:
        reserve(project, args.reserve, credits=args.credits, cost=args.cost, provider=args.source, recipe=args.recipe, shots_file=args.shots, yes=args.yes)
        return 0
    if args.release:
        release(project, args.release)
        return 0
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
