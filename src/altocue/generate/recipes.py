"""Recipes: what a hosted command makes, what it costs and what leaves the machine, before anything runs."""

from __future__ import annotations

import argparse
import json
import math
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import CONFIG, Project
from . import uploads
from .catalog import Catalog, catalog_for, credit_rate, load_catalog

PACKAGED = Path(__file__).parents[1] / "data" / "recipes.toml"
REQUIRED = ("id", "title", "group", "command", "makes", "when", "inputs", "enters_at", "tool", "uploads_footage", "sends", "basis", "as_of", "fallback")
BASES = ("quote", "stated", "observed", "estimate", "unknown")
GROUPS = ("reel", "workflow", "gallery", "tool")
ENTRIES = ("source", "insert", "finish", "still", "audio")
LOADERS = ("get_preset_instructions", "get_workflow_instructions", "get_presets", "tool")
FALLBACK_KINDS = ("fal", "format", "native")
HOST = "higgsfield"
MCP_WARNING = "Nothing runs from here. An agent spends through the MCP server, after a reservation and your yes."


@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    group: str
    command: str
    makes: str
    when: str
    inputs: tuple[str, ...]
    enters_at: str
    tool: str
    uploads_footage: bool
    sends: str
    basis: str
    as_of: str
    fallback: str
    loader: str = "tool"
    load: str = ""
    limits: str = ""
    variants: tuple[str, ...] = ()
    synthetic: tuple[str, ...] = ()
    truth_gate: bool = False
    slash_trap: bool = False
    fixed_credits: float | None = None
    per_second: dict[str, float] = field(default_factory=dict)
    each_credits: float | None = None
    each_unit: str = ""
    cost_note: str = ""
    fallback_note: str = ""
    fallback_checked: str = ""
    excluded: str = ""

    @property
    def priced(self) -> bool:
        return self.fixed_credits is not None or bool(self.per_second) or self.each_credits is not None

    @property
    def firm(self) -> bool:
        return self.basis == "quote"


@dataclass
class Quote:
    """A cost, or the plain statement that nobody knows it. `credits` is None when a total cannot be given."""

    recipe: str
    credits: float | None
    usd: float | None
    rate: float | None
    basis: str
    as_of: str
    firm: bool
    needs: str = ""


def _problem(row: dict[str, Any]) -> str | None:
    missing = [key for key in REQUIRED if key not in row]
    if missing:
        return f"is missing {', '.join(missing)}"
    if row["basis"] not in BASES:
        return f"has basis {row['basis']}, and a price needs a basis of {', '.join(BASES)}"
    priced = any(key in row for key in ("fixed_credits", "per_second", "each_credits"))
    if row["basis"] == "unknown" and priced:
        return "says its price is unknown and gives a number"
    if row["basis"] != "unknown" and not priced:
        return f"gives a {row['basis']} basis and no number"
    if row["group"] not in GROUPS or row["enters_at"] not in ENTRIES or row.get("loader", "tool") not in LOADERS:
        return "has an unknown group, enters_at or loader"
    if row.get("loader") == "get_workflow_instructions" and str(row.get("load", "")).startswith("/"):
        return "loads a workflow by slash. A workflow loads by name, because a slash can fall through to ordinary generation"
    kind, _, target = str(row["fallback"]).partition(":")
    if row["fallback"] != "none" and (kind not in FALLBACK_KINDS or not target):
        return f"has fallback {row['fallback']}, and a fallback is none, fal:<model>, format:<id> or native:<command>"
    try:
        date.fromisoformat(row["as_of"])
    except ValueError:
        return "has a bad as_of date"
    return None


def load_recipes(path: str | Path | None = None) -> dict[str, Recipe]:
    source = Path(path) if path else PACKAGED
    try:
        rows = tomllib.loads(source.read_text()).get("recipe", [])
    except tomllib.TOMLDecodeError as error:
        raise AltocueError(f"Cannot read {source}: {error}", fix="Fix the TOML syntax.") from error
    found: dict[str, Recipe] = {}
    for row in rows:
        problem = _problem(row)
        if problem:
            raise AltocueError(f"Recipe {row.get('id', row)} {problem}.", fix=f"Fix it in {source}.")
        if row["id"] in found:
            raise AltocueError(f"Recipe {row['id']} is listed twice.", fix=f"Keep one entry in {source}.")
        known = {key: value for key, value in row.items() if key in Recipe.__dataclass_fields__}
        tuples = {key: tuple(row.get(key, ())) for key in ("inputs", "variants", "synthetic")}
        found[row["id"]] = Recipe(**{**known, **tuples})
    return found


def get(name: str) -> Recipe:
    found = load_recipes().get(name)
    if found is None:
        raise AltocueError(f"No recipe called {name}.", fix="See them with: altocue recipes list")
    return found


