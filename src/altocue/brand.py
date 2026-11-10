"""A brand is one JSON file. Swap it and the same edit wears a different look.

Every project starts by reading the owner's brand kit: logo, faces and colours. Nothing that makes an asset runs until it has.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import AltocueError
from .project import CONFIG, Project, write_json

CAPTION_STYLES = ("clean", "bold", "boxed", "rail")
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
SAFE_NAME = re.compile(r"[\w .-]+")
FONT_TYPES = {".ttf", ".otf", ".woff", ".woff2"}
LOGO_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}


def data_path(*parts: str) -> Path:
    return Path(str(resources.files("altocue").joinpath("data", *parts)))


def asset(root: Path, value: str, kinds: set[str], what: str) -> Path:
    """A brand file can come from anyone, so what it names must sit beside it and be the right kind of file."""
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() not in kinds or not SAFE_NAME.fullmatch(path.name):
        raise AltocueError(f"The brand's {what} {value} cannot be used.", fix=f"Put a {', '.join(sorted(kinds))} file next to the brand file.")
    return path


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in over.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


@dataclass
class Brand:
    data: dict[str, Any]
    root: Path

    @property
    def colors(self) -> dict[str, str]:
        return self.data["colors"]

    @property
    def caption(self) -> dict[str, Any]:
        return self.data["caption"]

    @property
    def font_file(self) -> Path:
        value = self.data["font"]["file"]
        local = asset(self.root, value, FONT_TYPES, "font")
        return local if local.exists() else data_path("fonts", local.name)

    @property
    def logo(self) -> Path | None:
        value = self.data.get("logo")
        return asset(self.root, value, LOGO_TYPES, "logo") if value else None


def load(project: Project) -> Brand:
    """Read the project's brand. A partial file only overrides what it names."""
    default = json.loads(data_path("brands", "default.json").read_text())
    chosen = str(project.setting("brand", "name", "default")).strip()
    if not chosen:
        raise AltocueError(
            "This project has no brand kit yet, and Altocue makes no asset before it reads one.",
            fix="Run `altocue brand ingest <kit folder>` with the logo, fonts and colours. With no brand of your own, `altocue brand ingest --house`.",
        )
    if chosen == "default":
        return Brand(default, data_path("brands"))
    path = (project.root / chosen).resolve()
    if not path.exists():
        raise AltocueError(f"Brand file {chosen} was not found.", fix="Set [brand] name in altocue.toml to `default` or a path to a brand JSON file.")
    brand = Brand(_merge(default, json.loads(path.read_text())), path.parent)
    for name, value in brand.colors.items():
        if not COLOUR.fullmatch(str(value)):
            raise AltocueError(f"The brand colour {name} is {value!r}.", fix="Use six-digit hex, such as #b5f4d9.")
    if brand.caption["style"] not in CAPTION_STYLES:
        raise AltocueError(f"Unknown caption style {brand.caption['style']}.", fix=f"Use one of {', '.join(CAPTION_STYLES)}.")
    return brand


# ---------------------------------------------------------------- ingest

KIT_TEXT = {".json", ".txt", ".md", ".css", ".scss", ".svg", ".html", ".ase"}
HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
BRAND_DIR = "brand"


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    ingest = sub.add_parser("ingest", help="Read a brand kit into the project: logo, faces and colours. The first step, before any asset")
    ingest.add_argument("kit", nargs="?", help="A brand kit folder, or a brand.json")
    ingest.add_argument("--house", action="store_true", help="No brand of your own: use Altocue's house look, on purpose")
    ingest.add_argument("--project", "-p", default=".", help="Project folder")
    show = sub.add_parser("show", help="Show the brand this project uses")
    show.add_argument("--project", "-p", default=".", help="Project folder")


def hex6(value: str) -> str:
    value = value.lstrip("#").lower()
    return "#" + (value if len(value) == 6 else "".join(c * 2 for c in value))


def kit_colours(folder: Path) -> list[str]:
    """Every colour the kit writes down, most used first."""
    seen: Counter[str] = Counter()
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in KIT_TEXT and path.stat().st_size < 2_000_000:
            seen.update(hex6(found) for found in HEX.findall(path.read_text(errors="ignore")))
    return [colour for colour, _ in seen.most_common()]


def _light(colour: str) -> float:
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _vivid(colour: str) -> float:
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    _, light, sat = colorsys.rgb_to_hls(r, g, b)
    return sat * (1 - abs(2 * light - 1))


