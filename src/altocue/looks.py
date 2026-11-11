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
    DISPLAY_GAMMA,
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
from .grade import (
    LOOKS as SMALL_LOOKS,
)
from .project import Project

VERSION = 2  # Raise it when the maths of a look changes, so a grade baked with the old maths is solved again.
SKIN_GUARDS = (0.75, 0.9, 1.0)  # How much of a look the colours at the centre of skin refuse. Tighter comes before weaker.
FLOOR = 0.25  # A look a person asked for never backs off to nothing.
GREY_SKIN = 0.25  # A look that leaves skin less colour than this share is black and white on purpose.
# Five skin colours from light to deep, to ask what a look does to skin. They are fixtures, not measurements of people.
SKIN_PROBE = np.array([[0.86, 0.70, 0.60], [0.78, 0.58, 0.47], [0.60, 0.42, 0.32], [0.42, 0.28, 0.21], [0.25, 0.17, 0.14]])
SHEET_CELL = 260
SHEET_ROW = 5
CUBE_MOST = 256  # Points per side. A 256-point cube is 16 million rows and no real LUT is larger.


Points = tuple[tuple[float, float], ...]  # A curve as (input, output) control points on the 0 to 1 signal.


def _matte(rgb: np.ndarray, black: float, white: float) -> np.ndarray:
    return black + (white - black) * rgb


def _s_curve(rgb: np.ndarray, amount: float) -> np.ndarray:
    return rgb + amount * (rgb * rgb * (3 - 2 * rgb) - rgb)


def _shoulder(rgb: np.ndarray, knee: float) -> np.ndarray:
    """Straight to the knee, then a soft roll into white. Blown skies keep some shape."""
    span = 1 - knee
    over = np.maximum(rgb - knee, 0)
    return np.minimum(rgb, knee) + span * (1 - np.exp(-over / span)) / (1 - np.exp(-1))


def _curve(points: Points) -> Callable[[np.ndarray], np.ndarray]:
    """A monotone cubic through control points (Fritsch and Carlson, 1980), so a curve can never overshoot or reverse."""
    xs, ys = (np.array(axis, np.float64) for axis in zip(*points, strict=True))
    step = np.diff(xs)
    rise = np.diff(ys) / step
    slope = np.zeros_like(xs)
    slope[0], slope[-1] = rise[0], rise[-1]
    for i in range(1, len(xs) - 1):
        if rise[i - 1] * rise[i] > 0:
            first, second = 2 * step[i] + step[i - 1], step[i] + 2 * step[i - 1]
            slope[i] = (first + second) / (first / rise[i - 1] + second / rise[i])

    def at(x: np.ndarray) -> np.ndarray:
        x = np.clip(np.asarray(x, np.float64), xs[0], xs[-1])
        i = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(xs) - 2)
        t = (x - xs[i]) / step[i]
        start, end = (2 * t**3 - 3 * t**2 + 1) * ys[i], (3 * t**2 - 2 * t**3) * ys[i + 1]
        return start + end + step[i] * ((t**3 - 2 * t**2 + t) * slope[i] + (t**3 - t**2) * slope[i + 1])

    return at


def _tone(rgb: np.ndarray, points: Points) -> np.ndarray:
    """A curve on luma. Colour rides on the ratio, so hue holds, and the ratio is capped so shadows cannot gain wild colour."""
    grey = (rgb @ LUMA_709)[..., None]
    curved = _curve(points)(grey)
    ratio = (curved + 1e-4) / (grey + 1e-4)
    return np.clip(curved + (rgb - grey) * np.minimum(ratio, 1.25), 0, 1)


def _tone_each(rgb: np.ndarray, red: Points, green: Points | None = None, blue: Points | None = None) -> np.ndarray:
    """A curve per channel, as a print has. Colour rises with contrast and hue bends, which is the point."""
    curves = (red, green or red, blue or red)
    return np.stack([_curve(points)(rgb[..., i]) for i, points in enumerate(curves)], axis=-1)


