"""Colour: HDR to SDR, one bounded correction per clip, a named look, one LUT per clip.

The maths lives here in numpy, where it can be tested. The render is one `lut3d` filter.
Corrections are solved once per clip from sampled frames, so nothing can flicker, and every
change is capped, because a colour room or a sunset looks like a colour cast to any
automatic method. Skin wins: if a correction moves skin hue too far, it backs off.

White balance averages near-neutral, non-skin pixels in linear light. Shot matching is the
mean and spread transfer from Reinhard et al., "Color Transfer between Images" (2001),
done in CIELAB, separately for skin and for everything else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .errors import AltocueError
from .frame import Face, detect_faces, read_rgb, source_matrix, source_range
from .models import Clip
from .project import Project, write_json

HLG = "arib-std-b67"
PQ = "smpte2084"
LUT_SIZE = 33
HDR_LUT_SIZE = 65
LOOKS = ("natural", "warm", "cool", "punchy", "filmic")
VERSION = 1

HLG_A, HLG_B, HLG_C = 0.17883277, 0.28466892, 0.55991073
HLG_PEAK_NITS = 1000.0
HLG_SYSTEM_GAMMA = 1.2
PQ_WHITE_NITS = 203.0  # BT.2408 reference white.
HLG_WHITE_NITS = 400.0  # Phones expose HLG far above BT.2408: faces near 0.6, walls near 0.9. At 203 walls clip.
DISPLAY_GAMMA = 2.4  # BT.1886. Tone mapping ends in display light, so this is its inverse.
KNEE = 0.4  # Linear SDR level where roll-off starts. Skin sits below it and passes untouched.
HIGHLIGHT_FADE = 0.85  # How much colour the very top of the roll-off gives up.

LUMA_2020 = np.array([0.2627, 0.6780, 0.0593])
LUMA_709 = np.array([0.2126, 0.7152, 0.0722])
BT2020_TO_BT709 = np.array(
    [
        [1.660491, -0.587641, -0.072850],
        [-0.124550, 1.132900, -0.008349],
        [-0.018151, -0.100579, 1.118730],
    ]
)
RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
XYZ_TO_RGB = np.linalg.inv(RGB_TO_XYZ)
WHITE_XYZ = RGB_TO_XYZ.sum(axis=1)

CAP = 0.08  # The most any one correction may change, as a share.
FACE_BUDGET = 0.10  # The most all of them together may move face luma.
LAB_SHIFT_CAP = np.array([8.0, 4.0, 4.0])
WB_STRENGTH = 0.6
MATCH_STRENGTH = 0.5  # A full match reads bleach-bypass grey.
MIN_CONFIDENCE = 0.25
MIN_NEUTRAL_SHARE = 0.03
MIN_SKIN_PIXELS = 150
NEUTRAL_CHROMA = 24.0
FACE_BAND = (0.45, 0.75)  # Face luma where exposure is left alone.
SKIN_HUE_LIMIT = 3.0  # Degrees.
SAME_SKIN_HUE = 5.0  # Degrees. Wider than this, two clips show different people or very different light.
SAME_SKIN_CHROMA = 0.25
SCENE_LIMIT = 18.0  # Lab distance past which two backgrounds are different rooms.
SKIN_SIGMA_AB = 12.0
SKIN_SIGMA_L = 30.0
SAMPLES = 10
SAMPLE_SIDE = 640

FLAGS = "flags=bicubic+accurate_rnd+full_chroma_int+full_chroma_inp"
TAGS = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"


def hlg_inverse_oetf(signal: np.ndarray) -> np.ndarray:
    """BT.2100 HLG signal to scene light from 0 to 1."""
    e = np.asarray(signal, np.float64)
    low = e * e / 3
    high = (np.exp((e - HLG_C) / HLG_A) + HLG_B) / 12
    return np.where(e <= 0.5, low, high)


def hlg_to_nits(rgb: np.ndarray) -> np.ndarray:
    """HLG signal to display light on the nominal 1000 nit display. The OOTF works on luminance."""
    scene = hlg_inverse_oetf(rgb)
    luma = np.maximum(scene @ LUMA_2020, 1e-12)[..., None]
    return HLG_PEAK_NITS * luma ** (HLG_SYSTEM_GAMMA - 1) * scene


def pq_to_nits(signal: np.ndarray) -> np.ndarray:
    m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 107 / 128, 2413 / 128, 2392 / 128
    p = np.maximum(np.asarray(signal, np.float64), 0) ** (1 / m2)
    return 10000 * (np.maximum(p - c1, 0) / (c2 - c3 * p)) ** (1 / m1)


def tone_curve(x: np.ndarray) -> np.ndarray:
    """Identity up to the knee, then an exponential shoulder that never reaches past 1."""
    x = np.asarray(x, np.float64)
    span = 1 - KNEE
    return np.where(x <= KNEE, x, KNEE + span * (1 - np.exp(-(x - KNEE) / span)))


def _smoothstep(low: float, high: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - low) / (high - low), 0, 1)
    return t * t * (3 - 2 * t)


def hdr_to_sdr(rgb: np.ndarray, transfer: str) -> np.ndarray:
    """Coded HDR RGB to BT.709 SDR RGB. Dolby Vision Profile 8.4 is HLG underneath and takes this path."""
    if transfer not in (HLG, PQ):
        raise AltocueError(f"No HDR conversion for transfer {transfer}.", fix="Expected arib-std-b67 or smpte2084.")
    relative = hlg_to_nits(rgb) / HLG_WHITE_NITS if transfer == HLG else pq_to_nits(rgb) / PQ_WHITE_NITS
    linear = relative @ BT2020_TO_BT709.T

    # Colours outside BT.709 go negative. Pull them toward their own luminance, which keeps hue.
    luma = np.maximum(linear @ LUMA_709, 0)[..., None]
    low = linear.min(axis=-1, keepdims=True)
    pull = np.where(low < 0, luma / np.maximum(luma - low, 1e-9), 1.0)
    linear = luma + (linear - luma) * pull

    peak = np.maximum(linear.max(axis=-1, keepdims=True), 1e-9)
    mapped = linear * tone_curve(peak) / peak
    # Far above diffuse white, colours walk to white. Keeping full colour there makes speculars
    # ring in the LUT, because the level is flat while the channel ratios still swing.
    white = mapped.max(axis=-1, keepdims=True)
    mapped = mapped + (white - mapped) * HIGHLIGHT_FADE * _smoothstep(0.8, 2.5, peak)
    return np.clip(mapped, 0, 1) ** (1 / DISPLAY_GAMMA)


def identity_grid(size: int) -> np.ndarray:
    """Every LUT input in `.cube` order: red changes fastest."""
    axis = np.linspace(0, 1, size)
    blue, green, red = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack((red, green, blue), axis=-1).reshape(-1, 3)


def hdr_lut(transfer: str, size: int = HDR_LUT_SIZE) -> np.ndarray:
    return hdr_to_sdr(identity_grid(size), transfer)


def write_cube(path: str | Path, table: np.ndarray, title: str) -> Path:
    size = round(len(table) ** (1 / 3))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^\w .-]", "", title)
    head = f'TITLE "{safe_title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n'
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in np.clip(table, 0, 1).tolist())
    target.write_text(head + rows + "\n")
    return target


def read_cube(path: str | Path) -> np.ndarray:
    rows = [line.split() for line in Path(path).read_text().splitlines()]
    values = [row for row in rows if len(row) == 3 and row[0][0] in "0123456789.-"]
    return np.array(values, np.float64)


def apply_lut(table: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Tetrahedral lookup, the same interpolation the render uses, to measure a LUT without a render."""
    size = round(len(table) ** (1 / 3))
    cube = table.reshape(size, size, size, 3)
    position = np.clip(np.asarray(rgb, np.float64), 0, 1) * (size - 1)
    corner = np.minimum(np.floor(position).astype(int), size - 2)
    part = position - corner
    order = np.argsort(-part, axis=-1)
    ranked = np.take_along_axis(part, order, axis=-1)

    def at(index: np.ndarray) -> np.ndarray:
        return cube[index[..., 2], index[..., 1], index[..., 0]]

    out = at(corner) * (1 - ranked[..., 0:1])
    weights = np.concatenate((ranked[..., 0:1] - ranked[..., 1:2], ranked[..., 1:2] - ranked[..., 2:3], ranked[..., 2:3]), axis=-1)
    step = corner.copy()
    for rank in range(3):
        np.put_along_axis(step, order[..., rank : rank + 1], np.take_along_axis(step, order[..., rank : rank + 1], axis=-1) + 1, axis=-1)
        out = out + at(step) * weights[..., rank : rank + 1]
    return out


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """BT.709 signal to CIELAB, D65, through display light."""
    linear = np.clip(np.asarray(rgb, np.float64), 0, 1) ** DISPLAY_GAMMA
    xyz = linear @ RGB_TO_XYZ.T / WHITE_XYZ
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack((116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])), axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    f = np.stack((fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200), axis=-1)
    cube = f**3
    xyz = np.where(cube > 216 / 24389, cube, (116 * f - 16) * 27 / 24389) * WHITE_XYZ
    return np.clip(xyz @ XYZ_TO_RGB.T, 0, 1) ** (1 / DISPLAY_GAMMA)


