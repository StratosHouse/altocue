"""`models.toml` as code can use it: routes from tier and capability to a model."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from ..direct import TIERS
from ..errors import AltocueError
from .base import CAPABILITIES, Cost, CostLine, GenRequest

PACKAGED = Path(__file__).with_name("models.toml")
REQUIRED = ("provider", "tier", "capability", "kind", "model", "aspects", "as_of", "source_url")


def capability_of(req: GenRequest) -> str:
    if req.kind == "image":
        return "reference_to_image" if req.refs else "text_to_image"
    if req.first_frame and req.refs:
        return "frame_and_refs_to_video"
    if req.first_frame:
        return "image_to_video"
    return "reference_to_video" if req.refs else "text_to_video"


PRICED_FIELDS = {"duration", "seconds", "num_frames", "num_images", "num_outputs", "n", "resolution", "quality"}


@dataclass(frozen=True)
class ModelEntry:
    provider: str
    tier: str
    capability: str
    kind: str
    model: str
    aspects: tuple[str, ...]
    as_of: str
    source_url: str
    params: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    duration_format: str = ""
    min_duration: int = 0
    max_duration: int = 0
    max_images: int = 0
    usd_per_second: float | None = None
    usd_per_second_audio: float | None = None
    usd_per_image: float | None = None
    price: str = ""
    note: str = ""

    def without_field(self, name: str) -> ModelEntry:
        return replace(self, fields={k: v for k, v in self.fields.items() if k != name})

    def with_price(self, **prices: float) -> ModelEntry:
        return replace(self, **prices)

    def cost(self, req: GenRequest) -> float | None:
        if self.kind == "image":
            return self.usd_per_image
        rate = self.usd_per_second_audio if req.audio and self.usd_per_second_audio else self.usd_per_second
        return None if rate is None else rate * req.duration_s

    def payload(self, req: GenRequest, media: Callable[[Path], str]) -> dict[str, Any]:
        """The request as this model's own field names. `media` turns a path into what it accepts."""
        body: dict[str, Any] = dict(self.params)
        names = self.fields
        body[names["aspect"]] = req.aspect
        if self.kind == "video":
            body[names["duration"]] = self.duration_format.format(req.duration_s) if self.duration_format else req.duration_s
        if "audio" in names:
            body[names["audio"]] = req.audio
        if req.first_frame:
            body[names["first_frame"]] = media(req.first_frame)
        if req.last_frame:
            body[names["last_frame"]] = media(req.last_frame)
        if req.refs:
            body[names["refs"]] = [media(path) for path in req.refs]
        if req.seed is not None and "seed" in names:
            body[names["seed"]] = req.seed
        return {**body, **req.extra}

    def limits(self, req: GenRequest) -> list[str]:
        """What this request asks for that this model cannot do."""
        problems = []
        if req.aspect not in self.aspects:
            problems.append(f"aspect {req.aspect} (it takes {', '.join(self.aspects)})")
        if self.kind == "video" and not self.min_duration <= req.duration_s <= self.max_duration:
            problems.append(f"duration {req.duration_s}s (it takes {self.min_duration} to {self.max_duration}s)")
        images = len(req.refs) + bool(req.first_frame) + bool(req.last_frame)
        if images > self.max_images:
            problems.append(f"{images} reference images (it takes {self.max_images})")
        if req.last_frame and "last_frame" not in self.fields:
            problems.append("a last frame")
        if req.audio and "audio" not in self.fields:
            problems.append("generated audio")
        fixed = (set(self.fields.values()) | set(self.params) | PRICED_FIELDS) & set(req.extra)
        if fixed:
            problems.append(f"extra fields that would change the request or its price: {', '.join(sorted(fixed))}")
        return problems