def require_usable(project: Project, name: str, catalog: Catalog) -> Recipe:
    """A recipe that is left out stays out, and one that sends project files waits for the project's yes.

    The yes is checked against the recipe's own host, Higgsfield, whatever provider the caller names.
    """
    recipe = get(name)
    if recipe.excluded:
        raise AltocueError(f"{recipe.id} is excluded by default. {recipe.excluded}", fix="Pick another recipe: altocue recipes list")
    if recipe.uploads_footage:
        uploads.require(project, catalog, HOST, f"The {recipe.id} recipe")
    return recipe


def quote(recipe: Recipe, *, seconds: float | None = None, resolution: str | None = None, count: int = 1, rate: float | None = None) -> Quote:
    """What a job would cost, and how far to trust the figure. An unknown price stays unknown."""
    base = {"recipe": recipe.id, "rate": rate, "basis": recipe.basis, "as_of": recipe.as_of, "firm": recipe.firm}
    if not recipe.priced:
        return Quote(credits=None, usd=None, **base)
    if recipe.per_second and (seconds is None or resolution is None):
        return Quote(credits=None, usd=None, needs="Pass --seconds and --resolution for a total.", **base)
    credits = recipe.fixed_credits or 0.0
    if recipe.per_second:
        if resolution not in recipe.per_second:
            raise AltocueError(f"{recipe.id} has no rate at {resolution}.", fix=f"Use one of {', '.join(recipe.per_second)}.")
        credits += recipe.per_second[resolution] * (seconds or 0)
    if recipe.each_credits is not None:
        credits += recipe.each_credits * count
    return Quote(credits=round(credits, 4), usd=None if rate is None else round(credits * rate, 2), **base)


def cost_summary(recipe: Recipe) -> str:
    if not recipe.priced:
        return "unknown"
    parts = []
    if recipe.fixed_credits is not None:
        parts.append(_credits(recipe.fixed_credits))
    if recipe.per_second:
        rates = list(recipe.per_second.values())
        parts.append(f"{min(rates):g} to {max(rates):g} credits a second")
    if recipe.each_credits is not None:
        parts.append(f"{recipe.each_credits:g} credits per {recipe.each_unit}")
    return f"{' plus '.join(parts)} ({recipe.basis})"


def _credits(value: float) -> str:
    return f"{value:g} credit" if value == 1 else f"{value:g} credits"


def _open(path: str) -> tuple[Project | None, Catalog]:
    """A quote works outside a project, at the catalog's cautious rate."""
    if (Path(path) / CONFIG).exists():
        project = Project.open(path)
        return project, catalog_for(project)
    return None, load_catalog()


def _basis_line(recipe: Recipe) -> str:
    said = {
        "quote": f"Live quote, dated {recipe.as_of}.",
        "stated": f"Stated in the provider's own text, read {recipe.as_of}. Treat it as an estimate, not a live quote.",
        "observed": f"Observed on a real run, dated {recipe.as_of}. Treat it as an estimate, not a live quote.",
        "estimate": f"An estimate from a price file dated {recipe.as_of}, and not a live quote.",
        "unknown": f"Unknown. Checked {recipe.as_of} and no figure was found.",
    }[recipe.basis]
    return f"{said} {recipe.cost_note}".rstrip()


def _print_quote(recipe: Recipe, result: Quote, *, seconds: float | None, resolution: str | None, blocked: bool) -> None:
    length = f"{seconds:g} s at {resolution}" if result.credits is not None and recipe.per_second else ""
    print(f"{recipe.id}: {recipe.title}{f', {length}' if length else ''}")
    if result.credits is not None:
        dollars = "" if result.usd is None else f", ${result.usd:.2f} at ${result.rate:g} a credit"
        print(f"  Cost: {_credits(result.credits)}{dollars}")
    elif recipe.per_second:
        print("  Cost, credits a second:")
        for name, value in recipe.per_second.items():
            print(f"    {name}: {value:g}")
        print(f"  {result.needs}")
    else:
        print("  Cost: unknown")
    print(f"  Basis: {_basis_line(recipe)}")
    print(f"  Footage: {_where(recipe)}")
    if blocked:
        print(f"  Blocked until the project agrees to uploads, after reading the warning: altocue generate --allow-upload {HOST}")
    print(f"  {MCP_WARNING}")
    if result.credits:
        print(f"  Reserve first: altocue generate --reserve <shot> --credits {math.ceil(result.credits)} --recipe {recipe.id}")
    elif result.credits is None and not recipe.per_second:
        print(f"  Agree a ceiling with the user, then reserve it: altocue generate --reserve <shot> --credits <ceiling> --recipe {recipe.id}")


def _where(recipe: Recipe) -> str:
    return f"uploads to Higgsfield: {recipe.sends}." if recipe.uploads_footage else f"stays on this machine. Sent: {recipe.sends}."


