"""Log footage: a camera's log curve to scene light, its primaries to BT.709, and one neutral render to SDR.

A log clip is a measurement of the scene, not a picture. Shown as it is, it is grey and flat, and every later reading
(face level, white balance, skin hue) is a number about the encoding. So it becomes a normal picture before the
correction, in the place HDR is converted today, as part of the clip's one cube.

Every curve and every set of primaries is from its maker's published document, named beside it. The numbers were
compared with colour-science (BSD-3-Clause), which Altocue does not depend on. Nothing here is a maker's LUT, and the
render will not match one, because those carry the maker's look as well. It gives the correction and the looks a
normal picture to work on.

Nothing here guesses. No log curve has a tag that ffprobe prints, so `auto` converts HDR by its tags and leaves the
rest alone. A flat picture, or tags like Apple Log's, is a hint: the grade says so and names the setting.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache, lru_cache
from typing import Any

import numpy as np

from . import ffmpeg
from .errors import AltocueError
from .frame import read_rgb
from .grade import LUMA_709, _smoothstep, rgb_to_lab
from .models import Clip
from .project import Project

Decode = Callable[[np.ndarray], np.ndarray]

SHOULDER = 0.6  # Scene light where the roll-off starts. A face one stop over grey (0.36) sits below it and passes untouched.
HIGHLIGHT_FADE = 0.85  # How much colour the very top gives up, as the HDR conversion does.
FLAT_BLACK, FLAT_WHITE = 0.07, 0.80  # A rule of thumb: an unconverted log picture reaches neither black nor white,
FLAT_CHROMA = 20.0  # and has little colour. Mean CIELAB chroma. A hint to ask about, never a reason to convert.
HINT_SIDE = 320  # One small frame from the middle of the clip is enough to ask the question.
UNTAGGED = ("", "unknown", "unspecified")


def _log10(x: np.ndarray) -> np.ndarray:
    return np.log10(np.maximum(x, 1e-12))


# Apple Log. "Apple Log Profile White Paper", Apple, September 2023. The curve takes the decoded signal from 0 to 1.
APPLE_R0, APPLE_RT, APPLE_C, APPLE_BETA, APPLE_GAMMA, APPLE_DELTA = -0.05641088, 0.01, 47.28711236, 0.00964052, 0.08550479, 0.69336945
APPLE_PT = APPLE_C * (APPLE_RT - APPLE_R0) ** 2


def apple_log_decode(signal: np.ndarray) -> np.ndarray:
    p = np.asarray(signal, np.float64)
    low = np.sqrt(np.maximum(p, 0) / APPLE_C) + APPLE_R0
    return np.where(p >= APPLE_PT, 2.0 ** ((p - APPLE_DELTA) / APPLE_GAMMA) - APPLE_BETA, low)


def apple_log_encode(light: np.ndarray) -> np.ndarray:
    r = np.asarray(light, np.float64)
    low = APPLE_C * np.maximum(r - APPLE_R0, 0) ** 2
    return np.where(r >= APPLE_RT, APPLE_GAMMA * np.log2(np.maximum(r + APPLE_BETA, 1e-12)) + APPLE_DELTA, low)


# Sony S-Log3. "Technical Summary for S-Gamut3.Cine/S-Log3 and S-Gamut3/S-Log3", Sony. It prints the 10 bit code
# values this must reproduce: black 95, 18% grey 420, 90% white 598.
SLOG3_CUT = 171.2102946929


def slog3_decode(code: np.ndarray) -> np.ndarray:
    value = np.asarray(code, np.float64) * 1023
    return np.where(value >= SLOG3_CUT, 10.0 ** ((value - 420) / 261.5) * 0.19 - 0.01, (value - 95) * 0.01125 / (SLOG3_CUT - 95))


def slog3_encode(light: np.ndarray) -> np.ndarray:
    x = np.asarray(light, np.float64)
    return np.where(x >= 0.01125, 420 + _log10((x + 0.01) / 0.19) * 261.5, x * (SLOG3_CUT - 95) / 0.01125 + 95) / 1023


# Panasonic V-Log. "V-Log/V-Gamut Reference Manual", Panasonic, 2014. Its printed code values: black 128, grey 433, white 602.
VLOG_B, VLOG_C, VLOG_D = 0.00873, 0.241514, 0.598206


def vlog_decode(code: np.ndarray) -> np.ndarray:
    v = np.asarray(code, np.float64)
    return np.where(v < 0.181, (v - 0.125) / 5.6, 10.0 ** ((v - VLOG_D) / VLOG_C) - VLOG_B)


def vlog_encode(light: np.ndarray) -> np.ndarray:
    x = np.asarray(light, np.float64)
    return np.where(x < 0.01, 5.6 * x + 0.125, VLOG_C * _log10(x + VLOG_B) + VLOG_D)


# Canon Log 3. "Canon Log Gamma Curves", Canon, with the constants of its 2020 revision. Canon writes the curve on its own
# scale, where 0.9 is 90% reflectance, so the 0.9 here turns that into scene light with 18% grey at 0.18.
CLOG3_SLOPE, CLOG3_GAIN = 0.36726845, 14.98325


def clog3_decode(code: np.ndarray) -> np.ndarray:
    v = np.asarray(code, np.float64)
    low = -(10.0 ** ((0.12783901 - v) / CLOG3_SLOPE) - 1) / CLOG3_GAIN
    high = (10.0 ** ((v - 0.12240537) / CLOG3_SLOPE) - 1) / CLOG3_GAIN
    return 0.9 * np.where(v < 0.097465473, low, np.where(v <= 0.15277891, (v - 0.12512219) / 1.9754798, high))


def clog3_encode(light: np.ndarray) -> np.ndarray:
    x = np.asarray(light, np.float64) / 0.9
    low = -CLOG3_SLOPE * _log10(-x * CLOG3_GAIN + 1) + 0.12783901
    high = CLOG3_SLOPE * _log10(x * CLOG3_GAIN + 1) + 0.12240537
    return np.where(x < -0.014, low, np.where(x <= 0.014, 1.9754798 * x + 0.12512219, high))


# Fujifilm F-Log2. "F-Log2 Data Sheet Ver.1.0", Fujifilm, 2022.
FLOG2_A, FLOG2_B, FLOG2_C, FLOG2_D, FLOG2_E, FLOG2_F = 5.555556, 0.064829, 0.245281, 0.384316, 8.799461, 0.092864


def flog2_decode(code: np.ndarray) -> np.ndarray:
    v = np.asarray(code, np.float64)
    return np.where(v < 0.100686685370811, (v - FLOG2_F) / FLOG2_E, (10.0 ** ((v - FLOG2_D) / FLOG2_C) - FLOG2_B) / FLOG2_A)


def flog2_encode(light: np.ndarray) -> np.ndarray:
    x = np.asarray(light, np.float64)
    return np.where(x < 0.000889, FLOG2_E * x + FLOG2_F, FLOG2_C * _log10(FLOG2_A * x + FLOG2_B) + FLOG2_D)


# DJI D-Log. "White Paper on D-Log and D-Gamut of DJI Cinema Color System", DJI, 2017. This is not D-Log M, whose
# formula DJI has not published.
def dlog_decode(code: np.ndarray) -> np.ndarray:
    v = np.asarray(code, np.float64)
    return np.where(v <= 0.14, (v - 0.0929) / 6.025, (10.0 ** (3.89616 * v - 2.27752) - 0.0108) / 0.9892)


def dlog_encode(light: np.ndarray) -> np.ndarray:
    x = np.asarray(light, np.float64)
    return np.where(x <= 0.0078, 6.025 * x + 0.0929, _log10(x * 0.9892 + 0.0108) * 0.256663 + 0.584555)


# Chromaticities of red, green and blue, as each maker publishes them. All of these use the D65 white point.
D65 = (0.3127, 0.3290)
PRIMARIES: dict[str, tuple[tuple[float, float], ...]] = {
    "bt709": ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
    "bt2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),  # ITU-R BT.2020. Apple Log and Fujifilm's F-Gamut use these.
    "s_gamut3_cine": ((0.766, 0.275), (0.225, 0.800), (0.089, -0.087)),  # Sony, the same technical summary
    "v_gamut": ((0.730, 0.280), (0.165, 0.840), (0.100, -0.030)),  # Panasonic, the same reference manual
    "cinema_gamut": ((0.740, 0.270), (0.170, 1.140), (0.080, -0.100)),  # Canon
    "d_gamut": ((0.710, 0.310), (0.210, 0.880), (0.090, -0.080)),  # DJI, the same white paper
}


def _to_xyz(gamut: str) -> np.ndarray:
    """Linear RGB in these primaries to CIE XYZ, with white at D65."""

    def xyz(point: tuple[float, float]) -> list[float]:
        return [point[0] / point[1], 1.0, (1 - point[0] - point[1]) / point[1]]

    colours = np.array([xyz(point) for point in PRIMARIES[gamut]]).T
    return colours * np.linalg.solve(colours, np.array(xyz(D65)))


@cache
def to_bt709(gamut: str) -> np.ndarray:
    """The matrix from a camera's linear RGB to linear BT.709. Each row sums to 1, so white stays white."""
    return np.linalg.solve(_to_xyz("bt709"), _to_xyz(gamut))