def assign_colours(found: list[str], fallback: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The kit's colours, given the jobs a brand needs. Whatever the kit leaves open keeps the house colour, and is named."""
    colours, kept = dict(fallback), []
    pool = list(dict.fromkeys(found))
    if not pool:
        return colours, sorted(fallback)
    darkest = sorted(pool, key=_light)
    colours["panel"] = darkest[0]
    colours["ink"] = darkest[-1] if _light(darkest[-1]) - _light(darkest[0]) > 0.4 else ("#ffffff" if _light(darkest[0]) < 0.5 else "#0a0a0a")
    rest = [c for c in pool if c not in (colours["panel"], colours["ink"])]
    vivid = sorted(rest, key=_vivid, reverse=True)
    if vivid and _vivid(vivid[0]) > 0.15:
        colours["accent"] = vivid[0]
        rest.remove(vivid[0])
    else:
        kept.append("accent")
    near = [c for c in rest if abs(_light(c) - _light(colours["panel"])) < 0.15]
    if near:
        colours["surface"] = near[0]
    else:
        kept.append("surface")
    soft = [c for c in rest if c != colours.get("surface") and abs(_light(c) - _light(colours["ink"])) < 0.45]
    if soft:
        colours["soft"] = soft[0]
    else:
        kept.append("soft")
    return colours, kept


def _pick(folder: Path, kinds: set[str], prefer: str) -> Path | None:
    files = [p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() in kinds and not p.name.startswith(".")]
    named = [p for p in files if prefer in p.name.lower()]
    return (named or files or [None])[0]


def _copy(source: Path, target_dir: Path) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.-]+", "-", source.name)
    shutil.copy2(source, target_dir / name)
    return name


def ingest(project: Project, kit: Path | None, *, house: bool = False) -> dict[str, Any]:
    """Read a kit into `brand/` in the project and point [brand] at it. Returns what was read and what fell back."""
    default = json.loads(data_path("brands", "default.json").read_text())
    if house:
        set_brand(project, "default")
        return {"brand": "default", "read": [], "kept": ["everything: the house look, chosen on purpose"]}
    if kit is None:
        raise AltocueError("Name the brand kit.", fix="Pass a kit folder or a brand.json, or --house to use Altocue's house look on purpose.")
    kit = kit.expanduser().resolve()
    if not kit.exists():
        raise AltocueError(f"No brand kit at {kit}.")
    target = project.root / BRAND_DIR
    if kit.is_file() and kit.suffix.lower() == ".json":
        data = json.loads(kit.read_text())
        folder = kit.parent
    elif (kit / "brand.json").is_file():
        data, folder = json.loads((kit / "brand.json").read_text()), kit
    else:
        data, folder = {}, kit
    if not folder.is_dir():
        raise AltocueError(f"{kit} is neither a kit folder nor a brand.json.")
    read, kept = [], []
    made: dict[str, Any] = {"name": data.get("name") or (kit.stem if kit.is_file() else kit.name)}
    if data.get("colors"):
        made["colors"] = {**default["colors"], **data["colors"]}
        read.append("colours from brand.json")
    else:
        found = kit_colours(folder)
        made["colors"], kept_colours = assign_colours(found, default["colors"])
        if found:
            read.append(f"{len(found)} colours from the kit")
        kept += [f"{name} colour" for name in kept_colours]
    font_value = (data.get("font") or {}).get("file")
    font = (folder / font_value) if font_value else _pick(folder, FONT_TYPES, "bold")
    if font and font.is_file():
        name = _copy(font, target)
        family = (data.get("font") or {}).get("family") or re.split(r"[-_ ]", font.stem)[0]
        made["font"] = {"family": family, "file": name}
        read.append(f"face {family} ({font.name})")
    else:
        kept.append("face")
    logo_value = data.get("logo")
    logo = (folder / logo_value) if logo_value else _pick(folder, LOGO_TYPES, "logo")
    if logo and logo.is_file():
        made["logo"] = _copy(logo, target)
        read.append(f"logo {logo.name}")
    else:
        kept.append("logo")
    if data.get("caption"):
        made["caption"] = data["caption"]
        read.append("caption style")
    write_json(target / "brand.json", made)
    set_brand(project, f"{BRAND_DIR}/brand.json")
    load(Project.open(project.root))  # it must load cleanly, or nothing downstream will
    return {"brand": f"{BRAND_DIR}/brand.json", "read": read, "kept": kept}


def set_brand(project: Project, value: str) -> None:
    path = project.root / CONFIG
    text = path.read_text()
    line = f'name = "{value}"'
    section = re.search(r"(?ms)^\[brand\][^\n]*\n(.*?)(?=^\[|\Z)", text)
    if section and re.search(r"(?m)^name\s*=.*$", section.group(1)):
        body = re.sub(r'(?m)^name\s*=\s*"[^"]*"', line, section.group(1), count=1)
        text = text[: section.start(1)] + body + text[section.end(1) :]
    elif section:
        text = text[: section.start(1)] + line + "\n" + text[section.start(1) :]
    else:
        text = text.rstrip("\n") + f"\n\n[brand]\n{line}\n"
    path.write_text(text)
    project.config.setdefault("brand", {})["name"] = value


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    if args.action == "show":
        brand = load(project)
        print(json.dumps({"brand": project.setting("brand", "name"), **brand.data}, indent=2))
        return 0
    done = ingest(project, Path(args.kit) if args.kit else None, house=args.house)
    print(f"Brand: {done['brand']}")
    if done["read"]:
        print("Read: " + "; ".join(done["read"]))
    if done["kept"]:
        print("Kept from the house look, since the kit does not say: " + ", ".join(done["kept"]) + ". Edit brand/brand.json to set them.")
    return 0