def _show(recipe: Recipe, rate: float | None) -> None:
    print(f"{recipe.id}: {recipe.title}")
    if recipe.excluded:
        print(f"Excluded by default. {recipe.excluded}")
    rows = [("Command", recipe.command), ("Makes", recipe.makes), ("When", recipe.when), ("Inputs", "; ".join(recipe.inputs))]
    if recipe.limits:
        rows.append(("Limits", recipe.limits))
    if recipe.variants:
        rows.append(("Variants", ", ".join(recipe.variants)))
    rows.append(("Enters the edit at", recipe.enters_at))
    rows.append(("Tool", recipe.tool))
    rows.append(("Load", _load_line(recipe)))
    rows.append(("Cost", f"{cost_summary(recipe)}{f' at ${rate:g} a credit' if rate and recipe.priced else ''}"))
    rows.append(("Basis", _basis_line(recipe)))
    rows.append(("Footage", _where(recipe)))
    if recipe.synthetic:
        rows.append(("Synthetic", f"{', '.join(recipe.synthetic)}. Importing with --recipe sets these flags for the honesty gates."))
    if recipe.truth_gate:
        rows.append(("Truth gate", "Needs an approved claims list, or a script of only what can be seen. A generated person never claims experience."))
    rows.append(("Without Higgsfield", f"{recipe.fallback}{f'. {recipe.fallback_note}' if recipe.fallback_note else ''}"))
    for label, value in rows:
        print(f"{label}: {value}")


def _load_line(recipe: Recipe) -> str:
    if recipe.loader == "tool":
        return f"call {recipe.load or recipe.tool} directly"
    line = f"{recipe.loader} with {recipe.load}"
    if recipe.slash_trap:
        line += f". Never type /{recipe.load}: it does not resolve, it falls through to ordinary generation without a word, and the truth gate is lost"
    return line


def _rows(found: list[Recipe]) -> list[str]:
    width = max((len(r.id) for r in found), default=4)
    rows = []
    for recipe in found:
        where = "uploads" if recipe.uploads_footage else "local"
        flag = "  EXCLUDED" if recipe.excluded else ""
        rows.append(f"{recipe.id:<{width}}  {recipe.group:<8}  {where:<7}  {cost_summary(recipe)}{flag}")
    return rows


def _search(words: list[str], recipes: list[Recipe]) -> list[Recipe]:
    wanted = [word.lower() for word in words]

    def text(recipe: Recipe) -> str:
        fields = [recipe.id, recipe.title, recipe.command, recipe.makes, recipe.when, recipe.tool, *recipe.variants]
        return " ".join(fields).lower()

    return [recipe for recipe in recipes if all(word in text(recipe) for word in wanted)]


def _one(args: argparse.Namespace) -> Recipe:
    if not args.terms:
        raise AltocueError(f"`altocue recipes {args.action}` needs a recipe id.", fix="See them with: altocue recipes list")
    return get(args.terms[0])


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["list", "search", "show", "quote"], help="What to do")
    parser.add_argument("terms", nargs="*", help="Search words, or a recipe id")
    parser.add_argument("--group", choices=GROUPS, help="With list: only this group")
    parser.add_argument("--all", action="store_true", help="With list: include what is excluded by default")
    parser.add_argument("--seconds", type=float, help="With quote: the length of the job")
    parser.add_argument("--resolution", help="With quote: 480p, 720p, 1080p and so on")
    parser.add_argument("--count", type=int, default=1, help="With quote: how many images or takes")
    parser.add_argument("--project", "-p", default=".", help="Project folder, for its credit rate")
    parser.add_argument("--json", action="store_true", help="Print as JSON")


def run(args: argparse.Namespace) -> int:
    project, catalog = _open(args.project)
    rate = credit_rate(catalog, HOST, project)
    everything = list(load_recipes().values())
    if args.action in {"list", "search"}:
        found = [r for r in everything if (args.all or not r.excluded) and (not args.group or r.group == args.group)]
        found = _search(args.terms, found) if args.action == "search" else found
        print(json.dumps([asdict(r) for r in found], indent=2) if args.json else "\n".join(_rows(found)) or "Nothing matches that.")
        return 0 if found else 1
    recipe = _one(args)
    if args.action == "show":
        if args.json:
            print(json.dumps(asdict(recipe), indent=2))
        else:
            _show(recipe, rate)
        return 0
    if recipe.excluded:
        raise AltocueError(f"{recipe.id} is excluded by default. {recipe.excluded}", fix="Pick another recipe: altocue recipes list")
    result = quote(recipe, seconds=args.seconds, resolution=args.resolution, count=args.count, rate=rate)
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        blocked = recipe.uploads_footage and not (project and uploads.allowed(project, catalog, HOST))
        _print_quote(recipe, result, seconds=args.seconds, resolution=args.resolution, blocked=blocked)
    return 0