def _zones(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """How much each colour is a shadow, a mid and a highlight."""
    light = np.clip(lab[..., 0] / 100, 0, 1)
    return (1 - light) ** 2, 4 * light * (1 - light), light**2


def _tint(lab: np.ndarray, *, shadows: tuple[float, float] = (0, 0), mids: tuple[float, float] = (0, 0), highlights: tuple[float, float] = (0, 0)) -> np.ndarray:
    """Move a and b by how dark or bright each colour is."""
    low, mid, high = _zones(lab)
    out = lab.copy()
    for channel in (0, 1):
        out[..., channel + 1] += low * shadows[channel] + mid * mids[channel] + high * highlights[channel]
    return out


def _deepen(lab: np.ndarray, amount: float) -> np.ndarray:
    """Film gets darker as it gets more colourful, where video gets brighter. Greys do not move."""
    out = lab.copy()
    out[..., 0] = np.maximum(lab[..., 0] - amount * np.hypot(lab[..., 1], lab[..., 2]), 0)
    return out


def _dye(rgb: np.ndarray, amount: float, saturation: float) -> np.ndarray:
    """Subtractive saturation, in linear light.

    Video saturation pivots on luma, so more colour is brighter. Dye pivots toward the largest channel, so more colour
    is denser. `amount` slides from the first to the second, and greys do not move at any setting.
    """
    linear = np.clip(rgb, 0, 1) ** DISPLAY_GAMMA
    grey = (linear @ LUMA_709)[..., None]
    pivot = grey + amount * (linear.max(axis=-1, keepdims=True) - grey)
    return np.clip(pivot + (linear - pivot) * saturation, 0, 1) ** (1 / DISPLAY_GAMMA)


def _chroma(lab: np.ndarray, scale: float) -> np.ndarray:
    out = lab.copy()
    out[..., 1:] *= scale
    return out


def _chroma_by_zone(lab: np.ndarray, shadows: float = 1.0, mids: float = 1.0, highlights: float = 1.0) -> np.ndarray:
    low, mid, high = _zones(lab)
    out = lab.copy()
    out[..., 1:] *= ((low * shadows + mid * mids + high * highlights) / (low + mid + high))[..., None]
    return out


def _vibrance(lab: np.ndarray, amount: float, knee: float = 40.0) -> np.ndarray:
    """Lift weak colour more than strong colour, so a wall wakes up and a red jumper does not shout."""
    chroma = np.hypot(lab[..., 1], lab[..., 2])[..., None]
    out = lab.copy()
    out[..., 1:] *= 1 + amount * knee / (chroma + knee)
    return out


def _ceiling(lab: np.ndarray, limit: float, knee: float = 0.6) -> np.ndarray:
    """A chroma ceiling with a knee: untouched below `knee * limit`, then easing toward `limit`, which it never passes."""
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    start = knee * limit
    eased = np.where(chroma <= start, chroma, start + (limit - start) * np.tanh((chroma - start) / (limit - start)))
    out = lab.copy()
    out[..., 1:] *= (eased / np.maximum(chroma, 1e-6))[..., None]
    return out


def _near_hue(lab: np.ndarray, centre: float, width: float) -> np.ndarray:
    """1 at the centre hue, 0 at `width` degrees away, smooth between.

    CIELAB hue for BT.709: red 40, skin 45 to 60, yellow 103, green 136, cyan 196, sky 250 to 275, blue 306, magenta 328.
    """
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))
    near = np.clip(1 - np.abs((hue - centre + 180) % 360 - 180) / width, 0, 1)
    return near * near * (3 - 2 * near)


def _turn(lab: np.ndarray, centre: float, width: float, *, degrees: float = 0.0, saturation: float = 1.0) -> np.ndarray:
    """Turn or strengthen one band of hues. Near-greys have no hue to move, so they are left out."""
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    weight = _near_hue(lab, centre, width) * np.clip(chroma / 12, 0, 1)
    hue = np.arctan2(lab[..., 2], lab[..., 1]) + np.radians(degrees) * weight
    scaled = chroma * (1 + (saturation - 1) * weight)
    out = lab.copy()
    out[..., 1], out[..., 2] = scaled * np.cos(hue), scaled * np.sin(hue)
    return out


def _clean_white(lab: np.ndarray, start: float, keep: float, reach: float = 25.0) -> np.ndarray:
    """Near-white loses its cast. Only colours weaker than `reach` are touched, so a bright strong colour keeps its colour."""
    top = np.clip((lab[..., 0] - start) / (100 - start), 0, 1)
    top = top * top * (3 - 2 * top)
    weak = np.clip(1 - np.hypot(lab[..., 1], lab[..., 2]) / reach, 0, 1)
    out = lab.copy()
    out[..., 1:] *= (1 - (1 - keep) * top * weak)[..., None]
    return out


def warm_film(rgb: np.ndarray) -> np.ndarray:
    lab = rgb_to_lab(_s_curve(_shoulder(_matte(rgb, 0.018, 1.0), 0.8), 0.32))
    return lab_to_rgb(_tint(_chroma(_deepen(lab, 0.05), 0.97), shadows=(-1.6, 0.8), mids=(1.2, 4.5), highlights=(1.4, 5.5)))