@dataclass(frozen=True)
class Curve:
    """One camera log format: its curve both ways, and the primaries it is usually recorded with."""

    name: str
    gamut: str
    decode: Decode
    encode: Decode
    on_code_values: bool = True  # Camera makers write the curve on the 10 bit code value over 1023. Apple writes it on the signal.


CURVES: dict[str, Curve] = {
    "apple_log": Curve("Apple Log", "bt2020", apple_log_decode, apple_log_encode, on_code_values=False),
    "slog3": Curve("Sony S-Log3 with S-Gamut3.Cine", "s_gamut3_cine", slog3_decode, slog3_encode),
    "vlog": Curve("Panasonic V-Log with V-Gamut", "v_gamut", vlog_decode, vlog_encode),
    "clog3": Curve("Canon Log 3 with Cinema Gamut", "cinema_gamut", clog3_decode, clog3_encode),
    "flog2": Curve("Fujifilm F-Log2 with F-Gamut", "bt2020", flog2_decode, flog2_encode),
    "dlog": Curve("DJI D-Log with D-Gamut", "d_gamut", dlog_decode, dlog_encode),
}
INPUTS = ("auto", "video", *CURVES)


def code_value(signal: np.ndarray, full_range: bool) -> np.ndarray:
    """Put a decoded frame back on the 10 bit code value scale the camera formulas use.

    ffmpeg decodes a limited range file so that code 64 is 0 and code 940 is 1. The formulas want the code over 1023.
    """
    signal = np.asarray(signal, np.float64)
    return signal if full_range else (signal * 876 + 64) / 1023


