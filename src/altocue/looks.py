"""Looks written as code, and a user's own LUT, on top of the correction. Nothing here is a bundled LUT.

A look is a pure function of colour, so it bakes into the one `.cube` per clip the plate reads.
Skin is protected the way the correction protects it: the colours around this clip's skin take
less of the look, and if skin hue or face level still moves too far, the whole look backs off.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .errors import AltocueError
from .grade import (
    LOOKS as SMALL_LOOKS,
)
from .grade import (
    LUMA_709,
    _hue_gap,
    apply_look,
    apply_lut,
    back_off,
    lab_to_rgb,
    median_hue,
    rgb_to_lab,
    skin_weight,
)
from .project import Project

SKIN_GUARDS = (0.75, 0.9, 1.0)  # How much of a look the colours at the centre of skin refuse. Tighter comes before weaker.
FLOOR = 0.25  # A look a person asked for never backs off to nothing.
GREY_SKIN = 0.25  # Skin left with less colour than this share was turned to mono on purpose.
SHEET_CELL = 260
SHEET_ROW = 5
CUBE_MOST = 256  # Points per side. A 256-point cube is 16 million rows and no real LUT is larger.


def _matte(rgb: np.ndarray, black: float, white: float) -> np.ndarray:
    return black + (white - black) * rgb


def _s_curve(rgb: np.ndarray, amount: float) -> np.ndarray:
    return rgb + amount * (rgb * rgb * (3 - 2 * rgb) - rgb)


def _shoulder(rgb: np.ndarray, knee: float) -> np.ndarray:
    """Straight to the knee, then a soft roll into white. Blown skies keep some shape."""
    span = 1 - knee
    over = np.maximum(rgb - knee, 0)
    return np.minimum(rgb, knee) + span * (1 - np.exp(-over / span)) / (1 - np.exp(-1))


def _tint(lab: np.ndarray, *, shadows: tuple[float, float] = (0, 0), mids: tuple[float, float] = (0, 0), highlights: tuple[float, float] = (0, 0)) -> np.ndarray:
    """Move a and b by how dark or bright each colour is."""
    light = np.clip(lab[..., 0] / 100, 0, 1)
    low, mid, high = (1 - light) ** 2, 4 * light * (1 - light), light**2
    out = lab.copy()
    for channel in (0, 1):
        out[..., channel + 1] += low * shadows[channel] + mid * mids[channel] + high * highlights[channel]
    return out


def _density(lab: np.ndarray, amount: float) -> np.ndarray:
    """Film gets darker as it gets more colourful, where video gets brighter."""
    out = lab.copy()
    out[..., 0] -= amount * np.hypot(lab[..., 1], lab[..., 2])
    return out


def _chroma(lab: np.ndarray, scale: float) -> np.ndarray:
    out = lab.copy()
    out[..., 1:] *= scale
    return out


def warm_film(rgb: np.ndarray) -> np.ndarray:
    lab = rgb_to_lab(_s_curve(_shoulder(_matte(rgb, 0.018, 1.0), 0.8), 0.32))
    return lab_to_rgb(_tint(_chroma(_density(lab, 0.05), 0.97), shadows=(-1.6, 0.8), mids=(1.2, 4.5), highlights=(1.4, 5.5)))


def cool_film(rgb: np.ndarray) -> np.ndarray:
    lab = rgb_to_lab(_s_curve(_shoulder(_matte(rgb, 0.012, 1.0), 0.8), 0.34))
    return lab_to_rgb(_tint(_chroma(_density(lab, 0.05), 0.92), shadows=(-2.0, -6.0), mids=(-0.8, -3.0), highlights=(0.4, 0.8)))


def faded_matte(rgb: np.ndarray) -> np.ndarray:
    lab = rgb_to_lab(_matte(_s_curve(rgb, 0.12), 0.075, 0.93))
    return lab_to_rgb(_tint(_chroma(lab, 0.8), shadows=(0.6, 1.5), mids=(0.6, 2.0), highlights=(0.2, 1.0)))


def mono_contrast(rgb: np.ndarray) -> np.ndarray:
    """A black and white stock leans on red and green, so skin stays bright and skies fall away."""
    grey = rgb @ np.array([0.34, 0.58, 0.08])
    curved = _s_curve(_s_curve(grey, 0.9), 0.5)
    return np.stack((curved, curved, curved), axis=-1)


def bleach_bypass(rgb: np.ndarray) -> np.ndarray:
    """Silver left in the print: its own black and white laid over the colour, which goes hard and drains."""
    grey = (rgb @ LUMA_709)[..., None]
    silver = np.where(grey < 0.5, 2 * rgb * grey, 1 - 2 * (1 - rgb) * (1 - grey))
    return lab_to_rgb(_chroma(rgb_to_lab(rgb + (silver - rgb) * 0.75), 0.52))


def teal_orange(rgb: np.ndarray) -> np.ndarray:
    """Warm hues lean to orange and the others to teal, by less as they near the line between the two."""
    lab = rgb_to_lab(_s_curve(rgb, 0.22))
    hue, chroma = np.arctan2(lab[..., 2], lab[..., 1]), np.hypot(lab[..., 1], lab[..., 2])
    orange, teal = np.radians(62.0), np.radians(218.0)
    warmth = np.cos(hue - orange)
    target = np.where(warmth > 0, orange, teal)
    lean = np.arctan2(np.sin(target - hue), np.cos(target - hue)) * 0.45 * np.abs(warmth)
    turned = hue + lean
    lab[..., 1], lab[..., 2] = 1.08 * chroma * np.cos(turned), 1.08 * chroma * np.sin(turned)
    return lab_to_rgb(_tint(lab, shadows=(-5.0, -7.0), highlights=(3.0, 7.0)))


def soft_pastel(rgb: np.ndarray) -> np.ndarray:
    """Open the middle, flatten the ends, and take the top off strong colour."""
    lab = rgb_to_lab(_matte(np.maximum(rgb, 0) ** 0.82, 0.05, 0.985))
    chroma = np.hypot(lab[..., 1], lab[..., 2])[..., None]
    lab[..., 1:] *= 1 - 0.42 * chroma / (chroma + 45)
    return lab_to_rgb(_tint(lab, shadows=(1.6, -2.0), mids=(0.8, 0.2), highlights=(1.0, 1.2)))


def neutral_show(rgb: np.ndarray) -> np.ndarray:
    """One curve for a whole show: a little contrast, a kind highlight, and no opinion about colour."""
    grey = (rgb @ LUMA_709)[..., None]
    curved = _s_curve(_shoulder(grey, 0.82), 0.24)
    ratio = (curved + 1e-4) / (grey + 1e-4)
    return np.clip(grey * ratio + (rgb - grey) * np.minimum(ratio, 1.15) * 0.97, 0, 1)


RECIPES: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "warm_film": warm_film,
    "cool_film": cool_film,
    "faded_matte": faded_matte,
    "mono_contrast": mono_contrast,
    "bleach_bypass": bleach_bypass,
    "teal_orange": teal_orange,
    "soft_pastel": soft_pastel,
    "neutral_show": neutral_show,
}
IDS = tuple(RECIPES)
ALL = (*SMALL_LOOKS, *IDS)


def render(name: str, rgb: np.ndarray) -> np.ndarray:
    return np.clip(RECIPES[name](np.clip(np.asarray(rgb, np.float64), 0, 1)), 0, 1)


@dataclass(frozen=True)
class Cube:
    """A `.cube` file as read: 3D tables are in `.cube` order, 1D tables are one row per step."""

    table: np.ndarray
    dimensions: int
    low: tuple[float, float, float] = (0.0, 0.0, 0.0)
    high: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        low, high = np.array(self.low), np.array(self.high)
        place = np.clip((np.asarray(rgb, np.float64) - low) / (high - low), 0, 1)
        if self.dimensions == 3:
            return np.clip(apply_lut(self.table, place), 0, 1)
        steps = np.linspace(0, 1, len(self.table))
        return np.clip(np.stack([np.interp(place[..., i], steps, self.table[:, i]) for i in range(3)], axis=-1), 0, 1)


def _bad_cube(path: Path, why: str) -> AltocueError:
    return AltocueError(f"{path.name} is not a usable .cube file: {why}.", fix="Export it again as a Resolve or Adobe .cube, with LUT_3D_SIZE or LUT_1D_SIZE.")


def _three_numbers(path: Path, parts: list[str]) -> tuple[float, float, float]:
    try:
        values = tuple(float(part) for part in parts)
    except ValueError:
        raise _bad_cube(path, f"cannot read the line {' '.join(parts)!r}") from None
    if len(values) != 3 or not np.isfinite(values).all():
        raise _bad_cube(path, f"cannot read the line {' '.join(parts)!r}")
    return values  # type: ignore[return-value]


def read_cube(path: str | Path) -> Cube:
    """The published format, as Resolve and Adobe write it, with its comments, title and domain."""
    target = Path(path)
    sizes = {"LUT_3D_SIZE": 0, "LUT_1D_SIZE": 0}
    low, high = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    rows: list[tuple[float, float, float]] = []
    for raw in target.read_text(errors="replace").splitlines():
        parts = raw.split("#")[0].split()
        if not parts or parts[0].upper() == "TITLE":
            continue
        word = parts[0].upper()
        if word in sizes:
            sizes[word] = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
        elif word == "DOMAIN_MIN":
            low = _three_numbers(target, parts[1:])
        elif word == "DOMAIN_MAX":
            high = _three_numbers(target, parts[1:])
        elif word in {"LUT_1D_INPUT_RANGE", "LUT_3D_INPUT_RANGE"} and len(parts) == 3:
            low, high = (float(parts[1]),) * 3, (float(parts[2]),) * 3
        else:
            rows.append(_three_numbers(target, parts))
    size, dimensions = (sizes["LUT_3D_SIZE"], 3) if sizes["LUT_3D_SIZE"] else (sizes["LUT_1D_SIZE"], 1)
    if not 2 <= size <= CUBE_MOST and dimensions == 3 or size < 2:
        raise _bad_cube(target, "it does not say a usable size")
    if len(rows) != size**dimensions:
        raise _bad_cube(target, f"it says {size**dimensions} rows and has {len(rows)}")
    if any(b <= a for a, b in zip(low, high, strict=True)):
        raise _bad_cube(target, "its domain is empty")
    return Cube(np.array(rows, np.float64), dimensions, low, high)


@dataclass(frozen=True)
class Finish:
    """A look or a LUT for one clip. `strength` is what this clip takes once skin has had its say."""

    name: str = "natural"
    strength: float = 1.0
    cube: Cube | None = None
    skin_center: tuple[float, float, float] | None = None
    guard: float = SKIN_GUARDS[0]
    moved: float = 0.0  # Degrees of skin hue this finish moves.

    @property
    def is_identity(self) -> bool:
        return (self.name == "natural" and self.cube is None) or self.strength == 0

    def render(self, rgb: np.ndarray) -> np.ndarray:
        if self.cube is not None:
            return self.cube.apply(rgb)
        return render(self.name, rgb) if self.name in RECIPES else apply_look(rgb, self.name)

    def apply(self, rgb: np.ndarray, strength: float | None = None) -> np.ndarray:
        """`strength` is a share of this finish, so `back_off` can search the way it does for a correction."""
        base = np.asarray(rgb, np.float64)
        amount = self.strength * (1.0 if strength is None else strength)
        if self.is_identity or amount == 0:
            return base
        share = np.full((*base.shape[:-1], 1), amount)
        if self.skin_center is not None:
            share = share * (1 - self.guard * skin_weight(rgb_to_lab(base), self.skin_center))
        return np.clip(base + (self.render(base) - base) * share, 0, 1)


def _loose(name: str) -> str:
    return re.sub(r"[\s-]+", "_", name.strip().lower())


def choose(look: str = "natural", *, strength: float = 1.0, lut: str | Path | None = None) -> Finish:
    """Check what was asked for. `fit` then sizes it to one clip's skin."""
    name = _loose(look)
    if name not in ALL:
        raise AltocueError(f"Unknown look {look}.", fix=f"Use one of {', '.join(ALL)}, or `--lut your.cube`.")
    if not 0 <= strength <= 1:
        raise AltocueError(f"A look strength of {strength:g} is not between 0 and 1.", fix="Use 0 for none and 1 for all of it.")
    if lut is None:
        return Finish(name=name, strength=strength)
    path = Path(lut).expanduser()
    if name != "natural":
        raise AltocueError(f"Asked for the look {name} and the LUT {path.name}.", fix="Use one or the other: a LUT replaces the look.")
    if not path.is_file():
        raise AltocueError(f"No LUT at {path}.", fix="Check the path. A relative one in altocue.toml starts at the project folder.")
    return Finish(name=f"lut:{path.name}", strength=strength, cube=read_cube(path))


