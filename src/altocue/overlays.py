"""Film grain, light leak, dust, vignette and paper, all made here. Nothing is bundled.

Each goes on the picture after inserts and before the graphics, so captions stay clean. Grain,
dust and paper are grey plates laid over luma with hardlight, which leaves mid grey alone and
works hardest in the mids, as film does, so colour is untouched. A plate is built once and looped.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from . import ffmpeg
from .errors import AltocueError
from .project import Project

KINDS = ("grain", "light_leak", "dust", "vignette", "paper")
DEFAULT_AMOUNT = 0.25
VERSION = 1
SEED = 4177
LEAK_SECONDS = 8  # One slow drift, then it loops without a seam.
LEAK_SCALE = 8
DUST_SCALE = 2
LEAK_FADE = 0.3
GREY = 128
TO_RGB = "scale=in_color_matrix=bt709:in_range=tv:flags=bicubic+accurate_rnd+full_chroma_int+full_chroma_inp,format=gbrp"
TO_YUV = "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p"


def parse(raw: Any) -> list[dict[str, Any]]:
    """`{"type": "grain", "amount": 0.2}` or a list of them. They stack in the order written."""
    if raw is None:
        return []
    items = []
    for entry in raw if isinstance(raw, list) else [raw]:
        kind = entry.get("type") if isinstance(entry, dict) else None
        if kind not in KINDS:
            raise AltocueError(f"Unknown overlay {entry!r}.", fix=f'Use {{"type": "grain", "amount": 0.2}}. Types: {", ".join(KINDS)}.')
        amount = float(entry.get("amount", DEFAULT_AMOUNT))
        if not 0 <= amount <= 1:
            raise AltocueError(f"An overlay amount of {amount:g} is not between 0 and 1.", fix="Most films want 0.1 to 0.3.")
        items.append({**entry, "amount": amount})
    return items


def _plate(project: Project, name: str, frames: list[np.ndarray], fps: int) -> str:
    """Grey frames as yuv420p with no loss, so mid grey stays exactly neutral through the blend."""
    folder = project.work / "overlays"
    folder.mkdir(parents=True, exist_ok=True)
    height, width = frames[0].shape
    dest = folder / f"{name}.mkv"
    raw = dest.with_suffix(".yuv")
    side = ((width + 1) // 2) * ((height + 1) // 2)
    with raw.open("wb") as handle:
        for frame in frames:
            handle.write(frame.astype(np.uint8).tobytes())
            handle.write(bytes([GREY]) * (side * 2))
    args = ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{width}x{height}", "-r", str(fps), "-i", str(raw)]
    ffmpeg.run([*args, "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", str(dest)])
    raw.unlink()
    return str(dest)


def _even(value: float) -> int:
    return max(2, int(value / 2) * 2)


def dust_frames(width: int, height: int, count: int) -> list[np.ndarray]:
    """Specks and a few hairs, new every frame. Dirt on a print is dark, and on a negative it is bright."""
    rng = np.random.default_rng(SEED)
    frames = []
    reach = max(width, height)
    for _ in range(count):
        frame = np.full((height, width), GREY, np.uint8)
        for _ in range(int(rng.integers(3, 9))):
            level = int(rng.choice([24, 232]))
            center = (int(rng.integers(0, width)), int(rng.integers(0, height)))
            cv2.circle(frame, center, int(rng.integers(1, max(2, reach // 480) + 2)), level, -1, cv2.LINE_AA)
        if rng.random() < 0.3:
            points = np.cumsum(rng.normal(0, reach / 90, (7, 2)), axis=0) + (rng.integers(0, width), rng.integers(0, height))
            cv2.polylines(frame, [points.astype(np.int32)], False, int(rng.choice([24, 232])), 1, cv2.LINE_AA)
        frames.append(frame)
    return frames


def paper_frame(width: int, height: int) -> np.ndarray:
    """Soft mottle at three sizes with short fibres through it."""
    rng = np.random.default_rng(SEED)
    scale = max(width, height) / 1920
    field = np.zeros((height, width), np.float32)
    for sigma, weight in ((1.2, 0.6), (5.0, 0.9), (24.0, 1.0)):
        layer = cv2.GaussianBlur(rng.normal(0, 1, (height, width)).astype(np.float32), (0, 0), sigma * max(scale, 0.25))
        field += weight * layer / max(float(layer.std()), 1e-6)
    fibres = np.zeros((height, width), np.float32)
    for _ in range(int(2600 * (width * height) / (1080 * 1920)) + 40):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        angle, length = rng.uniform(0, np.pi), rng.uniform(8, 40) * scale
        tip = (int(x + np.cos(angle) * length), int(y + np.sin(angle) * length))
        cv2.line(fibres, (int(x), int(y)), tip, float(rng.choice([-1.0, 1.0])), 1, cv2.LINE_AA)
    mixed = GREY + field * 7 + cv2.GaussianBlur(fibres, (0, 0), 0.7) * 14
    return np.clip(mixed, 0, 255).astype(np.uint8)


def leak_frames(width: int, height: int, count: int) -> list[np.ndarray]:
    """Warm light off one edge, then the other. Every path is a whole number of turns, so the loop has no seam."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    xs, ys = xs / width, ys / height
    tall = height / width
    frames = []
    for index in range(count):
        turn = 2 * np.pi * index / count
        left = np.exp(-(((xs + 0.08 - 0.1 * np.sin(turn)) ** 2) / 0.09 + ((ys - 0.34 - 0.2 * np.sin(turn + 1.1)) * tall) ** 2 / 0.5))
        right = np.exp(-(((xs - 1.06 - 0.1 * np.cos(turn)) ** 2) / 0.07 + ((ys - 0.72 - 0.18 * np.cos(2 * turn)) * tall) ** 2 / 0.4))
        light = np.clip(left * (0.65 + 0.35 * np.sin(turn)) + right * (0.65 - 0.35 * np.sin(turn)), 0, 1)
        frames.append((np.stack((light, 0.46 * light**1.3, 0.14 * light**2), axis=-1) * 255).astype(np.uint8))
    return frames