def cool_film(rgb: np.ndarray) -> np.ndarray:
    lab = rgb_to_lab(_s_curve(_shoulder(_matte(rgb, 0.012, 1.0), 0.8), 0.34))
    return lab_to_rgb(_tint(_chroma(_deepen(lab, 0.05), 0.92), shadows=(-2.0, -6.0), mids=(-0.8, -3.0), highlights=(0.4, 0.8)))


def faded_matte(rgb: np.ndarray) -> np.ndarray:
    lab = rgb_to_lab(_matte(_s_curve(rgb, 0.12), 0.075, 0.93))
    return lab_to_rgb(_tint(_chroma(lab, 0.8), shadows=(0.6, 1.5), mids=(0.6, 2.0), highlights=(0.2, 1.0)))


def mono_contrast(rgb: np.ndarray) -> np.ndarray:
    """A black and white stock leans on red and green, so skin stays bright and skies fall away.

    The channels are mixed as light, the way a filter over the lens mixes them, and only then coded for the display.
    """
    grey = ((np.clip(rgb, 0, 1) ** DISPLAY_GAMMA) @ np.array([0.34, 0.58, 0.08])) ** (1 / DISPLAY_GAMMA)
    curved = _s_curve(_s_curve(grey, 0.9), 0.5)
    return np.stack((curved, curved, curved), axis=-1)


def mono_silver(rgb: np.ndarray) -> np.ndarray:
    """A softer black and white: the whole tonal range, with a gentle toe and shoulder."""
    grey = ((np.clip(rgb, 0, 1) ** DISPLAY_GAMMA) @ np.array([0.30, 0.59, 0.11])) ** (1 / DISPLAY_GAMMA)
    curved = _curve(((0, 0.02), (0.1, 0.085), (0.25, 0.24), (0.5, 0.53), (0.75, 0.79), (0.92, 0.93), (1, 0.975)))(grey)
    return np.stack((curved, curved, curved), axis=-1)


def bleach_bypass(rgb: np.ndarray) -> np.ndarray:
    """Silver left in the print: its own black and white laid over the colour, which goes hard and drains."""
    grey = (rgb @ LUMA_709)[..., None]
    silver = np.where(grey < 0.5, 2 * rgb * grey, 1 - 2 * (1 - rgb) * (1 - grey))
    return lab_to_rgb(_chroma(rgb_to_lab(rgb + (silver - rgb) * 0.75), 0.52))


TEAL, ORANGE = 218.0, 62.0  # CIELAB hue. Skin of every tone sits near the orange, and the teal is close to its opposite.


def teal_orange(rgb: np.ndarray) -> np.ndarray:
    """The cool half of the wheel leans to teal, and warm colours keep their own hue, so a red stays that red.

    Every move here turns or scales the colour a pixel already has, and adds none. So black, white and every grey come
    out as they went in: a grey wall does not turn blue-green and a white shirt does not turn cream.
    """
    lab = rgb_to_lab(_tone(rgb, ((0, 0), (0.08, 0.062), (0.25, 0.235), (0.5, 0.52), (0.75, 0.80), (0.92, 0.94), (1, 1))))
    hue, chroma = np.arctan2(lab[..., 2], lab[..., 1]), np.hypot(lab[..., 1], lab[..., 2])
    warmth = np.cos(hue - np.radians(ORANGE))
    cool, warm = np.clip(-warmth, 0, 1), np.clip(warmth, 0, 1)
    turned = hue + np.arctan2(np.sin(np.radians(TEAL) - hue), np.cos(np.radians(TEAL) - hue)) * 0.45 * cool
    shadows, _, highlights = _zones(lab)
    scaled = chroma * (1 + 0.18 * cool * shadows + 0.06 * warm * (1 + highlights))
    lab[..., 1], lab[..., 2] = scaled * np.cos(turned), scaled * np.sin(turned)
    return lab_to_rgb(_ceiling(lab, 100))


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


def clean_commercial(rgb: np.ndarray) -> np.ndarray:
    """Crisp and neutral: true whites, clean blacks, weak colour lifted and strong colour held."""
    lab = rgb_to_lab(_tone(rgb, ((0, 0), (0.06, 0.052), (0.25, 0.25), (0.5, 0.535), (0.75, 0.80), (0.92, 0.945), (1, 1))))
    lab = _clean_white(_ceiling(_vibrance(lab, 0.14), 95), 84, 0.35)
    return lab_to_rgb(_chroma_by_zone(lab, shadows=0.85))