def bt709_oetf(light: np.ndarray) -> np.ndarray:
    """The BT.709 camera curve. With a 2.4 display after it, this is the mild contrast ordinary video has."""
    x = np.maximum(np.asarray(light, np.float64), 0)
    return np.where(x < 0.018, 4.5 * x, 1.099 * x**0.45 - 0.099)


def shoulder(light: np.ndarray) -> np.ndarray:
    """Straight to the knee, then a roll-off that nears 1 and never reaches it. The slope is 1 on both sides of the knee."""
    span = 1 - SHOULDER
    over = np.maximum(np.asarray(light, np.float64) - SHOULDER, 0) / span
    return np.where(light <= SHOULDER, light, SHOULDER + span * over / (1 + over))


def scene_to_sdr(light: np.ndarray, gamut: str = "bt709") -> np.ndarray:
    """Scene light in a camera's primaries, with 18% grey at 0.18, to the BT.709 picture.

    Built from published parts: the gamut matrix, the hue-keeping pull the HDR conversion uses, a highlight shoulder and
    the BT.709 camera curve. Grey lands at 0.41, where ordinary video puts it, so log clips sit beside phone clips.
    """
    linear = np.asarray(light, np.float64) @ to_bt709(gamut).T
    luma = np.maximum(linear @ LUMA_709, 0)[..., None]
    low = linear.min(axis=-1, keepdims=True)
    linear = luma + (linear - luma) * np.where(low < 0, luma / np.maximum(luma - low, 1e-9), 1.0)  # outside BT.709: toward its own luminance
    peak = np.maximum(linear.max(axis=-1, keepdims=True), 1e-9)
    mapped = linear * shoulder(peak) / peak
    white = mapped.max(axis=-1, keepdims=True)
    mapped = mapped + (white - mapped) * HIGHLIGHT_FADE * _smoothstep(1.0, 8.0, peak)  # far above white, colour walks to white
    return bt709_oetf(np.clip(mapped, 0, 1))


def log_to_sdr(signal: np.ndarray, curve: str, *, full_range: bool = False) -> np.ndarray:
    """Coded log RGB, as ffmpeg decoded it, to the BT.709 picture. `full_range` says how ffmpeg read the file."""
    chosen = CURVES[curve]
    coded = code_value(signal, full_range) if chosen.on_code_values else np.asarray(signal, np.float64)
    return scene_to_sdr(chosen.decode(coded), chosen.gamut)


@lru_cache(maxsize=256)
def _flagged_full(path: str) -> bool:
    streams = ffmpeg.probe_json(path).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return video.get("color_range") in ("pc", "jpeg")


def full_range(clip: Clip) -> bool:
    """How ffmpeg reads this file: full range when its format or its flag says so, and limited when nothing does."""
    return clip.pix_fmt.startswith(("yuvj", "gbr", "rgb")) or _flagged_full(clip.path)