def _leak_plate(project: Project, name: str, frames: list[np.ndarray], fps: int) -> str:
    folder = project.work / "overlays"
    folder.mkdir(parents=True, exist_ok=True)
    height, width = frames[0].shape[:2]
    dest = folder / f"{name}.mkv"
    raw = dest.with_suffix(".rgb")
    raw.write_bytes(b"".join(frame.tobytes() for frame in frames))
    args = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", str(raw)]
    ffmpeg.run([*args, "-c:v", "libx264", "-crf", "8", "-preset", "veryfast", "-pix_fmt", "yuv444p", str(dest)])
    raw.unlink()
    return str(dest)


def prepare(project: Project, items: list[dict[str, Any]], c: Any) -> list[dict[str, Any]]:
    """Build the plates this film needs, once. Grain and vignette are live filters and need none."""
    ready = []
    for item in items:
        kind = item["type"]
        name = f"{kind}-v{VERSION}-{c.width}x{c.height}-{c.fps}"
        dest = project.work / "overlays" / f"{name}.mkv"
        if kind in {"grain", "vignette"}:
            ready.append(item)
            continue
        if dest.exists():
            ready.append({**item, "plate": str(dest)})
            continue
        if kind == "dust":
            made = _plate(project, name, dust_frames(_even(c.width / DUST_SCALE), _even(c.height / DUST_SCALE), c.fps), c.fps)
        elif kind == "paper":
            made = _plate(project, name, [paper_frame(c.width, c.height)], c.fps)
        else:
            made = _leak_plate(project, name, leak_frames(_even(c.width / LEAK_SCALE), _even(c.height / LEAK_SCALE), LEAK_SECONDS * c.fps), c.fps)
        ready.append({**item, "plate": made})
    return ready


def _when(item: dict[str, Any]) -> str:
    if item.get("start") is None and item.get("end") is None:
        return ""
    return f":enable='between(t,{float(item.get('start', 0)):.3f},{float(item.get('end', 1e6)):.3f})'"


def graph(items: list[dict[str, Any]], base: str, c: Any, next_input: int) -> tuple[list[str], list[str], str]:
    """Extra inputs, filter lines, and the label the picture ends on."""
    inputs: list[str] = []
    lines: list[str] = []
    for index, item in enumerate(items):
        kind, amount, after = item["type"], item["amount"], f"over{index}"
        fit = f"scale={c.width}:{c.height}:flags=bicubic,format=yuv420p"
        if kind == "vignette":
            lines.append(f"[{base}]vignette=angle={0.2 + amount * 0.95:.4f}{_when(item)}[{after}]")
        elif kind == "grain":
            soft = max(c.width / 1080, 0.4) * 0.6
            lines.append(f"color=c=gray:s={c.width}x{c.height}:r={c.fps},format=yuv420p,lutyuv=y={GREY},noise=c0s=48:c0f=t+u:c0_seed={SEED},gblur=sigma={soft:.3f}[plate{index}]")
            lines.append(f"[{base}]format=yuv420p[under{index}]")
            lines.append(f"[under{index}][plate{index}]blend=c0_mode=hardlight:c0_opacity={amount:.4f}:shortest=1{_when(item)}[{after}]")
        elif kind == "light_leak":
            inputs += ["-stream_loop", "-1", "-i", item["plate"]]
            fade = ""
            if item.get("start") is not None:
                first, last = float(item["start"]), float(item.get("end", 1e6))
                fade = f",fade=t=in:st={first:.3f}:d={LEAK_FADE}" + (f",fade=t=out:st={max(last - LEAK_FADE, first):.3f}:d={LEAK_FADE}" if last < 1e6 else "")
            lines.append(f"[{next_input}:v]scale={c.width}:{c.height}:flags=bicubic{fade},format=gbrp[plate{index}]")
            lines.append(f"[{base}]{TO_RGB}[under{index}]")
            lines.append(f"[under{index}][plate{index}]blend=all_mode=screen:all_opacity={amount:.4f}:shortest=1{_when(item)},{TO_YUV}[{after}]")
            next_input += 1
        else:
            inputs += ["-stream_loop", "-1", "-i", item["plate"]]
            opacity = min(amount * (1.6 if kind == "dust" else 1.2), 1.0)
            lines.append(f"[{next_input}:v]{fit}[plate{index}]")
            lines.append(f"[{base}]format=yuv420p[under{index}]")
            lines.append(f"[under{index}][plate{index}]blend=c0_mode=hardlight:c0_opacity={opacity:.4f}:shortest=1{_when(item)}[{after}]")
            next_input += 1
        base = after
    return inputs, lines, base