def median_luma(rgb: np.ndarray) -> float:
    return float(np.median(np.asarray(rgb) @ LUMA_709))


def lab_stats(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lab = rgb_to_lab(rgb)
    return lab.mean(axis=0), np.maximum(lab.std(axis=0), 1e-3)


def median_hue(rgb: np.ndarray) -> float:
    lab = rgb_to_lab(rgb)
    return math.degrees(math.atan2(float(np.median(lab[..., 2])), float(np.median(lab[..., 1]))))


def _hue_gap(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@dataclass(frozen=True)
class Match:
    """Move colours around `mean`: scale their spread, then shift them."""

    mean: tuple[float, float, float]
    shift: tuple[float, float, float]
    scale: tuple[float, float, float]

    def apply(self, lab: np.ndarray, strength: float) -> np.ndarray:
        mean, shift, scale = np.array(self.mean), np.array(self.shift), np.array(self.scale)
        return mean + (lab - mean) * (1 + (scale - 1) * strength) + shift * strength


@dataclass(frozen=True)
class Correction:
    """One clip's change. A pure function of colour, so it bakes into a LUT."""

    gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    lift: float = 0.0
    skin_match: Match | None = None
    rest_match: Match | None = None
    skin_center: tuple[float, float, float] | None = None
    strength: float = 1.0

    @property
    def is_identity(self) -> bool:
        changed = self.gains != (1.0, 1.0, 1.0) or self.lift != 0 or self.skin_match or self.rest_match
        return not changed or self.strength == 0

    def _skin_weight(self, lab: np.ndarray) -> np.ndarray:
        if self.skin_center is None:
            return np.zeros((*lab.shape[:-1], 1))
        l0, a0, b0 = self.skin_center
        chroma = ((lab[..., 1] - a0) ** 2 + (lab[..., 2] - b0) ** 2) / SKIN_SIGMA_AB**2
        light = (lab[..., 0] - l0) ** 2 / SKIN_SIGMA_L**2
        return np.exp(-0.5 * (chroma + light))[..., None]

    def apply(self, rgb: np.ndarray, strength: float | None = None) -> np.ndarray:
        amount = self.strength if strength is None else strength
        out = np.asarray(rgb, np.float64)
        if amount == 0:
            return out
        gains = 1 + (np.array(self.gains) - 1) * amount
        if self.gains != (1.0, 1.0, 1.0):
            linear = np.clip(out, 0, 1) ** DISPLAY_GAMMA
            # Paper-white highlights stay white; only what sits below them takes the gain.
            guard = 1 - _smoothstep(0.8, 1.0, linear.max(axis=-1, keepdims=True))
            out = np.clip(linear * (1 + (gains - 1) * guard), 0, 1) ** (1 / DISPLAY_GAMMA)
        if self.lift:
            out = out * (1 + self.lift * amount * (1 - out))
        if self.skin_match or self.rest_match:
            lab = rgb_to_lab(out)
            weight = self._skin_weight(lab)
            skin = self.skin_match.apply(lab, amount) if self.skin_match else lab
            rest = self.rest_match.apply(lab, amount) if self.rest_match else lab
            out = lab_to_rgb(weight * skin + (1 - weight) * rest)
        return np.clip(out, 0, 1)


@dataclass
class ClipStats:
    """Pixels sampled from one clip, sorted by what they are."""

    clip: str
    skin: np.ndarray
    rest: np.ndarray
    neutral: np.ndarray
    face_rate: float = 0.0
    face_score: float = 0.0
    neutral_share: float = 0.0
    coded_skin: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    coded_rest: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    duration: float = 0.0

    @property
    def has_skin(self) -> bool:
        return len(self.skin) >= MIN_SKIN_PIXELS

    @property
    def skin_hue(self) -> float:
        return median_hue(self.skin)

    @property
    def confidence(self) -> float:
        faces = self.face_rate * self.face_score if self.has_skin else 0.0
        return 0.6 * faces + 0.4 * min(self.neutral_share / 0.12, 1.0)

    def corrected(self, correction: Correction) -> ClipStats:
        return replace(self, skin=correction.apply(self.skin), rest=correction.apply(self.rest), neutral=correction.apply(self.neutral))


def _face_masks(lab: np.ndarray, faces: list[Face]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Skin inside the face ovals, skin-coloured pixels anywhere, and the area around each head."""
    height, width = lab.shape[:2]
    skin = np.zeros((height, width), bool)
    alike = np.zeros((height, width), bool)
    heads = np.zeros((height, width), np.uint8)
    for face in faces:
        cx, cy = face.center
        oval = np.zeros((height, width), np.uint8)
        cv2.ellipse(
            oval, (round(cx), round(cy + face.h * 0.05)), (max(1, round(face.w * 0.36)), max(1, round(face.h * 0.42))), 0, 0, 360, 1, -1
        )
        inside = oval.astype(bool)
        if not inside.any():
            continue
        center = np.median(lab[inside], axis=0)
        gap = np.hypot(lab[..., 1] - center[1], lab[..., 2] - center[2])
        light = np.abs(lab[..., 0] - center[0])
        skin |= inside & (gap < 12) & (light < 25)
        alike |= (gap < 9) & (light < 35)
        x0, x1 = round(cx - face.w * 0.8), round(cx + face.w * 0.8)
        y0, y1 = round(face.y - face.h * 0.5), round(face.y + face.h * 1.3)
        heads[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = 1
    return skin, alike, heads.astype(bool)


def _thin(rng: np.random.Generator, count: int, limit: int) -> np.ndarray:
    return np.sort(rng.choice(count, limit, replace=False)) if count > limit else np.arange(count)


def measure_frames(clip_id: str, frames: list[np.ndarray], coded: list[np.ndarray] | None = None, *, duration: float = 0.0) -> ClipStats:
    """Sort sampled pixels into skin, the rest, and near-neutrals. `coded` holds the HDR originals."""
    rng = np.random.default_rng(0)
    pools: dict[str, list[np.ndarray]] = {"skin": [], "rest": [], "neutral": [], "coded_skin": [], "coded_rest": []}
    seen_faces, scores, neutral_count, rest_count = 0, [], 0, 0
    for index, frame in enumerate(frames):
        lab = rgb_to_lab(frame)
        faces = detect_faces((np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8))
        faces = [f for f in faces if f.area >= faces[0].area * 0.3]
        skin, alike, heads = _face_masks(lab, faces)
        rest = ~alike & ~heads
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        neutral = rest & (chroma < NEUTRAL_CHROMA) & (lab[..., 0] > 12) & (lab[..., 0] < 95)
        if skin.sum() >= 30:
            seen_faces += 1
            scores.append(faces[0].score)
        neutral_count += int(neutral.sum())
        rest_count += int(rest.sum())
        source = coded[index] if coded is not None else frame
        for name, mask, limit in (("skin", skin, 3000), ("rest", rest, 6000), ("neutral", neutral, 6000)):
            pixels = frame[mask]
            keep = _thin(rng, len(pixels), limit)
            pools[name].append(pixels[keep])
            if name != "neutral":
                pools[f"coded_{name}"].append(source[mask][keep])
    joined = {name: np.concatenate(items) if items else np.zeros((0, 3)) for name, items in pools.items()}
    return ClipStats(
        clip=clip_id,
        skin=joined["skin"],
        rest=joined["rest"],
        neutral=joined["neutral"],
        face_rate=seen_faces / max(len(frames), 1),
        face_score=float(np.median(scores)) if scores else 0.0,
        neutral_share=neutral_count / max(rest_count, 1),
        coded_skin=joined["coded_skin"],
        coded_rest=joined["coded_rest"],
        duration=duration,
    )


def white_balance(stats: ClipStats) -> tuple[float, float, float]:
    """Gray world in linear light, on the most neutral fifth of the non-skin candidates.

    A cream wall beside white trim is a colour, not a cast, and that fifth is the trim.
    """
    if stats.neutral_share < MIN_NEUTRAL_SHARE or len(stats.neutral) < 300:
        return (1.0, 1.0, 1.0)
    lab = rgb_to_lab(stats.neutral)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    greyest = stats.neutral[chroma <= np.percentile(chroma, 20)]
    mean = (greyest**DISPLAY_GAMMA).mean(axis=0)
    raw = (mean @ LUMA_709) / np.maximum(mean, 1e-6)
    gains = 1 + (raw - 1) * WB_STRENGTH
    gains = np.clip(gains / (gains @ LUMA_709), 1 - CAP, 1 + CAP)
    return (1.0, 1.0, 1.0) if np.abs(gains - 1).max() < 0.01 else (float(gains[0]), float(gains[1]), float(gains[2]))


def exposure_lift(stats: ClipStats, base: Correction) -> float:
    """Bring a face that sits outside the band to its edge. The window beside it has no vote."""
    if not stats.has_skin or stats.face_rate < 0.4:
        return 0.0
    face = median_luma(base.apply(stats.skin))
    low, high = FACE_BAND
    if low <= face <= high:
        return 0.0
    wanted = low if face < low else high
    ratio = float(np.clip(wanted / face, 1 - CAP, 1 + CAP))
    return (ratio - 1) / max(1 - face, 0.05)


def _match(source: np.ndarray, reference: np.ndarray) -> Match:
    mean, spread = lab_stats(source)
    ref_mean, ref_spread = lab_stats(reference)
    shift = np.clip((ref_mean - mean) * MATCH_STRENGTH, -LAB_SHIFT_CAP, LAB_SHIFT_CAP)
    scale = np.clip(1 + (ref_spread / spread - 1) * MATCH_STRENGTH, 1 - CAP, 1 + CAP)
    return Match(tuple(mean.tolist()), tuple(shift.tolist()), tuple(scale.tolist()))


def back_off(correction: Correction, skin: np.ndarray, limit: float = SKIN_HUE_LIMIT) -> tuple[float, float]:
    """The largest strength that keeps skin hue within `limit` degrees and face luma within budget.

    Each step has its own cap, but they stack, so the face is checked once more at the end.
    """
    hue, luma = median_hue(skin), median_luma(skin)
    for strength in (1.0, 0.85, 0.7, 0.55, 0.4, 0.25, 0.1):
        changed = correction.apply(skin, strength)
        moved = _hue_gap(hue, median_hue(changed))
        if moved <= limit and abs(median_luma(changed) / max(luma, 1e-3) - 1) <= FACE_BUDGET:
            return strength, moved
    return 0.0, 0.0


def _median_chroma(skin: np.ndarray) -> float:
    lab = np.median(rgb_to_lab(skin), axis=0)
    return float(np.hypot(lab[1], lab[2]))


def same_skin(a: ClipStats, b: ClipStats) -> bool:
    """Could these be one person? Pulling one person's skin toward another's is never a correction."""
    if not a.has_skin or not b.has_skin:
        return False
    chroma = _median_chroma(a.skin) / max(_median_chroma(b.skin), 1e-3)
    return _hue_gap(a.skin_hue, b.skin_hue) <= SAME_SKIN_HUE and abs(chroma - 1) <= SAME_SKIN_CHROMA


def solve_clip(stats: ClipStats, reference: ClipStats | None = None) -> Correction:
    """White balance, then exposure, then a match to the reference take, then the skin check."""
    if stats.confidence < MIN_CONFIDENCE:
        return Correction()
    correction = Correction(gains=white_balance(stats))
    correction = replace(correction, lift=exposure_lift(stats, correction))
    if reference is not None:
        skin_match, rest_match, center = None, None, None
        if stats.has_skin:
            center = tuple(rgb_to_lab(correction.apply(stats.skin)).mean(axis=0).tolist())
        if same_skin(stats, reference):
            skin_match = _match(correction.apply(stats.skin), reference.skin)
        if len(stats.rest) >= 300 and len(reference.rest) >= 300:
            source = correction.apply(stats.rest)
            if np.linalg.norm(lab_stats(source)[0] - lab_stats(reference.rest)[0]) <= SCENE_LIMIT:
                rest_match = _match(source, reference.rest)
        correction = replace(correction, skin_match=skin_match, rest_match=rest_match, skin_center=center)
    if stats.has_skin:
        correction = replace(correction, strength=back_off(correction, stats.skin)[0])
    return correction


def apply_look(rgb: np.ndarray, look: str) -> np.ndarray:
    """A small taste on top of the correction. Every look is a few lines of plain maths."""
    if look == "natural":
        return rgb
    out = np.asarray(rgb, np.float64)
    if look == "filmic":
        out = 0.03 + 0.95 * (out + 0.06 * (out - 0.5) * 4 * out * (1 - out))
    lab = rgb_to_lab(out)
    light = lab[..., 0] / 100
    mid = 4 * light * (1 - light)
    if look == "warm":
        lab[..., 1] += 0.7 * mid
        lab[..., 2] += 3.0 * mid
    elif look == "cool":
        lab[..., 1] -= 0.3 * mid
        lab[..., 2] -= 3.0 * mid
    elif look == "punchy":
        lab[..., 0] = 100 * np.clip(light + 0.16 * (light - 0.5) * mid, 0, 1)
        lab[..., 1:] *= 1.08
    elif look == "filmic":
        lab[..., 1:] *= (1 - 0.12 * light)[..., None]
        lab[..., 1] += 0.5 * (light - 0.5) * mid
        lab[..., 2] += 2.5 * (light - 0.5) * mid
    else:
        raise AltocueError(f"Unknown look {look}.", fix=f"Use one of {', '.join(LOOKS)}.")
    return lab_to_rgb(lab)


def _escape(path: Path) -> str:
    """Quote a path for both levels of ffmpeg's filter parser."""
    value = re.sub(r"([\\':])", r"\\\1", str(path))
    return re.sub(r"([\\'\[\],;])", r"\\\1", value)


def video_filter(clip: Clip, lut_path: str | Path | None = None, *, geometry: str = "") -> str:
    """The exact chain for one clip, ending in tagged BT.709 yuv420p ready to encode.

    The LUT runs on 16-bit RGB and the drop to 8 bits dithers, so 10-bit sources do not band.
    Put crop and scale in `geometry` and they run before that drop.
    """
    if clip.is_hdr and lut_path is None:
        raise AltocueError(f"{clip.id} is HDR and has no LUT.", fix="Run `altocue grade` first.")
    source = f"in_color_matrix={source_matrix(clip)}:in_range={source_range(clip)}"
    target = "out_color_matrix=bt709:out_range=tv"
    if lut_path is None and not geometry:
        return f"scale={source}:{target}:{FLAGS},format=yuv420p,{TAGS}"
    parts = [f"scale={source}:{FLAGS}", "format=gbrp16le"]
    if lut_path is not None:
        parts.append(f"lut3d=file={_escape(Path(lut_path).resolve())}:interp=tetrahedral")
    if geometry:
        parts.append(geometry)
    return ",".join([*parts, f"scale={target}:{FLAGS}", "format=yuv420p", TAGS])


def clip_lut(project: Project, clip: Clip) -> Path | None:
    path = project.lut_dir / f"{clip.id}.cube"
    return path if path.exists() else None


def _measure(skin: np.ndarray, rest: np.ndarray) -> dict[str, float | None]:
    pixels = np.concatenate((skin, rest)) if len(skin) else rest
    luma = pixels @ LUMA_709
    lab = rgb_to_lab(pixels)
    rest_lab = rgb_to_lab(rest).mean(axis=0) if len(rest) else np.zeros(3)
    result: dict[str, float | None] = {
        "luma_p2": float(np.percentile(luma, 2)),
        "luma_p98": float(np.percentile(luma, 98)),
        "chroma": float(np.hypot(lab[..., 1], lab[..., 2]).mean()),
        "rest_l": float(rest_lab[0]),
        "rest_a": float(rest_lab[1]),
        "rest_b": float(rest_lab[2]),
        "face_luma": None,
        "skin_hue": None,
        "skin_chroma": None,
    }
    if len(skin) >= MIN_SKIN_PIXELS:
        skin_lab = np.median(rgb_to_lab(skin), axis=0)
        result.update(face_luma=median_luma(skin), skin_hue=median_hue(skin), skin_chroma=float(np.hypot(skin_lab[1], skin_lab[2])))
    return {key: None if value is None else round(value, 3) for key, value in result.items()}


def _kept_ranges(project: Project) -> dict[str, list[tuple[float, float]]]:
    if not project.edl_path.exists():
        return {}
    ranges: dict[str, list[tuple[float, float]]] = {}
    for segment in project.edl().segments:
        ranges.setdefault(segment.clip, []).append((segment.start, segment.end))
    return ranges


def sample_times(clip: Clip, spans: list[tuple[float, float]] | None, count: int = SAMPLES) -> list[float]:
    """Even steps through what survives the cut, or the whole clip before there is a cut."""
    spans = spans or [(0.0, clip.duration)]
    total = sum(end - start for start, end in spans)
    times = []
    for i in range(count):
        offset = total * (i + 0.5) / count
        for start, end in spans:
            if offset <= end - start:
                times.append(start + offset)
                break
            offset -= end - start
    return times


def _read_clip(clip: Clip, spans: list[tuple[float, float]] | None) -> ClipStats:
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = [f for f in pool.map(lambda at: read_rgb(clip, at, SAMPLE_SIDE), sample_times(clip, spans)) if f is not None]
    if not frames:
        raise AltocueError(
            f"Could not read any frame of {clip.id}.", fix=f"Check that {clip.path} plays. If it lives in iCloud, download it first."
        )
    if clip.is_hdr:
        sdr = [hdr_to_sdr(f, clip.transfer) for f in frames]
        stats = measure_frames(clip.id, sdr, frames)
    else:
        stats = measure_frames(clip.id, frames)
    stats.duration = sum(end - start for start, end in spans) if spans else clip.duration
    return stats


def _pick_reference(stats: dict[str, ClipStats]) -> str:
    with_faces = [s for s in stats.values() if s.has_skin and s.face_rate >= 0.5]
    return max(with_faces or list(stats.values()), key=lambda s: s.duration).clip


def match_targets(stats: dict[str, ClipStats]) -> dict[str, str]:
    """Which take each clip matches: the longest clip with the same skin, else the main reference."""
    main = _pick_reference(stats)
    leaders: list[ClipStats] = []
    targets = {}
    if stats[main].has_skin:
        leaders.append(stats[main])
    for sample in sorted(stats.values(), key=lambda s: s.duration, reverse=True):
        if not sample.has_skin:
            targets[sample.clip] = main
            continue
        leader = next((other for other in leaders if same_skin(sample, other)), None)
        if leader is None:
            leaders.append(sample)
            leader = sample
        targets[sample.clip] = leader.clip
    return targets


def _bake(project: Project, clip: Clip, correction: Correction, look: str) -> np.ndarray:
    """One `.cube` per clip, always, so the plate stage has one rule for every clip."""
    path = project.lut_dir / f"{clip.id}.cube"
    if clip.is_hdr and correction.is_identity and look == "natural":
        table = hdr_lut(clip.transfer)
    else:
        grid = identity_grid(LUT_SIZE)
        table = apply_look(correction.apply(hdr_to_sdr(grid, clip.transfer) if clip.is_hdr else grid), look)
    write_cube(path, table, f"Altocue {clip.id}")
    return table


def _changes(clip: Clip, correction: Correction, stats: ClipStats | None) -> dict[str, Any]:
    moved = 0.0
    if stats is not None and stats.has_skin and not correction.is_identity:
        moved = _hue_gap(median_hue(stats.skin), median_hue(correction.apply(stats.skin)))

    def show(match: Match | None) -> dict[str, list[float]] | None:
        return {"shift": [round(v, 3) for v in match.shift], "scale": [round(v, 3) for v in match.scale]} if match else None

    return {
        "hdr": clip.transfer if clip.is_hdr else None,
        "gains": [round(g, 4) for g in correction.gains],
        "lift": round(correction.lift, 4),
        "skin_match": show(correction.skin_match),
        "rest_match": show(correction.rest_match),
        "strength": correction.strength,
        "skin_hue_moved": round(moved, 2),
    }


def _stamp(project: Project, clips: list[Clip], look: str, enabled: bool, ranges: dict[str, list[tuple[float, float]]]) -> str:
    files = []
    for clip in clips:
        if not Path(clip.path).exists():
            raise AltocueError(
                f"{clip.id} points at a missing file: {clip.path}.", fix="Restore the source file, then run `altocue ingest`."
            )
        info = Path(clip.path).stat()
        files.append([clip.id, clip.path, info.st_size, info.st_mtime_ns, ranges.get(clip.id)])
    text = json.dumps({"version": VERSION, "look": look, "enabled": enabled, "files": files}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def grade_project(project: Project, force: bool = False, look: str | None = None) -> dict[str, Any]:
    """Bake one LUT per clip and write `work/luts/report.json`."""
    chosen = look or project.setting("grade", "look", "natural")
    if chosen not in LOOKS:
        raise AltocueError(f"Unknown look {chosen}.", fix=f"Use one of {', '.join(LOOKS)}.")
    enabled = bool(project.setting("grade", "enabled", True))
    clips = project.clips()
    ranges = _kept_ranges(project)
    stamp = _stamp(project, clips, chosen, enabled, ranges)
    report_path = project.lut_dir / "report.json"
    if not force and report_path.exists():
        saved = json.loads(report_path.read_text())
        if saved.get("stamp") == stamp and all((project.lut_dir / f"{c.id}.cube").exists() for c in clips if c.is_hdr or enabled):
            return saved

    project.lut_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"stamp": stamp, "version": VERSION, "look": chosen if enabled else "natural", "reference": None, "clips": {}}
    if not enabled:
        for clip in clips:
            (project.lut_dir / f"{clip.id}.cube").unlink(missing_ok=True)  # an older grade must not keep applying
            if clip.is_hdr:
                write_cube(project.lut_dir / f"{clip.id}.cube", hdr_lut(clip.transfer), f"Altocue {clip.id}")
                report["clips"][clip.id] = {"lut": f"{clip.id}.cube", "changes": _changes(clip, Correction(), None)}
        write_json(report_path, report)
        return report

    stats = {clip.id: _read_clip(clip, ranges.get(clip.id)) for clip in clips}
    reference_id = _pick_reference(stats)
    report["reference"] = reference_id
    targets = match_targets(stats)
    leaders = {target: solve_clip(stats[target]) for target in set(targets.values())}
    corrected = {target: stats[target].corrected(correction) for target, correction in leaders.items()}
    for clip in clips:
        sample = stats[clip.id]
        target = targets[clip.id]
        correction = leaders[clip.id] if target == clip.id else solve_clip(sample, corrected[target])
        table = _bake(project, clip, correction, chosen)
        after_skin = apply_lut(table, sample.coded_skin) if len(sample.coded_skin) else sample.coded_skin
        after = _measure(after_skin, apply_lut(table, sample.coded_rest))
        entry: dict[str, Any] = {
            "lut": f"{clip.id}.cube",
            "reference": clip.id == reference_id,
            "matched_to": None if target == clip.id else target,
            "confidence": round(sample.confidence, 3),
            "faces": {"rate": round(sample.face_rate, 3), "score": round(sample.face_score, 3)},
            "before": _measure(sample.skin, sample.rest),
            "after": after,
            "changes": _changes(clip, correction, sample),
        }
        if clip.is_hdr:
            entry["plain_conversion"] = _measure(sample.coded_skin, sample.coded_rest)
        report["clips"][clip.id] = entry
    write_json(report_path, report)
    return json.loads(report_path.read_text())


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--force", action="store_true", help="Solve again even if nothing changed")
    parser.add_argument("--look", choices=LOOKS, help="Overrides [grade] look")


def _summary(clip_id: str, entry: dict[str, Any]) -> str:
    changes = entry["changes"]
    notes = []
    if changes.get("hdr"):
        notes.append("HDR to SDR")
    before, after = entry.get("before", {}), entry.get("after", {})
    if before.get("face_luma") is not None:
        notes.append(f"face {before['face_luma']:.2f} to {after['face_luma']:.2f}")
    if "gains" in changes and changes["gains"] != [1.0, 1.0, 1.0]:
        notes.append("white balance " + " ".join(f"{g:.3f}" for g in changes["gains"]))
    if changes.get("strength", 1) < 1:
        notes.append(f"backed off to {changes['strength']:.0%} for skin")
    if entry.get("reference"):
        notes.append("reference")
    return f"{clip_id}: {', '.join(notes) or 'no change'}"


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    report = grade_project(project, force=args.force, look=args.look)
    for clip_id, entry in report["clips"].items():
        print(_summary(clip_id, entry))
    print(f"Graded {len(report['clips'])} clips, look {report['look']}. Report: {project.lut_dir / 'report.json'}")
    return 0