def stamp(lut: str | Path | None) -> str:
    return hashlib.sha256(Path(lut).expanduser().read_bytes()).hexdigest()[:16] if lut else ""


def fit(finish: Finish, skin: np.ndarray | None) -> Finish:
    """Size a finish to one clip. `skin` is that clip's skin after its correction, or None."""
    if finish.is_identity or skin is None or not len(skin):
        return finish
    bare = rgb_to_lab(skin)
    after = rgb_to_lab(finish.render(skin))
    colour = np.hypot(np.median(bare[..., 1]), np.median(bare[..., 2]))
    if np.hypot(np.median(after[..., 1]), np.median(after[..., 2])) < GREY_SKIN * colour:
        return finish  # Black and white with coloured skin would be a mistake, not protection.
    center = tuple(bare.mean(axis=0).tolist())
    for guard in SKIN_GUARDS:  # A colourist tightens the key on skin before turning the whole grade down.
        guarded = replace(finish, skin_center=center, guard=guard)
        share = back_off(guarded, skin)[0]
        if share == 1.0:
            break
    settled = replace(guarded, strength=finish.strength * max(share, FLOOR))
    return replace(settled, moved=_hue_gap(median_hue(skin), median_hue(settled.apply(skin))))


def sheet(project: Project, dest: str | Path | None = None) -> Path:
    """Every look on one real frame, sized and skin-guarded as the grade would, to choose from."""
    from PIL import Image, ImageDraw

    from . import grade
    from .frame import read_rgb

    clips = project.clips()
    clip = max(clips, key=lambda c: c.duration)
    spans = grade._kept_ranges(project).get(clip.id)
    stats = grade._read_clip(clip, spans)
    correction = grade.solve_clip(stats)
    times = grade.sample_times(clip, spans, 1)
    raw = read_rgb(clip, times[0], max(SHEET_CELL * 2, 640))
    if raw is None:
        raise AltocueError(f"Could not read a frame of {clip.id}.", fix=f"Check that {clip.path} plays.")
    frame = correction.apply(grade.hdr_to_sdr(raw, clip.transfer) if clip.is_hdr else raw)
    skin = correction.apply(stats.skin) if stats.has_skin else None
    names = ALL
    height, width = frame.shape[:2]
    cell = (SHEET_CELL, round(SHEET_CELL * height / width)) if height >= width else (round(SHEET_CELL * width / height), SHEET_CELL)
    rows = -(-len(names) // SHEET_ROW)
    board = Image.new("RGB", (cell[0] * SHEET_ROW, (cell[1] + 28) * rows), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    target = Path(dest) if dest else project.lut_dir / "looks.jpg"
    singles = target.parent / "looks"  # each look on its own, and the frame before any correction, to show side by side
    singles.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(raw, 0, 1) * 255 + 0.5).astype(np.uint8)).save(singles / "raw.jpg", quality=90)
    for index, name in enumerate(names):
        picture = fit(choose(name), skin).apply(frame)
        whole = Image.fromarray((np.clip(picture, 0, 1) * 255 + 0.5).astype(np.uint8))
        whole.save(singles / f"{name}.jpg", quality=90)
        image = whole.resize(cell, Image.Resampling.LANCZOS)
        x, y = (index % SHEET_ROW) * cell[0], (index // SHEET_ROW) * (cell[1] + 28)
        board.paste(image, (x, y))
        draw.text((x + 8, y + cell[1] + 8), name, fill=(235, 235, 235))
    target.parent.mkdir(parents=True, exist_ok=True)
    board.save(target, quality=90)
    return target
