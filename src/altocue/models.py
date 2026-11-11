"""The files Altocue passes between stages, as plain dataclasses.

Every model round-trips through JSON with `to_dict` and `from_dict`, because every
stage reads and writes files a person or an agent can open and change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal, Self

WordKind = Literal["word", "filler", "cue"]
RemovalKind = Literal["silence", "filler", "stutter", "retake", "cue", "quiet", "manual"]
Decision = Literal["auto", "review", "keep"]


@dataclass
class Model:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Clip(Model):
    """One source file, as `ffprobe` saw it."""

    id: str
    path: str
    duration: float
    fps: float
    width: int
    height: int
    rotation: int = 0
    pix_fmt: str = ""
    transfer: str = ""
    primaries: str = ""
    has_audio: bool = True
    sample_rate: int = 0
    variable_frame_rate: bool = False
    video_duration: float = 0.0  # where the picture ends, counted from the file's start. 0 when the file does not say, or media.json is older

    @property
    def picture_duration(self) -> float:
        """How long there is picture. Sound often outlasts it on a phone, so the file's own length proves nothing."""
        return min(self.video_duration, self.duration) if self.video_duration > 0 else self.duration

    @property
    def is_hdr(self) -> bool:
        return self.transfer in {"arib-std-b67", "smpte2084"}

    @property
    def display_size(self) -> tuple[int, int]:
        """Width and height once rotation metadata is applied."""
        return (self.height, self.width) if abs(self.rotation) % 180 == 90 else (self.width, self.height)


@dataclass
class Word(Model):
    """One spoken word, with seconds from the start of its clip."""

    text: str
    start: float
    end: float
    confidence: float = 1.0
    kind: WordKind = "word"
    speaker: str = ""  # such as S1. Empty until `altocue transcribe --speakers` has run


@dataclass
class Segment(Model):
    """One kept range of one clip, in source seconds."""

    id: str
    clip: str
    start: float
    end: float
    zoom_from: float = 1.0
    zoom_to: float = 1.0
    gain: float = 1.0
    label: str = ""
    crop: list[float] | None = None
    path: list[list[float]] | None = None  # [seconds into the segment, x, y] keyframes when the crop moves. `crop` holds its size
    no_snap_in: bool = False
    no_snap_out: bool = False
    held: list[str] = field(default_factory=list)  # gain, crop or zoom set by hand: `frame` leaves these, and a re-cut carries them


@dataclass
class Removed(Model):
    """One removed span, with why it went and how sure we are."""

    clip: str
    start: float
    end: float
    kind: RemovalKind
    evidence: str
    decision: Decision = "auto"
    score: float = 1.0


@dataclass
class Edl(Model):
    """The cut. Segments play in order; `removed` explains everything else."""

    fps: int
    segments: list[Segment] = field(default_factory=list)
    removed: list[Removed] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)  # hand edits the last re-cut could not keep

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            fps=data["fps"],
            segments=[Segment.from_dict(s) for s in data.get("segments", [])],
            removed=[Removed.from_dict(r) for r in data.get("removed", [])],
            lost=data.get("lost", []),
        )

    @property
    def duration(self) -> float:
        return sum(s.end - s.start for s in self.segments)


@dataclass
class GateResult(Model):
    """One fact check. Code decides `passed`; nothing here is an opinion."""

    id: str
    passed: bool
    value: Any = None
    threshold: Any = None
    detail: str = ""
    skipped: bool = False
