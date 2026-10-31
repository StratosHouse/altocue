"""What a renderer must provide."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..brand import Brand


@dataclass
class Layer:
    path: Path
    renderer: str
    concat: bool = False  # True when `path` is an ffconcat list of PNG states


class Renderer(Protocol):
    id: str
    licence: str

    def available(self) -> tuple[bool, str]: ...

    def render(self, timeline: dict[str, Any], brand: Brand, dest: Path, *, draft: bool = False) -> Layer: ...