@dataclass
class Catalog:
    entries: list[ModelEntry]
    providers: dict[str, dict[str, Any]]
    path: Path = PACKAGED

    def provider(self, name: str) -> dict[str, Any]:
        if name not in self.providers:
            raise AltocueError(f"Unknown provider {name}.", fix=f"Use one of {', '.join(sorted(self.providers))}.")
        return self.providers[name]

    def find(self, provider: str, tier: str, capability: str) -> ModelEntry | None:
        return next((e for e in self.entries if (e.provider, e.tier, e.capability) == (provider, tier, capability)), None)

    def resolve(self, provider: str, req: GenRequest) -> ModelEntry:
        """The one route for this request, or an error that says what would work."""
        capability = capability_of(req)
        entry = self.find(provider, req.tier, capability)
        if entry is None:
            raise AltocueError(f"{provider} has no {req.tier} route for {capability}.", fix=self._suggest(provider, req, capability))
        problems = entry.limits(req)
        if problems:
            model = f"{provider} {req.tier} ({entry.model})"
            raise AltocueError(f"{model} cannot do {'; '.join(problems)}.", fix=self._suggest(provider, req, capability))
        return entry

    def _suggest(self, provider: str, req: GenRequest, capability: str) -> str:
        options = [e for e in self.entries if e.capability == capability and not e.limits(req)]
        others = [e for e in options if e.provider != provider]
        tiers = [e for e in options if e.provider == provider]
        if tiers:
            return f"Set this shot's tier to {' or '.join(sorted({e.tier for e in tiers}))}. Altocue will not swap models for you."
        if others:
            names = ", ".join(sorted({f"{e.provider} ({e.tier})" for e in others}))
            return f"Use {names}: set provider under [generate] in altocue.toml. Altocue will not swap models for you."
        return f"Nothing in models.toml does this. Change the shot, or add a route to {self.path}."


def priced(catalog: Catalog, provider: str, shot: str, req: GenRequest) -> Cost:
    """A catalog price for one request."""
    entry = catalog.resolve(provider, req)
    usd = entry.cost(req)
    line = CostLine(shot=shot, model=entry.model, usd=usd, note=entry.note, as_of=entry.as_of)
    return Cost(usd=usd or 0.0, lines=[line], unpriced=[] if usd is not None else [shot])


def _entry(raw: dict[str, Any], path: Path) -> ModelEntry:
    missing = [key for key in REQUIRED if key not in raw]
    fix = f"Fix this route in {path}."
    if missing:
        raise AltocueError(f"A model route is missing {', '.join(missing)}: {raw.get('model', raw)}.", fix=fix)
    if raw["tier"] not in TIERS or raw["capability"] not in CAPABILITIES:
        raise AltocueError(f"Unknown tier or capability in {raw['model']}.", fix=fix)
    if raw["kind"] == "video" and "duration" not in raw.get("fields", {}):
        raise AltocueError(f"{raw['model']} has no duration field.", fix=fix)
    try:
        date.fromisoformat(raw["as_of"])
    except ValueError as error:
        raise AltocueError(f"{raw['model']} has a bad as_of date.", fix=fix) from error
    known = {key: value for key, value in raw.items() if key in ModelEntry.__dataclass_fields__}
    return ModelEntry(**{**known, "aspects": tuple(raw["aspects"])})


def load_catalog(path: str | Path | None = None) -> Catalog:
    source = Path(path) if path else PACKAGED
    if not source.is_file():
        raise AltocueError(f"No model catalog at {source}.", fix="Fix `models` under [generate] in altocue.toml, or remove it.")
    try:
        data = tomllib.loads(source.read_text())
    except tomllib.TOMLDecodeError as error:
        raise AltocueError(f"Cannot read {source}: {error}", fix="Fix the TOML syntax.") from error
    entries = [_entry(raw, source) for raw in data.get("model", [])]
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        route = (entry.provider, entry.tier, entry.capability)
        if route in seen:
            raise AltocueError(f"{' '.join(route)} is routed twice in {source}.", fix="Keep one model per provider, tier and capability.")
        seen.add(route)
    return Catalog(entries=entries, providers=data.get("providers", {}), path=source)
