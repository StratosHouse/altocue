"""The provider contract. Nothing here names a vendor or a model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from ..errors import AltocueError
from ..models import Model

if TYPE_CHECKING:
    from .catalog import Catalog

Kind = Literal["video", "image"]
Tier = Literal["draft", "standard", "hero"]
State = Literal["queued", "running", "succeeded", "failed", "moderated", "canceled"]

CAPABILITIES: tuple[str, ...] = (
    "text_to_video",
    "image_to_video",
    "reference_to_video",
    "frame_and_refs_to_video",
    "text_to_image",
    "reference_to_image",
)
TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "moderated", "canceled"})


class Moderated(AltocueError):
    """The provider's safety system refused the request. Never retried automatically."""


class SubmitUnknown(AltocueError):
    """A submit may have reached the provider, and we cannot tell. Retrying could pay twice."""


@dataclass
class GenRequest:
    """One asset to make. It asks for a tier, never a vendor model."""

    kind: Kind
    tier: Tier
    prompt: str
    aspect: str
    duration_s: int
    audio: bool = False
    first_frame: Path | None = None
    last_frame: Path | None = None
    refs: list[Path] = field(default_factory=list)
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobRef(Model):
    """Everything needed to find a paid job again after a crash."""

    provider: str
    job_id: str
    model: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobStatus(Model):
    state: State
    detail: str = ""


@dataclass
class Asset(Model):
    path: str
    url: str = ""
    seed: int | None = None


@dataclass
class CostLine(Model):
    shot: str
    model: str
    usd: float | None
    note: str = ""
    as_of: str = ""


@dataclass
class Cost(Model):
    """A total plus the lines behind it. `unpriced` shots block a paid run."""

    usd: float = 0.0
    lines: list[CostLine] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)


@runtime_checkable
class GenProvider(Protocol):
    id: str
    catalog: Catalog
    poll_seconds: float
    destination: str
    cost_basis: str

    def preflight(self) -> None: ...

    def upload(self, path: Path) -> str: ...

    def estimate(self, req: GenRequest) -> Cost | None: ...

    def submit(self, req: GenRequest, idem_key: str) -> JobRef: ...

    def poll(self, job: JobRef) -> JobStatus: ...

    def fetch(self, job: JobRef, dest: Path) -> list[Asset]: ...

    def cancel(self, job: JobRef) -> None: ...