def product_white(rgb: np.ndarray) -> np.ndarray:
    """A white background goes white, greys stay grey, and the product keeps its colour."""
    lab = rgb_to_lab(_tone(rgb, ((0, 0), (0.06, 0.05), (0.25, 0.26), (0.5, 0.56), (0.75, 0.835), (0.90, 0.965), (0.97, 1.0), (1, 1))))
    lab = _clean_white(lab, 78, 0.0)
    weak = np.clip(1 - np.hypot(lab[..., 1], lab[..., 2]) / 15, 0, 1)
    lab[..., 1:] *= (1 - 0.5 * _zones(lab)[0] * weak)[..., None]  # grey shadows lose half their cast
    return lab_to_rgb(_ceiling(_vibrance(lab, 0.10), 100))


def warm_lifestyle(rgb: np.ndarray) -> np.ndarray:
    """Bright, open and golden: warm mids and highlights, quiet greens, a soft top."""
    lifted = _tone(rgb, ((0, 0.012), (0.08, 0.085), (0.25, 0.275), (0.5, 0.56), (0.75, 0.81), (0.92, 0.94), (1, 0.985)))
    lab = _tint(rgb_to_lab(_dye(lifted, 0.35, 1.04)), shadows=(0.6, 1.5), mids=(1.6, 5.0), highlights=(1.2, 6.0))
    lab = _turn(lab, 135, 55, degrees=-12, saturation=0.72)  # greens toward olive, and no stronger for the gold laid over them
    return lab_to_rgb(_turn(lab, 265, 50, degrees=-6, saturation=0.90))  # sky a touch toward cyan


def airy_bright(rgb: np.ndarray) -> np.ndarray:
    """Light and soft: lifted shadows, gentle colour, a little warmth."""
    lifted = _tone(rgb, ((0, 0.04), (0.1, 0.125), (0.25, 0.29), (0.5, 0.585), (0.75, 0.84), (0.92, 0.95), (1, 0.99)))
    lab = _tint(_ceiling(_chroma(rgb_to_lab(lifted), 0.9), 70), shadows=(0.8, -0.5), mids=(1.0, 1.5), highlights=(0.6, 2.0))
    return lab_to_rgb(_turn(lab, 135, 55, degrees=-8, saturation=0.75))


def earthy_doc(rgb: np.ndarray) -> np.ndarray:
    """Documentary: natural contrast, olive greens, slate blues, earth and skin held."""
    curved = _tone(rgb, ((0, 0.01), (0.08, 0.07), (0.25, 0.24), (0.5, 0.51), (0.75, 0.77), (0.92, 0.92), (1, 0.975)))
    lab = _deepen(_chroma(rgb_to_lab(curved), 0.95), 0.05)
    lab = _turn(_turn(lab, 135, 60, degrees=-14, saturation=0.72), 265, 55, degrees=-8, saturation=0.78)
    return lab_to_rgb(_tint(_turn(lab, 55, 40, saturation=1.06), shadows=(0.5, 1.0), mids=(0.6, 1.8), highlights=(0.2, 1.5)))


def moody_low(rgb: np.ndarray) -> np.ndarray:
    """Dark mids, capped whites, dense quiet colour, cool green shadows."""
    curved = _tone(rgb, ((0, 0.03), (0.1, 0.085), (0.25, 0.20), (0.5, 0.44), (0.75, 0.69), (0.92, 0.84), (1, 0.90)))
    lab = _tint(_deepen(_chroma(rgb_to_lab(curved), 0.88), 0.06), shadows=(-2.5, -1.5), mids=(-0.5, 1.0), highlights=(0.5, 2.5))
    return lab_to_rgb(_turn(_chroma_by_zone(lab, shadows=0.75, mids=0.9, highlights=0.8), 135, 55, degrees=10, saturation=0.7))


def print_film(rgb: np.ndarray) -> np.ndarray:
    """A print: toe and shoulder, half of the curve per channel, dense colour, cool shadows and a warm top."""
    points = ((0, 0.022), (0.08, 0.065), (0.25, 0.225), (0.5, 0.525), (0.75, 0.815), (0.90, 0.93), (1, 0.975))
    lab = rgb_to_lab(_dye(0.5 * _tone_each(rgb, points) + 0.5 * _tone(rgb, points), 0.8, 1.08))
    lab = _tint(lab, shadows=(-1.2, -2.5), mids=(0.3, 1.0), highlights=(0.8, 3.5))
    return lab_to_rgb(_ceiling(_turn(_turn(lab, 135, 55, degrees=-6, saturation=0.85), 265, 50, degrees=-8, saturation=0.9), 85))


