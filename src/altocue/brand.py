"""A brand is one JSON file. Swap it and the same edit wears a different look."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import AltocueError
from .project import Project

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
    chosen = str(project.setting("brand", "name", "default"))
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