def to_sdr(signal: np.ndarray, clip: Clip) -> np.ndarray:
    """One frame, or one LUT grid, of a clip whose `input` names its log curve."""
    return log_to_sdr(signal, clip.input, full_range=full_range(clip))


def _fix() -> str:
    return f'Under [grade] in altocue.toml use input = "auto", "video" (not log), or one curve: {", ".join(CURVES)}. For some clips only: input = {{ a = "slog3" }}.'


def asked(project: Project) -> str | dict[str, str]:
    """What `[grade] input` says, checked. One word for every clip, or a table that names clips."""
    raw: Any = project.setting("grade", "input", "auto")
    values = list(raw.values()) if isinstance(raw, dict) else [raw]
    for value in values:
        if not isinstance(value, str) or value not in INPUTS:
            raise AltocueError(f"[grade] input cannot be {value!r}.", fix=_fix())
    return raw


def mark(project: Project) -> str:
    """What the setting adds to the grade's cache mark. Nothing while it says auto, so an older grade stays good."""
    chosen = asked(project)
    return "" if chosen == "auto" else "input:" + json.dumps(chosen, sort_keys=True)


def tagged(project: Project, clips: list[Clip]) -> list[Clip]:
    """Each clip with the log curve it is to be read with in `input`. Empty means none: HDR by its tags, or ordinary video.

    A file's own tags come first. HDR is tagged and log is not, so one word for the whole project leaves HDR clips on
    their own path. Naming a tagged HDR clip in the table is a contradiction, and is refused.
    """
    chosen = asked(project)
    if isinstance(chosen, dict):
        known = {clip.id for clip in clips}
        unknown = sorted(set(chosen) - known)
        if unknown:
            raise AltocueError(f"[grade] input names {', '.join(unknown)}, and no clip has that id.", fix=f"The clips are {', '.join(sorted(known))}.")
    out = []
    for clip in clips:
        curve = chosen.get(clip.id, "auto") if isinstance(chosen, dict) else chosen
        if curve in CURVES and clip.is_hdr and isinstance(chosen, dict):
            fix = f"Take {clip.id} out of [grade] input. Its own tags settle it."
            raise AltocueError(f"{clip.id} is tagged {clip.transfer}, which is HDR, and [grade] input calls it {curve}.", fix=fix)
        out.append(replace(clip, input=curve if curve in CURVES and not clip.is_hdr else ""))
    return out


def bit_depth(clip: Clip) -> int:
    found = re.search(r"(\d+)(?:le|be)$", clip.pix_fmt)
    return int(found.group(1)) if found else 8


def flat(pixels: np.ndarray) -> str:
    """What makes a picture look like unconverted log, in words, or nothing. Every log curve keeps black well above 0
    and white well below 1, and a wide gamut read as BT.709 has little colour."""
    luma = np.asarray(pixels, np.float64) @ LUMA_709
    dark, bright = np.percentile(luma, [0.5, 99.5])
    lab = rgb_to_lab(pixels)
    if dark > FLAT_BLACK and bright < FLAT_WHITE and np.hypot(lab[..., 1], lab[..., 2]).mean() < FLAT_CHROMA:
        return f"a flat picture, with nothing darker than {dark:.2f} or brighter than {bright:.2f} and little colour"
    return ""


def hint(project: Project, clip: Clip) -> str:
    """Why this clip may be log footage, in words for the grade summary. Empty when nothing suggests it.

    A hint is never proof: Apple Log is tagged like any other 10 bit BT.2020 file with no transfer function, and a
    foggy morning is flat too. Only files of 10 bits or more are looked at, because that is how log is recorded and
    an ordinary 8 bit clip of someone against a plain wall is flat as well. The frame is read here, whole, since the
    grade's own samples are sorted by a face search that an unconverted log picture confuses. Once the setting says
    what the clip is, a curve or `video`, there is nothing left to hint at.
    """
    chosen = asked(project)
    if clip.is_hdr or clip.input or (chosen.get(clip.id, "auto") if isinstance(chosen, dict) else chosen) != "auto" or bit_depth(clip) < 10:
        return ""
    reasons = []
    if clip.primaries == "bt2020" and clip.transfer in UNTAGGED:
        reasons.append(f"{bit_depth(clip)} bit BT.2020 with no transfer function, as Apple Log is tagged")
    frame = read_rgb(clip, clip.duration / 2, HINT_SIDE)
    reasons.append(flat(frame.reshape(-1, 3)) if frame is not None else "")
    if not any(reasons):
        return ""
    reasons = [reason for reason in reasons if reason]
    seen, curves = "; ".join(reasons), ", ".join(CURVES)
    return f'may be log footage ({seen}). If it is, set input under [grade] to its curve ({curves}). If it is not, input = "video" stops this note'