def vintage_print(rgb: np.ndarray) -> np.ndarray:
    """An old print: three channel curves that cross. Blue is lifted in the shadows and held back on top, so whites go cream."""
    aged = _tone_each(
        rgb,
        ((0, 0.04), (0.25, 0.245), (0.5, 0.525), (0.75, 0.795), (1, 0.975)),
        ((0, 0.04), (0.25, 0.245), (0.5, 0.515), (0.75, 0.78), (1, 0.96)),
        ((0, 0.055), (0.25, 0.25), (0.5, 0.49), (0.75, 0.725), (1, 0.90)),
    )
    lab = _turn(_deepen(_chroma(rgb_to_lab(aged), 0.92), 0.04), 135, 55, degrees=-10, saturation=0.8)
    return lab_to_rgb(_ceiling(_turn(lab, 35, 35, degrees=4, saturation=1.05), 75))  # reds toward orange


def _spare_warm(lab: np.ndarray, cooled: np.ndarray, spare: float) -> np.ndarray:
    """Lamps, fire and skin keep most of their own colour while the rest of the frame takes the night."""
    warm = spare * _near_hue(lab, 60, 60)[..., None]
    return cooled * (1 - warm) + lab * warm


def night_blue(rgb: np.ndarray) -> np.ndarray:
    """Night that reads as night: darker mids, cool shadows, less colour in the dark, and warm lights left warm."""
    lab = rgb_to_lab(_tone(rgb, ((0, 0.0), (0.1, 0.06), (0.25, 0.17), (0.5, 0.40), (0.75, 0.68), (0.92, 0.88), (1, 0.97))))
    cooled = _tint(lab, shadows=(-2.0, -7.0), mids=(-2.0, -6.0), highlights=(0.0, -1.5))
    return lab_to_rgb(_spare_warm(lab, _chroma_by_zone(cooled, shadows=0.6, mids=0.85), 0.85))


def neon_night(rgb: np.ndarray) -> np.ndarray:
    """City night: violet shadows, magenta and cyan signs pushed, green spill turned to cyan, warm skin kept."""
    lab = rgb_to_lab(_tone(rgb, ((0, 0), (0.1, 0.065), (0.25, 0.20), (0.5, 0.47), (0.75, 0.77), (0.92, 0.94), (1, 1))))
    lab = _spare_warm(lab, _tint(lab, shadows=(1.5, -6.0), mids=(1.0, -3.0), highlights=(-1.0, -1.0)), 0.8)
    lab = _turn(_turn(lab, 330, 60, saturation=1.15), 215, 55, saturation=1.12)
    return lab_to_rgb(_ceiling(_turn(lab, 135, 45, degrees=25, saturation=0.7), 90))


RECIPES: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "warm_film": warm_film,
    "cool_film": cool_film,
    "faded_matte": faded_matte,
    "mono_contrast": mono_contrast,
    "bleach_bypass": bleach_bypass,
    "teal_orange": teal_orange,
    "soft_pastel": soft_pastel,
    "neutral_show": neutral_show,
    "clean_commercial": clean_commercial,
    "product_white": product_white,
    "warm_lifestyle": warm_lifestyle,
    "airy_bright": airy_bright,
    "earthy_doc": earthy_doc,
    "moody_low": moody_low,
    "print_film": print_film,
    "vintage_print": vintage_print,
    "mono_silver": mono_silver,
    "night_blue": night_blue,
    "neon_night": neon_night,
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
    """What a finish is made of, for the grade's cache: the recipes as they are written now, and the bytes of a user's LUT."""
    return f"looks{VERSION}:" + (hashlib.sha256(Path(lut).expanduser().read_bytes()).hexdigest()[:16] if lut else "")


def removes_colour(finish: Finish) -> bool:
    """Is this finish black and white, or close to it?

    Asked of the look and not of one clip's skin: a hard look can crush a dark face until little colour is left in it,
    and that face needs the guard most. So every clip of a film gets the same answer.
    """
    bare, after = rgb_to_lab(SKIN_PROBE), rgb_to_lab(finish.render(SKIN_PROBE))
    return bool(np.hypot(after[..., 1], after[..., 2]).sum() < GREY_SKIN * np.hypot(bare[..., 1], bare[..., 2]).sum())


def fit(finish: Finish, skin: np.ndarray | None) -> Finish:
    """Size a finish to one clip. `skin` is that clip's skin after its correction, or None."""
    if finish.is_identity or skin is None or not len(skin):
        return finish
    if removes_colour(finish):
        return finish  # Black and white with coloured skin would be a mistake, not protection.
    center = tuple(rgb_to_lab(skin).mean(axis=0).tolist())
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
